"""tools/s7/f80_legalize.py and tools/s7/cross_build.py: what the pass makes of each x86_fp80
form, what it refuses, its census, and, where the pinned zig is on this machine, that its
output computes the x87's bytes and that cross_build compiles what the link reads.

The rewrite is tested on hand-written IR in the shapes remill and the PC24 pass emit, each
against the exact lines expected. The census is tested on symbol names and on objects the
pinned zig compiles. The hardware group compiles the selftest module (f80_legalize.py
selftest): every exact operation is run through the shims beside the same IR lowered to this
machine's x87, over the fixed table of special encodings and random ones; with qemu-user under
private/, the legalized module built for aarch64 must give the same bytes; and two mutated
copies of the shims must fail it, so the instrument is shown able to fail.

This file runs in run_all_gates.py's "script tests" gate, so all of it runs on every push.
Its x87 oracle is the machine's CPU, which the selftest summary names (`oracle_host`): the NaN
tie rule the shims carry was measured on it, and an x87 that settles equal-significand NaN ties
otherwise would fail the hardware tests. Without the pinned zig or qemu those tests skip.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s7"))

import f80_legalize as F  # noqa: E402

HEAD = """target triple = "i386-unknown-windows-msvc-coff"
"""


def legal(body: str, decls: str = "") -> tuple[list[str], dict]:
    text = HEAD + body + decls
    out, st = F.legalize(text)
    return out.split("\n"), st


def fn(lines: list[str]) -> list[str]:
    """The body lines of the one function, comments of the pass dropped."""
    start = next(i for i, l in enumerate(lines) if l.startswith("define"))
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    return [l.split("   ; sa2-f80")[0] for l in lines[start + 1:end]]


def test_fadd_becomes_a_call_on_the_80_bit_image_and_keeps_its_name():
    lines, st = legal("""define internal x86_fp80 @f(x86_fp80 %a, x86_fp80 %b) {
  %r = fadd x86_fp80 %a, %b
  ret x86_fp80 %r
}
""")
    assert lines[0] == F.MARKER
    assert fn(lines) == [
        "  %sa2f80.1.i = bitcast i80 %a to i80",
        "  %sa2f80.1.w = zext i80 %sa2f80.1.i to i128",
        "  %sa2f80.2.i = bitcast i80 %b to i80",
        "  %sa2f80.2.w = zext i80 %sa2f80.2.i to i128",
        "  %sa2f80.3.r = call i128 @sa2_lf80_add(i128 %sa2f80.1.w, i128 %sa2f80.2.w)",
        "  %sa2f80.4.t = trunc i128 %sa2f80.3.r to i80",
        "  %r = bitcast i80 %sa2f80.4.t to i80",
        "  ret i80 %r",
    ]
    assert "declare i128 @sa2_lf80_add(i128, i128) " + F.RC_READ in lines
    assert st["ops"] == {"fadd": 1}


@pytest.mark.parametrize("pred,mask", [("oeq", 1), ("olt", 2), ("ogt", 4), ("uno", 8),
                                       ("ueq", 9), ("une", 14), ("one", 6), ("uge", 13),
                                       ("false", 0), ("true", 15)])
def test_fcmp_is_one_compare_call_with_the_predicate_s_outcome_mask(pred, mask):
    lines, _ = legal(f"""define internal i1 @f(x86_fp80 %a) {{
  %c = fcmp {pred} x86_fp80 %a, 0xK7FFF8000000000000000
  ret i1 %c
}}
""")
    inf = str(0x7FFF8000000000000000)
    assert fn(lines) == [
        "  %sa2f80.1.i = bitcast i80 %a to i80",
        "  %sa2f80.1.w = zext i80 %sa2f80.1.i to i128",
        f"  %sa2f80.2.r = call i32 @sa2_lf80_cmp(i128 %sa2f80.1.w, i128 {inf}, i32 {mask})",
        "  %c = icmp ne i32 %sa2f80.2.r, 0",
        "  ret i1 %c",
    ]


def test_conversions():
    lines, st = legal("""define i64 @f(float %x, i32 %n, ptr %p) {
  %e = fpext float %x to x86_fp80
  %i = sitofp i32 %n to x86_fp80
  %t = fptrunc x86_fp80 %e to float
  store float %t, ptr %p
  %k = fptosi x86_fp80 %i to i64
  ret i64 %k
}
""")
    assert fn(lines) == [
        "  %sa2f80.1.r = call i128 @sa2_lf80_from_f32(float %x)",
        "  %sa2f80.2.t = trunc i128 %sa2f80.1.r to i80",
        "  %e = bitcast i80 %sa2f80.2.t to i80",
        "  %sa2f80.3.x = sext i32 %n to i64",
        "  %sa2f80.4.r = call i128 @sa2_lf80_from_i64(i64 %sa2f80.3.x)",
        "  %sa2f80.5.t = trunc i128 %sa2f80.4.r to i80",
        "  %i = bitcast i80 %sa2f80.5.t to i80",
        "  %sa2f80.6.i = bitcast i80 %e to i80",
        "  %sa2f80.6.w = zext i80 %sa2f80.6.i to i128",
        "  %t = call float @sa2_lf80_to_f32(i128 %sa2f80.6.w)",
        "  store float %t, ptr %p",
        "  %sa2f80.7.i = bitcast i80 %i to i80",
        "  %sa2f80.7.w = zext i80 %sa2f80.7.i to i128",
        "  %k = call i64 @sa2_lf80_to_i64(i128 %sa2f80.7.w)",
        "  ret i64 %k",
    ]
    assert st["conversions"] == {"fpext.float": 1, "sitofp.i32": 1, "fptrunc.float": 1,
                                 "fptosi.i64": 1}


def test_fneg_and_fabs_are_sign_bit_integer_code():
    lines, _ = legal("""define internal x86_fp80 @f(x86_fp80 %a) {
  %n = fneg x86_fp80 %a
  %m = call x86_fp80 @llvm.fabs.f80(x86_fp80 %n) #3
  ret x86_fp80 %m
}
""", "declare x86_fp80 @llvm.fabs.f80(x86_fp80) #1\n")
    assert fn(lines) == [
        "  %sa2f80.1.b = bitcast i80 %a to i80",
        f"  %sa2f80.2.n = xor i80 %sa2f80.1.b, {1 << 79}",
        "  %n = bitcast i80 %sa2f80.2.n to i80",
        "  %sa2f80.3.b = bitcast i80 %n to i80",
        f"  %sa2f80.4.a = and i80 %sa2f80.3.b, {(1 << 79) - 1}",
        "  %m = bitcast i80 %sa2f80.4.a to i80",
        "  ret i80 %m",
    ]
    assert not any("llvm.fabs.f80" in l for l in lines)


def test_a_libm_long_double_call_becomes_its_shim_and_its_declaration_goes():
    lines, st = legal("""define internal x86_fp80 @f(x86_fp80 %a, x86_fp80 %b) {
  %s = call x86_fp80 @sinl(x86_fp80 noundef %a) #7
  %q = tail call x86_fp80 @fmodl(x86_fp80 noundef %s, x86_fp80 noundef %b) #7
  ret x86_fp80 %q
}
""", "declare dso_local x86_fp80 @sinl(x86_fp80 noundef) local_unnamed_addr #2\n"
     "declare dso_local x86_fp80 @fmodl(x86_fp80 noundef, x86_fp80 noundef) local_unnamed_addr #2\n"
     "declare dso_local x86_fp80 @atanl(x86_fp80 noundef) local_unnamed_addr #2\n")
    calls = [l for l in fn(lines) if " call " in l]
    assert calls == ["  %sa2f80.2.r = call i128 @sa2_lf80_sin(i128 %sa2f80.1.w)",
                     "  %sa2f80.6.r = call i128 @sa2_lf80_fmod(i128 %sa2f80.4.w, i128 %sa2f80.5.w)"]
    assert [l for l in lines if l.startswith("declare")] == [
        "declare i128 @sa2_lf80_fmod(i128, i128) " + F.PURE,
        "declare i128 @sa2_lf80_sin(i128) nounwind",
    ]
    assert st["transcendental"] == {"sinl": 1} and st["exact"] == {"fmodl": 1}
    assert sorted(st["declarations_dropped"]) == ["atanl", "fmodl", "sinl"]


def test_fenv_calls_are_renamed_to_the_x87_constant_shims():
    lines, st = legal("""define i32 @f(i32 %m) {
  %a = call i32 @fesetround(i32 noundef %m) #7
  %b = call i32 @fegetround() #8
  ret i32 %b
}
""", "declare dso_local i32 @fesetround(i32 noundef) local_unnamed_addr #3\n"
     "declare dso_local i32 @fegetround() local_unnamed_addr #4\n")
    assert fn(lines) == ["  %a = call i32 @sa2_x87_fesetround(i32 noundef %m) #7",
                         "  %b = call i32 @sa2_x87_fegetround() #8",
                         "  ret i32 %b"]
    assert "declare dso_local i32 @sa2_x87_fesetround(i32 noundef) local_unnamed_addr #3" in lines
    assert st["fenv"] == {"fesetround": 1, "fegetround": 1}


def test_data_movement_and_internal_helpers_are_left_alone():
    body = """define internal x86_fp80 @__sa2_pc_round(x86_fp80 %v, i16 %cw) {
  %b = bitcast x86_fp80 %v to i80
  %r = bitcast i80 %b to x86_fp80
  ret x86_fp80 %r
}
define void @f(ptr %p, ptr %q, i1 %c, i16 %cw) {
  %a = load x86_fp80, ptr %p, align 16
  %s = alloca x86_fp80, align 16
  %r = call x86_fp80 @__sa2_pc_round(x86_fp80 %a, i16 %cw)   ; sa2-pc24pass
  %x = select i1 %c, x86_fp80 %r, x86_fp80 0xK3FFF8000000000000000
  store x86_fp80 %x, ptr %q, align 16
  ret void
}
"""
    out, st = F.legalize(HEAD + body)
    # nothing to rewrite, and the type is i80 throughout: the same lines with i80 and the
    # constant's integer
    assert out == F.MARKER + "\n" + HEAD + body.replace("x86_fp80", "i80").replace(
        "0xK3FFF8000000000000000", str(0x3FFF8000000000000000))
    assert "x86_fp80" not in out.split("\n", 1)[1] and st["rewrites"] == 0


def test_an_unused_f80_declaration_is_dropped():
    lines, _ = legal("define void @f() {\n  ret void\n}\n",
                     "declare x86_fp80 @llvm.nearbyint.f80(x86_fp80) #2\n")
    assert not any("nearbyint" in l for l in lines)


def test_idempotent():
    text = HEAD + """define internal x86_fp80 @f(x86_fp80 %a) {
  %r = fmul x86_fp80 %a, 0xK40008000000000000000
  %c = fcmp olt x86_fp80 %r, %a
  %s = select i1 %c, x86_fp80 %r, x86_fp80 %a
  ret x86_fp80 %s
}
"""
    once, _ = F.legalize(text)
    assert F.legalize(once)[0] == once
    stripped = once.split("\n", 1)[1]
    again, st = F.legalize(stripped)
    assert again == once and st["rewrites"] == 0


@pytest.mark.parametrize("line,why", [
    ("%r = frem x86_fp80 %a, %a", "frem"),
    ("%r = call x86_fp80 @llvm.pow.f80(x86_fp80 %a, x86_fp80 %a)", "no semantics"),
    ("%r = call x86_fp80 @mystery(x86_fp80 %a)", "mystery"),
    ("%r = uitofp i64 %n to x86_fp80", "uitofp i64"),
    ("%r = fptoui x86_fp80 %a to i32", "fptoui"),
    ("%e = call i32 @fesetenv(ptr %p)", "not modelled"),
])
def test_what_it_has_no_rule_for_is_refused(line, why):
    text = HEAD + f"define void @f(x86_fp80 %a, i64 %n, ptr %p) {{\n  {line}\n  ret void\n}}\n"
    with pytest.raises(F.Refused, match=why):
        F.legalize(text)


def test_a_form_the_pass_does_not_know_is_caught_by_the_postcondition():
    text = HEAD + "define void @f(<2 x x86_fp80> %v, ptr %p) {\n" \
                  "  %e = extractelement <2 x i80> %v, i32 0\n  ret void\n}\n"
    with pytest.raises(F.Refused, match="left"):
        F.legalize(text)


def test_a_named_type_holding_x86_fp80_is_data_movement_only():
    body = """%union.union_ld = type { x86_fp80 }
%struct.T = type { i32, %union.union_ld }
define void @f(ptr %p) {
  %u = alloca %union.union_ld, align 16
  %g = getelementptr inbounds %union.union_ld, ptr %u, i64 0, i32 0
  %t = alloca %struct.T, align 16
  %v = load %struct.T, ptr %t, align 16
  store %struct.T %v, ptr %p, align 16
  ret void
}
"""
    out, _ = F.legalize(HEAD + body)
    assert out == F.MARKER + "\n" + HEAD + body.replace("x86_fp80", "i80")


@pytest.mark.parametrize("line,decl", [
    ("%s = call %S @ext()", "declare %S @ext()"),
    ("%s = call i32 @ext2(%S %v)", "declare i32 @ext2(%S)"),
    ("%x = extractvalue %S %v, 0", ""),
    ("%n = phi %N [ %v2, %e ]", ""),
])
def test_a_named_type_holding_x86_fp80_is_refused_outside_data_movement(line, decl):
    text = HEAD + ("%S = type { x86_fp80, i32 }\n%N = type { [2 x %S] }\n"
                   f"define internal void @f(%S %v, %N %v2) {{\ne:\n  {line}\n  ret void\n}}\n"
                   f"{decl}\n")
    with pytest.raises(F.Refused, match="left"):
        F.legalize(text)


def test_a_fenv_function_used_other_than_by_a_call_is_refused():
    text = HEAD + """define void @f(ptr %p) {
  store ptr @fegetround, ptr %p, align 8
  ret void
}
declare i32 @fegetround()
"""
    with pytest.raises(F.Refused, match="fegetround"):
        F.legalize(text)


FMF_BODY = """define internal x86_fp80 @__sa2_pc_round(x86_fp80 %v, i16 %cw) {
  ret x86_fp80 %v
}
define void @f(ptr %p, i1 %c, i16 %cw, float %f) {
e:
  %a = load x86_fp80, ptr %p, align 16
  br label %j
j:
  %h = phi fast x86_fp80 [ %a, %e ]
  %s = select nnan nsz i1 %c, x86_fp80 %h, x86_fp80 %a
  %r = call fast x86_fp80 @__sa2_pc_round(x86_fp80 %s, i16 %cw)
  %k = select nnan i1 %c, float %f, float 1.0
  store x86_fp80 %r, ptr %p, align 16
  store float %k, ptr %p, align 4
  ret void
}
"""


def test_fast_math_flags_go_with_the_type_on_phi_select_and_call():
    lines, _ = legal(FMF_BODY)
    body = fn(lines[lines.index("}") + 1:])
    assert body[4:7] == ["  %h = phi i80 [ %a, %e ]",
                         "  %s = select i1 %c, i80 %h, i80 %a",
                         "  %r = call i80 @__sa2_pc_round(i80 %s, i16 %cw)"]
    assert body[7] == "  %k = select nnan i1 %c, float %f, float 1.0"   # not x86_fp80: kept


@pytest.mark.parametrize("sym,cls", [
    ("sub_401010", "lifted"), ("__remill_read_memory_f80", "remill"),
    ("__sa2_dispatch_stub", "runtime"), ("sa2_guest_fast_base", "runtime"),
    ("sa2_lf80_add", "shim"), ("sa2_x87_fegetround", "shim"), ("sqrt", "host-libc"),
    ("nearbyint", "host-libc"), ("trunc", "host-libc"), ("rint", "disallowed"),
    ("__gcc_qlt", "disallowed"), ("__addxf3", "disallowed"), ("__extendsfxf2", "disallowed"),
    ("__floatsixf", "disallowed"), ("__eqtf2", "disallowed"), ("__fixunsxfdi", "disallowed"),
    ("sinl", "disallowed"), ("sqrtl", "disallowed"), ("fesetround", "disallowed"),
    # what a denylist misses: the census is an allowlist
    ("__mulxc3", "disallowed"), ("__divtc3", "disallowed"), ("exp10l", "disallowed"),
    ("significandl", "disallowed"), ("lgammal_r", "disallowed"), ("__fe_getround", "disallowed"),
    ("memcpy", "disallowed"), ("__printf_chk", "disallowed"), ("sub_401010x", "disallowed"),
    ("SUB_401010", "disallowed"),
    # a shim name the shims object does not define (misspelt, or a pass/shims disagreement)
    ("sa2_lf80_ad", "disallowed"), ("sa2_x87_fesetenv", "disallowed")])
def test_census_classes(sym, cls):
    got = F.classify_symbol(sym, frozenset({"sa2_lf80_add", "sa2_x87_fegetround"}))
    assert got.split(":")[0] == cls


# ------------------------------------------------------------------ against the hardware

HAVE_ZIG = F.ZIG.is_file()
needs_zig = pytest.mark.skipif(not HAVE_ZIG, reason="the pinned zig is not on this machine")


@needs_zig
def test_the_shims_compute_the_x87_s_bytes_on_this_host(tmp_path):
    exe = F.build_selftest(tmp_path / "x86", "x86_64-linux-musl", oracle=True)
    res = F.run_selftest(exe, "x86_64-linux-musl", 3000)
    s = F.summarize(res, None, F.selftest_entries(True))
    assert s["exact_ops"] == (52 if F.helper_lines() else 47)
    assert s["exact_ops_mismatching"] == []
    # the fixed table ran for every op, and the fenv checks ran
    assert all(r["special_calls"] > 0 for n, r in s["ops"].items())
    assert s["ops"]["fenv_rc_roundtrip"]["calls"] > 0 and s["ops"]["fenv_exceptions"]["calls"] > 0
    assert s["oracle_host"]["machine"] == "x86_64" and s["oracle_host"]["model"]
    # compiler-rt's candidates are inexact on this host, which is why none is used
    assert s["ops"]["crt_addxf3"]["mismatch_vs_x86"] > 0
    assert s["ops"]["crt_extendsfxf2"]["mismatch_vs_x86"] > 0
    assert s["ops"]["crt_fixxfdi"]["mismatch_vs_x86"] > 0


@needs_zig
@pytest.mark.skipif(not F.QEMU_AARCH64.is_file(), reason="qemu-aarch64 is not under private/")
def test_the_legalized_module_built_for_aarch64_gives_the_same_bytes(tmp_path):
    nat = F.run_selftest(F.build_selftest(tmp_path / "x86", "x86_64-linux-musl", oracle=True),
                         "x86_64-linux-musl", 3000)
    a64 = F.run_selftest(F.build_selftest(tmp_path / "a64", "aarch64-linux-musl", oracle=False),
                         "aarch64-linux-musl", 3000)
    s = F.summarize(nat, a64, F.selftest_entries(True))
    assert s["exact_ops_mismatching"] == [] and s["cross_target_differs"] == []
    # __floatsixf is exact on x86-64 and gives other bytes on aarch64: the ABI finding
    assert s["ops"]["crt_floatsixf"]["mismatch_vs_x86"] == 0
    assert s["ops"]["crt_floatsixf"]["cross_target_hash_equal"] is False
    assert s["candidates_exact"] == []
    assert s["candidates_exact_on_x86_only"] == ["crt_floatdixf", "crt_floatsixf"]


@pytest.fixture(scope="module")
def a64_shims(tmp_path_factory):
    """f80_shims.c compiled for aarch64, and the shim names it defines."""
    obj = tmp_path_factory.mktemp("shims") / "f80_shims.o"
    subprocess.run([str(F.ZIG), "cc", "-target", "aarch64-linux-musl", "-O2", "-w", "-c",
                    str(F.SHIMS_C), "-o", str(obj)], check=True, capture_output=True)
    return F.shim_names(obj)


@needs_zig
@pytest.mark.skipif(not F.QEMU_AARCH64.is_file(), reason="qemu-aarch64 is not under private/")
def test_the_measuring_build_s_shims_compute_the_port_s_bytes(tmp_path):
    """The arm64 replays link the shims with tools/s7/arm64_link.py's SHIM_DEFINES, the call and
    per-site counters, so the run that is compared is the run that is counted. The counters
    must touch no value: built so for aarch64 and run under qemu-user, every exact operation
    gives the x87's bytes on this host and the same hashes as the x86-64 build."""
    sys.path.insert(0, str(F.ROOT / "tools" / "s7"))
    import arm64_link as A  # noqa: PLC0415
    assert set(A.SHIM_DEFINES) == {"-DSA2_F80_COUNT", "-DSA2_F80_SITES"}
    nat = F.run_selftest(F.build_selftest(tmp_path / "x86", "x86_64-linux-musl", oracle=True,
                                          with_crt=False), "x86_64-linux-musl", 3000)
    a64 = F.run_selftest(F.build_selftest(tmp_path / "a64", "aarch64-linux-musl", oracle=False,
                                          shims_defs=list(A.SHIM_DEFINES), with_crt=False),
                         "aarch64-linux-musl", 3000)
    s = F.summarize(nat, a64, F.selftest_entries(False))
    assert s["exact_ops"] == (52 if F.helper_lines() else 47)
    assert s["exact_ops_mismatching"] == [] and s["cross_target_differs"] == []


@needs_zig
def test_the_shims_object_defines_every_shim_the_pass_calls(a64_shims):
    assert a64_shims == frozenset(F.DECLS) | frozenset(F.FENV.values())


@needs_zig
def test_no_shim_takes_a_name_the_runtime_defines(a64_shims):
    """The runtime exports conversions of its own under sa2_f80_ (runtime/cpu_state.h and
    remill_runtime.h, on its sa2_f80 type), and a shim of the same name is a duplicate symbol
    when the legalised objects are linked beside the runtime: the first aarch64 link failed so,
    which is why the shims are sa2_lf80_*."""
    import re  # noqa: PLC0415
    runtime = set()
    for header in ("runtime/cpu_state.h", "runtime/remill_runtime.h"):
        runtime |= set(re.findall(r"\b(sa2_\w+)\s*\(", (F.ROOT / header).read_text()))
    assert {"sa2_f80_from_f64", "sa2_f80_to_f64", "sa2_f80_to_f32"} <= runtime
    assert a64_shims and not a64_shims & runtime


@needs_zig
def test_the_census_fails_on_a_symbol_outside_the_allowlist(tmp_path, a64_shims):
    src = tmp_path / "odd.ll"
    src.write_text("""define double @f(double %x, ptr %p) {
  %a = call double @exp10l(double %x)
  %b = call i128 @sa2_lf80_ad(i128 0, i128 0)
  %c = call i128 @sa2_lf80_add(i128 0, i128 0)
  %d = call ptr @__remill_error(ptr %p, i32 0, ptr %p)
  %e = call double @sqrt(double %a)
  call void @__mulxc3(ptr %p)
  ret double %e
}
declare double @exp10l(double)
declare i128 @sa2_lf80_ad(i128, i128)
declare i128 @sa2_lf80_add(i128, i128)
declare ptr @__remill_error(ptr, i32, ptr)
declare double @sqrt(double)
declare void @__mulxc3(ptr)
""")
    obj = tmp_path / "odd.o"
    subprocess.run([str(F.ZIG), "cc", "-target", "aarch64-linux-musl", "-O2", "-w", "-c",
                    str(src), "-o", str(obj)], check=True, capture_output=True)
    res = F.census([obj], a64_shims, 1)
    assert not res["ok"]
    assert sorted(res["disallowed"]) == ["__mulxc3", "exp10l", "sa2_lf80_ad"]
    assert res["disallowed"]["exp10l"]["class"] == "disallowed:not on the census allowlist"
    assert {s: e["class"] for s, e in res["symbols"].items() if s not in res["disallowed"]} == {
        "__remill_error": "remill", "sa2_lf80_add": "shim", "sqrt": "host-libc"}


@needs_zig
def test_the_census_sees_what_the_backend_left(tmp_path, a64_shims):
    raw = tmp_path / "raw.ll"
    raw.write_text("""define i1 @f(ptr %p, ptr %q) {
  %a = load x86_fp80, ptr %p
  %b = load x86_fp80, ptr %q
  %c = fcmp olt x86_fp80 %a, %b
  ret i1 %c
}
""")
    good = tmp_path / "good.ll"
    good.write_text(F.legalize(raw.read_text())[0])
    for src in (raw, good):
        subprocess.run([str(F.ZIG), "cc", "-target", "aarch64-linux-musl", "-O2", "-w", "-c",
                        str(src), "-o", str(src.with_suffix(".o"))], check=True,
                       capture_output=True)
    bad = F.census([raw.with_suffix(".o")], a64_shims, 1)
    ok = F.census([good.with_suffix(".o")], a64_shims, 1)
    assert not bad["ok"] and any(s.startswith("__gcc_q") for s in bad["disallowed"])
    assert ok["ok"] and list(ok["symbols"]) == ["sa2_lf80_cmp"]


@needs_zig
def test_fast_math_flags_dropped_leave_ir_the_backend_takes(tmp_path):
    ll = tmp_path / "fmf.ll"
    ll.write_text(F.legalize(FMF_BODY)[0])
    subprocess.run([str(F.ZIG), "cc", "-target", "aarch64-linux-musl", "-O2", "-w", "-c",
                    str(ll), "-o", str(tmp_path / "fmf.o")], check=True, capture_output=True)


# Two of a review's mutations of the shims, which the random vectors alone missed at this
# count: -0 ordered below +0 (a +-0 pair is about one in 10^5 random vectors) and fegetround
# reporting the RC one bit off (the driver never called it). The fixed table and the RC round
# trip must catch both.
MUTATIONS = {
    "fcmp_zero_sign": ("else if (ca == ZERO && cb == ZERO) code = 0;",
                       "else if (ca == ZERO && cb == ZERO) code = a.s == b.s ? 0 : (a.s ? 1 : 2);",
                       {"fcmp_oeq", "fcmp_olt", "fcmp_ogt", "fcmp_une"}),
    "fegetround_shift": ("return (int)(host_rc() << 10);", "return (int)(host_rc() << 11);",
                         {"fenv_rc_roundtrip"}),
}


@needs_zig
@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_a_mutated_shim_fails_the_selftest(tmp_path, monkeypatch, name):
    old, new, expect = MUTATIONS[name]
    src = F.SHIMS_C.read_text()
    assert src.count(old) == 1
    mutated = tmp_path / "f80_shims.c"
    mutated.write_text(src.replace(old, new))
    monkeypatch.setattr(F, "SHIMS_C", mutated)
    exe = F.build_selftest(tmp_path / "x86", "x86_64-linux-musl", oracle=True, with_crt=False)
    s = F.summarize(F.run_selftest(exe, "x86_64-linux-musl", 3000), None,
                    F.selftest_entries(False))
    assert expect <= set(s["exact_ops_mismatching"])


# ------------------------------------------------------------------ tools/s7/cross_build.py

def test_cross_build_reads_each_population_s_command_from_its_tool():
    import cross_build as XB
    for pop in ("map", "arms", "extra"):
        assert XB.command_template(pop) == ("{zig}", "cc", "-target", "{triple}", "-O2", "-w",
                                            "-c", "{ir}", "-o", "{object}")
    cmd = XB.compile_command("arms", "aarch64-linux-musl", Path("a.ll"), Path("a.o"))
    assert cmd[1:] == ["cc", "-target", "aarch64-linux-musl", "-O2", "-w", "-c", "a.ll", "-o",
                       "a.o"]


def _staged_ready() -> bool:
    try:
        import cross_build as XB
        return XB.STAGED.is_dir() and XB.EXTRA_STATE.is_file()
    except Exception:
        return False


needs_staged = pytest.mark.skipif(not (HAVE_ZIG and _staged_ready()),
                                  reason="the staged link objects or the pinned zig are absent")


@needs_staged
def test_cross_build_s_command_for_x86_64_gives_the_link_s_objects_byte_for_byte(tmp_path):
    """Sampled modules of each population (the smallest, the median and the 90th percentile
    by IR size), compiled with cross_build's command for the native triple without
    legalization, must be the staged link's objects: what the command reading cannot see."""
    import hashlib
    import cross_build as XB
    pops = XB.populations()
    extra_obj = {XB.va_of(Path(o).name): Path(o)
                 for o in json.loads(XB.EXTRA_STATE.read_text())["objects"]}
    for pop, mods in pops.items():
        by_size = sorted(mods, key=lambda m: (m[1].stat().st_size, m[0]))
        for va, ir in {by_size[i] for i in (0, len(by_size) // 2, len(by_size) * 9 // 10)}:
            obj = tmp_path / f"{pop}_{va:08X}.o"
            subprocess.run(XB.compile_command(pop, "x86_64-linux-musl", ir, obj), check=True,
                           capture_output=True, cwd=str(tmp_path))
            ref = extra_obj[va] if pop == "extra" else XB.STAGED / f"ir_{va:08X}.o"
            assert hashlib.sha256(obj.read_bytes()).digest() == \
                hashlib.sha256(ref.read_bytes()).digest(), f"{pop} {ir.name}"


@needs_staged
def test_cross_build_end_to_end_keeps_only_this_build_s_objects(tmp_path):
    out = tmp_path / "a64"
    (out / "map").mkdir(parents=True)
    (out / "map" / "ir_DEADBEEF.o").write_bytes(b"left by an earlier build")
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "s7" / "cross_build.py"),
                        "--target", "aarch64-linux-musl", "--limit", "2", "--jobs", "4",
                        "--out-root", str(out)], capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]
    assert not (out / "map" / "ir_DEADBEEF.o").exists()
    recs = [json.loads(l) for l in (out / "build.jsonl").read_text().splitlines()]
    assert len(recs) == 6 and all(r["compiled"] for r in recs)
    assert sorted(str(o.relative_to(out)) for o in out.glob("*/ir_*.o")) == \
        sorted(r["object"] for r in recs)
    summary = json.loads((out / "summary.json").read_text())
    assert summary["stale_objects_removed"] == 1 and summary["census"]["ok"]
    assert summary["census"]["objects"] == 6


@pytest.mark.parametrize("triple,legal", [("aarch64-linux-musl", True), ("x86_64-linux-musl", False),
                                          ("x86_64-windows-gnu", False), ("riscv64-linux-musl", True)])
def test_cross_build_legalizes_exactly_the_non_x86_triples(triple, legal):
    import cross_build as XB
    assert (not XB.x86(triple)) is legal
