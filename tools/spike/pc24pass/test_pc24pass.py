#!/usr/bin/env python3
"""Tests for pc24pass.py, hermetic except the three checks named below.

Each check builds a small module in the shape remill v5.0.7 emits (the
%struct.State type chain the pass walks, a lifted define whose first parameter
is the State pointer, blocks labelled with remill's mangled semantic names)
and a stand-in helper IR (a define of __sa2_pc_round with no floating-point
arithmetic in it), then asserts on the pass's text output and its stats:

  1. marker-stripped re-run is byte-identical: instrumenting the output with
     its first line removed returns the same bytes, wraps nothing twice, does
     not splice the helper a second time;
  2. a define whose first parameter is not a pointer is skipped and counted,
     and an x86_fp80 op inside it is left alone;
  3. an op under a transcendental semantic label (FSIN, FSCALE, FPREM,
     FRNDINT) or under no remill label is not instrumented and is counted in
     skipped_by_semantic; the same op under FMULmem / FADDP / FDIVRP / FSQRT is;
  4. the enclosing-semantic rule and the `sqrtl` form: remill lowers `fsqrt` to
     a call to the host's `sqrtl` inside a block whose label carries no semantic
     name, so the op is attributed to the semantic whose body encloses it;
  5. the FIST/FISTP rewrite: with the integer helper's IR, `llvm.nearbyint.f80`
     and `fptosi x86_fp80 to iN` under a FIST semantic become calls that take
     the live control word, and neither survives in the lifted define;
  6. the control-word masks: remill's FLDCW stores `cw & -769`, which clears the
     precision-control field, and FNSTCW rebuilds from `cw & -3841`; the pass
     rewrites them to `-1` and `-3073` inside any define that touches the
     control word, and `--no-fix-control-word` turns that off.
  7. the FPATAN quadrant: remill divides st(1) by st(0) and calls one-argument
     `atanl`, losing the sign of st(0); the pass restores `atan2l` on the
     division's own operands, and `--no-fix-fpatan` turns that off. Which
     division that is comes from resolving the call's own argument backwards
     through remill's store/memcpy/load chain, so a second division between the
     chain and the call does not capture the site and an argument with no such
     history is refused rather than paired with the nearest `fdiv`;
  8. the 80-bit memory accessors: remill declares `__remill_read_memory_f80` and
     `__remill_write_memory_f80` `readnone` although their value travels through
     an out-pointer, so -O2 folds every `fld tbyte` to 0.0; the pass moves them
     to a fresh attribute group without it, leaves the group the return-value
     accessors share alone, and `--no-fix-f80-accessors` turns that off.

  9. the FXAM sign: remill's FXAM stores C1 = the sign of st(0) on its normal
     and denormal arms and C1 = 0 on its zero, NaN and infinity arms, where the
     x87 stores the sign on every class; the pass stores the sign the semantic
     already computed on those three arms, only after showing it is the sign
     bit of the value the zero test classifies, that its block dominates each
     store, and that every path from it to the FXAM's exit then stores it into
     C1 last (so an arm laid out before the sign, or storing through a pointer
     the pass does not recognise, refuses the FXAM rather than keeping its 0),
     and refuses (rewriting nothing) otherwise; `--no-fix-fxam` turns it off.

Each of 4, 5, 6, 7, 8 and 9 also re-runs the pass over its own marker-stripped output
and requires byte identity, so the new rules keep the idempotence guarantee.

Two checks go beyond text and need what the private tree holds, and print
"skipped" without it: `test_fxam_c1_matches_the_host_x87` compiles an FXAM in
remill's shape, before and after the pass, with the pinned zig, and holds its
C0 to C3 to this host's own `fxam` on +-{0, inf, NaN, denormal, normal}, and
its C1 (with remill's class bits, which differ where the pass's docstring says)
on +-{pseudo-denormal, unnormal, pseudo-NaN, pseudo-infinity};
`test_fxam_on_the_lifted_dispatchers` runs the pass over the whole-image lift's
two modules that hold an FXAM, the CRT's __trandisp1 and __trandisp2.

When the ten lifted files from the spike exist under
private/generated/spike/remill/ (ignored, never committed) a final check runs
the pass over them and requires 52 instrumented sites, none skipped by
semantic, no non-lifted define; otherwise it prints "skipped".

Documentation/scripts/check_emitter_determinism.py runs this file in every gate
pass and fails on any line that starts `skipped:`: the main tree holds what all
three need, so a skip there is a check that did not run, not a pass.

Usage: .venv/bin/python tools/spike/pc24pass/test_pc24pass.py
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import pc24pass  # noqa: E402

ROOT = HERE.parents[2]
REMILL_OUT = ROOT / "private" / "generated" / "spike" / "remill"

TYPES = """\
%struct.State = type { %struct.X86State }
%struct.X86State = type { %struct.ArchState, [32 x %union.VectorReg], %struct.ArithFlags, %union.anon, %struct.Segments, %struct.AddressSpace, %struct.GPR, %struct.X87Stack, %struct.MMX, %struct.FPUStatusFlags, %union.anon, %union.FPU, %struct.SegmentCaches, %struct.K_REG }
%union.FPU = type { %struct.anon.13 }
%struct.anon.13 = type { %struct.FpuFXSAVE, [96 x i8] }
%struct.FpuFXSAVE = type { %union.FPUControlWord, %union.SegmentSelector, %union.FPUAbridgedTagWord, i8, i16, i32, %union.SegmentSelector, i16, i32, %union.SegmentSelector, i16, %union.anon.1, %union.anon.1, [8 x %struct.FPUStackElem], [16 x %union.vec128_t] }
%union.FPUControlWord = type { i16 }
"""

HELPER_LL = """\
; ModuleID = 'pc_round.c'
define dso_local x86_fp80 @__sa2_pc_round(x86_fp80 noundef %v, i16 noundef zeroext %cw) #0 {
entry:
  ret x86_fp80 %v
}
"""

EXPECTED_GEP = "i64 0, i32 0, i32 11, i32 0, i32 0, i32 0, i32 0"


def label(sem: str, suffix: str = "I3RnWI9float80_tEEEP6MemoryS5_R5StateT_S8_.exit") -> str:
    """A block label in remill's shape: _ZN12_GLOBAL__N_1<len><name><template args>.exit."""
    return f"_ZN12_GLOBAL__N_1{len(sem)}{sem}{suffix}"


def module(body_blocks: list[tuple[str | None, str]], extra_defines: str = "") -> str:
    """A lifted define with the given (label, instruction lines) blocks, plus optional extra defines."""
    lines = [TYPES, "define ptr @sub_400000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {"]
    for lab, code in body_blocks:
        if lab:
            lines.append(f"{lab}:")
        lines.append(code)
    lines.append("  ret ptr %memory")
    lines.append("}")
    if extra_defines:
        lines.append(extra_defines)
    return "\n".join(lines) + "\n"


def check(cond: bool, what: str) -> None:
    print(("ok   " if cond else "FAIL ") + what)
    if not cond:
        raise SystemExit(1)


def test_marker_stripped_rerun() -> None:
    src = module([
        (None, "  %a = load x86_fp80, ptr %state, align 16\n  %b = load x86_fp80, ptr %state, align 16"),
        (label("FMULmem"), "  %mul.i.i = fmul x86_fp80 %a, %b"),
        (label("FADDP"), "  %add.i.i = fadd x86_fp80 %mul.i.i, %b"),
    ])
    out1, st1 = pc24pass.instrument(src, HELPER_LL)
    check(out1.startswith(pc24pass.MARKER), "output starts with the marker")
    check(st1["ops_instrumented"] == 2 and st1["by_semantic"] == {"FMULmem": 1, "FADDP": 1}, f"two ops instrumented by semantic: {st1['by_semantic']}")
    check(f"getelementptr inbounds %struct.State, ptr %state, {EXPECTED_GEP}" in out1, "control-word GEP is State index 11, FpuFXSAVE field 0")
    check(out1.count(f"define internal x86_fp80 @{pc24pass.HELPER}(") == 1, "helper spliced once")
    # the marker short-circuit
    out2, st2 = pc24pass.instrument(out1, HELPER_LL)
    check(out2 == out1 and st2["already_instrumented"], "second run over the marked output is a no-op")
    # the guard without the marker
    stripped = out1.split("\n", 1)[1]
    check(not stripped.startswith(pc24pass.MARKER), "marker line stripped")
    out3, st3 = pc24pass.instrument(stripped, HELPER_LL)
    check(out3 == out1, "marker-stripped re-run is byte-identical to the first output")
    check(st3["ops_instrumented"] == 0 and st3["skipped_already_wrapped"] == 2, f"nothing wrapped twice: {st3['skipped_already_wrapped']} sa2pre ops seen")
    check(st3["skipped_non_lifted"] == 1 and st3["skipped_non_lifted_defines"] == [pc24pass.HELPER], "the spliced helper's own define is skipped as non-lifted")
    check(out3.count(f"define internal x86_fp80 @{pc24pass.HELPER}(") == 1, "helper not spliced a second time")


def test_non_lifted_define_skipped() -> None:
    extra = "\n".join([
        "define i32 @clang_added(i32 %x, x86_fp80 %y) {",
        "entry:",
        "  %q = fmul x86_fp80 %y, %y",
        "  ret i32 %x",
        "}",
        "define x86_fp80 @no_params() {",
        "  ret x86_fp80 0xK00000000000000000000",
        "}",
    ])
    src = module([(label("FMULmem"), "  %mul.i.i = fmul x86_fp80 %a, %b")], extra)
    out, st = pc24pass.instrument(src, HELPER_LL)
    check(st["skipped_non_lifted"] == 2 and st["skipped_non_lifted_defines"] == ["clang_added", "no_params"], f"non-lifted defines skipped and counted: {st['skipped_non_lifted_defines']}")
    check(st["skipped_in_non_lifted"] == 1, "the x86_fp80 op inside the non-lifted define is counted, not wrapped")
    check("  %q = fmul x86_fp80 %y, %y\n" in out and "%sa2pre.q" not in out, "the non-lifted define's op is left untouched")
    check(st["ops_instrumented"] == 1 and "%sa2pre.mul.i.i = fmul" in out, "the lifted define's op is still instrumented")


def test_semantic_gate() -> None:
    blocks = [
        (label("FSIN"), "  %sin.mul = fmul x86_fp80 %a, %b"),
        (label("FSCALE"), "  %scale.mul = fmul x86_fp80 %a, %b"),
        (label("FPREM"), "  %prem.sub = fsub x86_fp80 %a, %b"),
        (label("FPREM1"), "  %prem1.div = fdiv x86_fp80 %a, %b"),
        (label("FRNDINT"), "  %rnd.add = fadd x86_fp80 %a, %b"),
        (label("IsDenormal", "E9float80_t.exit.i.i.i"), "  %den.mul = fmul x86_fp80 %a, %b"),
        (label("FMULmem"), "  %ok.mul = fmul x86_fp80 %a, %b"),
        (label("FADDP"), "  %ok.add = fadd x86_fp80 %a, %b"),
        (label("FDIVRP"), "  %ok.div = fdiv x86_fp80 %a, %b"),
        (label("FSUBRmem"), "  %ok.sub = fsub x86_fp80 %a, %b"),
        (label("FISUB"), "  %ok.isub = fsub x86_fp80 %a, %b"),
        (label("FSQRT"), "  %ok.sqrt = call x86_fp80 @llvm.sqrt.f80(x86_fp80 %a)"),
        # after the last semantic label of the define: nothing encloses it
        ("if.then.i.i", "  %nolabel.mul = fmul x86_fp80 %a, %b"),
    ]
    out, st = pc24pass.instrument(module(blocks), HELPER_LL)
    check(st["skipped_by_semantic"] == {"FSIN": 1, "FSCALE": 1, "FPREM": 1, "FPREM1": 1, "FRNDINT": 1, "IsDenormal": 1, pc24pass.NO_SEMANTIC: 1},
          f"transcendental / FPREM / FSCALE / FRNDINT / unenclosed ops skipped and counted: {st['skipped_by_semantic']}")
    for name in ("sin.mul", "scale.mul", "prem.sub", "prem1.div", "rnd.add", "den.mul", "nolabel.mul"):
        check(f"%sa2pre.{name}" not in out and f"  %{name} = f" in out, f"%{name} left untouched")
    check(st["ops_instrumented"] == 6 and st["by_semantic"] == {"FMULmem": 1, "FADDP": 1, "FDIVRP": 1, "FSUBRmem": 1, "FISUB": 1, "FSQRT": 1},
          f"basic-op semantics instrumented: {st['by_semantic']}")
    check(st["by_op"] == {"fmul": 1, "fadd": 1, "fdiv": 1, "fsub": 2, "sqrt": 1}, f"by_op: {st['by_op']}")
    check(st["by_semantic_enclosing"] == {}, "none of these needed the enclosing rule")
    check("%sa2pre.ok.sqrt = call x86_fp80 @llvm.sqrt.f80(x86_fp80 %a)" in out, "sqrt intrinsic wrapped under FSQRT")
    check("frem" not in pc24pass.RE_OP.pattern, "frem is not matched")
    check(pc24pass.semantic_of(label("FMULmem")) == "FMULmem" and pc24pass.semantic_of(label("FADDP")) == "FADDP"
          and pc24pass.semantic_of("fpclassify_not_nan.i.i.i.i") == pc24pass.NO_SEMANTIC and pc24pass.semantic_of(None) == pc24pass.NO_SEMANTIC,
          "semantic names are read by their Itanium length prefix")


# A static helper of remill's semantics library: its label carries no semantic name, and FSQRT's and
# FISTP's arithmetic both sit inside one (measured on remill v5.0.7, tools/s2/patch/probes.py).
CHECKED_UNARY = "_ZL19CheckedFloatUnaryOpIPF9float80_tS0_ES0_EDTclfp0_fp1_EER5StateT_T0_.exit.i"


def test_enclosing_semantic_and_sqrtl() -> None:
    """remill lowers fsqrt to a call to the host's sqrtl, inside a helper block, under the label DoFSQRT."""
    blocks = [
        (CHECKED_UNARY, "  %call1.i = call x86_fp80 @sqrtl(x86_fp80 noundef %a)"),
        (label("DoFSQRT"), "  %after = fmul x86_fp80 %call1.i, %b"),
    ]
    out, st = pc24pass.instrument(module(blocks), HELPER_LL)
    check(st["ops_instrumented"] == 2 and st["by_op"] == {"sqrtl": 1, "fmul": 1}, f"sqrtl and the fmul instrumented: {st['by_op']}")
    check(st["by_semantic"] == {"DoFSQRT": 2}, f"both attributed to DoFSQRT: {st['by_semantic']}")
    check(st["by_semantic_enclosing"] == {"DoFSQRT": 1}, f"the sqrtl call needed the enclosing rule: {st['by_semantic_enclosing']}")
    check("%sa2pre.call1.i = call x86_fp80 @sqrtl(x86_fp80 noundef %a)" in out, "the sqrtl call is renamed and rounded")
    check(f"%call1.i = call x86_fp80 @{pc24pass.HELPER}(x86_fp80 %sa2pre.call1.i" in out, "the original name now holds the rounded value")
    again, _ = pc24pass.instrument(out.split("\n", 1)[1], HELPER_LL)
    check(again == out, "marker-stripped re-run over the sqrtl form is byte-identical")


INT_HELPER_LL = """\
; ModuleID = 'x87_int.c'
define dso_local x86_fp80 @__sa2_x87_round_int(x86_fp80 noundef %v, i16 noundef zeroext %cw) #0 {
entry:
  ret x86_fp80 %v
}
define dso_local signext i16 @__sa2_fistp_i16(x86_fp80 noundef %v, i16 noundef zeroext %cw) #0 {
entry:
  ret i16 0
}
define dso_local i32 @__sa2_fistp_i32(x86_fp80 noundef %v, i16 noundef zeroext %cw) #0 {
entry:
  ret i32 0
}
define dso_local i64 @__sa2_fistp_i64(x86_fp80 noundef %v, i16 noundef zeroext %cw) #0 {
entry:
  ret i64 0
}
"""


def test_fist_rewrite() -> None:
    """FIST/FISTP: nearbyint follows the host rounding mode and fptosi is poison out of range; both are replaced."""
    blocks = [
        ("33", "  %r = call x86_fp80 @llvm.nearbyint.f80(x86_fp80 %a)"),
        (CHECKED_UNARY, "  %conv = fptosi x86_fp80 %r to i32"),
        (label("FISTPm32"), "  %keep = fptosi x86_fp80 %r to i32"),
    ]
    src = module(blocks)
    # without --int-helper-ll nothing is rewritten
    out0, st0 = pc24pass.instrument(src, HELPER_LL)
    check(st0["fist_rewrites"] == 0 and "nearbyint" in out0 and "fptosi" in out0, "the FIST rewrite is off unless the helper IR is given")
    out, st = pc24pass.instrument(src, HELPER_LL, INT_HELPER_LL)
    check(st["fist_rewrites"] == 3 and st["by_fist_semantic"] == {"FISTPm32": 3}, f"three FIST sites rewritten: {st['by_fist_semantic']}")
    check(f"%r = call x86_fp80 @{pc24pass.ROUND_INT_HELPER}(x86_fp80 %a, i16 %sa2cw.r)" in out, "nearbyint replaced by the control-word rounding helper")
    check(f"%conv = call i32 @{pc24pass.FISTP_HELPERS['i32']}(x86_fp80 %r, i16 %sa2cw.conv)" in out, "fptosi replaced by the width's fistp helper")
    check("nearbyint" not in out and "fptosi" not in out, "no nearbyint or fptosi survives in the lifted define")
    for name in pc24pass.INT_HELPERS:
        check(out.count(f"define internal ") >= 1 and f"@{name}(" in out, f"{name} spliced")
    again, _ = pc24pass.instrument(out.split("\n", 1)[1], HELPER_LL, INT_HELPER_LL)
    check(again == out, "marker-stripped re-run over the rewritten FIST form is byte-identical")


def test_control_word_masks() -> None:
    """remill's FLDCW clears the precision-control field before storing the control word; the pass keeps it."""
    gep = f"getelementptr inbounds %struct.State, ptr %state, {EXPECTED_GEP}"
    blocks = [
        ("1", f"  %7 = {gep}\n  %bf.clear.i = and i16 %call.i.i, -769\n  store i16 %bf.clear.i, ptr %7, align 16"),
        ("sw.bb.i", "  %bf.clear2.i = and i16 %bf.load.i, -3841\n"
                    "  br label %_ZN12_GLOBAL__N_16FNSTCWEP6MemoryR5State3MnWItE.exit"),
    ]
    src = module(blocks)
    out, st = pc24pass.instrument(src, HELPER_LL)
    check(st["control_word_masks_fixed"] == 2, f"both masks rewritten: {st['control_word_masks_fixed']}")
    check("%bf.clear.i = and i16 %call.i.i, -1   ; sa2-pc24pass cw (-769)" in out, "-769 (0xFCFF, clears precision control) becomes -1")
    check("%bf.clear2.i.sa2cw = and i16 %bf.load.i, -3073   ; sa2-pc24pass cw (-3841)" in out, "-3841 (0xF0FF) becomes -3073 (0xF3FF, rounding control only)")
    check("%bf.clear2.i = or i16 %bf.clear2.i.sa2cw, 64   ; sa2-pc24pass cw bit 6" in out,
          "FNSTCW's read-back sets bit 6, as the hardware reads it (fldcw 0x023F -> fnstcw 0x027F)")
    check(st["control_word_bit6_set"] == 1, f"bit 6 set at the one FNSTCW read-back: {st['control_word_bit6_set']}")
    # the same constant outside FNSTCW's arms keeps its precision fix and gets no bit 6
    stray = module([("1", f"  %7 = {gep}\n  %m = and i16 %x, -3841\n  ret ptr %0")])
    sout, sst = pc24pass.instrument(stray, HELPER_LL)
    check("%m = and i16 %x, -3073" in sout and "cw bit 6" not in sout
          and sst["control_word_bit6_refused"] == 1,
          "-3841 outside an FNSTCW arm is not given bit 6, and is counted")
    off, sto = pc24pass.instrument(src, HELPER_LL, None, False)
    check(sto["control_word_masks_fixed"] == 0 and "-769" in off, "--no-fix-control-word leaves the masks alone")
    # a define that never touches the control word is not rewritten
    other = module([("1", "  %bf.clear.i = and i16 %x, -769")])
    _, st2 = pc24pass.instrument(other, HELPER_LL)
    check(st2["control_word_masks_fixed"] == 0, "a define with no control-word GEP is left alone")
    again, _ = pc24pass.instrument(out.split("\n", 1)[1], HELPER_LL)
    check(again == out, "marker-stripped re-run over the rewritten masks is byte-identical")


FPATAN_LABEL = label("DoFPATAN", "EP6MemoryR5State2InIjES4_ItE.exit")

# remill's own chain from the quotient to the call, in the shape sub_0086BF68 carries it:
# store the fp80 into a `union_ld`, memcpy ten bytes into the semantic's `float80_t`
# return slot, reload the two halves, store them into the argument slot, memcpy again,
# load back as an x86_fp80. Nothing connects the two ends as SSA values.
FPATAN_CHAIN = """\
  %ld2 = getelementptr inbounds %union.union_ld, ptr %ldu14, i64 0, i32 0
  store x86_fp80 %div.i.i, ptr %ld2, align 16
  %ret0 = getelementptr inbounds %struct.float80_t, ptr %retval, i64 0, i32 0, i64 0
  %cp1 = call ptr @memcpy(ptr noundef nonnull %ret0, ptr noundef nonnull %ldu14, i64 noundef 10)
  %lo = load i64, ptr %retval, align 8
  %hiptr = getelementptr inbounds %struct.float80_t, ptr %retval, i64 0, i32 0, i64 8
  %hi = load i16, ptr %hiptr, align 8
  store i64 %lo, ptr %val, align 8
  %vhi = getelementptr inbounds %struct.float80_t, ptr %val, i64 0, i32 0, i64 8
  store i16 %hi, ptr %vhi, align 8
  %val0 = getelementptr inbounds %struct.float80_t, ptr %val, i64 0, i32 0, i64 0
  %cp2 = call ptr @memcpy(ptr noundef nonnull %ldu223, ptr noundef nonnull %val0, i64 noundef 10)
  %ld229 = getelementptr inbounds %union.union_ld, ptr %ldu223, i64 0, i32 0
  %q = load x86_fp80, ptr %ld229, align 16"""


def test_fpatan_quadrant() -> None:
    """remill divides st(1) by st(0) and calls one-argument `atanl`; the pass restores the
    two-argument form on the division's own operands -- but only after resolving the call's
    own argument back to that division through remill's store/memcpy/load chain."""
    blocks = [
        ("body", "  %div.i.i = fdiv x86_fp80 %y, %x\n" + FPATAN_CHAIN),
        (FPATAN_LABEL, "  %call1.i = call x86_fp80 @atanl(x86_fp80 noundef %q) #14"),
    ]
    src = module(blocks) + "declare dso_local x86_fp80 @atanl(x86_fp80 noundef) local_unnamed_addr #2\n"
    out, st = pc24pass.instrument(src, HELPER_LL)
    check(st["fpatan_rewrites"] == 1, f"the atanl call is rewritten: {st['fpatan_rewrites']}")
    check("%call1.i = call x86_fp80 @atan2l(x86_fp80 noundef %y, x86_fp80 noundef %x) #14"
          "   ; sa2-pc24pass fpatan" in out, "it becomes atan2l on the division's own operands")
    check(pc24pass.ATAN2_DECLARE in out, "atan2l is declared")
    check(st["fpatan_sites"][0]["st1"] == "%y" and st["fpatan_sites"][0]["st0"] == "%x"
          and st["fpatan_sites"][0]["quotient"] == "%q", f"site recorded: {st['fpatan_sites'][0]}")
    check(st["fpatan_refused"] == 0, "nothing refused when the argument does resolve")
    check(st["skipped_by_semantic"] == {"DoFPATAN": 1},
          f"the division itself is still not PC-rounded: {st['skipped_by_semantic']}")
    off, sto = pc24pass.instrument(src, HELPER_LL, None, True, True, False)
    check(sto["fpatan_rewrites"] == 0 and "@atanl(x86_fp80 noundef %q)" in off,
          "--no-fix-fpatan leaves the call alone")
    # an `atanl` with no FPATAN division before it is not touched
    lone = module([(label("FSIN"), "  %c = call x86_fp80 @atanl(x86_fp80 noundef %q)")])
    _, st2 = pc24pass.instrument(lone, HELPER_LL)
    check(st2["fpatan_rewrites"] == 0, "an atanl outside an FPATAN semantic is left alone")
    again, st3 = pc24pass.instrument(out.split("\n", 1)[1], HELPER_LL)
    check(st3["fpatan_rewrites"] == 0, "the rewritten call is not rewritten twice")
    check(again == out, "marker-stripped re-run over the rewritten FPATAN is byte-identical")


def test_fpatan_refuses_an_unrelated_division() -> None:
    """The rule the G2 fold replaced: "the last `fdiv x86_fp80` before the call", with no
    check that its quotient reaches the call. A division whose result goes nowhere near the
    argument must not be substituted for the argument's own history."""
    # Same module, but a second division sits between the chain and the call. The old
    # nearest-`fdiv` rule would take *its* operands; the argument's own history is the
    # first division's.
    blocks = [
        ("body", "  %div.i.i = fdiv x86_fp80 %y, %x\n" + FPATAN_CHAIN
                 + "\n  %other = fdiv x86_fp80 %p, %r"),
        (FPATAN_LABEL, "  %call1.i = call x86_fp80 @atanl(x86_fp80 noundef %q) #14"),
    ]
    src = module(blocks) + "declare dso_local x86_fp80 @atanl(x86_fp80 noundef) local_unnamed_addr #2\n"
    out, st = pc24pass.instrument(src, HELPER_LL)
    check(st["fpatan_rewrites"] == 1, "the site is still rewritten")
    check(st["fpatan_sites"][0]["st1"] == "%y" and st["fpatan_sites"][0]["st0"] == "%x",
          f"on the division the argument came from, not the nearest one: {st['fpatan_sites'][0]}")
    check(st["fpatan_nearest_fdiv_agreed"] == 0 and st["fpatan_sites"][0]["nearest_fdiv_agreed"] is False,
          "and the disagreement with the old nearest-fdiv rule is recorded")

    # An argument with no history at all: nothing is rewritten, and the site is counted.
    bare = module([
        ("body", "  %div.i.i = fdiv x86_fp80 %y, %x"),
        (FPATAN_LABEL, "  %call1.i = call x86_fp80 @atanl(x86_fp80 noundef %q) #14"),
    ]) + "declare dso_local x86_fp80 @atanl(x86_fp80 noundef) local_unnamed_addr #2\n"
    out2, st2 = pc24pass.instrument(bare, HELPER_LL)
    check(st2["fpatan_rewrites"] == 0 and st2["fpatan_refused"] == 1,
          f"an unresolved argument is refused, not guessed: {st2['fpatan_refused']}")
    check("@atanl(x86_fp80 noundef %q)" in out2, "remill's call is left exactly as it was")
    check(st2["fpatan_refused_sites"][0]["nearest_fdiv"] == ["%y", "%x"],
          "the division the old rule would have used is recorded with the refusal")
    check(pc24pass.ATAN2_DECLARE not in out2, "and atan2l is not declared for a refused site")
    again, _ = pc24pass.instrument(out.split("\n", 1)[1], HELPER_LL)
    check(again == out, "marker-stripped re-run over the rewritten FPATAN is byte-identical")


def test_f80_accessor_attrs() -> None:
    """remill's out-pointer 80-bit accessors are declared `readnone`; at -O2 that folds every
    `fld tbyte` to 0.0. The pass moves them off the shared group, leaving the return-value
    accessors alone."""
    decls = ("declare dso_local ptr @__remill_read_memory_f80(ptr noundef readnone, i32 noundef, "
             "ptr noundef nonnull align 16 dereferenceable(16)) #5\n"
             "declare dso_local ptr @__remill_write_memory_f80(ptr noundef, i32 noundef, "
             "ptr noundef nonnull align 16 dereferenceable(16)) #5\n"
             "declare dso_local i32 @__remill_read_memory_32(ptr noundef readnone, i32 noundef) #5\n"
             "attributes #5 = { noduplicate noinline nounwind optnone readnone \"no-builtins\" }\n")
    src = module([("1", "  %r = fmul x86_fp80 %a, %b")]) + decls
    out, st = pc24pass.instrument(src, HELPER_LL)
    check(st["f80_accessor_attrs_fixed"] == 2, f"both 80-bit accessors repaired: {st['f80_accessor_attrs_fixed']}")
    check({s["intrinsic"] for s in st["f80_accessor_sites"]} ==
          {"__remill_read_memory_f80", "__remill_write_memory_f80"},
          "only the out-pointer forms are touched")
    check(all(s["removed"] == ["readnone"] for s in st["f80_accessor_sites"]),
          f"`readnone` is what was removed: {[s['removed'] for s in st['f80_accessor_sites']]}")
    check("attributes #5 = { noduplicate noinline nounwind optnone readnone \"no-builtins\" }" in out,
          "the shared group is not edited, so every other intrinsic keeps its attributes")
    check("attributes #6 = { noduplicate noinline nounwind optnone \"no-builtins\" }" in out,
          "a fresh group without readnone is appended")
    check("@__remill_read_memory_f80(ptr noundef readnone, i32 noundef, ptr noundef nonnull align 16 "
          "dereferenceable(16)) #6   ; sa2-pc24pass f80" in out, "the read accessor points at it")
    check("@__remill_read_memory_32(ptr noundef readnone, i32 noundef) #5" in out,
          "the return-value accessor is left on the shared group")
    off, sto = pc24pass.instrument(src, HELPER_LL, None, True, False)
    check(sto["f80_accessor_attrs_fixed"] == 0 and "#6" not in off,
          "--no-fix-f80-accessors leaves the declarations alone")
    clean, st2 = pc24pass.instrument(module([("1", "  %r = fmul x86_fp80 %a, %b")]), HELPER_LL)
    check(st2["f80_accessor_attrs_fixed"] == 0, "a module with no 80-bit memory operand is untouched")
    again, st3 = pc24pass.instrument(out.split("\n", 1)[1], HELPER_LL)
    check(st3["f80_accessor_attrs_fixed"] == 0, "the repaired declarations are not repaired twice")
    check(again == out, "marker-stripped re-run over the repaired declarations is byte-identical")


# remill's FXAM, as it lifts 0x0082C064 in __trandisp2 (private/generated/s3/whole_image/lift/next/
# sub_0082C037.ll): the sign is read once, before the class branches; the zero (sw.bb32.i), NaN
# (sw.bb.i) and infinity (sw.bb23.i) arms store C1 = 0; only the normal/denormal arm stores it.
FXAM_EXIT = "_ZN12_GLOBAL__N_16DoFXAMEP6MemoryR5State2InIjES4_ItE.exit"
SW = "getelementptr inbounds %struct.State, ptr %state, i64 0, i32 0, i32 9"
FPU_STATUS_TYPE = ("%struct.FPUStatusFlags = type { " + ", ".join(["i8"] * 20) + ", [4 x i8] }\n")


def fxam_body(load: str = "  %97 = load x86_fp80, ptr %opp, align 1",
              sign: str = "  %98 = bitcast x86_fp80 %97 to i80\n  %.lobit.i = lshr i80 %98, 79\n"
                          "  %99 = trunc i80 %.lobit.i to i8",
              normal_c1: str = "%99", zero_c1: str = "0", extra_normal: str = "") -> str:
    """One FXAM in remill's shape; the defaults are remill's own."""
    return f"""\
  %opp = getelementptr inbounds %struct.State, ptr %state, i64 0, i32 0, i32 0
  br label %90

90:
{load}
{sign}
  %iszero.i = fcmp oeq x86_fp80 %97, 0xK00000000000000000000
  br i1 %iszero.i, label %sw.bb32.i, label %fpclassify_not_zero.i

fpclassify_not_zero.i:                            ; preds = %90
  %cmp19.i = fcmp uno x86_fp80 %97, 0xK00000000000000000000
  br i1 %cmp19.i, label %sw.bb.i, label %fpclassify_not_nan.i

fpclassify_not_nan.i:                             ; preds = %fpclassify_not_zero.i
  %100 = call x86_fp80 @llvm.fabs.f80(x86_fp80 %97)
  %isinf.i = fcmp oeq x86_fp80 %100, 0xK7FFF8000000000000000
  br i1 %isinf.i, label %sw.bb23.i, label %fpclassify_not_inf.i

fpclassify_not_inf.i:                             ; preds = %fpclassify_not_nan.i
{extra_normal}  %isnormal.i = fcmp uge x86_fp80 %100, 0xK00018000000000000000
  %c052.i = {SW}, i32 1
  store i8 0, ptr %c052.i, align 1
  %c154.i = {SW}, i32 3
  store i8 {normal_c1}, ptr %c154.i, align 1
  %c256.i = {SW}, i32 5
  store i8 1, ptr %c256.i, align 1
  %c358.i = {SW}, i32 7
  br i1 %isnormal.i, label %sw.bb50.i, label %sw.bb41.i

sw.bb.i:                                          ; preds = %fpclassify_not_zero.i
  %c0.i = {SW}, i32 1
  store i8 1, ptr %c0.i, align 1
  %c1.i = {SW}, i32 3
  store i8 0, ptr %c1.i, align 1
  %c2.i = {SW}, i32 5
  store i8 0, ptr %c2.i, align 1
  %c3.i = {SW}, i32 7
  store i8 0, ptr %c3.i, align 1
  br label %{FXAM_EXIT}

sw.bb23.i:                                        ; preds = %fpclassify_not_nan.i
  %c025.i = {SW}, i32 1
  store i8 1, ptr %c025.i, align 1
  %c127.i = {SW}, i32 3
  store i8 0, ptr %c127.i, align 1
  %c229.i = {SW}, i32 5
  store i8 1, ptr %c229.i, align 1
  %c331.i = {SW}, i32 7
  store i8 0, ptr %c331.i, align 1
  br label %{FXAM_EXIT}

sw.bb32.i:                                        ; preds = %90
  %c034.i = {SW}, i32 1
  store i8 0, ptr %c034.i, align 1
  %c136.i = {SW}, i32 3
  store i8 {zero_c1}, ptr %c136.i, align 1
  %c238.i = {SW}, i32 5
  store i8 0, ptr %c238.i, align 1
  %c340.i = {SW}, i32 7
  store i8 1, ptr %c340.i, align 1
  br label %{FXAM_EXIT}

sw.bb41.i:                                        ; preds = %fpclassify_not_inf.i
  store i8 1, ptr %c358.i, align 1
  br label %{FXAM_EXIT}

sw.bb50.i:                                        ; preds = %fpclassify_not_inf.i
  store i8 0, ptr %c358.i, align 1
  br label %{FXAM_EXIT}

{FXAM_EXIT}: ; preds = %sw.bb50.i, %sw.bb41.i, %sw.bb32.i, %sw.bb23.i, %sw.bb.i
  ret ptr %memory
"""


def fxam_module(body: str, types: str = TYPES + FPU_STATUS_TYPE) -> str:
    return (types + "define ptr @sub_400000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {\n"
            + body + "}\ndeclare x86_fp80 @llvm.fabs.f80(x86_fp80)\n")


def test_fxam_sign() -> None:
    """remill's FXAM stores C1 = 0 on the zero, NaN and infinity arms; the x87 stores the sign."""
    src = fxam_module(fxam_body())
    out, st = pc24pass.instrument(src, HELPER_LL)
    check(st["fxam_c1_stores_fixed"] == 3 and st["fxam_refused"] == 0,
          f"the three constant C1 stores are rewritten: {st['fxam_c1_stores_fixed']}, refused {st['fxam_refused_sites']}")
    for ptr in ("%c1.i", "%c127.i", "%c136.i"):
        check(f"  store i8 %99, ptr {ptr}, align 1{pc24pass.FXAM_TAG}" in out and f"store i8 0, ptr {ptr}," not in out,
              f"C1 on the arm that stores through {ptr} is the sign")
    check("  store i8 %99, ptr %c154.i, align 1\n" in out, "the normal/denormal arm's own sign store is untouched")
    for kept in ("store i8 1, ptr %c0.i", "store i8 0, ptr %c2.i", "store i8 0, ptr %c3.i",
                 "store i8 1, ptr %c025.i", "store i8 1, ptr %c229.i", "store i8 0, ptr %c331.i",
                 "store i8 0, ptr %c034.i", "store i8 0, ptr %c238.i", "store i8 1, ptr %c340.i"):
        check(f"  {kept}, align 1\n" in out, f"class bit kept as remill wrote it: {kept}")
    site = st["fxam_sites"][0]
    check(site["sign"] == "%99" and site["operand"] == "%97" and site["arms"] == ["sw.bb.i", "sw.bb23.i", "sw.bb32.i"],
          f"site recorded with the sign, the classified value and the three arms: {site}")
    off, sto = pc24pass.instrument(src, HELPER_LL, None, True, True, True, False)
    check(sto["fxam_c1_stores_fixed"] == 0 and "store i8 0, ptr %c136.i" in off, "--no-fix-fxam leaves the stores alone")
    check(off.split("\n", 1)[1] == src.rstrip("\n") + "\n" or off.split("\n", 1)[1] == src,
          "and with it off the module is otherwise untouched")
    again, st2 = pc24pass.instrument(out.split("\n", 1)[1], HELPER_LL)
    check(again == out and st2["fxam_c1_stores_fixed"] == 0 and st2["fxam_already_signed"] == 1,
          "marker-stripped re-run over the rewritten FXAM is byte-identical, and it is counted as already signed")


def test_fxam_refusals() -> None:
    """Nothing is rewritten on a guess: each FXAM that does not show its sign is left and recorded."""
    def refused(src: str, why: str, types: str | None = None) -> None:
        out, st = pc24pass.instrument(src, HELPER_LL)
        check(st["fxam_c1_stores_fixed"] == 0 and st["fxam_refused"] == 1 and pc24pass.FXAM_TAG not in out,
              f"refused, nothing rewritten ({why}): {[r['reason'] for r in st['fxam_refused_sites']]}")
    # the value stored into C1 is not the sign bit of what the zero test classifies
    refused(fxam_module(fxam_body(sign="  %99 = load i8, ptr %opp, align 1")), "C1's value is a load, not the sign")
    refused(fxam_module(fxam_body(sign="  %98 = bitcast x86_fp80 %97 to i80\n  %.lobit.i = lshr i80 %98, 78\n"
                                       "  %99 = trunc i80 %.lobit.i to i8")), "bit 78 is not the sign")
    # the sign is defined on the normal arm only, which does not dominate the other arms
    refused(fxam_module(fxam_body(sign="", extra_normal="  %98 = bitcast x86_fp80 %97 to i80\n"
                                  "  %.lobit.i = lshr i80 %98, 79\n  %99 = trunc i80 %.lobit.i to i8\n")),
            "the sign's block does not dominate the zero arm")
    # a constant other than 0, and two different signs
    refused(fxam_module(fxam_body(zero_c1="1")), "a constant 1 stored into C1")
    refused(fxam_module(fxam_body(zero_c1="%other.sign").replace(
        "  %iszero.i", "  %other.sign = trunc i80 %98 to i8\n  %iszero.i")),
            "two different values stored into C1")
    # a block the pass cannot parse (a quoted label): the CFG is incomplete, so no dominance claim
    quoted = fxam_body().replace("  br label %90\n", '  br label %"odd block"\n\n"odd block":\n  br label %90\n')
    refused(fxam_module(quoted), "a quoted block label the CFG reader does not parse")
    # an arm the plan would miss: laid out before the sign's line, or storing through a pointer
    # that is not C1's recognised getelementptr; refused, not fixed on the other arms alone
    body = fxam_body()
    zero_arm = body[body.index("sw.bb32.i:"):body.index("sw.bb41.i:")]
    moved = body.replace(zero_arm, "").replace("  br label %90\n\n90:", "  br label %90\n\n" + zero_arm + "90:")
    refused(fxam_module(moved), "the zero arm laid out before the sign")
    # every constant arm laid out before the sign: nothing is left to plan, which must not read
    # as "already signed" while all three arms keep C1 = 0
    arms = [body[body.index(a):body.index(b)] for a, b in (("sw.bb.i:", "sw.bb23.i:"),
            ("sw.bb23.i:", "sw.bb32.i:"), ("sw.bb32.i:", "sw.bb41.i:"))]
    early_arms = body
    for arm in arms:
        early_arms = early_arms.replace(arm, "")
    early_arms = early_arms.replace("  br label %90\n\n90:", "  br label %90\n\n" + "".join(arms) + "90:")
    refused(fxam_module(early_arms), "every constant arm laid out before the sign, none left to plan")
    refused(fxam_module(body.replace(f"  %c136.i = {SW}, i32 3", "  %c136.i = getelementptr inbounds i8, ptr %state, i64 99")),
            "the zero arm's C1 store through an unrecognised pointer")
    # an arm laid out after the exit that leaves the define without passing the exit label: its
    # store is outside the FXAM's lines and no path through it reaches the exit
    bypass = body.replace(zero_arm, "") + zero_arm.replace(f"  br label %{FXAM_EXIT}\n", "  ret ptr %memory\n")
    refused(fxam_module(bypass), "the zero arm laid out after the exit, returning without passing it")
    # the sign stored into C1 before the class branch, then the zero arm's 0 through a pointer the
    # pass does not recognise: the path reaches the exit signed as far as the recognised stores go
    common = body.replace("  %iszero.i = fcmp", f"  %c1com = {SW}, i32 3\n  store i8 %99, ptr %c1com, align 1\n"
                          "  %iszero.i = fcmp").replace(f"  %c136.i = {SW}, i32 3",
                                                       "  %c136.i = getelementptr inbounds i8, ptr %state, i64 99")
    refused(fxam_module(common), "the sign stored before the branch, then the zero arm's 0 through an unrecognised pointer")
    # C1's address cannot be derived from the module's types
    out, st = pc24pass.instrument(fxam_module(fxam_body(), TYPES), HELPER_LL)
    check(st["fxam_refused"] == 1 and "FPUStatusFlags" in st["fxam_refused_sites"][0]["reason"]
          and st["fxam_c1_stores_fixed"] == 0, "no %struct.FPUStatusFlags type: refused, not guessed")
    # a C1 store before the sign exists belongs to an earlier instruction and is left alone
    early = fxam_body().replace("  br label %90\n", f"  %c1.pre = {SW}, i32 3\n  store i8 0, ptr %c1.pre, align 1\n  br label %90\n")
    out, st = pc24pass.instrument(fxam_module(early), HELPER_LL)
    check(st["fxam_c1_stores_fixed"] == 3 and "  store i8 0, ptr %c1.pre, align 1\n" in out
          and st["fxam_sites"][0]["earlier_c1_stores_left"] == 1,
          "an earlier instruction's C1 store is not an FXAM arm: left, counted, and the FXAM still fixed")


# The ten operands, as 80-bit values (sign+exponent, significand): +-0, +-inf, +-quiet NaN,
# +-the smallest denormal, +-1.0.
FXAM_OPERANDS = [("+0", "0000", "0000000000000000"), ("-0", "8000", "0000000000000000"),
                 ("+inf", "7fff", "8000000000000000"), ("-inf", "ffff", "8000000000000000"),
                 ("+nan", "7fff", "c000000000000000"), ("-nan", "ffff", "c000000000000000"),
                 ("+denormal", "0000", "0000000000000001"), ("-denormal", "8000", "0000000000000001"),
                 ("+normal", "3fff", "8000000000000000"), ("-normal", "bfff", "8000000000000000")]

# Four encodings remill does not class as the x87 does, each with both signs: a pseudo-denormal
# (exponent 0, integer bit set), which the x87 reports as denormal and remill as normal; and three
# unsupported encodings (integer bit clear under a non-zero exponent: an unnormal, a pseudo-NaN,
# a pseudo-infinity), which the x87 reports as unsupported (C3 C2 C0 = 0 0 0) and remill as NaN.
# The pass leaves the class bits as remill wrote them; C1 is the sign on these too.
FXAM_ODD_OPERANDS = [("+pseudoden", "0000", "8000000000000000"), ("-pseudoden", "8000", "8000000000000000"),
                     ("+unnormal", "3fff", "4000000000000000"), ("-unnormal", "bfff", "4000000000000000"),
                     ("+pseudonan", "7fff", "4000000000000000"), ("-pseudonan", "ffff", "4000000000000000"),
                     ("+pseudoinf", "7fff", "0000000000000000"), ("-pseudoinf", "ffff", "0000000000000000")]
REMILL_CLASS_GAP = {"pseudoden": ["C3"], "unnormal": ["C0"], "pseudonan": ["C0"], "pseudoinf": ["C0"]}

# A State whose fields are byte arrays, so the flags sit at a fixed offset: the operand is
# field 0 (16 bytes) and %struct.FPUStatusFlags field 9, at 16 + 8 * 8 = 80.
EXEC_TYPES = ("%struct.State = type { %struct.X86State }\n"
              "%struct.X86State = type { [16 x i8], " + ", ".join(["[8 x i8]"] * 8)
              + ", %struct.FPUStatusFlags, [8 x i8], %union.FPU }\n" + FPU_STATUS_TYPE
              + "%union.FPU = type { %struct.anon.13 }\n%struct.anon.13 = type { %struct.FpuFXSAVE, [96 x i8] }\n"
              "%struct.FpuFXSAVE = type { %union.FPUControlWord }\n%union.FPUControlWord = type { i16 }\n")

EXEC_HARNESS = r"""
#include <stdio.h>
#include <string.h>
void *sub_400000(void *state, unsigned pc, void *memory);
int main(void) {
    char name[16], hi[8], lo[24];
    while (scanf("%15s %7s %23s", name, hi, lo) == 3) {
        unsigned char v[16] = {0}, state[512];
        unsigned long long m = strtoull(lo, 0, 16);
        unsigned se = (unsigned)strtoul(hi, 0, 16);
        memcpy(v, &m, 8); v[8] = se & 0xff; v[9] = se >> 8;
        memset(state, 0xAA, sizeof state);
        memcpy(state, v, 10);
        sub_400000(state, 0, 0);
        long double ld; unsigned short sw;
        memcpy(&ld, v, 10);
        __asm__ volatile("fldt %1\n\tfxam\n\tfnstsw %0\n\tfstp %%st(0)" : "=m"(sw) : "m"(ld) : "st");
        printf("%s %d%d%d%d %d%d%d%d\n", name, state[81], state[83], state[85], state[87],
               (sw >> 8) & 1, (sw >> 9) & 1, (sw >> 10) & 1, (sw >> 14) & 1);
    }
    return 0;
}
"""
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"


def run_fxam_exec(ll_text: str, work: Path) -> dict[str, tuple[str, str]]:
    """{operand: (lifted C0C1C2C3, host C0C1C2C3)} for the module compiled with the pinned zig."""
    import subprocess
    (work / "fxam.ll").write_text(ll_text)
    (work / "harness.c").write_text("#include <stdlib.h>\n" + EXEC_HARNESS)
    for cmd in ([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O2", "-w", "-c", "fxam.ll", "-o", "fxam.o"],
                [str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O2", "-w", "harness.c", "fxam.o", "-o", "fxam_exec"]):
        p = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
        check(p.returncode == 0, f"{cmd[1]} {cmd[-3]}: {p.stderr[-800:]}")
    p = subprocess.run([str(work / "fxam_exec")],
                       input="".join(f"{n} {h} {l}\n" for n, h, l in FXAM_OPERANDS + FXAM_ODD_OPERANDS),
                       capture_output=True, text=True)
    check(p.returncode == 0, f"the FXAM program ran: {p.stderr[-400:]}")
    return {n: (a, b) for n, a, b in (ln.split() for ln in p.stdout.splitlines())}


def test_fxam_c1_matches_the_host_x87() -> None:
    """Compiled, before and after the pass, against this host's own `fxam` on each class, and on
    the encodings remill does not class as the x87 does."""
    if not ZIG.is_file():
        print(f"skipped: the pinned zig is not at {ZIG}")
        return
    import tempfile
    src = EXEC_TYPES + ("define ptr @sub_400000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {\n"
                        + fxam_body() + "}\ndeclare x86_fp80 @llvm.fabs.f80(x86_fp80)\n")
    fixed, st = pc24pass.instrument(src, HELPER_LL)
    unfixed, _ = pc24pass.instrument(src, HELPER_LL, None, True, True, True, False)
    check(st["fxam_c1_stores_fixed"] == 3, "the compiled module's three stores are rewritten")
    with tempfile.TemporaryDirectory(prefix="fxam-") as d:
        (Path(d) / "a").mkdir()
        (Path(d) / "b").mkdir()
        after = run_fxam_exec(fixed, Path(d) / "a")
        before = run_fxam_exec(unfixed, Path(d) / "b")
    names = [n for n, _, _ in FXAM_OPERANDS + FXAM_ODD_OPERANDS]
    check(sorted(after) == sorted(names) and sorted(before) == sorted(names), f"every operand ran: {sorted(after)}")
    host = {n: h for n, (_, h) in after.items()}
    check(all(host[n][1] == ("1" if n.startswith("-") else "0") for n in host),
          f"this host's fxam sets C1 to the sign on every class and every encoding: {host}")
    for n, _, _ in FXAM_OPERANDS:
        check(after[n][0] == after[n][1], f"{n}: after the pass C0C1C2C3 {after[n][0]}, the host's fxam {after[n][1]}")
    bits = ("C0", "C1", "C2", "C3")
    for n, _, _ in FXAM_ODD_OPERANDS:
        lifted, h = after[n]
        differ = [b for i, b in enumerate(bits) if lifted[i] != h[i]]
        check(differ == REMILL_CLASS_GAP[n[1:]],
              f"{n}: after the pass C0C1C2C3 {lifted}, the host's fxam {h}: C1 the host's, and remill's class "
              f"differs in {differ} (expected {REMILL_CLASS_GAP[n[1:]]}, the pass's docstring)")
    wrong = sorted(n for n, (lifted, h) in before.items() if lifted[1] != h[1])
    check(wrong == sorted(["-0", "-inf", "-nan", "-unnormal", "-pseudonan", "-pseudoinf"]),
          f"before the pass, remill's C1 differs from the host's on exactly the negative zero, infinity, NaN "
          f"and unsupported encodings: {wrong}")
    check(all(before[n][0][0] + before[n][0][2:] == after[n][0][0] + after[n][0][2:] for n in names),
          "the pass changes no class bit: C0, C2 and C3 are remill's, before and after")


def test_fxam_on_the_lifted_dispatchers() -> None:
    """The whole-image lift's two FXAM modules, when the private tree holds them."""
    lift = ROOT / "private" / "generated" / "s3" / "whole_image" / "lift" / "next"
    helper_ll = ROOT / "private" / "generated" / "spike" / "pc24pass" / "pc_round.ll"
    want = {"sub_0082C037.ll": (2, 6), "sub_0082BFD0.ll": (1, 3)}   # __trandisp2, __trandisp1
    if not helper_ll.exists() or not all((lift / f).exists() for f in want):
        print("skipped: the whole-image lift's __trandisp1/2 or the helper IR are not under private/generated/")
        return
    helper = helper_ll.read_text()
    for f, (fxams, stores) in want.items():
        out, st = pc24pass.instrument((lift / f).read_text(), helper)
        check(len(st["fxam_sites"]) == fxams and st["fxam_c1_stores_fixed"] == stores and st["fxam_refused"] == 0,
              f"{f}: {len(st['fxam_sites'])} FXAMs, {st['fxam_c1_stores_fixed']} C1 stores fixed, "
              f"{st['fxam_refused']} refused")
        check(all(s["arms"] and len(s["arms"]) == 3 for s in st["fxam_sites"]),
              f"{f}: each FXAM's zero, NaN and infinity arms: {[s['arms'] for s in st['fxam_sites']]}")
        again, _ = pc24pass.instrument(out.split("\n", 1)[1], helper)
        check(again == out, f"{f}: marker-stripped re-run byte-identical")


# remill's FLD m32 / m64 / m80, as it lifts 0x00783C6C (`fld dword [ecx]`) in the event shape
# builder (private/generated/s3/whole_image/lift/next/sub_00783A30.ll), with the denormal test and
# the x87 stack push trimmed: `ie |= IsSignalingNaN(v)`, then the value quieted when the reloaded
# IE is set.
FLD_EXIT = {32: "_ZN12_GLOBAL__N_16FLDmemI2MnIfEEEP6MemoryS4_R5State3RnWI9float80_tET_2InIjESB_ItE.exit",
            64: "_ZN12_GLOBAL__N_16FLDmemI2MnIdEEEP6MemoryS4_R5State3RnWI9float80_tET_2InIjESB_ItE.exit",
            80: "_ZN12_GLOBAL__N_16FLDmemI2MnI9float80_tEEEP6MemoryS5_R5State3RnWIS2_ET_2InIjESB_ItE.exit"}
FLD_SNAN = {32: ("float", "i32", "2143289344", "2139095040", "4194303"),
            64: ("double", "i64", "9221120237041090560", "9218868437227405312", "2251799813685247")}


def fld_body(width: int = 32, snan_and: str = "  %86 = and i1 %85, %tobool6.i.i.i",
             ie_store: str = "  store i8 %or.i.i, ptr %ie.i.i, align 1",
             reload: str = "  %93 = load i8, ptr %ie.i.i, align 1",
             test: str = "  %tobool.not.i.i = icmp eq i8 %93, 0", quiet_extra: str = "",
             after_exit: str = "") -> str:
    """One FLD in remill's shape; the value it loads is stored into State's first field."""
    if width == 80:
        load = ("  %val.i = alloca x86_fp80, align 16\n"
                "  %call.i.i.i20 = call ptr @__remill_read_memory_f80(ptr noundef %memory, i32 noundef 4096, "
                "ptr noundef nonnull align 16 dereferenceable(16) %val.i)\n"
                "  %v80 = load x86_fp80, ptr %val.i, align 16\n"
                "  %bits80 = bitcast x86_fp80 %v80 to i80\n  %hi80 = lshr i80 %bits80, 62\n"
                "  %q80 = trunc i80 %hi80 to i1\n  %86 = xor i1 %q80, true\n"
                "  %conv.i.i21 = zext i1 %86 to i8\n")
        ext = "  store x86_fp80 %v80, ptr %res.i.i, align 16"
    else:
        fty, ity, qmask, exp, frac = FLD_SNAN[width]
        load = (f"  %call.i.i.i20 = call {fty} @__remill_read_memory_f{width}(ptr noundef %memory, i32 noundef 4096)\n"
                f"  %83 = bitcast {fty} %call.i.i.i20 to {ity}\n  %84 = and {ity} %83, {qmask}\n"
                f"  %85 = icmp eq {ity} %84, {exp}\n  %bf.clear5.i.i.i = and {ity} %83, {frac}\n"
                f"  %tobool6.i.i.i = icmp ne {ity} %bf.clear5.i.i.i, 0\n{snan_and}\n"
                "  %conv.i.i21 = zext i1 %86 to i8\n")
        ext = (f"  %conv.i2.i.i = fpext {fty} %call.i.i.i20 to x86_fp80\n"
               "  store x86_fp80 %conv.i2.i.i, ptr %res.i.i, align 16")
    return ("  %res.i.i = alloca x86_fp80, align 16\n" + load +
            f"  %ie.i.i = {SW}, i32 19\n  %87 = load i8, ptr %ie.i.i, align 1\n"
            f"  %or.i.i = or i8 %87, %conv.i.i21\n{ie_store}\n"
            "  br label %_ZN12_GLOBAL__N_110IsDenormalEf.exit.i.i\n\n"
            f"_ZN12_GLOBAL__N_110IsDenormalEf.exit.i.i:\n{ext}\n{reload}\n{test}\n"
            f"  br i1 %tobool.not.i.i, label %{FLD_EXIT[width]}, label %if.then.i.i\n\n"
            "if.then.i.i:\n  %bf.load.i.i = load i80, ptr %res.i.i, align 16\n"
            f"  %bf.set.i.i = or i80 %bf.load.i.i, 4611686018427387904\n{quiet_extra}"
            "  store i80 %bf.set.i.i, ptr %res.i.i, align 16\n"
            f"  br label %{FLD_EXIT[width]}\n\n"
            f"{FLD_EXIT[width]}:\n  %r = load x86_fp80, ptr %res.i.i, align 16\n"
            f"  store x86_fp80 %r, ptr %state, align 16\n{after_exit}  ret ptr %memory\n")


def fld_module(body: str, types: str = TYPES + FPU_STATUS_TYPE) -> str:
    return (types + "define ptr @sub_400000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {\n"
            + body + "}\n"
            "declare float @__remill_read_memory_f32(ptr, i32)\ndeclare double @__remill_read_memory_f64(ptr, i32)\n"
            "declare ptr @__remill_read_memory_f80(ptr, i32, ptr)\n")


def test_fld_quiet() -> None:
    """remill quiets every FLD m32/m64 load while the sticky IE is set; the x87 only an sNaN, and m80 never."""
    for width, now in ((32, "%conv.i.i21"), (64, "%conv.i.i21"), (80, "0")):
        src = fld_module(fld_body(width))
        out, st = pc24pass.instrument(src, HELPER_LL)
        check(st["fld_quiet_fixed"] == 1 and st["fld_quiet_refused"] == 0,
              f"m{width}: the quieting test is rewritten: {st['fld_quiet_fixed']}, refused {st['fld_quiet_refused_sites']}")
        check(f"  %tobool.not.i.i = icmp eq i8 {now}, 0{pc24pass.FLD_TAG}\n" in out
              and "icmp eq i8 %93, 0" not in out,
              f"m{width}: the branch tests {'the load' + chr(39) + 's own signalling-NaN test' if width != 80 else 'nothing (never quiet)'}")
        check("  store i8 %or.i.i, ptr %ie.i.i, align 1\n" in out and "or i80 %bf.load.i.i, 4611686018427387904" in out,
              f"m{width}: the sticky IE update and the quieting `or` are left as remill wrote them")
        site = st["fld_quiet_sites"][0]
        check(site["width"] == width and site["was"] == "%93" and site["now"] == now,
              f"m{width}: the site is recorded: {site}")
        off, sto = pc24pass.instrument(src, HELPER_LL, None, True, True, True, True, False)
        check(sto["fld_quiet_fixed"] == 0 and off.split("\n", 1)[1] == src,
              f"m{width}: --no-fix-fld-quiet leaves the module untouched")
        again, st2 = pc24pass.instrument(out.split("\n", 1)[1], HELPER_LL)
        check(again == out and st2["fld_quiet_fixed"] == 0 and st2["fld_quiet_already"] == 1,
              f"m{width}: marker-stripped re-run is byte-identical and counts the FLD as already fixed")
    # the optimiser's other form: the test reads the IE store's value instead of a reload
    out, st = pc24pass.instrument(fld_module(fld_body(reload="", test="  %tobool.not.i.i = icmp eq i8 %or.i.i, 0")),
                                  HELPER_LL)
    check(st["fld_quiet_fixed"] == 1 and "icmp eq i8 %conv.i.i21, 0" in out,
          "a test on the IE store's value (store-to-load forwarded) is rewritten the same way")
    # remill's select form of `a && b`
    out, st = pc24pass.instrument(fld_module(fld_body(snan_and="  %86 = select i1 %85, i1 %tobool6.i.i.i, i1 false")),
                                  HELPER_LL)
    check(st["fld_quiet_fixed"] == 1, "IsSignalingNaN as `select i1 a, i1 b, i1 false` is recognised")


def test_fld_quiet_refusals() -> None:
    """Nothing is rewritten on a guess: an FLD whose test is not shown to be remill's IE test is left and recorded."""
    def refused(src: str, why: str) -> None:
        out, st = pc24pass.instrument(src, HELPER_LL)
        check(st["fld_quiet_fixed"] == 0 and st["fld_quiet_refused"] == 1 and pc24pass.FLD_TAG not in out,
              f"refused, nothing rewritten ({why}): {[r['reason'] for r in st['fld_quiet_refused_sites']]}")
    refused(fld_module(fld_body(snan_and="  %86 = or i1 %85, %tobool6.i.i.i")), "the or-ed flag is not a NaN test")
    refused(fld_module(fld_body().replace("and i32 %83, 2143289344", "and i32 %83, 2139095040")),
            "the exponent test does not mask the quiet bit")
    refused(fld_module(fld_body(ie_store="  store i8 1, ptr %ie.i.i, align 1")), "IE is set to a constant")
    refused(fld_module(fld_body(reload="  %93 = load i8, ptr %other, align 1")
                       .replace("  %res.i.i = alloca", "  %other = alloca i8, align 1\n  %res.i.i = alloca")),
            "the test reads a byte that is not IE")
    refused(fld_module(fld_body(after_exit="  %keep = zext i1 %tobool.not.i.i to i8\n")), "the test has another use")
    refused(fld_module(fld_body(quiet_extra="  %bf.set2 = or i80 %bf.load.i.i, 4611686018427387904\n")),
            "two quieting `or`s")
    refused(fld_module(fld_body(test="  %tobool.not.i.i = icmp ne i8 %93, 0")),
            "the polarity quiets when IE is clear")
    out, st = pc24pass.instrument(fld_module(fld_body(), TYPES), HELPER_LL)
    check(st["fld_quiet_refused"] == 1 and "FPUStatusFlags" in st["fld_quiet_refused_sites"][0]["reason"],
          "no %struct.FPUStatusFlags type: refused, not guessed")


FLD_EXEC_HARNESS = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
static unsigned long long g_bits;
float __remill_read_memory_f32(void *m, unsigned a) { unsigned u = (unsigned)g_bits; float f; memcpy(&f, &u, 4); return f; }
double __remill_read_memory_f64(void *m, unsigned a) { double d; memcpy(&d, &g_bits, 8); return d; }
void *sub_400000(void *state, unsigned pc, void *memory);
int main(void) {
    char name[24]; int width, ie;
    while (scanf("%23s %d %llx %d", name, &width, &g_bits, &ie) == 4) {
        unsigned char state[512], host[10]; unsigned short sw; unsigned snan = 0x7f800001u;
        memset(state, 0, sizeof state);
        state[99] = (unsigned char)ie;
        sub_400000(state, 0, 0);
        __asm__ volatile("fninit");
        if (ie) __asm__ volatile("flds %0\n\tfstp %%st(0)" :: "m"(snan) : "st");
        if (width == 32) { unsigned u = (unsigned)g_bits; __asm__ volatile("flds %1\n\tfstpt %0" : "=m"(host) : "m"(u) : "st"); }
        else __asm__ volatile("fldl %1\n\tfstpt %0" : "=m"(host) : "m"(g_bits) : "st");
        __asm__ volatile("fnstsw %0" : "=m"(sw));
        printf("%s ", name);
        for (int i = 9; i >= 0; i--) printf("%02x", state[i]);
        printf(" %d ", state[99]);
        for (int i = 9; i >= 0; i--) printf("%02x", host[i]);
        printf(" %d\n", sw & 1);
    }
    return 0;
}
"""
# (name, width, the bits loaded, IE before the load): a normal value, 1.0, zero, infinity, the smallest
# denormal, a quiet and a signalling NaN, each with IE clear and set, in both widths.
FLD_EXEC_VALUES = {32: [("key", "bf09fe7f"), ("one", "3f800000"), ("zero", "00000000"), ("inf", "7f800000"),
                        ("denormal", "00000001"), ("qnan", "7fc00001"), ("snan", "7f800001")],
                   64: [("key", "bfe13fcfe0000000"), ("one", "3ff0000000000000"), ("zero", "0000000000000000"),
                        ("inf", "7ff0000000000000"), ("denormal", "0000000000000001"),
                        ("qnan", "7ff8000000000001"), ("snan", "7ff0000000000001")]}


def run_fld_exec(width: int, fixed: bool, work: Path) -> dict[str, tuple[str, str, str, str]]:
    """{case: (lifted f80, lifted IE, host f80, host IE)} for the m`width` FLD compiled with the pinned zig."""
    import subprocess
    src = EXEC_TYPES + ("define ptr @sub_400000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {\n"
                        + fld_body(width) + "}\ndeclare float @__remill_read_memory_f32(ptr, i32)\n"
                        "declare double @__remill_read_memory_f64(ptr, i32)\n")
    ll, st = pc24pass.instrument(src, HELPER_LL, None, True, True, True, True, fixed)
    check(st["fld_quiet_fixed"] == (1 if fixed else 0), f"m{width}: the compiled module is {'fixed' if fixed else 'as remill wrote it'}")
    (work / "fld.ll").write_text(ll)
    (work / "harness.c").write_text(FLD_EXEC_HARNESS)
    for cmd in ([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O2", "-w", "-c", "fld.ll", "-o", "fld.o"],
                [str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O2", "-w", "harness.c", "fld.o", "-o", "fld_exec"]):
        p = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
        check(p.returncode == 0, f"{cmd[1]} {cmd[-3]}: {p.stderr[-800:]}")
    cases = [(f"{n}.ie{ie}", width, b, ie) for n, b in FLD_EXEC_VALUES[width] for ie in (0, 1)]
    p = subprocess.run([str(work / "fld_exec")], input="".join(f"{n} {w} {b} {ie}\n" for n, w, b, ie in cases),
                       capture_output=True, text=True)
    check(p.returncode == 0, f"the FLD program ran: {p.stderr[-400:]}")
    return {r[0]: tuple(r[1:]) for r in (ln.split() for ln in p.stdout.splitlines())}


def test_fld_quiet_matches_the_host_x87() -> None:
    """Compiled, before and after the pass, against this host's own `fld dword` / `fld qword` with IE clear and set."""
    if not ZIG.is_file():
        print(f"skipped: the pinned zig is not at {ZIG}")
        return
    import tempfile
    for width in (32, 64):
        with tempfile.TemporaryDirectory(prefix="fld-") as d:
            (Path(d) / "a").mkdir()
            (Path(d) / "b").mkdir()
            after = run_fld_exec(width, True, Path(d) / "a")
            before = run_fld_exec(width, False, Path(d) / "b")
        check(len(after) == len(before) == 2 * len(FLD_EXEC_VALUES[width]), f"m{width}: every case ran: {sorted(after)}")
        check(after["key.ie1"][2] == after["key.ie0"][2] and after["key.ie0"][3] == "0"
              and after["snan.ie0"][3] == "1" and int(after["snan.ie0"][2][4], 16) >= 0xC,
              f"m{width}: this host's fld leaves a value alone with IE set, and quiets a signalling NaN and sets IE: "
              f"{after['key.ie0'][2]} {after['key.ie1'][2]} {after['snan.ie0']}")
        for case, (lifted, lie, host, hie) in after.items():
            check(lifted == host and lie == hie, f"m{width} {case}: after the pass {lifted} IE {lie}, the host's fld {host} IE {hie}")
        wrong = sorted(c for c, (lifted, _, host, _) in before.items() if lifted != host)
        check(wrong == sorted(f"{n}.ie1" for n, _ in FLD_EXEC_VALUES[width] if "nan" not in n),
              f"m{width}: before the pass, remill's value differs from the host's on exactly every non-NaN load "
              f"made with IE already set: {wrong}")
        check(all(before[c][1] == after[c][1] for c in after), f"m{width}: the pass changes no IE result")


def test_fld_quiet_on_the_lifted_shape_builder() -> None:
    """The event shape builder 0x00783A30 and an m80 module of the whole-image lift, when the private tree holds them."""
    lift = ROOT / "private" / "generated" / "s3" / "whole_image" / "lift" / "next"
    helper_ll = ROOT / "private" / "generated" / "spike" / "pc24pass" / "pc_round.ll"
    want = {"sub_00783A30.ll": {32: 51}, "sub_00829008.ll": {80: 2}, "sub_00420210.ll": {32: 8, 64: 1}}
    if not helper_ll.exists() or not all((lift / f).exists() for f in want):
        print("skipped: the whole-image lift's sub_00783A30, sub_00829008, sub_00420210 or the helper IR are not under "
              "private/generated/")
        return
    helper = helper_ll.read_text()
    for f, widths in want.items():
        out, st = pc24pass.instrument((lift / f).read_text(), helper)
        got = {w: sum(1 for s in st["fld_quiet_sites"] if s["width"] == w) for w in (32, 64, 80)}
        check({w: n for w, n in got.items() if n} == widths and st["fld_quiet_refused"] == 0,
              f"{f}: FLD tests fixed by width {got}, refused {st['fld_quiet_refused']}")
        again, _ = pc24pass.instrument(out.split("\n", 1)[1], helper)
        check(again == out, f"{f}: marker-stripped re-run byte-identical")


def test_spike_ten() -> None:
    files = sorted(REMILL_OUT.glob("sub_*.ll"))
    helper_ll = ROOT / "private" / "generated" / "spike" / "pc24pass" / "pc_round.ll"
    if len(files) != 10 or not helper_ll.exists():
        print("skipped: the ten lifted files or the helper IR are not under private/generated/spike/")
        return
    helper = helper_ll.read_text()
    total, skipped, non_lifted = 0, {}, 0
    by_sem: dict[str, int] = {}
    for f in files:
        out, st = pc24pass.instrument(f.read_text(), helper)
        total += st["ops_instrumented"]
        non_lifted += st["skipped_non_lifted"]
        for k, v in st["by_semantic"].items():
            by_sem[k] = by_sem.get(k, 0) + v
        for k, v in st["skipped_by_semantic"].items():
            skipped[k] = skipped.get(k, 0) + v
        again, _ = pc24pass.instrument(out.split("\n", 1)[1], helper)
        check(again == out, f"{f.name}: marker-stripped re-run byte-identical")
    check(total == 52 and skipped == {} and non_lifted == 0, f"ten spike files: instrumented {total} skipped_by_semantic {skipped} skipped_non_lifted {non_lifted}")
    print(f"     by_semantic over the ten: {by_sem}")


def main() -> None:
    for t in (test_marker_stripped_rerun, test_non_lifted_define_skipped, test_semantic_gate,
              test_enclosing_semantic_and_sqrtl, test_fist_rewrite, test_control_word_masks, test_fpatan_quadrant,
              test_fpatan_refuses_an_unrelated_division, test_f80_accessor_attrs, test_fxam_sign, test_fxam_refusals,
              test_fxam_c1_matches_the_host_x87, test_fxam_on_the_lifted_dispatchers, test_fld_quiet,
              test_fld_quiet_refusals, test_fld_quiet_matches_the_host_x87, test_fld_quiet_on_the_lifted_shape_builder,
              test_spike_ten):
        print(f"-- {t.__name__}")
        t()
    print("all checks passed")


if __name__ == "__main__":
    main()
