"""State localization and the guest-memory fast path (tools/s2/corpus_run/state_local.py),
the fourth and fifth speed passes.

The pass moves every x87 value of a selected lifted function out of State memory and into
SSA, so a defect here is a wrong number in every localized function at once. The tests:

* **the layout engine against LLVM's own**: every distinct State GEP in real lifted modules,
  and the size of every type in the State tree, placed by the engine and by LLVM, which
  folds `ptrtoint(getelementptr(T, null, ...))` to the offset when the fold's own compile
  command (tools/s4/rebuild_image.py `compile_commands`, so the same compiler and target)
  turns the probe into LLVM IR; and every State access LLVM keeps in the -O2 IR of a real
  module lies inside the bytes the pass copies;
* **default deny**: each use of State the pass cannot account for leaves the function
  exactly as it came and counts it refused, every kind in `state_local.REFUSALS` has a
  case, and a callee the pass does not know is bracketed, never trusted;
* **the safe callees tied to the runtime**: runtime/remill_runtime.cpp compiled to LLVM IR
  by the link's own command (tools/s4/link_s4.py `phase_compile`), in which no safe
  callee's definition refers to anything but guest memory and trap reporting or holds
  inline asm or an indirect call, and the spliced PC24 helpers, which are pure; a runtime
  copy and a helper copy with one change each are named, so the tie is shown able to fail.
  It fails, never skips, when the compiler or a helper is missing: it is the only check
  that the safe list is safe;
* **the bracket points**: `fesetround`, `fegetround` and `fetestexcept` (and the rest of
  `FENV_CALLS`) are copied around, whatever the safe list says; compiled, the copies order
  State memory around a rounding change exactly as the build without the pass does, and
  once State has escaped they reload it after the change (a control without the bracket
  does not);
* **the guest-memory ordering**: the memory-effect engine against LLVM's own reading of
  attribute lists; a localized function's guest read compiled before a later write, with a
  control that sinks it, also when the call carries a comment; the ten-byte read's buffer
  load kept, with a control that loses it; every memory attribute of a group replaced, with
  a control the check names;
* **the guest-memory fast path** (`fast`, the fifth pass): its output as written; its
  bounds read from runtime/guest_memory.h and its helpers run against `sa2_gp` at every
  edge; the runtime's side of it -- the base's lifecycle, and every access through the
  helpers equal to the same access through the intrinsics under each poison mode -- held
  over the runtime itself (harness/guest_fast_probe.c on a PE32 the test writes); guest
  memory in program order around a bracketed call, and a dead read's bounds check kept;
  a case for every reason it keeps a function, both halves of the module rule, and its
  output check, each shown failing;
* **literal IR in, exact IR out**, compiled once to show the output is valid IR, plus the
  selection rule at its threshold;
* **idempotence and determinism**: a second application changes nothing, and the output
  does not depend on the interpreter's hash seed.
"""

from __future__ import annotations

import os
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
# Appended, not inserted first: the directory also holds build.py, run.py and receipt.py.
sys.path.append(str(ROOT / "tools" / "s2" / "corpus_run"))
import internalize  # noqa: E402
import speed_rewrite as SR  # noqa: E402
import state_local as SL  # noqa: E402

GEN = ROOT / "private" / "generated"
RUNTIME = ROOT / "runtime" / "remill_runtime.cpp"
# The two modules the PC24 pass splices into every lifted module (rebuild_image.FOLD_HELPERS).
HELPER_FILES = (GEN / "spike" / "pc24pass" / "pc_round.ll",
                GEN / "s2" / "corpus_run" / "helper" / "x87_int.ll")
# Real folded modules: the CRI biquad (x87 at PC53, the FPU status flags), njCalcPoint (x87
# at PC24, GPRs), the CRT strlen (sub-registers through GEPs on GEPs) and the CRI float to
# int (FLDCW, so the control word and fesetround).
REAL_MODULES = tuple(GEN / "s3" / "whole_image" / "fold" / "next" / f"sub_{va}.pc24.ll"
                     for va in ("00842500", "00426CC0", "007A8C00", "00846AD0"))

# remill's State type tree, as every lifted module defines it.
TYPES = """\
%struct.State = type { %struct.X86State }
%struct.X86State = type { %struct.ArchState, [32 x %union.VectorReg], %struct.ArithFlags, %union.anon, %struct.Segments, %struct.AddressSpace, %struct.GPR, %struct.X87Stack, %struct.MMX, %struct.FPUStatusFlags, %union.anon, %union.FPU, %struct.SegmentCaches, %struct.K_REG }
%struct.ArchState = type { i32, i32, %union.anon }
%union.VectorReg = type { %union.vec512_t }
%union.vec512_t = type { %struct.uint64v8_t }
%struct.uint64v8_t = type { [8 x i64] }
%struct.ArithFlags = type { i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8 }
%struct.Segments = type { i16, %union.SegmentSelector, i16, %union.SegmentSelector, i16, %union.SegmentSelector, i16, %union.SegmentSelector, i16, %union.SegmentSelector, i16, %union.SegmentSelector }
%union.SegmentSelector = type { i16 }
%struct.AddressSpace = type { i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg }
%struct.Reg = type { %union.anon.1, i32 }
%union.anon.1 = type { i32 }
%struct.GPR = type { i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg, i64, %struct.Reg }
%struct.X87Stack = type { [8 x %struct.anon.3] }
%struct.anon.3 = type { [6 x i8], %struct.float80_t }
%struct.float80_t = type { [10 x i8] }
%struct.MMX = type { [8 x %struct.anon.4] }
%struct.anon.4 = type { i64, %union.vec64_t }
%union.vec64_t = type { %struct.uint64v1_t }
%struct.uint64v1_t = type { [1 x i64] }
%struct.FPUStatusFlags = type { i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, i8, [4 x i8] }
%union.anon = type { i64 }
%union.FPU = type { %struct.anon.13 }
%struct.anon.13 = type { %struct.FpuFXSAVE, [96 x i8] }
%struct.FpuFXSAVE = type { %union.SegmentSelector, %union.SegmentSelector, %union.FPUAbridgedTagWord, i8, i16, i32, %union.SegmentSelector, i16, i32, %union.SegmentSelector, i16, %union.anon.1, %union.anon.1, [8 x %struct.FPUStackElem], [16 x %union.vec128_t] }
%union.FPUAbridgedTagWord = type { i8 }
%struct.FPUStackElem = type { %union.anon.11, [6 x i8] }
%union.anon.11 = type { %struct.float80_t }
%union.vec128_t = type { %struct.uint128v1_t }
%struct.uint128v1_t = type { [1 x i128] }
%struct.SegmentCaches = type { %struct.SegmentShadow, %struct.SegmentShadow, %struct.SegmentShadow, %struct.SegmentShadow, %struct.SegmentShadow, %struct.SegmentShadow }
%struct.SegmentShadow = type { %union.anon, i32, i32 }
%struct.K_REG = type { [8 x %struct.anon.18] }
%struct.anon.18 = type { i64, i64 }
"""

DECLARES = """
declare ptr @sub_402000(ptr, i32, ptr)
declare ptr @__remill_function_return(ptr, i32, ptr)
declare ptr @__remill_error(ptr, i32, ptr)
declare i32 @__remill_read_memory_32(ptr noundef readnone, i32 noundef) #7
declare ptr @__remill_write_memory_32(ptr noundef, i32 noundef, i32 noundef) #7
declare i32 @fesetround(i32)
declare i32 @fegetround()
declare i32 @fetestexcept(i32)
declare double @sqrt(double)
declare void @llvm.memcpy.p0.p0.i64(ptr noalias nocapture writeonly, ptr noalias nocapture readonly, i64, i1 immarg)

attributes #7 = { nobuiltin nounwind readnone willreturn "no-builtins" }
attributes #9 = { nounwind }
"""

# The declarations and the groups a module gets once a function in it is localized: the
# guest-memory intrinsics ordered (state_local's docstring, "Ordering guest memory").
DECLARES_ORDERED = DECLARES.replace(
    "declare i32 @__remill_read_memory_32(ptr noundef readnone, i32 noundef) #7",
    "declare i32 @__remill_read_memory_32(ptr noundef, i32 noundef) #90002").replace(
    "declare ptr @__remill_write_memory_32(ptr noundef, i32 noundef, i32 noundef) #7",
    "declare ptr @__remill_write_memory_32(ptr noundef, i32 noundef, i32 noundef) #90003")
ORDERED_GROUPS = """
attributes #90002 = { nobuiltin nounwind memory(argmem: read) willreturn "no-builtins" }
attributes #90003 = { nobuiltin nounwind memory(argmem: readwrite) willreturn "no-builtins" }
"""

# x87 ST0 and ST1 (State.st[0..1], the 16-byte slots at 2480 and 2496), ST2 (2512) reached
# only by remill's slot copy, ESP (the 8-byte register at 2312), the x87 multiply, remill's
# barrier, a guest read, a call to another lifted function, and the return through a tail
# call.
BASIC_IN = """\
define ptr @sub_401000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
  %STATE = alloca ptr, align 4
  store ptr %state, ptr %STATE, align 4
  %ST0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 0, i32 1, i32 0, i32 0
  %ST1 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 1, i32 1, i32 0, i32 0
  %ESP = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 6, i32 13, i32 0, i32 0
  %a = load x86_fp80, ptr %ST0, align 2
  %b = load x86_fp80, ptr %ST1, align 2
  %c = fmul x86_fp80 %a, %b
  store x86_fp80 %c, ptr %ST0, align 2
  call void asm sideeffect "", "~{memory},~{dirflag},~{fpsr},~{flags}"() #9
  %ST2 = getelementptr inbounds %struct.State, ptr %state, i64 0, i32 0, i32 7, i32 0, i64 2, i32 1, i32 0, i64 0
  call void @llvm.memcpy.p0.p0.i64(ptr align 2 %ST2, ptr align 2 %ST1, i64 10, i1 false)
  call void @llvm.memcpy.p0.p0.i64(ptr align 2 %ST1, ptr align 2 %ST0, i64 10, i1 false)
  %sp = load i32, ptr %ESP, align 8
  %v = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef %sp) #7
  %m = call ptr @sub_402000(ptr %state, i32 %v, ptr %memory)
  br label %exit

exit:
  %r = tail call ptr @__remill_function_return(ptr %state, i32 %v, ptr %m)
  ret ptr %r
}
"""

BASIC_OUT = """\
define ptr @sub_401000(ptr noalias %state.host, i32 %program_counter, ptr noalias %memory) {
  %state = alloca %struct.State, align 16
  %sl.0.0.l = getelementptr inbounds i8, ptr %state, i64 2312
  %sl.0.0.h = getelementptr inbounds i8, ptr %state.host, i64 2312
  call void @llvm.memcpy.p0.p0.i64(ptr align 1 %sl.0.0.l, ptr align 1 %sl.0.0.h, i64 8, i1 false)
  %sl.0.1.l = getelementptr inbounds i8, ptr %state, i64 2480
  %sl.0.1.h = getelementptr inbounds i8, ptr %state.host, i64 2480
  call void @llvm.memcpy.p0.p0.i64(ptr align 1 %sl.0.1.l, ptr align 1 %sl.0.1.h, i64 48, i1 false)
  %STATE = alloca ptr, align 4
  store ptr %state, ptr %STATE, align 4
  %ST0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 0, i32 1, i32 0, i32 0
  %ST1 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 1, i32 1, i32 0, i32 0
  %ESP = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 6, i32 13, i32 0, i32 0
  %a = load x86_fp80, ptr %ST0, align 2
  %b = load x86_fp80, ptr %ST1, align 2
  %c = fmul x86_fp80 %a, %b
  store x86_fp80 %c, ptr %ST0, align 2
  call void asm sideeffect "", "~{memory},~{dirflag},~{fpsr},~{flags}"() #9
  %ST2 = getelementptr inbounds %struct.State, ptr %state, i64 0, i32 0, i32 7, i32 0, i64 2, i32 1, i32 0, i64 0
  call void @llvm.memcpy.p0.p0.i64(ptr align 2 %ST2, ptr align 2 %ST1, i64 10, i1 false)
  call void @llvm.memcpy.p0.p0.i64(ptr align 2 %ST1, ptr align 2 %ST0, i64 10, i1 false)
  %sp = load i32, ptr %ESP, align 8
  %v = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef %sp) #90002
  %sl.1.0.h = getelementptr inbounds i8, ptr %state.host, i64 2312
  %sl.1.0.l = getelementptr inbounds i8, ptr %state, i64 2312
  call void @llvm.memcpy.p0.p0.i64(ptr align 1 %sl.1.0.h, ptr align 1 %sl.1.0.l, i64 8, i1 false)
  %sl.1.1.h = getelementptr inbounds i8, ptr %state.host, i64 2480
  %sl.1.1.l = getelementptr inbounds i8, ptr %state, i64 2480
  call void @llvm.memcpy.p0.p0.i64(ptr align 1 %sl.1.1.h, ptr align 1 %sl.1.1.l, i64 48, i1 false)
  %m = call ptr @sub_402000(ptr %state.host, i32 %v, ptr %memory)
  %sl.2.0.l = getelementptr inbounds i8, ptr %state, i64 2312
  %sl.2.0.h = getelementptr inbounds i8, ptr %state.host, i64 2312
  call void @llvm.memcpy.p0.p0.i64(ptr align 1 %sl.2.0.l, ptr align 1 %sl.2.0.h, i64 8, i1 false)
  %sl.2.1.l = getelementptr inbounds i8, ptr %state, i64 2480
  %sl.2.1.h = getelementptr inbounds i8, ptr %state.host, i64 2480
  call void @llvm.memcpy.p0.p0.i64(ptr align 1 %sl.2.1.l, ptr align 1 %sl.2.1.h, i64 48, i1 false)
  br label %exit

exit:
  %sl.3.0.h = getelementptr inbounds i8, ptr %state.host, i64 2312
  %sl.3.0.l = getelementptr inbounds i8, ptr %state, i64 2312
  call void @llvm.memcpy.p0.p0.i64(ptr align 1 %sl.3.0.h, ptr align 1 %sl.3.0.l, i64 8, i1 false)
  %sl.3.1.h = getelementptr inbounds i8, ptr %state.host, i64 2480
  %sl.3.1.l = getelementptr inbounds i8, ptr %state, i64 2480
  call void @llvm.memcpy.p0.p0.i64(ptr align 1 %sl.3.1.h, ptr align 1 %sl.3.1.l, i64 48, i1 false)
  %r = tail call ptr @__remill_function_return(ptr %state.host, i32 %v, ptr %m)
  ret ptr %r
}
"""

# FLDCW's fesetround, FNSTCW's fegetround, an exception-flag read, a libm call, a return
# that is not after a bracket point, and a bracketed call that never returns.
FENV_IN = """\
define ptr @sub_403000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
entry:
  %ST0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 0, i32 1, i32 0, i32 0
  %a = load x86_fp80, ptr %ST0, align 2
  %r1 = call i32 @fesetround(i32 3072)
  %b = fadd x86_fp80 %a, %a
  store x86_fp80 %b, ptr %ST0, align 2
  %r2 = call i32 @fegetround()
  %f = call i32 @fetestexcept(i32 61)
  %q = call double @sqrt(double 2.000000e+00)
  %c = icmp eq i32 %f, 0
  br i1 %c, label %ok, label %bad

ok:
  ret ptr %memory

bad:
  %e = call ptr @__remill_error(ptr %state, i32 %program_counter, ptr %memory)
  unreachable
}
"""


def _copy(site: int, to_host: bool) -> str:
    """One copy of State.st[0] (bytes 2480 to 2496) as FENV_OUT spells it."""
    l, h = (f"%sl.{site}.0.l", "ptr %state"), (f"%sl.{site}.0.h", "ptr %state.host")
    dst, src = (h, l) if to_host else (l, h)
    return (f"  {dst[0]} = getelementptr inbounds i8, {dst[1]}, i64 2480\n"
            f"  {src[0]} = getelementptr inbounds i8, {src[1]}, i64 2480\n"
            f"  call void @llvm.memcpy.p0.p0.i64(ptr align 1 {dst[0]}, ptr align 1 {src[0]}, "
            f"i64 16, i1 false)\n")


FENV_OUT = (
    "define ptr @sub_403000(ptr noalias %state.host, i32 %program_counter, ptr noalias %memory) {\n"
    "entry:\n"
    "  %state = alloca %struct.State, align 16\n"
    + _copy(0, False)
    + "  %ST0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 0, i32 1, i32 0, i32 0\n"
    "  %a = load x86_fp80, ptr %ST0, align 2\n"
    + _copy(1, True) + "  %r1 = call i32 @fesetround(i32 3072)\n" + _copy(2, False)
    + "  %b = fadd x86_fp80 %a, %a\n"
    "  store x86_fp80 %b, ptr %ST0, align 2\n"
    + _copy(3, True) + "  %r2 = call i32 @fegetround()\n" + _copy(4, False)
    + _copy(5, True) + "  %f = call i32 @fetestexcept(i32 61)\n" + _copy(6, False)
    + _copy(7, True) + "  %q = call double @sqrt(double 2.000000e+00)\n" + _copy(8, False)
    + "  %c = icmp eq i32 %f, 0\n"
    "  br i1 %c, label %ok, label %bad\n"
    "\n"
    "ok:\n"
    + _copy(9, True) + "  ret ptr %memory\n"
    "\n"
    "bad:\n"
    + _copy(10, True)
    + "  %e = call ptr @__remill_error(ptr %state.host, i32 %program_counter, ptr %memory)\n"
    "  unreachable\n"
    "}\n")

COUNTS_NONE = {"local": 0, "local_kept": 0, "local_refused": 0}


def module(*functions: str) -> str:
    return TYPES + "\n" + "\n".join(functions) + DECLARES


def module_out(*functions: str) -> str:
    """`module` as the pass returns it when it localizes a function in it."""
    return TYPES + "\n" + "\n".join(functions) + DECLARES_ORDERED + ORDERED_GROUPS


def counts(local=0, kept=0, refused=0) -> dict[str, int]:
    return {"local": local, "local_kept": kept, "local_refused": refused}


def fold_command(probe: Path, out: Path) -> list[str]:
    """The fold's own compile command (rebuild_image.compile_commands: compile_all's, which
    every population shares) with LLVM IR out in place of an object, run from ROOT."""
    sys.path.insert(0, str(ROOT / "tools" / "s4"))
    try:
        import rebuild_image as RI
        commands = set(RI.compile_commands().values())
    finally:
        sys.path.remove(str(ROOT / "tools" / "s4"))
    assert len(commands) == 1, commands
    cmd = commands.pop().split()
    assert cmd[-4:] == ["-c", "{ir}", "-o", "{object}"], cmd
    zig = ROOT / cmd[0]
    assert zig.is_file(), f"the fold's compiler is not present: {zig}"
    return [str(zig)] + cmd[1:-4] + ["-S", "-emit-llvm", str(probe), "-o", str(out)]


def compile_ir(text: str, tmp_path: Path, name: str) -> str:
    """`text` through the fold's compile at -O2, as LLVM IR."""
    src, out = tmp_path / f"{name}.ll", tmp_path / f"{name}.opt.ll"
    src.write_text(text)
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(tmp_path / "zig-local"),
               ZIG_GLOBAL_CACHE_DIR=str(GEN / "zig-cache-tests"))
    done = subprocess.run(fold_command(src, out), capture_output=True, text=True, cwd=ROOT,
                          env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    return out.read_text()


def real(path: Path) -> str:
    assert path.is_file(), f"the real module {path} is not present; this check fails, never skips"
    return path.read_text(errors="replace")


# ------------------------------------------------------------ the layout against LLVM's

_STATE_GEP = re.compile(r"getelementptr (?:inbounds )?(?:nuw )?(%struct\.State), ptr %state, "
                        r"((?:i(?:32|64) -?\d+(?:, )?)+)")


def every_field(layout: SL.Layout, t: str = "%struct.State", prefix: str = "i64 0"):
    """The index list of every field and element at every depth of `t`, leaves included."""
    yield prefix
    r = layout.resolve(t)
    m = re.fullmatch(r"\[(\d+) x (.+)\]", r)
    if m:
        for i in range(int(m.group(1))):
            yield from every_field(layout, m.group(2), f"{prefix}, i64 {i}")
    elif r.startswith("{"):
        for i, (_, f) in enumerate(layout.fields(r)[0]):
            yield from every_field(layout, f, f"{prefix}, i32 {i}")


def test_the_layout_engine_places_every_state_gep_and_type_where_llvm_does(tmp_path):
    """Every distinct State GEP of the real modules, every field and element of the State
    tree at every depth, and the size of every type in it, by the engine and by LLVM under
    the fold's own compile command."""
    texts = [real(p) for p in REAL_MODULES]
    used = {(m.group(1), m.group(2).rstrip(", ")) for t in texts for m in _STATE_GEP.finditer(t)}
    layout = SL.Layout(texts[0])
    tree, todo = [], ["%struct.State"]
    while todo:
        t = todo.pop()
        if t not in tree:
            tree.append(t)
            todo += re.findall(r"%[-\w.$]+", layout.defs[t])
    fields = {("%struct.State", idx) for idx in every_field(layout)}
    assert len(used) >= 40 and len(tree) == 34 and len(fields) > 1000
    paths = sorted(used | fields)
    typedefs = "\n".join(line for line in texts[0].split("\n") if " = type " in line
                         and line.startswith("%"))
    probe = [typedefs]
    for i, (src, idx) in enumerate(paths):
        probe.append(f"define i64 @p{i}() {{\n  ret i64 ptrtoint (ptr getelementptr ({src}, "
                     f"ptr null, {idx}) to i64)\n}}")
    for i, t in enumerate(sorted(tree)):
        probe.append(f"define i64 @s{i}() {{\n  ret i64 ptrtoint (ptr getelementptr ({t}, "
                     f"ptr null, i32 1) to i64)\n}}")
    got = dict(re.findall(r"@([ps]\d+)\(\)[^{]*\{\n\s*ret i64 (-?\d+)",
                          compile_ir("\n".join(probe) + "\n", tmp_path, "probe")))
    engine = {f"p{i}": str(layout.gep(src, [int(x.split()[1]) for x in idx.split(", ")])[0])
              for i, (src, idx) in enumerate(paths)}
    engine.update({f"s{i}": str(layout.size(t)[0]) for i, t in enumerate(sorted(tree))})
    assert got == engine
    assert layout.size("%struct.State")[0] == 3504


# Padding remill's State spells out as explicit fields, left here to the data layout: an i64
# after an i8, an x86_fp80 between small fields, arrays of them, a struct at an odd offset.
PADDED = {
    "%struct.P1": "{ i8, i64 }",
    "%struct.P2": "{ i16, x86_fp80, i8 }",
    "%struct.P3": "{ [3 x i8], i32, %struct.P1, i16 }",
    "%struct.P4": "{ i8, [2 x %struct.P2], double, i1 }",
    "%struct.P5": "{ i8, i128, ptr, float }",
}


def test_the_layout_engine_pads_and_aligns_as_llvm_does(tmp_path):
    """Every field offset and the size of each type above, by the engine and by LLVM under
    the fold's command: the alignment and tail-padding rules State's explicit padding never
    exercises."""
    text = "".join(f"{name} = type {body}\n" for name, body in PADDED.items())
    layout = SL.Layout(text)
    cases = []
    for name in PADDED:
        for i in range(len(layout.fields(layout.resolve(name))[0])):
            cases.append((name, f"i32 0, i32 {i}", layout.gep(name, [0, i])[0]))
        cases.append((name, "i32 1", layout.size(name)[0]))
    probe = [text] + [f"define i64 @q{n}() {{\n  ret i64 ptrtoint (ptr getelementptr ({name}, "
                      f"ptr null, {idx}) to i64)\n}}" for n, (name, idx, _) in enumerate(cases)]
    got = dict(re.findall(r"@(q\d+)\(\)[^{]*\{\n\s*ret i64 (-?\d+)",
                          compile_ir("\n".join(probe) + "\n", tmp_path, "padded")))
    assert got == {f"q{n}": str(off) for n, (_, _, off) in enumerate(cases)}
    assert layout.size("%struct.P2")[0] == 48 and layout.gep("%struct.P1", [0, 1])[0] == 8


# The fold's input for a real module: the lifted, PC24-passed IR compile_all.py hands to
# internalize.rewrite (tools/s3/whole_image/compile_all.py `one`).
LIFTED = GEN / "s3" / "whole_image" / "lift" / "next"


def folded_without_local(path: Path, monkeypatch) -> str:
    """The real module `path` names as the fold makes it with every speed pass but "local":
    its lifted input through internalize.rewrite, whose speed_rewrite.rewrite call is given
    `passes` without "local". The fold on disk is localized once the pass has landed, so it
    cannot serve as the input of a check on what the pass would copy."""
    src = real(LIFTED / path.name)
    passes = tuple(p for p in SR.PASSES if p != "local")
    assert len(passes) == len(SR.PASSES) - 1, SR.PASSES
    with monkeypatch.context() as mp:
        mp.setattr(internalize, "speed_rewrite", type(
            "NoLocal", (), {"rewrite": staticmethod(lambda t: SR.rewrite(t, passes=passes))}))
        text, stats = internalize.rewrite(src)
    assert stats["speed_local"] == 0 and "%state.host" not in text, path
    return text


def test_every_state_access_llvm_keeps_lies_inside_the_copied_bytes(tmp_path, monkeypatch):
    """The -O2 IR of each real module as the fold makes it without the pass: LLVM rewrites
    each State access as a byte offset from %state (its own layout), and every load, store
    and mem* copy through one lies inside the ranges the pass would copy for that module's
    functions."""
    sizes = {"i8": 1, "i16": 2, "i32": 4, "i64": 8, "i128": 16, "float": 4, "double": 8,
             "x86_fp80": 10, "ptr": 8}
    checked = 0
    for path in REAL_MODULES[:2]:
        text = folded_without_local(path, monkeypatch)
        ranges = SL.merge(r for p in SL.plans(text, k=0) for r in p.ranges)
        assert ranges and not [p.refused for p in SL.plans(text, k=0) if p.refused]
        opt = compile_ir(text, tmp_path, path.stem.split(".")[0])
        offsets = {m.group(1): int(m.group(2)) for m in re.finditer(
            r"(%[-\w.$]+) = getelementptr inbounds (?:nuw )?i8, ptr %state, i64 (\d+)", opt)}
        offsets["%state"] = 0
        for line in opt.split("\n"):
            for kind in (r"load (\w+), ptr (%[-\w.$]+)", r"store (\w+) [^,]+, ptr (%[-\w.$]+)"):
                m = re.search(kind, line)
                if m and m.group(2) in offsets:
                    lo, size = offsets[m.group(2)], sizes.get(m.group(1), 16)
                    checked += 1
                    assert any(a <= lo and lo + size <= b for a, b in ranges), line
            m = re.search(r"llvm\.mem(?:cpy|move|set)[^(]*\((.*)\)", line)
            if m:
                for ptr in re.findall(r"(%[-\w.$]+)(?=,)", m.group(1)):
                    if ptr in offsets:
                        lo = offsets[ptr]
                        size = int(re.search(r"i64 (\d+), i1", line).group(1))
                        checked += 1
                        assert any(a <= lo and lo + size <= b for a, b in ranges), line
    assert checked > 50


# ------------------------------------------------------------------- literal in and out

def test_a_function_is_localized_as_written(tmp_path):
    text = module(BASIC_IN)
    assert SL.localize(text, k=1) == (module_out(BASIC_OUT), counts(local=1))
    compile_ir(module_out(BASIC_OUT), tmp_path, "basic")      # valid IR, as the fold compiles


def test_every_environment_call_libm_and_unknown_callee_is_a_bracket_point(tmp_path):
    """fesetround, fegetround, fetestexcept and sqrt are each copied around; the return in
    %ok copies back first; __remill_error copies back and, followed by `unreachable`, does
    not copy in again."""
    assert SL.localize(module(FENV_IN), k=0) == (module_out(FENV_OUT), counts(local=1))
    compile_ir(module_out(FENV_OUT), tmp_path, "fenv")


def test_the_environment_calls_are_bracket_points_whatever_the_safe_list_says(monkeypatch):
    for name in ("fesetround", "fegetround", "fetestexcept"):
        assert name in SL.FENV_CALLS
    assert not set(SL.FENV_CALLS) & SL.SAFE_CALLEES
    monkeypatch.setattr(SL, "SAFE_CALLEES", SL.SAFE_CALLEES | set(SL.FENV_CALLS))
    assert [n for n in SL.FENV_CALLS if SL.safe_callee(n)] == []
    assert SL.localize(module(FENV_IN), k=0) == (module_out(FENV_OUT), counts(local=1))


# FADD ST0, ST1 into ST2, FLDCW (fesetround), the same FADD again into ST0, after a lifted
# call that hands State on, so the host State has escaped before the rounding change.
PIN_IN = """\
define ptr @sub_40f000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
entry:
  %ST0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 0, i32 1, i32 0, i32 0
  %ST1 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 1, i32 1, i32 0, i32 0
  %ST2 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 2, i32 1, i32 0, i32 0
  %m0 = call ptr @sub_402000(ptr %state, i32 1, ptr %memory)
  %a = load x86_fp80, ptr %ST0, align 2
  %b = load x86_fp80, ptr %ST1, align 2
  %s1 = fadd x86_fp80 %a, %b
  store x86_fp80 %s1, ptr %ST2, align 2
  %r = call i32 @fesetround(i32 3072)
  %a2 = load x86_fp80, ptr %ST0, align 2
  %b2 = load x86_fp80, ptr %ST1, align 2
  %s2 = fadd x86_fp80 %a2, %b2
  store x86_fp80 %s2, ptr %ST0, align 2
  %t = tail call ptr @__remill_function_return(ptr %state, i32 %program_counter, ptr %m0)
  ret ptr %t
}
"""
# The same with nothing handing State on before the rounding change.
PIN_UNESCAPED_IN = PIN_IN.replace("  %m0 = call ptr @sub_402000(ptr %state, i32 1, ptr %memory)\n",
                                  "").replace("ptr %m0)", "ptr %memory)")


def _body(ir: str, name: str) -> list[str]:
    body = ir[ir.index(f"@{name}("):]
    return body[:body.index("\n}\n")].split("\n")


def _pinned(lines: list[str]) -> bool:
    """A store to the host State before `fesetround` and a load from it after."""
    host = {"%state.host"} | {m.group(1) for line in lines for m in [re.match(
        r"\s*(%[-\w.$]+) = getelementptr [^,]*, ptr %state\.host\b", line)] if m}
    i = next(n for n, line in enumerate(lines) if "@fesetround(" in line)

    def through_host(line, op):
        m = re.search(rf"\b{op} .*\bptr (%[-\w.$]+)", line)
        return bool(m) and m.group(1) in host
    return (any(through_host(line, "store") for line in lines[:i])
            and any(through_host(line, "load") for line in lines[i + 1:]))


@pytest.mark.parametrize("fn, fadds", [(PIN_IN, 2), (PIN_UNESCAPED_IN, 1)],
                         ids=["escaped", "not-escaped"])
def test_around_a_rounding_change_state_is_ordered_as_without_the_pass(tmp_path, fn, fadds):
    """The bracket around fesetround gives parity with the build without the pass, not more:
    compiled, both builds compute the second FADD after the rounding change once State has
    escaped (two fadds), and both reuse the first FADD's result when it has not (one: State
    is noalias and not yet captured, so LLVM takes fesetround not to touch it -- the case
    the risk register's default-environment entry names)."""
    base = module(fn)
    local, c = SL.localize(base, k=0)
    assert c == counts(local=1)
    got = [compile_ir(t, tmp_path, n).count("fadd x86_fp80") for t, n in ((base, "base"),
                                                                           (local, "local"))]
    assert got == [fadds, fadds]


def test_once_state_has_escaped_a_rounding_change_reloads_it(tmp_path, monkeypatch):
    """Compiled, the localized function stores State to the host before fesetround and loads
    it back after. The control, fesetround taken as safe (no bracket): neither, and the two
    FADDs fold into one, so the check can fail."""
    out, _ = SL.localize(module(PIN_IN), k=0)
    assert _pinned(_body(compile_ir(out, tmp_path, "pinned"), "sub_40f000"))
    monkeypatch.setattr(SL, "FENV_CALLS", ())
    monkeypatch.setattr(SL, "SAFE_CALLEES", SL.SAFE_CALLEES | {"fesetround"})
    control, _ = SL.localize(module(PIN_IN), k=0)
    opt = compile_ir(control, tmp_path, "unpinned")
    assert not _pinned(_body(opt, "sub_40f000")) and opt.count("fadd x86_fp80") == 1


TEXT_ORDER_IN = """\
define ptr @sub_40a000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
entry:
  br label %def

use:
  %AH = getelementptr inbounds i8, ptr %EAX, i64 1
  %h = load i8, ptr %AH, align 1
  %a = load x86_fp80, ptr %ST1, align 2
  store x86_fp80 %a, ptr %ST0, align 2
  ret ptr %memory

def:
  %ST0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 0, i32 1, i32 0, i32 0
  %ST1 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 1, i32 1, i32 0, i32 0
  %EAX = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 6, i32 1, i32 0, i32 0
  br label %use
}
"""


def test_a_use_above_its_gep_in_the_text_is_copied_too(tmp_path):
    """Blocks are not in dominance order: %use reads ST1 through a GEP that %def, below it
    in the text, defines, and AH through a GEP on a GEP %def defines. Both slots (2480 to
    2512) and EAX's register (2216 to 2224) are copied, in and back out."""
    out, c = SL.localize(module(TEXT_ORDER_IN), k=0)
    assert c == counts(local=1)
    assert [p.ranges for p in SL.plans(module(TEXT_ORDER_IN), k=0)] == [[(2216, 2224),
                                                                          (2480, 2512)]]
    assert out.count("i64 2480") == 4 and out.count("i64 32, i1 false)") == 2
    assert out.count("i64 2216") == 4 and out.count("i64 8, i1 false)") == 2
    compile_ir(out, tmp_path, "order")


def test_a_tail_call_stays_directly_before_its_ret():
    out, _ = SL.localize(module(BASIC_IN), k=1)
    lines = out.split("\n")
    i = lines.index("  %r = tail call ptr @__remill_function_return(ptr %state.host, i32 %v, "
                    "ptr %m)")
    assert lines[i + 1] == "  ret ptr %r"


def test_a_comment_between_a_tail_call_and_its_ret_is_read_past():
    """The tail call is still terminal: nothing is copied in after it, and the rule does not
    count it as a reload."""
    fn = rule_function(SL.K, 0, tail=True).replace("\n  ret ptr %t", "\n  ; a comment\n  ret ptr %t")
    out, c = SL.localize(module(fn))
    assert c == counts(local=1)
    lines = out.split("\n")
    i = lines.index("  %t = tail call ptr @sub_402000(ptr %state.host, i32 9, ptr %memory)")
    assert lines[i + 1:i + 3] == ["  ; a comment", "  ret ptr %t"]


# A guest read whose value reaches State (EAX), a write to the same address, and a use of
# the value in one other block (the other path overwrites EAX): with State local the value
# is SSA, and LLVM sinks a readnone read into that block, past the write (state_local's
# docstring, "Ordering guest memory").
ORDER_IN = """\
define ptr @sub_40b000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
entry:
  %ST0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 0, i32 1, i32 0, i32 0
  %EAX = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 6, i32 1, i32 0, i32 0
  %f = load x86_fp80, ptr %ST0, align 2
  %v = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef %program_counter) #7
  store i32 %v, ptr %EAX, align 4
  %m2 = call ptr @__remill_write_memory_32(ptr noundef %memory, i32 noundef %program_counter, i32 noundef 7) #7
  %c = fcmp oeq x86_fp80 %f, 0xK00000000000000000000
  br i1 %c, label %use, label %out

use:
  %x = load i32, ptr %EAX, align 4
  %m3 = call ptr @__remill_write_memory_32(ptr noundef %m2, i32 noundef 64, i32 noundef %x) #7
  ret ptr %m3

out:
  store i32 0, ptr %EAX, align 4
  ret ptr %m2
}
"""


def _calls_in_order(ir: str, name: str) -> list[str]:
    body = ir[ir.index(f"@{name}("):]
    body = body[:body.index("\n}\n")]
    return re.findall(r"@(__remill_(?:read|write)_memory_32)\(", body)


def test_a_guest_read_stays_before_a_later_write_once_state_is_local(tmp_path):
    """The localized function compiled at -O2 reads before it writes. The control: the same
    output with the intrinsics left as remill declares them (readnone), in which this
    compiler sinks the read past the write -- the S2 fixture ground-executor-multi-v1's
    divergence -- so the check is one that can fail."""
    out, c = SL.localize(module(ORDER_IN), k=0)
    assert c == counts(local=1)
    assert _calls_in_order(compile_ir(out, tmp_path, "ordered"), "sub_40b000")[:2] == [
        "__remill_read_memory_32", "__remill_write_memory_32"]
    unordered = "\n".join(line for line in out.split("\n")
                          if not line.startswith(("attributes #90002", "attributes #90003")))
    unordered = (unordered.replace("#90002", "#7").replace("#90003", "#7")
                 .replace("@__remill_read_memory_32(ptr noundef, i32 noundef)",
                          "@__remill_read_memory_32(ptr noundef readnone, i32 noundef)"))
    assert _calls_in_order(compile_ir(unordered, tmp_path, "unordered"), "sub_40b000")[:2] == [
        "__remill_write_memory_32", "__remill_read_memory_32"]


def test_ordering_touches_only_localized_functions_and_the_declarations():
    """A kept function's guest-memory calls keep their readnone group; the declarations take
    the effect; a module with nothing localized comes back as it went in."""
    kept = ORDER_IN.replace("sub_40b000", "sub_40c000").replace(
        "  %ST0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, "
        "i32 0, i32 1, i32 0, i32 0\n", "").replace(
        "  %f = load x86_fp80, ptr %ST0, align 2\n", "  %f = fadd x86_fp80 0xK0, 0xK0\n")
    out, c = SL.localize(module(ORDER_IN, kept), k=0)
    assert c == counts(local=1, kept=1)
    kept_out = out[out.index("define ptr @sub_40c000"):]
    assert kept_out[:kept_out.index("\n}\n")] == kept.rstrip("\n").rstrip("}").rstrip("\n")
    assert DECLARES_ORDERED.strip() in out and out.endswith(ORDERED_GROUPS)
    assert SL.localize(module(kept), k=0) == (module(kept), counts(kept=1))


def test_a_guest_memory_call_with_a_comment_is_ordered_and_keeps_it(tmp_path):
    """The read carries a comment after its group (the form the PC24 pass writes on the
    ten-byte accessors' declarations): it is pointed at the ordered group all the same, the
    comment kept, and compiled the read stays before the write. (Before the fold it was
    left readnone, and the write came first.)"""
    commented = ORDER_IN.replace("%program_counter) #7\n", "%program_counter) #7   ; a note\n")
    out, c = SL.localize(module(commented), k=0)
    assert c == counts(local=1)
    assert ("  %v = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef "
            "%program_counter) #90002   ; a note") in out.split("\n")
    assert SL.ordering_problems(out) == {}
    assert _calls_in_order(compile_ir(out, tmp_path, "commented"), "sub_40b000")[:2] == [
        "__remill_read_memory_32", "__remill_write_memory_32"]


def test_a_readnone_declaration_with_a_comment_is_ordered_and_keeps_it():
    """The declaration is pointed at the read's group, its comment kept. The controls: the
    same output with the declaration left on remill's readnone group, or with readnone left
    on its token, and the check names the read -- a call's effect is its call site's and its
    declaration's together, so an exact call site alone is not enough."""
    text = module(ORDER_IN).replace(
        "declare i32 @__remill_read_memory_32(ptr noundef readnone, i32 noundef) #7",
        "declare i32 @__remill_read_memory_32(ptr noundef readnone, i32 noundef) #7   ; a note")
    out, _ = SL.localize(text, k=0)
    ordered = "declare i32 @__remill_read_memory_32(ptr noundef, i32 noundef) #90002   ; a note"
    assert ordered in out.split("\n")
    assert SL.ordering_problems(out) == {}
    for control, why in (
            ("declare i32 @__remill_read_memory_32(ptr noundef, i32 noundef) #7   ; a note",
             "its effect is memory(none) where its intrinsic's is memory(argmem: read)"),
            ("declare i32 @__remill_read_memory_32(ptr noundef readnone, i32 noundef) #90002",
             "a memory attribute on a pointer parameter of its declaration")):
        [named] = SL.ordering_problems(out.replace(ordered, control))["sub_40b000"]
        assert named.startswith(why) and "@__remill_read_memory_32(" in named, named


# Call-site groups that make other memory claims than remill's readnone: memory(none) on
# the read; on the writes, an older attribute beside memory(...), which LLVM's parser lets
# replace it (readonly), and the older argmemonly with writeonly.
LEGACY_GROUPS = """
attributes #21 = { nounwind memory(none) willreturn }
attributes #22 = { nounwind readonly memory(argmem: readwrite) }
attributes #23 = { argmemonly nounwind writeonly }
"""


def test_every_memory_attribute_of_a_group_gives_way_to_the_effect():
    """Each copy has exactly the effect, where the first memory attribute was. The control
    is what the pass made before the fold, which kept `readonly` and `writeonly` beside the
    effect: LLVM reads `{ readonly memory(argmem: readwrite) }` as memory(read), so that
    write would have been a read, and the check names both writes."""
    fn = (ORDER_IN.replace("%program_counter) #7\n", "%program_counter) #21\n")
          .replace("i32 noundef 7) #7\n", "i32 noundef 7) #22\n")
          .replace("i32 noundef %x) #7\n", "i32 noundef %x) #23\n"))
    out, c = SL.localize(module(fn) + LEGACY_GROUPS, k=0)
    assert c == counts(local=1)
    made = [line for line in out.split("\n") if line.startswith("attributes #9000")]
    assert made == ["attributes #90002 = { nounwind memory(argmem: read) willreturn }",
                    "attributes #90003 = { nounwind memory(argmem: readwrite) }",
                    "attributes #90004 = { memory(argmem: readwrite) nounwind }",
                    'attributes #90005 = { nobuiltin nounwind memory(argmem: read) willreturn '
                    '"no-builtins" }',
                    'attributes #90006 = { nobuiltin nounwind memory(argmem: readwrite) '
                    'willreturn "no-builtins" }']
    assert SL.ordering_problems(out) == {}
    before = (out.replace("#90003 = { nounwind memory(argmem: readwrite) }",
                          "#90003 = { nounwind readonly memory(argmem: readwrite) }")
              .replace("#90004 = { memory(argmem: readwrite) nounwind }",
                       "#90004 = { argmemonly nounwind writeonly memory(argmem: readwrite) }"))
    named = SL.ordering_problems(before)["sub_40b000"]
    assert len(named) == 2, named
    assert "its effect is memory(argmem: read) where its intrinsic's is memory(argmem: " \
           "readwrite)" in named[0]
    assert "its effect is memory(argmem: write)" in named[1]


# remill's ten-byte accessors as the PC24 pass leaves them in every fold
# (tools/spike/pc24pass/pc24pass.py --fix-f80-accessors): a declaration group that makes
# no memory claim, a comment after it and after the group, and call sites on a nobuiltin
# group.
F80_DECLARES = """
declare ptr @__remill_read_memory_f80(ptr noundef readnone, i32 noundef, ptr noundef nonnull align 16 dereferenceable(16)) #13   ; sa2-pc24pass f80
attributes #12 = { nobuiltin nounwind "no-builtins" }
attributes #13 = { noduplicate noinline nounwind optnone "no-builtins" }   ; sa2-pc24pass f80 (was #7)
"""
# FLD m80: the ten-byte read fills a buffer, which is loaded and pushed to ST0.
F80_IN = """\
define ptr @sub_40e000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
entry:
  %buf = alloca x86_fp80, align 16
  %ST0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, i32 0, i32 1, i32 0, i32 0
  %m = call ptr @__remill_read_memory_f80(ptr noundef %memory, i32 noundef %program_counter, ptr noundef nonnull align 16 dereferenceable(16) %buf) #12
  %v = load x86_fp80, ptr %buf, align 16
  store x86_fp80 %v, ptr %ST0, align 2
  ret ptr %m
}
"""


def test_the_ten_byte_read_writes_its_buffer(tmp_path):
    """The f80 read's call carries memory(argmem: readwrite), its declaration (on the PC24
    pass's group, which already allows that) keeps its group and comment and loses
    readnone on the token, and compiled the load of the buffer it filled survives. The
    control, the call given memory(argmem: read) as a plain read is: LLVM then knows nothing
    wrote the buffer and the load is gone -- every FLD m80 would push garbage."""
    out, c = SL.localize(module(F80_IN) + F80_DECLARES, k=0)
    assert c == counts(local=1)
    call = next(line for line in out.split("\n") if "call ptr @__remill_read_memory_f80(" in line)
    group = re.search(r" #(\d+)$", call).group(1)
    assert (f"attributes #{group} = {{ nobuiltin nounwind \"no-builtins\" memory(argmem: "
            "readwrite) }") in out.split("\n")
    assert ("declare ptr @__remill_read_memory_f80(ptr noundef, i32 noundef, ptr noundef nonnull "
            "align 16 dereferenceable(16)) #13   ; sa2-pc24pass f80") in out.split("\n")
    assert SL.ordering_problems(out) == {}
    load = re.compile(r"= load x86_fp80, ptr %buf\b")
    assert load.search("\n".join(_body(compile_ir(out, tmp_path, "f80"), "sub_40e000")))
    control = out.replace(f"#{group} = {{ nobuiltin nounwind \"no-builtins\" memory(argmem: "
                          "readwrite) }", f"#{group} = {{ nobuiltin nounwind \"no-builtins\" "
                          "memory(argmem: read) }")
    assert control != out and SL.ordering_problems(control)
    assert not load.search("\n".join(_body(compile_ir(control, tmp_path, "f80read"),
                                           "sub_40e000")))


# ------------------------------------------------------- the check after rewriting
# The 2026-10-02 confirm review, NIT 1: seven deliberate breaks to `ordering_problems` passed
# every test, because every problem it finds refuses the function under one kind. These hold
# each problem by its own words.

def _read_call(out: str) -> str:
    [line] = [x.strip() for x in out.split("\n") if "= call i32 @__remill_read_memory_32(" in x]
    return line


_READ = "@__remill_read_memory_32(ptr noundef %memory, i32 noundef %program_counter)"
_READ_DECLARED = "declare i32 @__remill_read_memory_32(ptr noundef readnone, i32 noundef) #7"
# What the input changes in ORDER_IN's module, and the problem the check names after the kind.
POST_CHECK = {
    "a memory attribute on a pointer operand": (
        lambda t: t.replace(f"{_READ} #7", f"{_READ} #7".replace("noundef %memory",
                                                                  "noundef readonly %memory")),
        "a memory attribute on a pointer operand: %v = call i32 @__remill_read_memory_32(ptr "
        "noundef readonly %memory, i32 noundef %program_counter) #90002"),
    "a guest-memory intrinsic used as a value": (
        lambda t: t.replace("  %c = fcmp oeq", "  %fp = select i1 true, ptr "
                            "@__remill_read_memory_32, ptr null\n  %c = fcmp oeq"),
        "a guest-memory intrinsic used other than as the callee of a call: %fp = select i1 "
        "true, ptr @__remill_read_memory_32, ptr null"),
    "an intrinsic the module defines": (
        lambda t: t.replace(_READ_DECLARED, "define i32 @__remill_read_memory_32(ptr noundef "
                            "readnone %m, i32 noundef %a) #7 {\n  ret i32 0\n}"),
        f"no declaration of its intrinsic: %v = call i32 {_READ} #90002"),
}


@pytest.mark.parametrize("why", sorted(POST_CHECK))
def test_the_check_after_rewriting_refuses_what_it_cannot_order(why):
    """Each passes the analysis and fails only the check on the output: the function comes
    back as it went in, counted refused, and its reason names the problem."""
    change, problem = POST_CHECK[why]
    text = change(module(ORDER_IN))
    assert text != module(ORDER_IN)
    assert SL.localize(text, k=0) == (text, counts(refused=1))
    assert SL.refusals(text, k=0) == {"sub_40b000": [
        "a guest-memory call the ordering does not leave with exactly its effect: " + problem]}


def test_the_check_after_rewriting_reads_every_group_it_relies_on():
    """A call's effect is its call site's group and its declaration's together, so each must
    be defined and readable. One that is not is named for what it is, never read as the
    unknown effect, which intersected with the other side's would pass. `memory(read,
    write)` gives the default access twice, which LLVM's parser refuses, and `effect_of`
    reads it as nothing; so does a location LLVM does not have."""
    out, c = SL.localize(module(ORDER_IN), k=0)
    assert c == counts(local=1) and SL.ordering_problems(out) == {}
    read = _read_call(out)
    declared = "declare i32 @__remill_read_memory_32(ptr noundef, i32 noundef) #90002"
    assert read == f"%v = call i32 {_READ} #90002" and declared in out.split("\n")
    assert SL.effect_of("nounwind memory(read, write)") is None
    assert SL.effect_of("nounwind memory(argmem: sideways)") is None
    groups = ("\nattributes #90020 = { nounwind memory(argmem: sideways) }\n"
              "attributes #90021 = { nounwind memory(read, write) }\n")
    for edit, problem in (
            ((read, f"%v = call i32 {_READ} #90020"),
             f"its attribute group #90020 cannot be read: %v = call i32 {_READ} #90020"),
            ((declared, declared.replace("#90002", "#90077")),
             f"its declaration's attribute group #90077 is not defined: {read}"),
            ((declared, declared.replace("#90002", "#90021")),
             f"its declaration's attribute group #90021 cannot be read: {read}")):
        broken = (out + groups).replace(*edit)
        assert broken != out + groups
        assert SL.ordering_problems(broken) == {"sub_40b000": [problem]}, edit


def test_the_check_after_rewriting_reads_internal_copies():
    """The corpus localizes internal copies (`define internal ptr @sub_<va>.<n>`, as the arms
    fold writes them: sub_53fcf0.28 in sub_0053FF58's), and the check reads them as it reads
    every localized function. The control: the copy's read pointed back at remill's readnone
    group is named."""
    copy = ORDER_IN.replace("define ptr @sub_40b000(", "define internal ptr @sub_40b000.28(")
    out, c = SL.localize(module(copy), k=0)
    assert c == counts(local=1)
    assert ("define internal ptr @sub_40b000.28(ptr noalias %state.host, i32 %program_counter, "
            "ptr noalias %memory) {") in out.split("\n")
    assert SL.ordering_problems(out) == {}
    read = _read_call(out)
    control = out.replace(read, f"%v = call i32 {_READ} #7")
    assert SL.ordering_problems(control) == {"sub_40b000.28": [
        "its effect is memory(none) where its intrinsic's is memory(argmem: read): "
        f"%v = call i32 {_READ} #7"]}


# NIT 3 of the same review: a kept function calling a localized one of its module is not
# refused, and -O2 inlines the callee into it. Its read is pinned by a store to State before
# the call, or left in SSA past it.
INLINED_CALLER = """\
define ptr @sub_50a000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
entry:
  %EAX = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 6, i32 1, i32 0, i32 0
  %v = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef %program_counter) #7
  store i32 %v, ptr %EAX, align 4
  %m = call ptr @sub_50b000.1(ptr %state, i32 %program_counter, ptr %memory)
  ret ptr %m
}
"""
INLINED_CALLEE = (ORDER_IN.replace("define ptr @sub_40b000(", "define internal ptr @sub_50b000.1(")
                  .replace("  %v = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 "
                           "noundef %program_counter) #7\n  store i32 %v, ptr %EAX, align 4\n", ""))


@pytest.mark.parametrize("pinned,order", [
    (True, ["__remill_read_memory_32", "__remill_write_memory_32", "__remill_write_memory_32"]),
    (False, ["__remill_write_memory_32", "__remill_write_memory_32", "__remill_read_memory_32"])])
def test_a_kept_function_that_inlines_a_localized_one_compiles_as_before(tmp_path, pinned, order):
    """Compiled at -O2, the kept caller's guest calls come out in the order the build without
    the pass gives them: the read first when a store to State pins it, and after the
    callee's writes when it is left in SSA, in both builds alike (remill's own readnone
    hazard, which the risk register carries). The census found no such pair in the 15 mixed
    modules; this holds the docstring's "compiles as before" for the shape by compiling it."""
    caller = INLINED_CALLER if pinned else INLINED_CALLER.replace(
        "  store i32 %v, ptr %EAX, align 4\n  %m = call ptr @sub_50b000.1(ptr %state, i32 "
        "%program_counter, ptr %memory)\n", "  %m = call ptr @sub_50b000.1(ptr %state, i32 "
        "%program_counter, ptr %memory)\n  store i32 %v, ptr %EAX, align 4\n")
    base = module(caller, INLINED_CALLEE)
    out, c = SL.localize(base, k=0)
    assert c == counts(local=1, kept=1) and SL.ordering_problems(out) == {}
    got = {}
    for name, text in (("base", base), ("local", out)):
        ir = compile_ir(text, tmp_path, f"{name}{int(pinned)}")
        body = ir[ir.index("define ptr @sub_50a000("):]
        body = body[:body.index("\n}\n")]
        assert "@sub_50b000.1(" not in body          # inlined
        got[name] = re.findall(r"@(__remill_(?:read|write)_memory_32)\(", body)
    assert got == {"base": order, "local": order}


# Attribute lists, as call sites and declarations carry them, and what LLVM 20 makes of them.
EFFECT_CASES = (
    "nounwind", "readnone", "readonly", "writeonly", "argmemonly", "inaccessiblememonly",
    "inaccessiblemem_or_argmemonly", "readonly argmemonly", "memory(none)", "memory(read)",
    "memory(argmem: read)", "memory(argmem: readwrite)", "memory(read, argmem: readwrite)",
    "memory(argmem: write, inaccessiblemem: read)", "readonly memory(argmem: readwrite)",
    "memory(argmem: readwrite) writeonly", "memory(none) memory(argmem: read)",
    'nobuiltin nounwind readnone willreturn "no-builtins"',
    'noduplicate noinline nounwind optnone "frame-pointer"="none" "no-builtins"',
    'mustprogress nofree nosync nounwind memory(argmem: read) willreturn "tune-cpu"="generic"',
)


def test_the_effect_engine_reads_attribute_lists_as_llvm_does(tmp_path):
    """Each list as a declaration's group, compiled by the fold's command: the memory effect
    LLVM prints for it (none when it is unknown) is the engine's, `render(effect_of(...))`,
    the older attributes replacing a memory(...) beside them included."""
    probe = []
    for i, attrs in enumerate(EFFECT_CASES):
        probe.append(f"declare i32 @p{i}() #{i}\nattributes #{i} = {{ {attrs} }}\n"
                     f"define i32 @u{i}() {{\n  %v = call i32 @p{i}()\n  ret i32 %v\n}}")
    ir = compile_ir("\n".join(probe) + "\n", tmp_path, "effects")
    groups = SL.attribute_groups(ir)
    got = {}
    for m in re.finditer(r"^declare [^@\n]*@p(\d+)\(\)[^#\n]*(?:#(\d+))?$", ir, re.M):
        mem = re.search(r"memory\([^)]*\)", groups.get(m.group(2) or "", ""))
        got[int(m.group(1))] = mem.group(0) if mem else ""
    assert got == {i: SL.render(SL.effect_of(a)) for i, a in enumerate(EFFECT_CASES)}
    assert len(set(got.values())) >= 8


def test_a_module_without_the_copy_declaration_gets_one(tmp_path):
    text = module(BASIC_IN).replace(
        "declare void @llvm.memcpy.p0.p0.i64(ptr noalias nocapture writeonly, ptr noalias "
        "nocapture readonly, i64, i1 immarg)\n", "")
    assert "@llvm.memcpy.p0.p0.i64(ptr noalias" not in text
    out, c = SL.localize(text, k=1)
    assert c == counts(local=1)
    assert out.count("declare void @llvm.memcpy.p0.p0.i64(") == 1
    assert f"\n{SL.MEMCPY_DECLARATION}\n" in out
    compile_ir(out, tmp_path, "nodecl")


def test_the_pipeline_runs_it_last_on_every_fold():
    """internalize.rewrite ends with all five speed passes: "local" sees the other three's
    output and "fast" sees its: a function the rule selects is localized in the fold, and
    its guest access (here at a constant address) goes through the fast path."""
    text = module(rule_function(SL.K, 0).replace("  ret ptr %memory", (
        "  %g = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef 4096) #7\n"
        "  ret ptr %memory")))
    folded, stats = internalize.rewrite(text)
    assert SR.PASSES == ("spill", "intrin", "pcround", "local", "fast")
    assert (stats["speed_local"], stats["speed_local_kept"], stats["speed_local_refused"]) == \
        (1, 0, 0)
    assert (stats["speed_fast"], stats["speed_fast_kept"]) == (1, 0)
    three, _ = SR.rewrite(text, passes=("spill", "intrin", "pcround"))
    four = SL.localize(three)[0]
    assert folded == SL.fast(four)[0] != four != three
    assert "@sub_405000(ptr noalias %state.host," in folded
    assert "  %g = call i32 @__sa2_fast_read_32(ptr %sl.base, i32 4096)\n" in folded


# ------------------------------------------------------------------- the selection rule

def rule_function(st_refs: int, calls: int, tail: bool = False) -> str:
    lines = ["define ptr @sub_405000(ptr noalias %state, i32 %program_counter, "
             "ptr noalias %memory) {"]
    for i in range(st_refs):
        lines.append(f"  %s{i} = getelementptr inbounds %struct.State, ptr %state, i64 0, "
                     f"i32 0, i32 7, i32 0, i64 {i % 8}, i32 1, i32 0, i64 0")
    lines.append("  %a = load x86_fp80, ptr %s0, align 2")
    for j in range(calls):
        lines.append(f"  %m{j} = call ptr @sub_402000(ptr %state, i32 {j}, ptr %memory)")
        lines.append(f"  %b{j} = load x86_fp80, ptr %s1, align 2")
    if tail:
        lines += ["  %t = tail call ptr @sub_402000(ptr %state, i32 9, ptr %memory)",
                  "  ret ptr %t"]
    else:
        lines.append("  ret ptr %memory")
    return "\n".join(lines) + "\n}\n"


@pytest.mark.parametrize("calls", [0, 1, 3])
def test_the_rule_localizes_at_32_st_references_per_reloading_bracket_point(calls):
    at = SL.K * (calls + 1)
    assert SL.K == 32
    assert SL.localize(module(rule_function(at, calls)))[1] == counts(local=1)
    below = module(rule_function(at - 1, calls))
    assert SL.localize(below) == (below, counts(kept=1))


def test_a_terminal_bracket_point_is_not_a_reload():
    """A tail call followed by `ret` copies back once, like a return, so the rule does not
    count it."""
    assert SL.localize(module(rule_function(SL.K, 0, tail=True)))[1] == counts(local=1)


# ------------------------------------------------------------------------- default deny

ST0 = ("  %ST0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 7, i32 0, "
       "i32 0, i32 1, i32 0, i32 0\n  %a = load x86_fp80, ptr %ST0, align 2\n")
# Each use of State the pass cannot account for, with the reason it gives first: (body after
# ST0, reason) or (body, reason, extra), where extra may give the function another define
# line, text to append to the module, and how many other functions it keeps.
OTHER_USE = "a use of State the rewrite does not account for"
KEPT_CALLEE = ("define internal ptr @sub_40a100(ptr noalias %state, i32 %program_counter, "
               "ptr noalias %memory) {\n  ret ptr %memory\n}\n")
# An internal copy as the folds write one (sub_53fcf0.28 in the arms fold of sub_0053FF58).
KEPT_COPY = KEPT_CALLEE.replace("@sub_40a100(", "@sub_40a100.28(")
REFUSED = {
    "State to a safe callee": (
        "  %v = call i32 @__remill_read_memory_32(ptr %state, i32 0)\n",
        "State given to a safe callee"),
    "a State pointer to a bracketed call": (
        "  %m = call ptr @sub_402000(ptr %ST0, i32 0, ptr %memory)\n",
        "a pointer into State given to a bracketed call"),
    "a State pointer stored": (
        "  %p = alloca ptr, align 8\n  store ptr %ST0, ptr %p, align 8\n", "a State pointer stored"),
    "State as an integer": ("  %i = ptrtoint ptr %state to i64\n", OTHER_USE),
    "a variable index": (
        "  %x = getelementptr inbounds %struct.State, ptr %state, i64 0, i32 0, i32 7, i32 0, "
        "i64 %i, i32 1, i32 0, i64 0\n", "a GEP on State with an index that is not a constant"),
    "a GEP outside State": ("  %x = getelementptr inbounds i8, ptr %state, i64 4096\n",
                            "a GEP outside State"),
    "a field index out of range": (
        "  %x = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 99\n",
        "a GEP on State the layout engine cannot place"),
    "an access past State": (
        "  %y = getelementptr inbounds i8, ptr %state, i64 3500\n  %z = load i64, ptr %y, align 1\n",
        "an access outside State"),
    "%STATE read back": (
        "  %STATE = alloca ptr, align 4\n  store ptr %state, ptr %STATE, align 4\n"
        "  %s = load ptr, ptr %STATE, align 4\n", "a use of %STATE other than remill's store"),
    "a phi of State pointers": ("  br label %b\nb:\n  %p = phi ptr [ %ST0, %0 ]\n", OTHER_USE),
    "a select of State pointers": ("  %p = select i1 true, ptr %ST0, ptr %state\n", OTHER_USE),
    "a State pointer compared": ("  %c = icmp eq ptr %ST0, null\n", OTHER_USE),
    "a musttail call": (
        "  %t = musttail call ptr @sub_402000(ptr %state, i32 0, ptr %memory)\n  ret ptr %t\n",
        "a musttail call"),
    "an invoke": (
        "  %t = invoke ptr @sub_402000(ptr %state, i32 0, ptr %memory) to label %b unwind "
        "label %b\nb:\n", "an invoke or callbr"),
    "a callbr": ('  callbr void asm "", "!i"() to label %b [label %b]\nb:\n',
                 "an invoke or callbr"),
    "other inline asm": ("  call void asm sideeffect \"nop\", \"~{memory}\"()\n",
                         "inline asm other than remill's barrier"),
    "a call it cannot read": ("  %v = call ptr @sub_402000(ptr %state, i32 0, ptr %memory\n",
                              "a call the rewrite cannot read"),
    "State in an operand bundle": (
        '  %m = call ptr @sub_402000(ptr %state, i32 0, ptr %memory) [ "x"(ptr %ST0) ]\n',
        "State outside a call's arguments"),
    "a call through a State pointer": ("  call void %ST0()\n", "State outside a call's arguments"),
    "a State copy of no constant length": (
        "  call void @llvm.memcpy.p0.p0.i64(ptr %ST0, ptr %memory, i64 %n, i1 false)\n",
        "a State copy of no constant length"),
    "the result of a copy given State": (
        # memcpy returns its destination: ST1 written through it, past the copied ST0 unit
        "  %r = call ptr @memcpy(ptr %ST0, ptr %memory, i64 10)\n"
        "  %q = getelementptr inbounds i8, ptr %r, i64 16\n  store x86_fp80 %a, ptr %q, align 2\n",
        "the result of a safe callee given State"),
    "the result of a memset given State": (
        "  %r = call ptr @memset(ptr %ST0, i32 0, i64 10)\n"
        "  %q = getelementptr inbounds i8, ptr %r, i64 16\n  store x86_fp80 %a, ptr %q, align 2\n",
        "the result of a safe callee given State"),
    "the result of a memmove given State": (
        "  %r = call ptr @memmove(ptr %ST0, ptr %memory, i64 10)\n"
        "  %q = getelementptr inbounds i8, ptr %r, i64 16\n  store x86_fp80 %a, ptr %q, align 2\n",
        "the result of a safe callee given State"),
    "a volatile load": ("  %w = load volatile i32, ptr %ST0, align 4\n",
                        "a load from State the rewrite cannot read"),
    "a vector load": (
        "  %XMM0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 1, i32 0\n"
        "  %x = load <2 x i64>, ptr %XMM0, align 16\n",
        "a load of a type the layout engine does not model"),
    "an atomic store": ("  store atomic i32 0, ptr %ST0 seq_cst, align 4\n",
                        "a store the rewrite cannot read"),
    "a volatile store": ("  store volatile i32 0, ptr %ST0, align 4\n",
                         "a store the rewrite cannot read"),
    "a vector store": (
        "  %MM0 = getelementptr inbounds %struct.State, ptr %state, i32 0, i32 0, i32 8, i32 0, "
        "i32 0, i32 1\n  store <4 x i16> zeroinitializer, ptr %MM0, align 8\n",
        "a store of a type the layout engine does not model"),
    "a guest-memory group the module lacks": (
        "  %g = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef 0) #77\n",
        "a guest-memory call carrying an attribute group the module does not define"),
    "a guest-memory call with metadata": (
        "  %g = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef 0) #7, !dbg !5\n",
        "a guest-memory call with attributes, bundles or metadata after its arguments"),
    "a guest-memory call with attributes written out": (
        "  %g = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef 0) readnone\n",
        "a guest-memory call with attributes, bundles or metadata after its arguments"),
    "a guest-memory call left with the unknown effect": (
        # no group at the call, and a declaration whose group allows any effect, so it is
        # left as it is: the call's effect is not the read's
        "  %h = call i16 @__remill_read_memory_16(ptr noundef %memory, i32 noundef 0)\n",
        "a guest-memory call the ordering does not leave with exactly its effect",
        {"module": "declare i16 @__remill_read_memory_16(ptr noundef, i32 noundef) #9\n"}),
    "a guest-memory declaration it cannot read": (
        "  %h = call i16 @__remill_read_memory_16(ptr noundef %memory, i32 noundef 0) #7\n",
        "a guest-memory call the ordering does not leave with exactly its effect",
        {"module": "declare i16 @__remill_read_memory_16(ptr noundef readnone, i32 noundef) "
                   "readnone\n"}),
    "a name the rewrite adds": ("  %sl.0.0.l = add i32 0, 0\n",
                                "a name the rewrite adds is already in use"),
    "State returned": ("  ret ptr %ST0\n", "State returned"),
    "a tail call given State": (
        "  tail call void @llvm.memcpy.p0.p0.i64(ptr %ST0, ptr %memory, i64 10, i1 false)\n",
        "a tail call given a pointer into State"),
    "a call to a lifted function the module keeps": (
        "  %m = call ptr @sub_40a100(ptr %state, i32 0, ptr %memory)\n",
        "a call to a lifted function this module defines and leaves as it is",
        {"module": KEPT_CALLEE, "kept": 1}),
    "a call to an internal copy the module keeps": (
        "  %m = call ptr @sub_40a100.28(ptr %state, i32 0, ptr %memory)\n",
        "a call to a lifted function this module defines and leaves as it is",
        {"module": KEPT_COPY, "kept": 1}),
    "a define line it does not know": (
        "", "a lifted function's define line the rewrite does not know",
        {"define": "define ptr @sub_406000(ptr %state, i32 %program_counter, ptr %memory) {"}),
}
# Kinds that do not leave a function refused in `localize`, each with its own test.
REFUSED_ELSEWHERE = {
    "the layout engine cannot place State":
        "test_a_state_the_engine_cannot_place_is_kept_and_named_by_the_census",
}


@pytest.mark.parametrize("why", sorted(REFUSED))
def test_a_function_it_cannot_account_for_comes_back_unchanged_and_counted(why):
    body, reason, *rest = REFUSED[why]
    extra = rest[0] if rest else {}
    body = ST0 + body
    if not body.rstrip("\n").split("\n")[-1].lstrip().startswith("ret"):
        body += "  ret ptr %memory\n"
    define = extra.get("define", "define ptr @sub_406000(ptr noalias %state, i32 "
                                 "%program_counter, ptr noalias %memory) {")
    text = module(define + "\n" + body + "}\n") + extra.get("module", "")
    assert SL.localize(text, k=0) == (text, counts(refused=1, kept=extra.get("kept", 0))), why
    found = SL.refusals(text, k=0)
    assert list(found) == ["sub_406000"] and found["sub_406000"][0].startswith(reason), found


def test_a_state_the_engine_cannot_place_is_kept_and_named_by_the_census():
    """With no x87 stack the engine can place there is no x87 stack reference, so `localize`
    keeps the function; the census's full analysis names why it could not be localized."""
    text = module("define ptr @sub_406000(ptr noalias %state, i32 %program_counter, ptr "
                  "noalias %memory) {\n" + ST0 + "  ret ptr %memory\n}\n")
    text = text.replace("%struct.X87Stack = type { [8 x %struct.anon.3] }\n", "")
    assert SL.localize(text, k=0) == (text, counts(kept=1))
    [p] = SL.plans(text, k=0)
    assert p.refused[0].startswith("the layout engine cannot place State"), p.refused


def test_every_refusal_kind_has_a_case():
    """Every reason the pass writes starts with a kind in state_local.REFUSALS, and every
    kind has a case above, so a refusal no test can see cannot be added unnoticed."""
    kinds = {case[1] for case in REFUSED.values()} | set(REFUSED_ELSEWHERE)
    assert sorted(kinds) == sorted(SL.REFUSALS)
    assert all(callable(globals().get(name)) for name in REFUSED_ELSEWHERE.values())
    source = (ROOT / "tools" / "s2" / "corpus_run" / "state_local.py").read_text()
    written = [s for s in re.findall(
        r'(?:refuse\(|refused\.append\(|problems\[i\] = |gep_problems\.get\(i, |'
        r'forced\[name\] = \()\s*f?"([^"{]*)', source) if s.strip()]
    assert len(written) >= len(SL.REFUSALS)
    assert [s for s in written if not any(k.startswith(s.rstrip()) or s.startswith(k)
                                          for k in SL.REFUSALS)] == []


def test_a_callee_it_does_not_know_is_bracketed_not_trusted():
    for name in ("__remill_new_intrinsic", "sa2_new_helper", "llvm.new.intrinsic.f80", "cosl"):
        assert not SL.safe_callee(name), name
        fn = ("define ptr @sub_408000(ptr noalias %state, i32 %program_counter, ptr noalias "
              f"%memory) {{\n{ST0}  %v = call i32 @{name}(i32 0)\n"
              "  store x86_fp80 %a, ptr %ST0, align 2\n  ret ptr %memory\n}\n")
        out, c = SL.localize(module(fn), k=0)
        assert c == counts(local=1)
        lines = out.split("\n")
        i = lines.index(f"  %v = call i32 @{name}(i32 0)")
        assert lines[i - 1].startswith("  call void @llvm.memcpy") and "%sl.1.0.h" in lines[i - 1]
        assert lines[i + 3].startswith("  call void @llvm.memcpy") and "%sl.2.0.l" in lines[i + 3]


def test_kept_and_integer_functions_come_back_unchanged():
    text = module(rule_function(SL.K - 1, 0), BASIC_IN.replace("sub_401000", "sub_409000"))
    assert SL.localize(text) == (text, counts(kept=2))


# ---------------------------------------------------------- the safe list and the runtime

def _link_compile_commands(monkeypatch, tmp_path: Path) -> list[list[str]]:
    """Every command tools/s4/link_s4.py `phase_compile` compiles with (`[..., source, "-o",
    object]`), read from the phase itself (test_speed_rewrite.py reads it the same way)."""
    monkeypatch.syspath_prepend(str(ROOT / "tools" / "s4"))
    import link_s4 as LS
    ran = []
    monkeypatch.setattr(LS, "OUT", tmp_path / "s4" / "link")
    table = tmp_path / "s3" / "link" / "tag" / "function_table_whole.c"
    table.parent.mkdir(parents=True)
    table.write_text("")
    monkeypatch.setattr(LS.L, "run", lambda cmd, **kw: ran.append(cmd) or {"rc": 0, "stderr_tail": ""})
    LS.phase_compile({}, "state-local-tie", "tag", tmp_path / "main.c", True)
    return ran


def _link_command_for(commands: list[list[str]], source: Path) -> list[str]:
    mine = [cmd for cmd in commands if cmd[-3:-1] == [str(source), "-o"]]
    assert len(mine) == 1, commands
    return mine[0]


def _link_runtime_command(monkeypatch, tmp_path: Path) -> list[str]:
    """The command tools/s4/link_s4.py `phase_compile` compiles remill_runtime.cpp with."""
    return _link_command_for(_link_compile_commands(monkeypatch, tmp_path), RUNTIME)


def _runtime_ir(command: list[str], source: Path, out: Path) -> str:
    includes = [Path(command[i + 1]) for i, arg in enumerate(command[:-1]) if arg == "-I"]
    missing = [str(p) for p in [Path(command[0])] if not p.is_file()] + \
        [str(p) for p in includes if not p.is_dir()]
    assert not missing, f"the link's compiler or include directories are not present: {missing}"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(out.parent / "zig-local"),
               ZIG_GLOBAL_CACHE_DIR=str(GEN / "zig-cache-tests"))
    done = subprocess.run(command[:-3] + ["-g0", "-S", "-emit-llvm", str(source), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    return out.read_text()


def test_every_safe_callee_the_runtime_defines_reaches_no_state(monkeypatch, tmp_path):
    command = _link_runtime_command(monkeypatch, tmp_path)
    assert SL.runtime_problems(_runtime_ir(command, RUNTIME, tmp_path / "rt.ll")) == []


def test_the_spliced_helpers_are_pure():
    for path in HELPER_FILES:
        assert path.is_file(), f"{path} is not present; this check fails, never skips"
        assert SL.helper_problems(path.read_text()) == [], path
    defined = {n for p in HELPER_FILES for n in re.findall(r"^define [^@]*@(\w+)\(",
                                                           p.read_text(), re.M)}
    assert set(SL.HELPERS) <= defined


def test_the_safe_list_is_the_runtime_s_the_helpers_and_the_c_library_s():
    """Every safe callee is one the runtime defines (named `__remill_*`), a spliced helper,
    or the C library's copy; nothing else is trusted by name."""
    others = sorted(n for n in SL.SAFE_CALLEES if not n.startswith("__remill_")
                    and n not in SL.HELPERS and n not in SL.LIBC)
    assert others == []
    assert not [n for n in SL.SAFE_CALLEES if re.search(
        r"function_call|function_return|jump|error|missing_block|hyper_call|io_port", n)]


def _redefine(src: str, name: str, body: str) -> str:
    out, n = re.subn(rf"(\b{re.escape(name)}\([^)]*\)\s*)\{{[^{{}}]*\}}", rf"\g<1>{{ {body} }}", src)
    assert n == 1, f"{name}'s definition was not found once in {RUNTIME}"
    return out


def test_the_tie_names_a_safe_callee_that_stops_being_pure(monkeypatch, tmp_path):
    """A runtime copy in which a barrier counts in a runtime global, an atomic region sets
    the rounding mode, an undefined value is read through a runtime function that reads the
    host's rounding mode, one compare is no longer defined, another barrier holds an empty
    asm with a memory clobber, and a delay slot calls through its argument: the tie names
    exactly those six, the last two for the asm and the indirect call alone."""
    src = RUNTIME.read_text()
    src = _redefine(src, "__remill_barrier_load_load", "g_emulated++; return m;")
    src = _redefine(src, "__remill_atomic_begin", "fesetround(0); return m;")
    src = _redefine(src, "__remill_undefined_64", "return (uint64_t)sa2_rt_get_host_round();")
    src, n = re.subn(r"\bbool __remill_compare_ule\(", "bool __remill_compare_ule_gone(", src)
    assert n == 1
    src = _redefine(src, "__remill_barrier_store_store",
                    '__asm__ volatile("" ::: "memory"); return m;')
    src = _redefine(src, "__remill_delay_slot_end",
                    "return reinterpret_cast<Memory *(*)(Memory *)>(m)(m);")
    copy = tmp_path / "remill_runtime.cpp"
    copy.write_text(src)
    command = _link_runtime_command(monkeypatch, tmp_path)
    problems = SL.runtime_problems(_runtime_ir(command, copy, tmp_path / "rt.ll"))
    assert sorted(p.split(":")[0] for p in problems) == [
        "__remill_atomic_begin", "__remill_barrier_load_load", "__remill_barrier_store_store",
        "__remill_compare_ule", "__remill_delay_slot_end", "__remill_undefined_64"]
    named = {p.split(":")[0]: p for p in problems}
    assert "['inline asm in __remill_barrier_store_store']" in named["__remill_barrier_store_store"]
    assert "['an indirect call in __remill_delay_slot_end']" in named["__remill_delay_slot_end"]


_PC_ROUND_ENTRY = "zeroext %cw) local_unnamed_addr #0 {\nentry:\n"


@pytest.mark.parametrize("old, new, named", [
    (_PC_ROUND_ENTRY, _PC_ROUND_ENTRY + "  %sa2.p = alloca i32, align 4\n", "%sa2.p = alloca"),
    (_PC_ROUND_ENTRY, _PC_ROUND_ENTRY + "  call void %sa2.fp()\n", "call void %sa2.fp()"),
    (_PC_ROUND_ENTRY, _PC_ROUND_ENTRY + "  %sa2.g = ptrtoint ptr @sa2_guest_poison to i64\n",
     "@sa2_guest_poison"),
    ("zeroext %cw) local_unnamed_addr #0 {", "zeroext %cw, ptr %sa2.s) local_unnamed_addr #0 {",
     "a pointer parameter"),
], ids=["memory", "call", "global", "pointer"])
def test_the_tie_names_a_helper_that_stops_being_pure(old, new, named):
    """pc_round.ll with __sa2_pc_round given memory, a call to something that is not a
    helper, a global, or a pointer parameter: each is named, and nothing else is."""
    text = HELPER_FILES[0].read_text()
    assert text.count(old) == 1, f"{HELPER_FILES[0]} no longer has the line this test edits"
    problems = SL.helper_problems(text.replace(old, new))
    assert len(problems) == 1 and problems[0].startswith("__sa2_pc_round:"), problems
    assert named in problems[0], problems
    assert SL.helper_problems("define i32 @f() {\n  ret i32 0\n}\n") == [
        "the text defines none of the spliced helpers"]


# ------------------------------------------------------------ the guest-memory fast path

# BASIC_OUT through the fast path: the base loaded after the State alloca, and the read a call
# to the helper the pass splices.
BASIC_FAST_OUT = BASIC_OUT.replace(
    "  %state = alloca %struct.State, align 16\n",
    "  %state = alloca %struct.State, align 16\n"
    "  %sl.base = load ptr, ptr @sa2_guest_fast_base, align 8\n").replace(
    "  %v = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef %sp) #90002\n",
    "  %v = call i32 @__sa2_fast_read_32(ptr %sl.base, i32 %sp)\n")

# What the pass appends to a module whose fast-path functions read four bytes and nothing
# else: the helper (the bounds of runtime/guest_memory.h `sa2_gp` for four bytes, one
# unsigned compare: 0x20000000 - 0x1000 - 4 + 1 = 536866813), its slow half, the base.
FAST_TAIL_READ_32 = """\
define internal i32 @__sa2_fast_read_32(ptr %base, i32 %addr) alwaysinline {
entry:
  %off = add i32 %addr, -4096
  %inside = icmp ult i32 %off, 536866813
  %mapped = icmp ne ptr %base, null
  %ok = and i1 %mapped, %inside
  %likely = call i1 @llvm.expect.i1(i1 %ok, i1 true)
  br i1 %likely, label %fast, label %slow

fast:
  %index = zext i32 %addr to i64
  %at = getelementptr inbounds i8, ptr %base, i64 %index
  br label %access

slow:
  %checked = call ptr @sa2_guest_read_slow(i32 %addr, i32 4)
  br label %access

access:
  %p = phi ptr [ %at, %fast ], [ %checked, %slow ]
  %v = load i32, ptr %p, align 1
  ret i32 %v
}

declare dso_local ptr @sa2_guest_read_slow(i32, i32) cold nounwind

@sa2_guest_fast_base = external dso_local global ptr, align 8

declare i1 @llvm.expect.i1(i1, i1)
"""


def fast_counts(fast=0, kept=0) -> dict[str, int]:
    return {"fast": fast, "fast_kept": kept}


def test_a_localized_function_reads_through_the_fast_path_as_written(tmp_path):
    localized = module_out(BASIC_OUT)
    out = module_out(BASIC_FAST_OUT) + "\n" + FAST_TAIL_READ_32
    assert SL.fast(localized) == (out, fast_counts(fast=1))
    assert SL.fast_problems(out) == {}
    compile_ir(out, tmp_path, "basic_fast")                    # valid IR, as the fold compiles


def test_the_fast_path_bounds_are_the_runtime_s():
    """GUEST_NULL_GUARD and GUEST_END are SA2_GUEST_NULL_GUARD and SA2_GUEST_END as
    runtime/guest_memory.h defines them, and `sa2_gp` still tests exactly those two."""
    header = (ROOT / "runtime" / "guest_memory.h").read_text()
    defined = {m.group(1): int(m.group(2), 16) for m in re.finditer(
        r"^#define (SA2_GUEST_NULL_GUARD|SA2_GUEST_END)\s+(0x[0-9A-Fa-f]+)u\b", header, re.M)}
    assert defined == {"SA2_GUEST_NULL_GUARD": SL.GUEST_NULL_GUARD, "SA2_GUEST_END": SL.GUEST_END}
    gp = header[header.index("static inline uint8_t *sa2_gp("):]
    gp = gp[:gp.index("\n}\n")]
    assert re.findall(r"if \((.*)\) return NULL;", gp) == [
        "addr < SA2_GUEST_NULL_GUARD", "len > SA2_GUEST_END || addr > SA2_GUEST_END - len"]
    assert "return sa2_guest.base + addr;" in gp


FAST_DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include "guest_memory.h"

sa2_guest_t sa2_guest;
uint8_t *sa2_guest_fast_base;
static unsigned slow_calls;
static uint32_t slow_addr, slow_len;
static uint8_t scratch[16];
static uint8_t *slow(uint32_t a, uint32_t n, int fill) {
    slow_calls++; slow_addr = a; slow_len = n;
    memset(scratch, fill, sizeof scratch);
    return scratch;
}
uint8_t *sa2_guest_read_slow(uint32_t a, uint32_t n) { return slow(a, n, 0x5C); }
uint8_t *sa2_guest_write_slow(uint32_t a, uint32_t n) { return slow(a, n, 0); }
uint64_t wread(int op, uint32_t a);
void *wwrite(int op, uint32_t a, uint64_t v, void *m);

static const unsigned SIZE[6] = {1, 2, 4, 8, 4, 8};
static const uint32_t G = SA2_GUEST_NULL_GUARD, E = SA2_GUEST_END;

int main(void) {
    sa2_guest.base = mmap(NULL, E, PROT_READ | PROT_WRITE,
                          MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (sa2_guest.base == MAP_FAILED) return 2;
    const uint32_t addrs[] = {0, 1, 7, G - 9, G - 8, G - 5, G - 4, G - 2, G - 1, G, G + 1,
                              0x00401000u, 0x00401003u, E - 16, E - 9, E - 8, E - 7, E - 5,
                              E - 4, E - 3, E - 2, E - 1, E, E + 1, 0x7FFFFFFFu, 0x80000000u,
                              0xFFFFF000u, 0xFFFFFFF8u, 0xFFFFFFFCu, 0xFFFFFFFFu};
    unsigned checked = 0, bad = 0;
    for (int mapped = 0; mapped < 2; mapped++) {
        sa2_guest_fast_base = mapped ? sa2_guest.base : NULL;
        for (int op = 0; op < 12; op++) {
            const unsigned n = SIZE[op % 6];
            for (unsigned i = 0; i < sizeof addrs / sizeof *addrs; i++) {
                const uint32_t a = addrs[i];
                const int fast = mapped && sa2_gp(a, n) != NULL;
                uint8_t want[8], got[8];
                slow_calls = 0;
                if (op < 6) {
                    if (sa2_gp(a, n))
                        for (unsigned k = 0; k < n; k++) sa2_guest.base[a + k] = (uint8_t)(a * 7 + k + 1);
                    uint64_t v = wread(op, a);
                    memcpy(got, &v, n);
                    if (fast) memcpy(want, sa2_guest.base + a, n); else memset(want, 0x5C, n);
                } else {
                    uint64_t v = 0x8877665544332211ull ^ a;
                    if (sa2_gp(a, n)) memset(sa2_guest.base + a, 0xEE, n);
                    if (wwrite(op, a, v, scratch + 8) != scratch + 8) bad++;   /* the token */
                    memcpy(want, &v, n);
                    memcpy(got, fast ? sa2_guest.base + a : scratch, n);
                    if (!fast && sa2_gp(a, n))     /* the guest bytes stay as they were */
                        for (unsigned k = 0; k < n; k++) bad += sa2_guest.base[a + k] != 0xEE;
                }
                const int slow_ok = fast ? slow_calls == 0
                                         : slow_calls == 1 && slow_addr == a && slow_len == n;
                if (!slow_ok || memcmp(want, got, n)) {
                    printf("FAIL mapped=%d op=%d a=%#010x fast=%d slow_calls=%u\n",
                           mapped, op, a, fast, slow_calls);
                    bad++;
                }
                checked++;
            }
        }
    }
    printf("checked %u bad %u\n", checked, bad);
    return bad != 0;
}
"""


def _fast_wrappers() -> str:
    """Every helper as the pass splices it, behind `wread(op, a)` and `wwrite(op, a, v, m)`,
    which load the base as a fast-path function's entry block does; op is the index into
    FAST_ACCESS (reads first, then writes, in the same widths), and `wwrite` returns what the
    helper returns for the Memory token `m`. FAST_DRIVER and harness/guest_fast_probe.c
    call them."""
    names = list(SL.FAST_ACCESS)
    parts = [SL._fast_helper(c) for c in names]
    for kind, sig in (("read", "i64 @wread(i32 %op, i32 %a)"),
                      ("write", "ptr @wwrite(i32 %op, i32 %a, i64 %v, ptr %mem)")):
        ops = [(i, c) for i, c in enumerate(names) if SL.FAST_ACCESS[c][0] == kind]
        body = [f"define dso_local {sig} {{", "entry:", SL._BASE_LOAD,
                "  switch i32 %op, label %none [" +
                " ".join(f"i32 {i}, label %op{i}" for i, _ in ops) + "]"]
        for i, c in ops:
            _, ty, size = SL.FAST_ACCESS[c]
            name = SL._helper_name(c)
            body.append(f"op{i}:")
            if kind == "read":
                body.append(f"  %r{i} = call {ty} @{name}(ptr %sl.base, i32 %a)")
                if ty in ("float", "double"):
                    body.append(f"  %b{i} = bitcast {ty} %r{i} to i{size * 8}")
                    ty, val = f"i{size * 8}", f"%b{i}"
                else:
                    val = f"%r{i}"
                body.append(f"  %z{i} = zext {ty} {val} to i64" if ty != "i64"
                            else f"  %z{i} = or i64 {val}, 0")
                body.append(f"  ret i64 %z{i}")
            else:
                ity = f"i{size * 8}"
                body.append(f"  %t{i} = trunc i64 %v to {ity}" if ity != "i64"
                            else f"  %t{i} = or i64 %v, 0")
                val = f"%t{i}"
                if ty in ("float", "double"):
                    body.append(f"  %f{i} = bitcast {ity} %t{i} to {ty}")
                    val = f"%f{i}"
                body.append(f"  %w{i} = call ptr @{name}(ptr %sl.base, ptr %mem, i32 %a, {ty} {val})")
                body.append(f"  ret ptr %w{i}")
        body += ["none:", "  unreachable", "}"]
        parts.append("\n".join(body))
    parts += [f"declare dso_local ptr @{SL.SLOW_HALVES[k]}(i32, i32) cold nounwind"
              for k in ("read", "write")]
    parts += [f"@{SL.FAST_BASE} = external dso_local global ptr, align 8",
              "declare i1 @llvm.expect.i1(i1, i1)"]
    return "\n\n".join(parts) + "\n"


def test_the_helpers_take_the_fast_path_exactly_where_sa2_gp_accepts(tmp_path):
    """Every helper, compiled with the fold's command, against `sa2_gp` from
    runtime/guest_memory.h at each edge of the guest space, the null guard's and the end's,
    and past 2^31: an access the runtime accepts goes through `base + address` and calls no
    slow half; every other, and every access while the base is NULL, calls its slow half
    once with its address and width and goes through the pointer it returns. Reads return
    the bytes there; writes put the value there and nowhere else."""
    command = fold_command(tmp_path / "in.ll", tmp_path / "out.ll")
    zig, flags = command[0], command[1:command.index("-S")]
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(tmp_path / "zig-local"),
               ZIG_GLOBAL_CACHE_DIR=str(GEN / "zig-cache-tests"))
    (tmp_path / "helpers.ll").write_text(_fast_wrappers())
    (tmp_path / "driver.c").write_text(FAST_DRIVER)
    for src, obj in (("helpers.ll", "helpers.o"), ("driver.c", "driver.o")):
        done = subprocess.run([zig, *flags, "-I", str(ROOT / "runtime"), "-c",
                               str(tmp_path / src), "-o", str(tmp_path / obj)],
                              capture_output=True, text=True, cwd=ROOT, env=env)
        assert done.returncode == 0, done.stderr[-2000:]
    exe = tmp_path / "driver"
    done = subprocess.run([zig, *flags[:3], str(tmp_path / "driver.o"),
                           str(tmp_path / "helpers.o"), "-o", str(exe)],
                          capture_output=True, text=True, cwd=ROOT, env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.stdout.splitlines()[-1] == "checked 720 bad 0", run.stdout[-3000:]


# The runtime's side of the fast path (runtime/guest_memory.h, "the inlined fast path"), held
# by harness/guest_fast_probe.c over the runtime itself.
GUEST_FAST_PROBE = ROOT / "harness" / "guest_fast_probe.c"
# The runtime files remill_runtime.cpp's memory intrinsics and slow halves link against.
PROBE_RUNTIME = ("guest_memory.c", "sa2_trap.c", "function_table_impl.c", "remill_runtime.cpp")


def small_pe(image_base: int = 0x00400000) -> bytes:
    """A PE32 the runtime maps (runtime/guest_memory.c `pe_parse` reads only these fields):
    its headers, a code section, and a data section whose virtual size runs two pages past
    its file-backed bytes, so the guest holds every kind of page the poison tier tells apart.
    Every byte is the test's own; with another `image_base` the runtime refuses it."""
    f = bytearray(0x800)
    f[0:2] = b"MZ"
    struct.pack_into("<I", f, 0x3C, 0x40)
    f[0x40:0x44] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", f, 0x44, 0x014C, 2, 0, 0, 0, 0xE0, 0x0102)
    opt = 0x58                                       # PE32 optional header, 0xE0 bytes
    struct.pack_into("<H", f, opt, 0x010B)
    struct.pack_into("<III", f, opt + 28, image_base, 0x1000, 0x200)
    struct.pack_into("<II", f, opt + 56, 0x5000, 0x400)       # SizeOfImage, SizeOfHeaders
    for i, (name, va, size, raw, at, flags) in enumerate((
            (b".text", 0x1000, 0x1000, 0x200, 0x400, 0x60000020),
            (b".data", 0x2000, 0x3000, 0x200, 0x600, 0xC0000040))):
        struct.pack_into("<8sIIIIIIHHI", f, opt + 0xE0 + 40 * i, name, size, va, raw, at,
                         0, 0, 0, 0, flags)
    for k in range(0x400, 0x800):
        f[k] = (k * 7 + 1) & 0xFF
    return bytes(f)


def _guest_fast_probe(monkeypatch, tmp_path: Path) -> Path:
    """harness/guest_fast_probe.c linked over PROBE_RUNTIME, each compiled by the link's own
    command, and the helpers as `_fast_wrappers` writes them, compiled by the fold's."""
    commands = _link_compile_commands(monkeypatch, tmp_path)
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(tmp_path / "zig-local"),
               ZIG_GLOBAL_CACHE_DIR=str(GEN / "zig-cache-tests"))
    c_flags = _link_command_for(commands, ROOT / "runtime" / PROBE_RUNTIME[0])[:-3]
    assert Path(c_flags[0]).is_file(), f"the link's compiler is not present: {c_flags[0]}"
    jobs = [(_link_command_for(commands, ROOT / "runtime" / name)[:-1], f"{Path(name).stem}.o")
            for name in PROBE_RUNTIME]
    jobs.append((c_flags + [str(GUEST_FAST_PROBE), "-o"], "probe.o"))
    fold = fold_command(tmp_path / "in.ll", tmp_path / "out.ll")
    (tmp_path / "helpers.ll").write_text(_fast_wrappers())
    jobs.append(([fold[0], *fold[1:fold.index("-S")], "-c", str(tmp_path / "helpers.ll"), "-o"],
                 "helpers.o"))
    objects = []
    for command, obj in jobs:
        objects.append(str(tmp_path / obj))
        done = subprocess.run(command + [objects[-1]], capture_output=True, text=True, cwd=ROOT,
                              env=env)
        assert done.returncode == 0, f"{obj}: {done.stderr[-2000:]}"
    exe = tmp_path / "guest_fast_probe"
    done = subprocess.run([c_flags[0], "c++", *c_flags[2:4], *objects, "-o", str(exe)],
                          capture_output=True, text=True, cwd=ROOT, env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    return exe


GUEST_FAST_LIFECYCLE = [
    "lifecycle before init: NULL",
    "lifecycle poison 0, before init: NULL",
    "lifecycle after a failed init (no file): NULL",
    "lifecycle after a failed init (refused image): NULL",
    "lifecycle round 0, poison 0, after init: the mapping",
    "lifecycle round 0, after shutdown: NULL",
    "lifecycle round 1, poison 2, after init: NULL",
    "lifecycle round 1, after shutdown: NULL",
    "lifecycle round 2, poison 1, after init: NULL",
    "lifecycle round 2, after shutdown: NULL",
    "lifecycle round 3, poison 0, after init: the mapping",
    "lifecycle round 3, after shutdown: NULL",
    "lifecycle mapped, poison 0: the mapping",
    "lifecycle mapped, poison set to 2: NULL",
    "lifecycle mapped, poison set to 1: NULL",
    "lifecycle mapped, poison set to 0: the mapping",
    "lifecycle after shutdown: NULL",
    "lifecycle after a second shutdown: NULL",
    "lifecycle problems: 0",
]

# A few of the probe's lines, as the runtime gives them: the PE's code bytes read inline, a
# read of the .bss tail trapped by the poison tier, a write straddling the end of the guest
# space refused, an unwritten page's read counted, a signalling NaN's bits stored unquieted
# across a page the store marks.
GUEST_FAST_LINES = [
    "round=0 poison=0 r32 a=0x00401000 status=0 exit_pc=0000000000 note=[] value=[160f0801] "
    "after=[000001080f161d242b323940] poison_reads=0 poison_first=0000000000 pages=11",
    "round=2 poison=1 r32 a=0x00403000 status=2 exit_pc=0x00403000 note=[read of 4 byte(s) at "
    "guest 0x00403000 (region .data) from a page nothing has written: not the PE's "
    "file-initial bytes and not any plan's poke] value=[] after=[000000000000000000000000] "
    "poison_reads=1 poison_first=0x00403000 pages=00",
    "round=0 poison=0 w32 a=0x1ffffffd status=2 exit_pc=0x1ffffffd note=[write of 4 byte(s) "
    "at guest 0x1ffffffd is outside the guest space (region heap)] value=[] "
    "after=[3434125aa5..............] poison_reads=0 poison_first=0000000000 pages=0-",
    "round=1 poison=2 r8 a=0x00001000 status=0 exit_pc=0000000000 note=[] value=[00] "
    "after=[....00000000000000000000] poison_reads=1 poison_first=0x00001000 pages=00",
    "round=1 poison=2 wf32 a=0x00402ffe status=0 exit_pc=0000000000 note=[] "
    "value=[token kept] after=[00000100a07fab8967452301] poison_reads=0 "
    "poison_first=0000000000 pages=11",
]


def test_the_runtime_keeps_the_fast_path_s_side(monkeypatch, tmp_path):
    """harness/guest_fast_probe.c over the runtime as the link compiles it, on a small PE32
    written here: every one- to eight-byte access at the edges of the guest space and in
    every kind of page, made through the intrinsics and through the pass's helpers, in four
    rounds under poison modes 0, 2, 1 and 0, gives the same trap status, exit_pc and note,
    value, guest bytes, poison counters and page marks (the outputs are byte-identical); and
    the base is the mapping while guest memory is mapped with the poison tier off and NULL
    at every other point of its lifecycle. A check the runtime's checked path makes and the
    helpers do not (a permission, say) is a line that differs; a base left set under a
    poison mode, or never set, is a lifecycle line that differs."""
    exe = _guest_fast_probe(monkeypatch, tmp_path)
    (tmp_path / "small.exe").write_bytes(small_pe())
    (tmp_path / "refused.exe").write_bytes(small_pe(image_base=0x10000000))
    out = {}
    for side in ("intrinsic", "fast"):
        run = subprocess.run([str(exe), str(tmp_path / "small.exe"), str(tmp_path / "refused.exe"),
                              side], capture_output=True, text=True, timeout=300)
        out[side] = run.stdout.splitlines()
        assert run.returncode == 0, (side, run.stdout[-3000:], run.stderr[-2000:])
    differ = [(a, b) for a, b in zip(out["intrinsic"], out["fast"]) if a != b]
    assert not differ and len(out["intrinsic"]) == len(out["fast"]), differ[:5]
    lines = out["fast"]
    assert [line for line in lines if line.startswith("lifecycle")] == GUEST_FAST_LIFECYCLE
    cases = [line for line in lines if line.startswith("round=")]
    assert len(cases) == 4 * 12 * 38
    tally = {}
    for line in cases:
        key = (line.split()[0], line.split()[4])
        tally[key] = tally.get(key, 0) + 1
    assert tally == {("round=0", "status=0"): 238, ("round=0", "status=2"): 218,
                     ("round=1", "status=0"): 238, ("round=1", "status=2"): 218,
                     ("round=2", "status=0"): 157, ("round=2", "status=2"): 299,
                     ("round=3", "status=0"): 238, ("round=3", "status=2"): 218}
    assert sum("poison_reads=1 " in line for line in cases if line.startswith("round=1 ")) == 81
    assert sum("nothing has written" in line for line in cases if line.startswith("round=2 ")) == 81
    assert not [line for line in cases if "token LOST" in line]
    for line in GUEST_FAST_LINES:
        assert line in cases, line
    # A fresh init under the same mode gives the same guest: round 3 is round 0.
    assert [line[8:] for line in cases if line.startswith("round=3 ")] == \
        [line[8:] for line in cases if line.startswith("round=0 ")]


def _guest_ops_in_order(ir: str, name: str) -> list[str]:
    """The guest loads and stores, the slow-half calls and the bracketed lifted call of one
    function of -O2 IR, in text order."""
    body = ir[ir.index(f"@{name}("):]
    body = body[:body.index("\n}\n")]
    return re.findall(r"(load i32, ptr %[-\w.$]+|store i32 [-\w%.$]+, ptr %[-\w.$]+"
                      r"|@sa2_guest_(?:read|write)_slow|@sub_402000)\b", body)


def test_a_fast_path_function_is_ordered_by_its_loads_and_stores(tmp_path):
    """ORDER_IN through the fast path, compiled at -O2: no intrinsic is left, and the guest
    read stays before the write to the same address -- the order the local pass's ordering
    exists to keep, whose control (the intrinsics left readnone, the read sunk past the
    write) is test_a_guest_read_stays_before_a_later_write_once_state_is_local; here nothing
    is claimed by an attribute, the load and the store alias and LLVM keeps them in order.
    Then a store before a bracketed call stays before it, and the read of the same address
    after the call is a load, not the stored 7."""
    out, c = SL.fast(SL.localize(module(ORDER_IN), k=0)[0])
    assert c == fast_counts(fast=1) and SL.fast_problems(out) == {}
    assert not re.search(r"@__remill_(read|write)_memory_\w+\(ptr noundef %", out[
        out.index("define ptr @sub_40b000("):out.index("define internal")])
    ops = _guest_ops_in_order(compile_ir(out, tmp_path, "order_fast"), "sub_40b000")
    seven = [i for i, o in enumerate(ops) if o.startswith("store i32 7, ptr %")]
    assert seven and seven[0] > 0 and ops[0].startswith("load i32, ptr %"), ops
    across = ORDER_IN.replace(
        "  %c = fcmp oeq x86_fp80 %f, 0xK00000000000000000000\n",
        "  %mc = call ptr @sub_402000(ptr %state, i32 0, ptr %m2)\n"
        "  %w = call i32 @__remill_read_memory_32(ptr noundef %mc, i32 noundef %program_counter) #7\n"
        "  store i32 %w, ptr %EAX, align 4\n"
        "  %c = fcmp oeq x86_fp80 %f, 0xK00000000000000000000\n")
    out = SL.fast(SL.localize(module(across), k=0)[0])[0]
    ops = _guest_ops_in_order(compile_ir(out, tmp_path, "across"), "sub_40b000")
    call = ops.index("@sub_402000")
    assert any(o.startswith("store i32 7, ptr %") for o in ops[:call]), ops
    assert any(o.startswith("load i32, ptr %") for o in ops[call:]), ops


def test_a_guest_read_nothing_uses_keeps_its_bounds_check(tmp_path):
    """ORDER_IN with a second read of the address after the write, whose value replaces the
    first read's in EAX: -O2 deletes the first read's load, and its slow half stays, so an
    out-of-range address still faults there. (A readnone or argmem-read intrinsic whose
    value nothing uses can be deleted with its fault; the risk register's trap entry.)"""
    reread = ORDER_IN.replace(
        "  %c = fcmp oeq x86_fp80 %f, 0xK00000000000000000000\n",
        "  %w = call i32 @__remill_read_memory_32(ptr noundef %m2, i32 noundef %program_counter) #7\n"
        "  store i32 %w, ptr %EAX, align 4\n"
        "  %c = fcmp oeq x86_fp80 %f, 0xK00000000000000000000\n")
    out = SL.fast(SL.localize(module(reread), k=0)[0])[0]
    ops = _guest_ops_in_order(compile_ir(out, tmp_path, "reread"), "sub_40b000")
    write = next(i for i, o in enumerate(ops) if o == "@sa2_guest_write_slow")
    assert "@sa2_guest_read_slow" in ops[:write], ops
    assert not [o for o in ops[:write] if o.startswith("load i32, ptr %")], ops


# A localized function for each reason the fast path leaves one with its intrinsic calls; the
# last case has the pass's own output check name the function.
F80_READ = ("  %t = alloca x86_fp80, align 16\n"
            "  %r = call ptr @__remill_read_memory_f80(ptr noundef %memory, i32 noundef %sp, "
            "ptr noundef nonnull align 16 dereferenceable(16) %t) #90002\n")
SP_LOAD = "  %sp = load i32, ptr %ESP, align 8\n"
# The guest-memory and I/O-port intrinsics the pass does not replace, as a localized function
# would call them (remill declares them; the runtime makes each trap).
UNREPLACED = {
    "f128-read": ("  %q = call fp128 @__remill_read_memory_f128(ptr noundef %memory, i32 noundef %sp)\n",
                  "declare fp128 @__remill_read_memory_f128(ptr noundef, i32 noundef)\n"),
    "f128-write": ("  %q = call ptr @__remill_write_memory_f128(ptr noundef %memory, i32 noundef %sp, "
                   "fp128 0xL0)\n",
                   "declare ptr @__remill_write_memory_f128(ptr noundef, i32 noundef, fp128)\n"),
    "port-read": ("  %q = call i8 @__remill_read_io_port_8(ptr noundef %memory, i32 noundef %sp)\n",
                  "declare i8 @__remill_read_io_port_8(ptr noundef, i32 noundef)\n"),
    "port-write": ("  %q = call ptr @__remill_write_io_port_32(ptr noundef %memory, i32 noundef %sp, "
                   "i32 7)\n",
                   "declare ptr @__remill_write_io_port_32(ptr noundef, i32 noundef, i32)\n"),
}


def _with_unreplaced(kind: str) -> str:
    line, declare = UNREPLACED[kind]
    return module_out(BASIC_OUT.replace(SP_LOAD, SP_LOAD + line)) + declare


FAST_KEPT_CASES = {
    "a ten-byte guest access": lambda mp: module_out(BASIC_OUT.replace(SP_LOAD, SP_LOAD + F80_READ))
    + "declare ptr @__remill_read_memory_f80(ptr noundef, i32 noundef, ptr noundef)\n",
    "a guest-memory call the fast path cannot read": lambda mp: module_out(BASIC_OUT.replace(
        "i32 noundef %sp) #90002", "i32 noundef ptrtoint (ptr @g to i32)) #90002"))
    + "@g = global i32 0\n",
    "a guest-memory intrinsic used other than as the callee of a call": lambda mp: module_out(
        BASIC_OUT.replace(SP_LOAD, SP_LOAD + "  store ptr @__remill_read_memory_32, ptr %STATE, align 8\n")),
    "no State alloca where the local pass puts it": lambda mp: module_out(BASIC_OUT.replace(
        "  %state = alloca %struct.State, align 16\n", "  %state = alloca %struct.State, align 8\n")),
    "a name the fast path adds is already in use": lambda mp: module_out(BASIC_OUT)
    + "@sa2_guest_fast_base = external global ptr\n",
    "a lifted function of this module keeps its intrinsic calls": lambda mp: module_out(
        BASIC_OUT, rule_function(3, 1)),
    "a guest-memory or I/O-port intrinsic the fast path does not replace":
        lambda mp: _with_unreplaced("f128-read"),
    "the fast path's own check named a problem in this module's output": lambda mp: (
        mp.setattr(SL, "fast_problems", lambda out: {"sub_401000": ["a problem the check names"]}),
        module_out(BASIC_OUT))[1],
}


@pytest.mark.parametrize("why", sorted(FAST_KEPT_CASES))
def test_a_localized_function_the_fast_path_cannot_take_comes_back_unchanged(why, monkeypatch):
    text = FAST_KEPT_CASES[why](monkeypatch)
    assert SL.fast(text) == (text, fast_counts(kept=1))
    reasons = SL.fast_reasons(text)
    assert list(reasons) == ["sub_401000"] and reasons["sub_401000"].startswith(why), reasons


@pytest.mark.parametrize("kind", sorted(UNREPLACED))
def test_an_intrinsic_the_fast_path_does_not_replace_keeps_its_function(kind):
    """remill's sixteen-byte guest accesses and its port accesses are declared `readnone`
    and ordered only by their Memory token: beside real loads and stores nothing would order
    them, so a function that calls one keeps all its intrinsic calls, and the output check
    names a fast-path function that holds one."""
    text = _with_unreplaced(kind)
    assert SL.fast(text) == (text, fast_counts(kept=1))
    assert SL.fast_reasons(text)["sub_401000"].startswith(
        "a guest-memory or I/O-port intrinsic the fast path does not replace: %q = call ")
    line = UNREPLACED[kind][0]
    out = module_out(BASIC_FAST_OUT) + "\n" + FAST_TAIL_READ_32
    assert SL.fast_problems(out) == {}
    assert list(SL.fast_problems(out.replace(SP_LOAD, SP_LOAD + line, 1))) == ["sub_401000"]


def test_a_kept_localized_function_keeps_every_function_of_its_module():
    """The module rule's other half: a localized function the pass keeps (here for a
    ten-byte access) keeps every other localized function of its module with its intrinsic
    calls too, so -O2 cannot inline a function whose accesses are token-ordered calls into
    one whose accesses are loads and stores. The text comes back as it went in and both
    are counted; without the ten-byte access both go through the fast path."""
    other = BASIC_OUT.replace("sub_401000", "sub_409000")
    plain = module_out(BASIC_OUT, other)
    assert SL.fast(plain)[1] == fast_counts(fast=2)
    text = module_out(BASIC_OUT.replace(SP_LOAD, SP_LOAD + F80_READ), other) \
        + "declare ptr @__remill_read_memory_f80(ptr noundef, i32 noundef, ptr noundef)\n"
    assert SL.fast(text) == (text, fast_counts(kept=2))
    reasons = SL.fast_reasons(text)
    assert sorted(reasons) == ["sub_401000", "sub_409000"]
    assert reasons["sub_401000"].startswith("a ten-byte guest access: ")
    assert reasons["sub_409000"] == \
        "a lifted function of this module keeps its intrinsic calls: sub_401000"


def test_an_output_the_check_names_sends_every_function_of_its_module_back(monkeypatch):
    """When `fast_problems` names one function of the pass's output, the module comes back as
    it went in and every function the pass had rewritten is counted in `fast_kept`, with the
    check's reason, not only the one it named."""
    text = module_out(BASIC_OUT, BASIC_OUT.replace("sub_401000", "sub_409000"))
    assert SL.fast(text)[1] == fast_counts(fast=2)
    monkeypatch.setattr(SL, "fast_problems",
                        lambda out: {"sub_409000": ["a problem"]} if "%sl.base" in out else {})
    assert SL.fast(text) == (text, fast_counts(kept=2))
    why = "the fast path's own check named a problem in this module's output: sub_409000: a problem"
    assert SL.fast_reasons(text) == {"sub_401000": why, "sub_409000": why}


def test_every_fast_kept_kind_has_a_case():
    assert sorted(FAST_KEPT_CASES) == sorted(SL.FAST_KEPT)
    source = (ROOT / "tools" / "s2" / "corpus_run" / "state_local.py").read_text()
    fast_source = source[source.index("def _fast_call("):source.index("def fast_problems(")]
    assert "FAST_KEPT[" in fast_source
    used = {int(i) for i in re.findall(r"FAST_KEPT\[(\d+)\]", fast_source)}
    assert used == set(range(len(SL.FAST_KEPT)))


@pytest.mark.parametrize("broken, named", [
    ("  %v = call i32 @__sa2_fast_read_32(ptr %sl.base, i32 %sp)\n",
     "  %v = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef %sp) #90002\n"),
    ("  %v = call i32 @__sa2_fast_read_32(ptr %sl.base, i32 %sp)\n",
     "  %v = call i32 @__sa2_fast_read_32(ptr null, i32 %sp)\n"),
    ("  %sl.base = load ptr, ptr @sa2_guest_fast_base, align 8\n",
     "  %sl.base = load ptr, ptr @sa2_guest_fast_base, align 8\n"
     "  %sl.base2 = load ptr, ptr @sa2_guest_fast_base, align 8\n"
     "  %sl.base = load ptr, ptr @sa2_guest_fast_base, align 8\n"),
    ("  %v = call i32 @__sa2_fast_read_32(ptr %sl.base, i32 %sp)\n",
     "  %v = call i32 @__sa2_fast_read_32(ptr %sl.base, i32 %sp)\n"
     "  %p = call i8 @__remill_read_io_port_8(ptr noundef %memory, i32 noundef %sp)\n"),
], ids=["intrinsic-left", "wrong-base", "base-twice", "port-left"])
def test_the_fast_check_names_an_output_it_cannot_vouch_for(broken, named):
    out = module_out(BASIC_FAST_OUT) + "\n" + FAST_TAIL_READ_32
    assert SL.fast_problems(out) == {}
    bad = out.replace(broken, named, 1)
    assert bad != out and list(SL.fast_problems(bad)) == ["sub_401000"]
    assert SL.fast_problems(bad.replace("define internal i32 @__sa2_fast_read_32(",
                                        "define internal i32 @__sa2_fast_read_x(")) != {}


def test_the_tie_names_a_slow_half_that_reaches_further_or_is_gone(monkeypatch, tmp_path):
    """A runtime copy whose read slow half sets the rounding mode and whose write slow half
    is renamed: the tie names both, and the runtime as it is passes it (the safe-callee test
    above runs the same `runtime_problems`)."""
    src = RUNTIME.read_text()
    old = "uint8_t *sa2_guest_read_slow(gaddr a, uint32_t len) {\n    return checked_read(a, len);"
    assert src.count(old) == 1, f"{RUNTIME} no longer defines the read slow half as this test edits it"
    src = src.replace(old, old.replace("return checked_read", "fesetround(0);\n    return checked_read"))
    src, n = re.subn(r"\bsa2_guest_write_slow\(gaddr", "sa2_guest_write_slow_gone(gaddr", src)
    assert n == 1
    copy = tmp_path / "remill_runtime.cpp"
    copy.write_text(src)
    command = _link_runtime_command(monkeypatch, tmp_path)
    problems = SL.runtime_problems(_runtime_ir(command, copy, tmp_path / "rt.ll"))
    assert sorted(p.split(":")[0] for p in problems) == ["sa2_guest_read_slow",
                                                         "sa2_guest_write_slow"], problems


def test_fast_twice_is_fast_once_and_the_pipeline_leaves_its_output_alone():
    once, c = SL.fast(module_out(BASIC_OUT))
    assert c == fast_counts(fast=1)
    assert SL.fast(once) == (once, fast_counts())
    assert SR.rewrite(once)[0] == once


# ----------------------------------------------------- idempotence and determinism

def test_localizing_twice_is_localizing_once():
    text = module(BASIC_IN, FENV_IN, rule_function(3, 1))
    once, c = SL.localize(text, k=1)
    assert c == counts(local=2, kept=1)
    assert SL.localize(once, k=1) == (once, counts(kept=1))
    assert SR.rewrite(once)[0] == once


def test_the_output_does_not_depend_on_the_interpreter():
    """A real module and the fixtures through three interpreters with different hash seeds,
    localized and then through the fast path."""
    texts = [real(REAL_MODULES[0]), module(BASIC_IN, FENV_IN)]
    code = ("import hashlib, sys; sys.path.insert(0, sys.argv[1]); import state_local as S; "
            "t = sys.stdin.read(); l = S.localize(t, int(sys.argv[2])); "
            "print(hashlib.sha256(repr((l, S.fast(l[0]))).encode()).hexdigest())")
    for text, k in ((texts[0], 32), (texts[1], 0)):
        digests = set()
        for seed in ("0", "1", "4242"):
            done = subprocess.run([sys.executable, "-c", code,
                                   str(ROOT / "tools" / "s2" / "corpus_run"), str(k)],
                                  input=text, capture_output=True, text=True, check=True,
                                  env=dict(os.environ, PYTHONHASHSEED=seed))
            digests.add(done.stdout.strip())
        assert len(digests) == 1
