"""The speed rewrite of folded lifted IR: each pass on literal IR, what it must leave alone,
and the runtime definitions it depends on.

`tools/s2/corpus_run/speed_rewrite.py` rewrites every folded module the build compiles
(`internalize.rewrite` applies it), so a defect here is in every lifted object at once.
Each pass is held to exact expected text; the cases it must not touch (a memory
intrinsic carrying `readnone`, a `memcpy` carrying another attribute group, an intrinsic
the runtime does not define as an identity or a zero, a helper define carrying an
attribute) come back byte for byte; a second application changes nothing; and the output
does not depend on the interpreter's hash seed. Any pass can be left out (`passes`, the
risk register's experiment with the spill pass off), and one left out rewrites nothing.

The "intrin" pass is exact only while `runtime/remill_runtime.cpp` defines each listed
intrinsic as returning its first argument or zero. The runtime is compiled to LLVM IR by
the link's own command for it, read from tools/s4/link_s4.py `phase_compile` rather than
typed here, so a change to the link's compiler or flags changes what this compiles; and
every listed definition is held to that single `ret`. A copy of the runtime with four
definitions changed shows the check failing on each. The check fails, never skips, when
the compiler or remill's headers are missing: it is the only one that ties the "intrin"
replacements to the runtime, and a skip passes the "script tests" gate.

The spill pass rewrites only remill's two literal call forms, so any other `memcpy` or
`memset` keeps `nobuiltin`; and the fixture corpus's build folds again a cached fold
that older fold code made (tools/s2/corpus_run/build.py `fold_one`).

The fourth pass, "local" (State localization, tools/s2/corpus_run/state_local.py), and the
fifth, "fast" (the guest-memory fast path, in the same file), have their own tests in
test_state_local.py. Here they are two of the passes: they run by default, after the other
three and in that order, and can be left out like them. The modules below define no State
type, so "local" keeps each lifted function it sees as it is and counts it in
`local_kept`, and "fast" finds no localized function to rewrite.
"""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
# Appended, not inserted first: the directory also holds build.py, run.py and receipt.py,
# which must not shadow another tool's module of the same name in this pytest process.
sys.path.append(str(ROOT / "tools" / "s2" / "corpus_run"))
import internalize  # noqa: E402
import speed_rewrite as SR  # noqa: E402

RUNTIME = ROOT / "runtime" / "remill_runtime.cpp"



def counts(spill=0, intrin=0, pcround=0, local=0, kept=0, refused=0, fast=0,
           fast_kept=0) -> dict[str, int]:
    """`rewrite`'s counts: one per pass, the lifted functions "local" kept or refused, and
    the localized functions "fast" left with their intrinsic calls."""
    return {"spill": spill, "intrin": intrin, "pcround": pcround,
            "local": local, "local_kept": kept, "local_refused": refused,
            "fast": fast, "fast_kept": fast_kept}


NONE = counts()

SPILL_IN = """\
define ptr @sub_401000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
  %ldu.i = alloca x86_fp80, align 16
  %call.i.i16 = call ptr @memset(ptr noundef nonnull %ldu.i, i32 noundef 0, i64 noundef 16) #10
  %call5.i = call ptr @memcpy(ptr noundef nonnull %d, ptr noundef nonnull %ldu.i, i64 noundef 10) #10
  call ptr @memcpy(ptr noundef nonnull %e, ptr noundef nonnull %ldu.i, i64 noundef 10) #10
  ret ptr %memory
}

declare dso_local ptr @memset(ptr noundef, i32 noundef, i64 noundef) local_unnamed_addr #3
declare dso_local ptr @memcpy(ptr noundef, ptr noundef, i64 noundef) local_unnamed_addr #3

attributes #3 = { nounwind "frame-pointer"="none" "no-builtins" }
attributes #10 = { nobuiltin nounwind "no-builtins" }
"""

SPILL_OUT = """\
define ptr @sub_401000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
  %ldu.i = alloca x86_fp80, align 16
  %call.i.i16 = call ptr @memset(ptr noundef nonnull %ldu.i, i32 noundef 0, i64 noundef 16) #90001
  %call5.i = call ptr @memcpy(ptr noundef nonnull %d, ptr noundef nonnull %ldu.i, i64 noundef 10) #90001
  call ptr @memcpy(ptr noundef nonnull %e, ptr noundef nonnull %ldu.i, i64 noundef 10) #90001
  ret ptr %memory
}

declare dso_local ptr @memset(ptr noundef, i32 noundef, i64 noundef) local_unnamed_addr #3
declare dso_local ptr @memcpy(ptr noundef, ptr noundef, i64 noundef) local_unnamed_addr #3

attributes #3 = { nounwind "frame-pointer"="none" "no-builtins" }
attributes #10 = { nobuiltin nounwind "no-builtins" }

attributes #90001 = { nounwind }
"""

INTRIN_IN = """\
  %call.i = call zeroext i1 (i1, ...) @__remill_flag_computation_zero(i1 noundef zeroext %cmp.i, i32 noundef %4, i32 noundef 12, i32 noundef %sub.i) #7
  %call2.i = call zeroext i1 (i1, ...) @__remill_flag_computation_sign(i1 noundef zeroext %cmp1.i, i32 noundef %4) #7
  %call3.i = call zeroext i1 (i1, ...) @__remill_flag_computation_carry(i1 noundef zeroext true, i32 noundef %4) #7
  %call4.i = tail call zeroext i1 (i1, ...) @__remill_flag_computation_overflow(i1 noundef zeroext %cmp4.i, i32 noundef %4) #7
  %call9.i = call zeroext i1 @__remill_compare_neq(i1 noundef zeroext %tobool.not.i) #11
  %call10.i = call zeroext i1 @__remill_compare_ule(i1 noundef zeroext false) #11
  %call40.i.i = call zeroext i8 @__remill_undefined_8() #11
  %call41.i = call i64 @__remill_undefined_64() #11
  %call42.i = call float @__remill_undefined_f32() #7
  %call43.i = call double @__remill_undefined_f64() #7
  %call6.i.i.i = call i32 @__remill_fpu_exception_test_and_clear(i32 noundef 0, i32 noundef 61) #7
  %call13.i.i.i = call i32 @__remill_fpu_exception_test_and_clear(i32 noundef 61, i32 noundef %call6.i.i.i) #7
"""

INTRIN_OUT = """\
  %call.i = or i1 %cmp.i, false
  %call2.i = or i1 %cmp1.i, false
  %call3.i = or i1 true, false
  %call4.i = or i1 %cmp4.i, false
  %call9.i = or i1 %tobool.not.i, false
  %call10.i = or i1 false, false
  %call40.i.i = or i8 0, 0
  %call41.i = or i64 0, 0
  %call42.i = fadd float 0.000000e+00, 0.000000e+00
  %call43.i = fadd double 0.000000e+00, 0.000000e+00
  %call6.i.i.i = or i32 0, 0
  %call13.i.i.i = or i32 0, 0
"""

PCROUND_IN = """\
define internal x86_fp80 @__sa2_pc_round(x86_fp80 %v, i16 %cw) {
entry:
  ret x86_fp80 %v
}
define internal x86_fp80 @__sa2_x87_round_int(x86_fp80 %v, i16 %cw) {
entry:
  ret x86_fp80 %v
}
define internal i16 @__sa2_fistp_i16(x86_fp80 %v, i16 %cw) {
entry:
  ret i16 0
}
define internal i32 @__sa2_fistp_i32(x86_fp80 %v, i16 %cw) {
entry:
  ret i32 0
}
define internal i64 @__sa2_fistp_i64(x86_fp80 %v, i16 %cw) {
entry:
  ret i64 0
}
"""

PCROUND_OUT = PCROUND_IN.replace(") {\nentry:", ") alwaysinline {\nentry:")

# Calls and defines every pass must leave exactly as they are.
UNTOUCHED = """\
define ptr @sub_402000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
  %v = call i32 @__remill_read_memory_32(ptr noundef %memory, i32 noundef %a) #7
  %m2 = call ptr @__remill_write_memory_f32(ptr noundef %memory, i32 noundef %a, float noundef %f) #7
  %m3 = call ptr @__remill_write_memory_32(ptr noundef %m2, i32 noundef %a, i32 noundef %v) #7
  %c1 = call ptr @memcpy(ptr noundef nonnull %d, ptr noundef nonnull %s, i64 noundef 10) #7
  %c2 = call ptr @memset(ptr noundef nonnull %d, i32 noundef 0, i64 noundef 16) #8
  %c3 = call ptr @memcpy(ptr noundef nonnull %d, ptr noundef nonnull %s, i64 noundef 10)
  %u80 = call { i64, i16 } @__remill_undefined_f80() #7
  %p = call zeroext i1 (i1, ...) @__remill_flag_computation_parity(i1 noundef zeroext %x, i32 noundef 1) #7
  %q = call zeroext i1 @__remill_compare_foo(i1 noundef zeroext %x) #11
  %r = call i32 @__remill_fpu_exception_test(i32 noundef 0, i32 noundef 61) #7
  %w = call i32 @__remill_undefined_8() #11
  call zeroext i1 (i1, ...) @__remill_flag_computation_zero(i1 noundef zeroext %x, i32 noundef 1) #7
  %e = call ptr @__remill_error(ptr noundef %state, i32 noundef 4198400, ptr noundef %m3) #7
  ret ptr %e
}
define internal x86_fp80 @__sa2_pc_round(x86_fp80 %v, i16 %cw) #5 {
entry:
  ret x86_fp80 %v
}
define internal x86_fp80 @__sa2_other_helper(x86_fp80 %v, i16 %cw) {
entry:
  ret x86_fp80 %v
}
define x86_fp80 @__sa2_x87_round_int(x86_fp80 %v, i16 %cw) {
entry:
  ret x86_fp80 %v
}

attributes #5 = { noinline nounwind }
attributes #7 = { nobuiltin nounwind readnone willreturn "no-builtins" }
attributes #8 = { nounwind readnone }
attributes #11 = { alwaysinline nobuiltin nounwind readnone willreturn "no-builtins" }
"""


# ---------------------------------------------------------------- each pass

def test_spill_points_the_nobuiltin_mem_calls_at_a_plain_nounwind_group():
    assert SR.rewrite(SPILL_IN) == (SPILL_OUT, counts(spill=3, kept=1))


def test_intrin_replaces_each_call_with_the_value_the_runtime_returns():
    assert SR.rewrite(INTRIN_IN) == (INTRIN_OUT, counts(intrin=12))


def test_pcround_marks_the_spliced_helpers_alwaysinline():
    assert SR.rewrite(PCROUND_IN) == (PCROUND_OUT, counts(pcround=5))


def test_the_passes_compose_on_one_module():
    text = SPILL_IN + INTRIN_IN + PCROUND_IN
    want = SPILL_OUT.replace("\nattributes #90001 = { nounwind }\n", "") + INTRIN_OUT + \
        PCROUND_OUT + "\nattributes #90001 = { nounwind }\n"
    assert SR.rewrite(text) == (want, counts(3, 12, 5, kept=1))


# ------------------------------------------------------------- one pass off

def test_a_pass_left_out_rewrites_nothing_and_the_others_run_as_they_do_together():
    """The risk register's experiments: the spill pass off, or "local" or "fast" off, the
    others on (`passes`). A pass left out leaves its lines as they came and counts 0, and
    adds no #90001 group; the default is all five, so the build's output is the one above."""
    text = SPILL_IN + INTRIN_IN + PCROUND_IN
    assert SR.PASSES == ("spill", "intrin", "pcround", "local", "fast")
    assert SR.rewrite(text, passes=("intrin", "pcround", "local")) == (
        SPILL_IN + INTRIN_OUT + PCROUND_OUT, counts(intrin=12, pcround=5, kept=1))
    assert SR.rewrite(text, passes=("spill",)) == (
        SPILL_OUT.replace("\nattributes #90001 = { nounwind }\n", "") + INTRIN_IN
        + PCROUND_IN + "\nattributes #90001 = { nounwind }\n", counts(spill=3))
    assert SR.rewrite(text, passes=("pcround",)) == (
        SPILL_IN + INTRIN_IN + PCROUND_OUT, counts(pcround=5))
    assert SR.rewrite(text, passes=("spill", "intrin", "pcround"))[1] == counts(3, 12, 5)
    assert SR.rewrite(text, passes=()) == (text, NONE)
    assert SR.rewrite(text, passes=SR.PASSES) == SR.rewrite(text)


def test_a_pass_name_that_is_not_a_pass_is_refused():
    with pytest.raises(ValueError, match=r"no speed pass is named \['spil'\]; the passes are "
                                         r"\['spill', 'intrin', 'pcround', 'local', 'fast'\]"):
        SR.rewrite(SPILL_IN, passes=("spil", "intrin"))


def test_the_command_line_turns_a_pass_off(tmp_path):
    src, dst = tmp_path / "in.ll", tmp_path / "out.ll"
    src.write_text(SPILL_IN + INTRIN_IN)
    done = subprocess.run([sys.executable, str(ROOT / "tools" / "s2" / "corpus_run" /
                                               "speed_rewrite.py"), str(src), str(dst),
                           "--passes", "intrin,pcround"],
                          capture_output=True, text=True, check=True)
    assert done.stdout == ('{"spill": 0, "intrin": 12, "pcround": 0, "local": 0, '
                           '"local_kept": 0, "local_refused": 0, "fast": 0, '
                           '"fast_kept": 0}\n')
    assert dst.read_text() == SPILL_IN + INTRIN_OUT


# ---------------------------------------------------------- what it never touches

def test_memory_intrinsics_readnone_calls_and_unknown_intrinsics_come_back_unchanged():
    """A memory intrinsic call carrying `readnone` (#7), a memcpy/memset carrying any group
    but the nobuiltin one or none, `__remill_undefined_f80`, intrinsics the tables do not
    name, a zero intrinsic called at another type, a call whose result is not assigned,
    and helper defines that carry an attribute, are not helpers, or are not internal."""
    assert SR.rewrite(UNTOUCHED) == (UNTOUCHED, counts(kept=1))


# memcpy and memset calls carrying the nobuiltin group in a form remill does not emit today.
OTHER_SPILL_FORMS = """\
define ptr @sub_404000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
  %a = call ptr @memcpy(ptr noundef nonnull %d, ptr noundef nonnull %s, i64 noundef 12) #10
  %b = call ptr @memcpy(ptr noundef nonnull %d, ptr noundef nonnull %s, i64 noundef %n) #10
  %c = call ptr @memcpy(ptr noundef nonnull @g, ptr noundef nonnull %s, i64 noundef 10) #10
  %e = call ptr @memcpy(ptr noundef nonnull align 16 %d, ptr noundef nonnull %s, i64 noundef 10) #10
  %f = call ptr @memcpy(ptr noundef %d, ptr noundef nonnull %s, i64 noundef 10) #10
  %g = call ptr @memset(ptr noundef nonnull %d, i32 noundef 1, i64 noundef 16) #10
  %h = call ptr @memset(ptr noundef nonnull %d, i32 noundef %v, i64 noundef 16) #10
  %i = call ptr @memset(ptr noundef nonnull %d, i32 noundef 0, i64 noundef 10) #10
  %j = call ptr @memmove(ptr noundef nonnull %d, ptr noundef nonnull %s, i64 noundef 10) #10
  ret ptr %memory
}

attributes #10 = { nobuiltin nounwind "no-builtins" }
"""


def test_spill_leaves_every_other_memcpy_and_memset_form_a_real_call():
    """Another size, a size or fill that is not a constant, a global operand, a parameter
    attribute, an operand without `nonnull`, another fill, and `memmove`: each keeps the
    nobuiltin group, so a call form remill starts to emit stays a real call until read."""
    assert SR.rewrite(OTHER_SPILL_FORMS) == (OTHER_SPILL_FORMS, counts(kept=1))


def test_the_tables_name_no_memory_intrinsic():
    names = list(SR.RETURNS_ARGUMENT) + list(SR.RETURNS_ZERO)
    assert [n for n in names if re.search(r"(read|write)_memory", n)] == []


def test_a_spill_group_number_already_taken_is_refused():
    taken = SPILL_IN + "attributes #90001 = { noinline }\n"
    with pytest.raises(ValueError, match="#90001 is already defined"):
        SR.rewrite(taken)


# ------------------------------------------------------- idempotence, determinism

def test_rewriting_twice_is_rewriting_once():
    text = SPILL_IN + INTRIN_IN + PCROUND_IN + UNTOUCHED
    once, first = SR.rewrite(text)
    assert first == counts(3, 12, 5, kept=2)
    assert SR.rewrite(once) == (once, counts(kept=2))


def test_the_output_does_not_depend_on_the_interpreter():
    """The same input through two interpreters with different hash seeds, byte for byte."""
    text = SPILL_IN + INTRIN_IN + PCROUND_IN + UNTOUCHED
    code = ("import hashlib, sys; sys.path.insert(0, sys.argv[1]); import speed_rewrite as S; "
            "print(hashlib.sha256(S.rewrite(sys.stdin.read())[0].encode()).hexdigest())")
    digests = set()
    for seed in ("0", "1", "4242"):
        done = subprocess.run([sys.executable, "-c", code, str(ROOT / "tools" / "s2" / "corpus_run")],
                              input=text, capture_output=True, text=True, check=True,
                              env=dict(os.environ, PYTHONHASHSEED=seed))
        digests.add(done.stdout.strip())
    assert len(digests) == 1


# ------------------------------------------------------------- in the pipeline

def test_internalize_applies_the_speed_passes_after_the_stub_fold():
    """Every fold path calls `internalize.rewrite`; its output is the stub fold and then
    the speed passes, with each pass's count in the statistics."""
    text = """\
define ptr @sub_403000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
  %c = call ptr @memcpy(ptr noundef nonnull %d, ptr noundef nonnull %s, i64 noundef 10) #10
  %z = call zeroext i1 (i1, ...) @__remill_flag_computation_zero(i1 noundef zeroext %x, i32 noundef 1) #7
  %r = tail call ptr @sub_403010.1(ptr %state, i32 4206608, ptr %memory)
  ret ptr %r
}
define ptr @sub_403010.1(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
  store i32 %program_counter, ptr %rip, align 4
  %m = tail call ptr @__remill_missing_block(ptr %state, i32 %program_counter, ptr %memory)
  ret ptr %m
}
attributes #7 = { nobuiltin nounwind readnone willreturn "no-builtins" }
attributes #10 = { nobuiltin nounwind "no-builtins" }
"""
    want = """\
define ptr @sub_403000(ptr noalias %state, i32 %program_counter, ptr noalias %memory) {
  %c = call ptr @memcpy(ptr noundef nonnull %d, ptr noundef nonnull %s, i64 noundef 10) #90001
  %z = or i1 %x, false
  %r = tail call ptr @__sa2_dispatch_stub(ptr %state, i32 4206608, ptr %memory)
  ret ptr %r
}
attributes #7 = { nobuiltin nounwind readnone willreturn "no-builtins" }
attributes #10 = { nobuiltin nounwind "no-builtins" }

declare ptr @__sa2_dispatch_stub(ptr noalias, i32, ptr noalias)


attributes #90001 = { nounwind }
"""
    folded, stats = internalize.rewrite(text)
    assert folded == want
    assert (stats["stub_defines_removed"], stats["references_redirected"]) == (1, 1)
    assert (stats["speed_spill"], stats["speed_intrin"], stats["speed_pcround"]) == (1, 1, 0)
    assert (stats["speed_local"], stats["speed_local_kept"], stats["speed_local_refused"]) == \
        (0, 1, 0)
    assert (stats["speed_fast"], stats["speed_fast_kept"]) == (0, 0)


def _corpus_build():
    """tools/s2/corpus_run/build.py under a name of its own (the directory's `build` must
    not become this process's), with the path entries its import adds taken out again."""
    saved = sys.path[:]
    try:
        spec = importlib.util.spec_from_file_location(
            "s2_corpus_run_build", ROOT / "tools" / "s2" / "corpus_run" / "build.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path[:] = saved


def test_the_corpus_build_folds_again_a_cached_fold_older_code_made(tmp_path):
    """build.py kept a fold by mtime alone, so after the speed passes every cached fold and
    object stayed the old code's and the fixtures ran it. A cached fold newer than its
    lifted IR is written again when its text is not what the fold makes now; one that is
    stays untouched; `--force` writes it regardless; and the statistics are the fold's
    either way."""
    B = _corpus_build()
    src, dst = tmp_path / "sub_00403000.pc24.ll", tmp_path / "sub_00403000.pc24.fold.ll"
    src.write_text(SPILL_IN)
    current = SR.rewrite(SPILL_IN)[0]
    dst.write_text(SPILL_IN)                          # the fold before the speed passes
    os.utime(src, (time.time() - 60,) * 2)            # the lifted IR is the older file
    st, written = B.fold_one(src, dst, force=False)
    assert (written, dst.read_text(), st["speed_spill"]) == (True, current, 3)
    stamp = dst.stat().st_mtime_ns
    st, written = B.fold_one(src, dst, force=False)
    assert (written, dst.stat().st_mtime_ns, st["speed_spill"]) == (False, stamp, 3)
    st, written = B.fold_one(src, dst, force=True)
    assert (written, dst.read_text()) == (True, current)


# ------------------------------------------------------------- tied to the runtime

def _link_runtime_command(monkeypatch, tmp_path: Path) -> list[str]:
    """The command tools/s4/link_s4.py's `phase_compile` compiles the runtime's
    remill_runtime.cpp with, read from the phase itself: link_all.run stands in for the
    compiler and the link directory is the test's own, so nothing is compiled and no
    compiler, flag or include path is typed here (test_s5_stream.py `link_s4_compile`
    reads a hook's command the same way). The path entries link_s4's import adds are
    taken out again when the test ends."""
    monkeypatch.syspath_prepend(str(ROOT / "tools" / "s4"))
    import link_s4 as LS
    ran = []
    monkeypatch.setattr(LS, "OUT", tmp_path / "s4" / "link")
    table = tmp_path / "s3" / "link" / "tag" / "function_table_whole.c"
    table.parent.mkdir(parents=True)
    table.write_text("")
    monkeypatch.setattr(LS.L, "run", lambda cmd, **kw: ran.append(cmd) or {"rc": 0, "stderr_tail": ""})
    LS.phase_compile({}, "speed-rewrite-tie", "tag", tmp_path / "main.c", True)
    mine = [cmd for cmd in ran if cmd[-3:-1] == [str(RUNTIME), "-o"]]
    assert len(mine) == 1, ran
    return mine[0]


def _runtime_ir(command: list[str], source: Path, out: Path) -> str:
    """`source` compiled to LLVM IR by the link's `command` for the runtime: its compiler,
    target, optimisation, language and include paths, with `-g0 -S -emit-llvm` added (IR
    text where the link writes an object; `-S` takes precedence over the command's `-c`)
    and `source` and `out` in place of the runtime and its object. A missing compiler or
    include directory fails: this tie is the only check of the "intrin" replacements
    against the runtime, and a skip would pass the "script tests" gate."""
    includes = [Path(command[i + 1]) for i, arg in enumerate(command[:-1]) if arg == "-I"]
    missing = [str(p) for p in [Path(command[0])] if not p.is_file()] + \
        [str(p) for p in includes if not p.is_dir()]
    assert not missing, f"the link's compiler or include directories are not present: {missing}"
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(out.parent / "zig-local"),
               ZIG_GLOBAL_CACHE_DIR=str(ROOT / "private" / "generated" / "zig-cache-tests"))
    done = subprocess.run(command[:-3] + ["-g0", "-S", "-emit-llvm", str(source), "-o", str(out)],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr[-2000:]
    return out.read_text()


def test_the_runtime_defines_every_replaced_intrinsic_as_the_value_the_rewrite_uses(
        monkeypatch, tmp_path):
    command = _link_runtime_command(monkeypatch, tmp_path)
    assert SR.runtime_problems(_runtime_ir(command, RUNTIME, tmp_path / "rt.ll")) == []


def test_a_missing_compiler_fails_the_tie_rather_than_skipping(monkeypatch, tmp_path):
    """A skip is an outcome exception rather than an AssertionError, so it is caught here
    and failed rather than reported as a skip."""
    command = _link_runtime_command(monkeypatch, tmp_path)
    try:
        _runtime_ir([str(tmp_path / "no-zig")] + command[1:], RUNTIME, tmp_path / "rt.ll")
    except AssertionError as e:
        assert "are not present" in str(e)
    except BaseException as e:                       # noqa: BLE001
        pytest.fail(f"a missing compiler ended the tie with {type(e).__name__}, not a failure")
    else:
        pytest.fail("the tie ran with a compiler that is not there")


def _redefine(src: str, name: str, body: str) -> str:
    out, n = re.subn(rf"(\b{re.escape(name)}\([^)]*\)\s*)\{{[^{{}}]*\}}", rf"\g<1>{{ {body} }}", src)
    assert n == 1, f"{name}'s definition was not found once in {RUNTIME}"
    return out


def test_the_tie_fails_when_a_runtime_definition_stops_matching(monkeypatch, tmp_path):
    """A copy of the runtime in which a flag computation negates its argument, the FPU
    exception test returns its first argument, an undefined float is 1.0 and one compare
    is no longer defined: the check names each of the four, and nothing else."""
    src = RUNTIME.read_text()
    src = _redefine(src, "__remill_flag_computation_zero", "return !r;")
    src = _redefine(src, "__remill_undefined_f32", "return 1.0f;")
    src, n = re.subn(r"\b__remill_fpu_exception_test_and_clear\(uint32_t, uint32_t\)\s*\{[^{}]*\}",
                     "__remill_fpu_exception_test_and_clear(uint32_t a, uint32_t) { return a; }", src)
    assert n == 1
    src, n = re.subn(r"\bbool __remill_compare_ule\(", "bool __remill_compare_ule_gone(", src)
    assert n == 1
    copy = tmp_path / "remill_runtime.cpp"
    copy.write_text(src)
    command = _link_runtime_command(monkeypatch, tmp_path)
    problems = SR.runtime_problems(_runtime_ir(command, copy, tmp_path / "rt.ll"))
    assert sorted(p.split(":")[0] for p in problems) == [
        "__remill_compare_ule", "__remill_flag_computation_zero",
        "__remill_fpu_exception_test_and_clear", "__remill_undefined_f32"]
    assert any("does not define it" in p for p in problems if p.startswith("__remill_compare_ule"))
