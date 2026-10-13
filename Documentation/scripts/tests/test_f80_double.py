"""tools/s8/f80_double.py and tools/s8/f80_shims_double.c: the double mode of the x87
legalisation (the fast mode), what it makes of each form, what it refuses, and, with the pinned
zig, that its output computes what the x87 path computes.

The rewrite is tested on hand-written IR in the shapes remill, SROA and the PC24 pass emit, each
against the exact lines expected, and on the real folded modules where this machine has them.
The hardware group compiles the pass's output with the pinned zig and runs it: the PC24
rounding, FRNDINT and FIST against the exact mode's own helpers on this host's x87; the 80-bit
image of a double (`__sa2_dimg_lo`, `__sa2_dimg_hi`) against this host's x87 widening; the
runtime's double-mode register conversions (runtime/remill_runtime.cpp, compiled by
tools/s4/link_s4.py's own command); and remill's FLD m80 signalling-NaN test, run three ways
over a table of 80-bit images: as the x86-64 build runs it (native x87), in the double mode
through the runtime's conversion, and as the predicate written out here. Without the pinned zig
those tests skip.

The double mode lives beside the exact pass, never in it: tools/s7/f80_legalize.py and
tools/s7/f80_shims.c are pinned by the arm64-linux target's receipts, and the first test holds
them to those pins.
"""
from __future__ import annotations

import json
import platform
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s7"))
sys.path.insert(0, str(ROOT / "tools" / "s8"))

import f80_double as D  # noqa: E402
import f80_legalize as F  # noqa: E402

HEAD = """target triple = "i386-unknown-windows-msvc-coff"
"""
HAVE_ZIG = D.ZIG.is_file()
needs_zig = pytest.mark.skipif(not HAVE_ZIG, reason="the pinned zig is not on this machine")
X86_HOST = platform.machine() in ("x86_64", "AMD64")
needs_x87 = pytest.mark.skipif(not (HAVE_ZIG and X86_HOST),
                               reason="needs the pinned zig and an x86-64 host (its x87)")
FOLD = ROOT / "private" / "generated" / "s3" / "whole_image" / "fold" / "next"


def dbl(body: str, decls: str = "") -> tuple[list[str], dict]:
    out, st = D.legalize(HEAD + body + decls)
    return out.split("\n"), st


def fn(lines: list[str]) -> list[str]:
    """The body lines of the first function."""
    start = next(i for i, l in enumerate(lines) if l.startswith("define"))
    end = next(i for i in range(start, len(lines)) if lines[i] == "}")
    return lines[start + 1:end]


def refused(body: str, decls: str = "") -> str:
    with pytest.raises(F.Refused) as e:
        D.legalize(HEAD + body + decls)
    return str(e.value)


def sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ the pinned exact pass

@pytest.mark.parametrize("receipt", ["s7-arm64-linux-build-v1.json", "s7-arm64-replays-v1.json"])
def test_the_exact_pass_and_its_shims_are_the_files_the_arm64_receipts_pin(receipt):
    """The double mode is a module of its own because these two files are pinned: an edit to
    either unmeets the arm64-linux target (Documentation/scripts/check_s7_exit.py)."""
    doc = json.loads((ROOT / "Documentation" / "receipts" / receipt).read_text())
    pins = doc.get("target_passes") or doc["inputs"]["pipeline"]
    for rel in ("tools/s7/f80_legalize.py", "tools/s7/f80_shims.c"):
        assert pins.get(rel) == sha256(ROOT / rel), rel


def test_the_double_shims_include_the_exact_shims_unchanged():
    text = D.SHIMS_C.read_text()
    assert '#include "../s7/f80_shims.c"' in text
    assert "SA2_F80_DOUBLE" not in (ROOT / "tools/s7/f80_shims.c").read_text()


# ------------------------------------------------------------------ values

def test_double_mode_retypes_arithmetic_and_converts_constants():
    lines, st = dbl("""define internal x86_fp80 @f(x86_fp80 %a, float %x) {
  %e = fpext float %x to x86_fp80
  %r = fmul x86_fp80 %a, %e
  %s = fadd x86_fp80 %r, 0xK3FFF8000000000000000
  %c = fcmp olt x86_fp80 %s, 0xK00018000000000000000
  %t = fptrunc x86_fp80 %s to float
  ret x86_fp80 %s
}
""")
    assert lines[0] == D.DBL_MARKER
    assert fn(lines) == [
        "  %e = fpext float %x to double",
        "  %r = fmul double %a, %e",
        "  %s = fadd double %r, 0x3FF0000000000000",
        "  %c = fcmp olt double %s, 0x0000000000000000",   # the x87's smallest normal: 0 in double
        "  %t = fptrunc double %s to float",
        "  ret double %s",
    ]
    assert "define internal double @f(double %a, float %x) {" in lines


def test_double_mode_double_conversions_are_no_ops_and_fist_is_a_shim():
    lines, st = dbl("""define internal i32 @f(double %d) {
  %e = fpext double %d to x86_fp80
  %b = fptrunc x86_fp80 %e to double
  %i = fptosi x86_fp80 %e to i32
  ret i32 %i
}
""")
    assert fn(lines) == ["  %e = freeze double %d", "  %b = freeze double %e",
                         "  %i = call i32 @sa2_df80_to_i32(double %e)", "  ret i32 %i"]
    assert any(l.startswith("declare i32 @sa2_df80_to_i32(double)") for l in lines)


def test_double_mode_replaces_the_pc24_helper_and_keeps_its_calls():
    lines, st = dbl("""define internal x86_fp80 @__sa2_pc_round(x86_fp80 %0, i16 %1) alwaysinline {
  %3 = bitcast x86_fp80 %0 to i80
  ret x86_fp80 %0
}

define internal x86_fp80 @f(x86_fp80 %a, i16 %cw) {
  %p = fmul x86_fp80 %a, %a
  %r = call x86_fp80 @__sa2_pc_round(x86_fp80 %p, i16 %cw)   ; sa2-pc24pass
  ret x86_fp80 %r
}
""")
    text = "\n".join(lines)
    assert "define internal double @__sa2_pc_round(double %v, i16 %cw) alwaysinline {" in text
    assert "bitcast x86_fp80" not in text and "i80 %0" not in text
    assert "  %r = call double @__sa2_pc_round(double %p, i16 %cw)   ; sa2-pc24pass" in lines
    assert "declare double @sa2_df80_pc_round_slow(double, i16) " + F.PURE in lines
    assert st["helpers"] == ["__sa2_pc_round"]


def test_double_mode_renames_the_m80_intrinsics_and_libm_calls():
    lines, st = dbl("""define internal ptr @f(ptr %m, i32 %a, ptr %slot, x86_fp80 %v) {
  %1 = call ptr @__remill_read_memory_f80(ptr noundef %m, i32 noundef %a, ptr noundef nonnull align 16 dereferenceable(16) %slot)
  %s = call x86_fp80 @sinl(x86_fp80 noundef %v) #1
  %q = call x86_fp80 @llvm.fabs.f80(x86_fp80 %s)
  ret ptr %1
}
""", """declare ptr @__remill_read_memory_f80(ptr noundef readnone, i32 noundef, ptr noundef nonnull align 16 dereferenceable(16))
declare x86_fp80 @sinl(x86_fp80 noundef) #1
declare x86_fp80 @llvm.fabs.f80(x86_fp80)
""")
    body = fn(lines)
    assert body[0].startswith("  %1 = call ptr @__remill_read_memory_f80_dbl(")
    assert body[1] == "  %s = call double @sa2_df80_sin(double noundef %v) #1"
    assert body[2] == "  %q = call double @llvm.fabs.f64(double %s)"
    assert any(l.startswith("declare ptr @__remill_read_memory_f80_dbl(") for l in lines)
    assert not any("@sinl(" in l or "llvm.fabs.f80" in l for l in lines)
    assert "declare double @sa2_df80_sin(double) nounwind" in lines


def test_double_mode_keeps_the_80_bit_layout_as_i80():
    lines, _ = dbl("""%union.union_ld = type { x86_fp80 }

define void @f(float %x) {
  %u = alloca %union.union_ld, align 16
  %p = getelementptr inbounds %union.union_ld, ptr %u, i64 0, i32 0
  %e = fpext float %x to x86_fp80
  store x86_fp80 %e, ptr %p, align 16
  %s = alloca x86_fp80, align 16
  ret void
}
""")
    assert "%union.union_ld = type { i80 }" in lines
    assert "  store double %e, ptr %p, align 16" in lines and "  %s = alloca i80, align 16" in lines


@pytest.mark.parametrize("k,d", [("3FFF8000000000000000", "0x3FF0000000000000"),
                                 ("4000C90FDAA22168C235", "0x400921FB54442D18"),   # FLDPI
                                 ("7FFF8000000000000000", "0x7FF0000000000000"),
                                 ("FFFFC000000000000000", "0xFFF8000000000000"),   # the indefinite
                                 ("00018000000000000000", "0x0000000000000000"),
                                 ("BFFEC000000000000000", "0xBFE8000000000000"),
                                 ("7FFEFFFFFFFFFFFFFFFF", "0x7FF0000000000000"),
                                 ("7FFFA000000000000000", "0x7FF4000000000000"),   # an SNaN stays one
                                 ("7FFF8000000000000001", "0x7FF0000000000001")])  # payload below bit 11
def test_double_mode_constants_are_the_nearest_double(k, d):
    assert D.f80_double_hex(k) == d


# ------------------------------------------------------------------ the sign of the image

SIGN_LOAD = """%union.union_ld = type {{ x86_fp80 }}

define i1 @f(float %x) {{
  %slot = alloca %union.union_ld, align 16
  %e = fpext float %x to x86_fp80
  store x86_fp80 %e, ptr %slot, align 16
  %w = load i80, ptr %slot, align 16
  %c = {cmp}
  ret i1 %c
}}
"""
SIGN_CAST = """define i1 @f(float %x) {{
  %e = fpext float %x to x86_fp80
  %w = bitcast x86_fp80 %e to i80
  %c = {cmp}
  ret i1 %c
}}
"""
NOT_SIGN = ["icmp sgt i80 %w, 0", "icmp sle i80 %w, 0", "icmp sge i80 %w, -1",
            "icmp slt i80 %w, -1", "icmp sge i80 %w, 0", "icmp sle i80 %w, -1",
            "icmp ugt i80 %w, 0", "icmp eq i80 %w, 0", "icmp ne i80 %w, 0",
            "icmp slt i80 %w, 1"]


@pytest.mark.parametrize("source", [SIGN_LOAD, SIGN_CAST], ids=["load", "bitcast"])
@pytest.mark.parametrize("cmp", NOT_SIGN)
def test_double_mode_refuses_by_name_each_compare_of_the_image_that_is_not_a_sign_read(source, cmp):
    """The double mode's sign image is (sign << 79): only `slt 0` and `sgt -1` read the sign
    alone; every other compare depends on the other 79 bits, which the image does not hold."""
    msg = refused(source.format(cmp=cmp))
    assert f"`%c = {cmp}` reads the 80-bit image of a value beyond its sign" in msg


@pytest.mark.parametrize("source", [SIGN_LOAD, SIGN_CAST], ids=["load", "bitcast"])
@pytest.mark.parametrize("cmp", ["icmp slt i80 %w, 0", "icmp sgt i80 %w, -1"])
def test_double_mode_reads_the_two_sign_compares_from_the_double(source, cmp):
    lines, st = dbl(source.format(cmp=cmp))
    body = fn(lines)
    assert f"  %c = {cmp}" in body
    shl = [l for l in body if l.startswith("  %w = ")]
    assert len(shl) == 1 and re.fullmatch(r"  %w = shl i80 %sa2d\.s\d+\.z, 79", shl[0])
    assert st["sign_reads"] == 1


def test_double_mode_reads_the_sign_bit_of_the_image_by_its_shift():
    lines, st = dbl("""define i8 @f(float %x) {
  %e = fpext float %x to x86_fp80
  %b = bitcast x86_fp80 %e to i80
  %sg = lshr i80 %b, 79
  %t = trunc i80 %sg to i8
  ret i8 %t
}
""")
    body = fn(lines)
    assert any(re.fullmatch(r"  %sa2d\.s\d+\.b = bitcast double %e to i64", l) for l in body)
    assert "  %sg = lshr i80 %b, 79" in body and "  %t = trunc i80 %sg to i8" in body


def test_double_mode_refuses_the_image_read_wider_than_its_sign():
    assert "reads the 80-bit image of a value beyond its sign" in refused(
        SIGN_LOAD.format(cmp="icmp slt i80 %w, 0").replace(
            "  %c = icmp slt i80 %w, 0", "  %x = trunc i80 %w to i64\n  %c = icmp slt i64 %x, 0"))


def test_double_mode_refuses_an_i80_operation_on_no_image():
    msg = refused("""define i1 @f(i64 %a) {
  %z = zext i64 %a to i80
  %c = icmp sgt i80 %z, 0
  ret i1 %c
}
""")
    assert "an i80 operation the double mode has no rule for: %z = zext i64 %a to i80" in msg


QUIET = """%struct.float80_t = type {{ [10 x i8] }}
%union.anon.11 = type {{ %struct.float80_t }}

define void @f(ptr %out) {{
  %res = alloca %struct.float80_t, align 8
  %res_nan = alloca %union.anon.11, align 8
  %c = call ptr @memcpy(ptr %res_nan, ptr %res, i64 10)
  %q = load i80, ptr %res_nan, align 8
  %qs = or i80 %q, 4611686018427387904
{tail}
  ret void
}}

declare ptr @memcpy(ptr, ptr, i64)
"""


def test_double_mode_quiets_a_nan_in_the_double_s_quiet_bit():
    """remill's FLD m80 quiets a loaded value whenever IE is set by OR-ing the 80-bit quiet bit
    into its image; the double's quiet bit is bit 51."""
    src = QUIET.format(tail="  store i80 %qs, ptr %res_nan, align 8")
    lines, st = dbl(src)
    body = fn(lines)
    assert "  %q = load i80, ptr %res_nan, align 8" in body
    assert f"  %qs = or i80 %q, {1 << 51}" in body
    assert "  store i80 %qs, ptr %res_nan, align 8" in body and st["quiet_nan"] == 1


def test_double_mode_refuses_a_quieted_image_stored_outside_the_x87_slots():
    src = QUIET.format(tail="  store i80 %qs, ptr %out, align 8")
    assert "the NaN-quieting `or` of an 80-bit image is used beyond a store into an x87 slot" \
        in refused(src)


# ------------------------------------------------------------------ the bytes of a register

FLDMEM = """%struct.float80_t = type { [10 x i8] }
%union.union_ld = type { x86_fp80 }

define i8 @fld_snan(ptr %val) {
  %retval = alloca %struct.float80_t, align 8
  %x = alloca %struct.float80_t, align 8
  %ldu = alloca %union.union_ld, align 16
  %v = load x86_fp80, ptr %val, align 16
  %z = call ptr @memset(ptr noundef nonnull %ldu, i32 noundef 0, i64 noundef 16)
  %ld2 = getelementptr inbounds %union.union_ld, ptr %ldu, i64 0, i32 0
  store x86_fp80 %v, ptr %ld2, align 16
  %c = call ptr @memcpy(ptr noundef nonnull %retval, ptr noundef nonnull %ldu, i64 noundef 10)
  %lo = load i64, ptr %retval, align 8
  %hip = getelementptr inbounds %struct.float80_t, ptr %retval, i64 0, i32 0, i64 8
  %hi = load i16, ptr %hip, align 8
  call void @llvm.lifetime.end.p0(i64 10, ptr nonnull %retval)
  %e = and i16 %hi, 32767
  %emax = icmp eq i16 %e, 32767
  %q = and i64 %lo, 4611686018427387904
  %qclear = icmp eq i64 %q, 0
  %a = select i1 %emax, i1 %qclear, i1 false
  %f = and i64 %lo, 4611686018427387903
  %fnz = icmp ne i64 %f, 0
  %b = select i1 %a, i1 %fnz, i1 false
  %int = icmp slt i64 %lo, 0
  %s = select i1 %b, i1 %int, i1 false
  store i64 %lo, ptr %x, align 8
  %xhi = getelementptr inbounds %struct.float80_t, ptr %x, i64 0, i32 0, i64 8
  store i16 %hi, ptr %xhi, align 8
  %r = zext i1 %s to i8
  ret i8 %r
}

declare ptr @memset(ptr, i32, i64)
declare ptr @memcpy(ptr, ptr, i64)
declare void @llvm.lifetime.end.p0(i64, ptr)
"""


def test_double_mode_reads_remill_s_fld_m80_nan_test_on_the_80_bit_image_of_the_double():
    """remill's FLDmem<float80_t> (folded, as in sub_007BDCA0): the image copied into a
    float80_t, read as i64 and i16 for the signalling-NaN test, and copied on. The test's reads
    get the image of the double; the copies stay copies; bytes 0-7 for the high word are read
    where the i16 is, before the slot's lifetime ends."""
    lines, st = dbl(FLDMEM)
    body = fn(lines)
    i = body.index("  %hi = load i16, ptr %hip, align 8")
    lo_img = body[body.index("  %lo = load i64, ptr %retval, align 8") + 1]
    m = re.fullmatch(r"  (%sa2d\.\d+\.lo) = call i64 @__sa2_dimg_lo\(i64 %lo\)", lo_img)
    assert m, lo_img
    lo = m.group(1)
    p, d, hi = (re.match(r"  (%\S+) = ", l).group(1) for l in body[i + 1:i + 4])
    assert body[i + 1:i + 4] == [f"  {p} = getelementptr i8, ptr %hip, i64 -8",
                                 f"  {d} = load i64, ptr {p}, align 1",
                                 f"  {hi} = call i16 @__sa2_dimg_hi(i64 {d})"]
    assert body[i + 4] == "  call void @llvm.lifetime.end.p0(i64 10, ptr nonnull %retval)"
    assert f"  %e = and i16 {hi}, 32767" in body
    assert f"  %q = and i64 {lo}, 4611686018427387904" in body
    assert f"  %f = and i64 {lo}, 4611686018427387903" in body
    assert f"  %int = icmp slt i64 {lo}, 0" in body
    assert "  store i64 %lo, ptr %x, align 8" in body and "  store i16 %hi, ptr %xhi, align 8" in body
    assert "define internal i64 @__sa2_dimg_lo(i64 %b) alwaysinline {" in lines
    assert "define internal i16 @__sa2_dimg_hi(i64 %b) alwaysinline {" in lines
    assert any(l.startswith("declare i64 @llvm.ctlz.i64(i64, i1)") for l in lines)
    assert st["image_reads"] == {"lo": 3, "hi": 1}


COPY = """%struct.float80_t = type { [10 x i8] }

define void @f(ptr %state) {
  %t = alloca %struct.float80_t, align 8
  %s0 = getelementptr inbounds %struct.State, ptr %state, i64 0, i32 0, i32 7, i32 0, i64 0, i32 1
  %s0h = getelementptr inbounds %struct.State, ptr %state, i64 0, i32 0, i32 7, i32 0, i64 0, i32 1, i32 0, i64 8
  %lo = load i64, ptr %s0, align 2
  %hi = load i16, ptr %s0h, align 2
  store i64 %lo, ptr %t, align 8
  %th = getelementptr inbounds %struct.float80_t, ptr %t, i64 0, i32 0, i64 8
  store i16 %hi, ptr %th, align 8
  BODY
  ret void
}
"""


def test_double_mode_leaves_a_register_copied_through_its_pieces_alone():
    text = HEAD + COPY.replace("  BODY\n", "")
    out, st = D.legalize(text)
    assert fn(out.split("\n")) == fn(text.split("\n"))
    assert st["image_reads"] == {"lo": 0, "hi": 0} and "__sa2_dimg" not in out


def test_double_mode_gives_a_merge_of_bytes_0_7_its_image_where_it_is_read():
    text = """%struct.float80_t = type { [10 x i8] }

define i1 @f(ptr %state, i1 %c) {
entry:
  %a = alloca %struct.float80_t, align 8
  %b = alloca %struct.float80_t, align 8
  br i1 %c, label %l, label %r
l:
  %la = load i64, ptr %a, align 8
  br label %j
r:
  %lb = load i64, ptr %b, align 8
  br label %j
j:
  %m = phi i64 [ %la, %l ], [ %lb, %r ]
  %int = icmp slt i64 %m, 0
  ret i1 %int
}
"""
    body = fn(dbl(text)[0])
    k = body.index("  %m = phi i64 [ %la, %l ], [ %lb, %r ]")
    img = re.fullmatch(r"  (%sa2d\.\d+\.lo) = call i64 @__sa2_dimg_lo\(i64 %m\)", body[k + 1])
    assert img and body[k + 2] == f"  %int = icmp slt i64 {img.group(1)}, 0"


@pytest.mark.parametrize("body,why", [
    # a merge of high words: their image needs bytes 0-7 of each slot, which a phi has not
    ("""  %h2 = load i16, ptr %th, align 8
  %h = select i1 true, i16 %hi, i16 %h2
  %e = and i16 %h, 32767""", "reads bytes 8 (i16) of an x87 slot"),
    # an integer computed into a register: its bytes are not the double's
    ("""  %y = add i64 %lo, 1
  store i64 %y, ptr %t, align 8""", "an integer store into an x87 register or temporary"),
    # a register's bytes stored outside the x87 slots
    ("  store i64 %lo, ptr %state, align 8", "an x87 slot's bytes are stored outside the x87 slots"),
    # another width
    ("""  %w = load i32, ptr %t, align 8
  %e = and i32 %w, 255""", "reads bytes 0 (i32) of an x87 slot"),
    # bytes 0-7 placed at bytes 8-9's offset
    ("""  %lo16 = trunc i64 %lo to i16
  store i16 %lo16, ptr %th, align 8""", "an integer store into an x87 register or temporary"),
    # a use of the bytes that is not an integer operation
    ("  call void @g(i64 %lo)", "uses an x87 slot's bytes in a form the double mode has no rule for"),
    # bytes mixed with another value
    ("""  %mx = select i1 true, i64 %lo, i64 7
  %e = and i64 %mx, 1""", "a phi or select mixes an x87 slot's bytes with other values"),
    # part of a slot copied
    ("  %cp = call ptr @memcpy(ptr %t, ptr %s0, i64 8)", "a memory call over part of an x87 slot"),
    ("  %ms = call ptr @memset(ptr %t, i32 255, i64 16)", "filling one with other than zero"),
    ("  %cp = call ptr @memcpy(ptr %state, ptr %t, i64 10)", "between one and other memory"),
])
def test_double_mode_refuses_by_name_what_it_cannot_give_the_image_of(body, why):
    text = COPY.replace("  BODY", body) + "declare void @g(i64)\ndeclare ptr @memcpy(ptr, ptr, i64)\n" \
        "declare ptr @memset(ptr, i32, i64)\n"
    assert why in refused(text)


def test_double_mode_lets_remill_s_undefined_register_and_zero_into_a_slot():
    text = COPY.replace("  BODY", """  %u = call { i64, i16 } @__remill_undefined_f80()
  %u0 = extractvalue { i64, i16 } %u, 0
  %u1 = extractvalue { i64, i16 } %u, 1
  store i64 %u0, ptr %s0, align 2
  store i16 %u1, ptr %s0h, align 2
  store i64 0, ptr %t, align 8""") + "declare { i64, i16 } @__remill_undefined_f80()\n"
    out, _ = D.legalize(HEAD + text)
    assert "  store i64 %u0, ptr %s0, align 2" in out


@pytest.mark.parametrize("va,hi_reads", [("007BDCA0", 3), ("007BDAFD", 1), ("00828C2D", 4),
                                         ("008290BD", 2)])
def test_double_mode_on_real_folded_modules(va, hi_reads):
    """remill's FLD m80 (sub_007BDCA0, the reviewer's example), FRNDINT on ST0 with a phi of its
    result (sub_007BDAFD), an FMUL's precision-flag test of the low bits beside FLD m80's
    (sub_00828C2D), and register reads that are also merged (sub_008290BD): every high word the
    NaN test masks is the image's, and every low word it or the flag test masks is."""
    path = FOLD / f"sub_{va}.pc24.ll"
    if not path.is_file():
        pytest.skip(f"{path} is not on this machine")
    out, st = D.legalize(path.read_text())
    lines = out.split("\n")
    defs = {m.group(1): l for l in lines if (m := re.match(r"^\s+(%\S+) = ", l))}

    def made_by(name: str, helper: str) -> bool:
        return f"call i{'16' if helper == 'hi' else '64'} @__sa2_dimg_{helper}(" in defs.get(name, "")
    masks16 = re.findall(r"= and i16 (%\S+), 32767$", out, re.M)
    assert len(masks16) == st["image_reads"]["hi"] == hi_reads
    assert all(made_by(n, "hi") for n in masks16)
    for pat in (r"= and i64 (%\S+), 4611686018427387904$", r"= and i64 (%\S+), 4611686018427387903$",
                r"= and i64 (%\S+), 524287$"):
        assert all(made_by(n, "lo") for n in re.findall(pat, out, re.M))
    assert st["image_reads"]["lo"] > 0


# ------------------------------------------------------------------ against the hardware

def _zig(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([str(D.ZIG), *args], capture_output=True, text=True, cwd=cwd)


def _runtime_object(monkeypatch, tmp_path: Path) -> Path:
    """runtime/remill_runtime.cpp compiled by tools/s4/link_s4.py `phase_compile`'s own command
    (read from the phase, as test_state_local.py reads it), with each function in its own section
    so a driver links only what it calls."""
    monkeypatch.syspath_prepend(str(ROOT / "tools" / "s4"))
    import link_s4 as LS
    ran = []
    monkeypatch.setattr(LS, "OUT", tmp_path / "s4" / "link")
    table = tmp_path / "s3" / "link" / "tag" / "function_table_whole.c"
    table.parent.mkdir(parents=True)
    table.write_text("")
    monkeypatch.setattr(LS.L, "run", lambda cmd, **kw: ran.append(cmd) or {"rc": 0, "stderr_tail": ""})
    LS.phase_compile({}, "f80-double", "tag", tmp_path / "main.c", True)
    rt = str(ROOT / "runtime" / "remill_runtime.cpp")
    cmd = next(c for c in ran if c[-3:-1] == [rt, "-o"])
    obj = tmp_path / "rt.o"
    p = subprocess.run(cmd[:-3] + ["-ffunction-sections", "-fdata-sections", rt, "-o", str(obj)],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr[-2000:]
    return obj


DBL_DRIVER = r"""
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
long double __sa2_pc_round(long double v, uint16_t cw);
long double __sa2_x87_round_int(long double v, uint16_t cw);
int16_t __sa2_fistp_i16(long double v, uint16_t cw);
int32_t __sa2_fistp_i32(long double v, uint16_t cw);
int64_t __sa2_fistp_i64(long double v, uint16_t cw);
double sa2_df80_pc_round_slow(double v, uint16_t cw);
double sa2_df80_round_int(double v, uint16_t cw);
int16_t sa2_df80_fistp_i16(double v, uint16_t cw);
int32_t sa2_df80_fistp_i32(double v, uint16_t cw);
int64_t sa2_df80_fistp_i64(double v, uint16_t cw);
static uint64_t s = 88172645463325252ull;
static uint64_t rnd(void) { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return s; }
int main(void) {
    static const double fixed[] = {0.0, -0.0, 0.5, -0.5, 1.5, 2.5, -2.5, 1e-40, -3e-39, 1e-310,
        3.4028235677973366e38, 3.4028236e38, 1e300, 2147483647.4, 2147483647.5, -2147483648.5,
        32767.5, -32768.5, 9.3e18, -9.3e18, 1.0 / 3.0, 16777217.0, 0.1};
    long bad = 0, n = 0;
    for (int i = 0; i < 400000; i++) {
        double v;
        if (i < (int)(sizeof fixed / sizeof fixed[0])) v = fixed[i];
        else { uint64_t b = rnd(); int k = (int)(rnd() % 4);
               if (k == 0) b = (b & 0x800FFFFFFFFFFFFFull) | ((uint64_t)(1023 + (int)(rnd() % 80) - 40) << 52);
               memcpy(&v, &b, 8); if (v != v) continue; }
        for (unsigned rc = 0; rc < 4; rc++) for (unsigned pc = 0; pc < 4; pc++) {
            uint16_t cw = (uint16_t)(0x7F | (pc << 8) | (rc << 10));
            long double want = __sa2_pc_round((long double)v, cw);
            double got = sa2_df80_pc_round_slow(v, cw);
            /* a PC24 result outside double's range is the x87's alone */
            if (pc == 0 && !((double)want == (long double)want)) continue;
            n++;
            if ((long double)got != want || (got == 0 && signbit(got) != signbit(want))) {
                if (bad++ < 5) fprintf(stderr, "pc %a cw %x: %a vs %La\n", v, cw, got, want); }
            if ((long double)sa2_df80_round_int(v, cw) != __sa2_x87_round_int((long double)v, cw)) {
                if (bad++ < 5) fprintf(stderr, "rndint %a cw %x\n", v, cw); }
            if (sa2_df80_fistp_i16(v, cw) != __sa2_fistp_i16((long double)v, cw) ||
                sa2_df80_fistp_i32(v, cw) != __sa2_fistp_i32((long double)v, cw) ||
                sa2_df80_fistp_i64(v, cw) != __sa2_fistp_i64((long double)v, cw)) {
                if (bad++ < 5) fprintf(stderr, "fist %a cw %x\n", v, cw); }
        }
    }
    printf("%ld %ld\n", n, bad);
    return 0;
}
"""


@needs_x87
def test_double_mode_shims_round_as_the_x87_helpers_do_on_doubles(tmp_path):
    """The double mode's precision-control slow path, FRNDINT and FIST against the exact mode's
    own helpers (tools/spike/pc24pass/pc_round.c, tools/s2/patch/x87_int.c) on this host's x87,
    for every control word, over fixed edge values and random doubles: on a double operand the
    two must agree exactly wherever the result is a double."""
    drv = tmp_path / "drv.c"
    drv.write_text(DBL_DRIVER)
    exe = tmp_path / "drv"
    p = _zig("cc", "-target", "x86_64-linux-musl", "-O1", "-w", str(drv), str(D.SHIMS_C),
             str(ROOT / "tools/spike/pc24pass/pc_round.c"), str(ROOT / "tools/s2/patch/x87_int.c"),
             "-lm", "-o", str(exe))
    assert p.returncode == 0, p.stderr[-2000:]
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
    n, bad = map(int, r.stdout.split())
    assert n > 1_000_000 and bad == 0, r.stderr[-2000:]


@needs_zig
def test_double_mode_shims_define_every_callee_the_pass_declares(tmp_path):
    obj = tmp_path / "f80_shims.o"
    p = _zig("cc", "-target", "aarch64-linux-musl", "-O2", "-w", "-c", str(D.SHIMS_C), "-o", str(obj))
    assert p.returncode == 0, p.stderr[-2000:]
    names = D.shim_names(obj)
    want = {n for n in D.DBL_DECLS if n.startswith("sa2_df80_")}
    assert want <= names
    assert {"sa2_lf80_add", "sa2_x87_fesetround"} <= names     # the exact shims, included
    nm = subprocess.run(["nm", "--defined-only", str(obj)], capture_output=True, text=True).stdout
    assert " sa2_f80_state_double" in nm


PC_DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
double sa2_test_pc_round(double v, uint16_t cw);     /* the IR helper, exported */
double sa2_df80_pc_round_slow(double v, uint16_t cw);
static uint64_t s = 0x9E3779B97F4A7C15ull;
static uint64_t rnd(void) { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return s; }
int main(void) {
    long n = 0, bad = 0;
    for (int i = 0; i < 300000; i++) {
        uint64_t b = rnd();
        switch (i % 5) {
        case 0: b &= 0x800000003FFFFFFFull | (0x3FFull << 52); break;   /* ties and near-ties */
        case 1: b |= 0x1FFFFFFFull; break;                             /* carries */
        case 2: b &= 0x800FFFFFFFFFFFFFull; break;                     /* double denormals */
        case 3: b = (b & 0x800FFFFFFFFFFFFFull) | (0x7FEull << 52); break;   /* the top */
        default: break;
        }
        if (i < 6) b = (uint64_t[]){0, 0x8000000000000000ull, 0x7FF0000000000000ull,
                                    0xFFF8000000000001ull, 0x7FF0000000000001ull, 0x3FF0000010000000ull}[i];
        double v; memcpy(&v, &b, 8);
        for (unsigned rc = 0; rc < 4; rc++) for (unsigned pc = 0; pc < 4; pc++) {
            uint16_t cw = (uint16_t)(0x7F | (pc << 8) | (rc << 10));
            double g = sa2_test_pc_round(v, cw), w = sa2_df80_pc_round_slow(v, cw);
            uint64_t gb, wb; memcpy(&gb, &g, 8); memcpy(&wb, &w, 8);
            n++;
            if (gb != wb && !(g != g && w != w)) { if (bad++ < 5) fprintf(stderr, "%a cw %x: %a vs %a\n", v, cw, g, w); }
        }
    }
    printf("%ld %ld\n", n, bad);
    return 0;
}
"""


@needs_zig
def test_double_mode_inline_pc_round_is_the_slow_path_s_function(tmp_path):
    """The double mode's inline PC24 rounding (DBL_HELPERS, compiled as the pass emits it) gives
    the bytes of sa2_df80_pc_round_slow, the significand rounding the x87 helpers were held to
    above, for every control word, on ties, carries, double denormals, the top of the range,
    infinities and NaNs."""
    helper = "\n".join(D.DBL_HELPERS[D.PC_HELPER])
    ll = tmp_path / "h.ll"
    ll.write_text("\n".join([
        helper,
        "define double @sa2_test_pc_round(double %v, i16 %cw) {",
        "  %r = call double @__sa2_pc_round(double %v, i16 %cw)",
        "  ret double %r",
        "}",
        "declare double @sa2_df80_pc_round_slow(double, i16)", ""]))
    drv = tmp_path / "d.c"
    drv.write_text(PC_DRIVER)
    exe = tmp_path / "d"
    p = _zig("cc", "-target", "x86_64-linux-musl", "-O2", "-w", str(ll), str(drv), str(D.SHIMS_C),
             "-lm", "-o", str(exe))
    assert p.returncode == 0, p.stderr[-2000:]
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=300)
    n, bad = map(int, r.stdout.split())
    assert n == 300000 * 16 and bad == 0, r.stderr[-2000:]


IMG_DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
uint64_t t_lo(uint64_t b);
uint16_t t_hi(uint64_t b);
static uint64_t s = 0x2545F4914F6CDD1Dull;
static uint64_t rnd(void) { s ^= s << 13; s ^= s >> 7; s ^= s << 17; return s; }
int main(void) {
    long n = 0, bad = 0, snan = 0;
    for (int i = 0; i < 600000; i++) {
        uint64_t b = rnd();
        switch (i % 6) {
        case 0: b &= 0x800FFFFFFFFFFFFFull; break;                          /* denormal, zero */
        case 1: b = (b & 0x800FFFFFFFFFFFFFull) | 0x7FF0000000000000ull; break;   /* inf, NaN */
        case 2: b = (b & 0x8007FFFFFFFFFFFFull) | 0x7FF0000000000000ull; break;   /* SNaN, inf */
        case 3: b &= 0x800000000000000Full; break;                          /* tiny denormals */
        default: break;
        }
        if (i < 4) b = (uint64_t[]){0, 0x8000000000000000ull, 0x7FF0000000000000ull, 1}[i];
        double d; memcpy(&d, &b, 8);
        volatile double vd = d;
        long double x = vd;                         /* this host's x87: fld m64 */
        uint64_t m; uint16_t se;
        memcpy(&m, &x, 8); memcpy(&se, (char *)&x + 8, 2);
        int nan = (b & 0x7FF0000000000000ull) == 0x7FF0000000000000ull && (b & 0xFFFFFFFFFFFFFull);
        if (nan && !(b & (1ull << 51))) { m &= ~(1ull << 62); snan++; }   /* kept signalling */
        n++;
        if (t_lo(b) != m || t_hi(b) != se) {
            if (bad++ < 5) fprintf(stderr, "%016llx: %016llx %04x vs %016llx %04x\n",
                                   (unsigned long long)b, (unsigned long long)t_lo(b), t_hi(b),
                                   (unsigned long long)m, se);
        }
    }
    printf("%ld %ld %ld\n", n, bad, snan);
    return 0;
}
"""


@needs_x87
def test_the_80_bit_image_of_a_double_is_this_x87_s_widening_with_the_quiet_bit_kept(tmp_path):
    """`__sa2_dimg_lo` and `__sa2_dimg_hi`, compiled as the pass emits them, against this host's
    x87 widening a double (FLD m64), on zeros, denormals, normals, infinities and NaNs: the same
    ten bytes, except that a signalling NaN keeps its quiet bit clear, where FLD m64 sets it."""
    ll = tmp_path / "img.ll"
    ll.write_text("\n".join(D.IMG_HELPERS["__sa2_dimg_lo"] + D.IMG_HELPERS["__sa2_dimg_hi"] + [
        "define i64 @t_lo(i64 %b) {", "  %r = call i64 @__sa2_dimg_lo(i64 %b)", "  ret i64 %r", "}",
        "define i16 @t_hi(i64 %b) {", "  %r = call i16 @__sa2_dimg_hi(i64 %b)", "  ret i16 %r", "}",
        "declare i64 @llvm.ctlz.i64(i64, i1)", ""]))
    drv = tmp_path / "img.c"
    drv.write_text(IMG_DRIVER)
    exe = tmp_path / "img"
    p = _zig("cc", "-target", "x86_64-linux-musl", "-O2", "-w", str(ll), str(drv), "-o", str(exe))
    assert p.returncode == 0, p.stderr[-2000:]
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
    n, bad, snan = map(int, r.stdout.split())
    assert n == 600000 and snan > 10000 and bad == 0, r.stderr[-2000:]


def f80(sign: int, exp: int, mant: int) -> bytes:
    return struct.pack("<QH", mant, (sign << 15) | exp)


# 80-bit images for the FLD m80 test: signalling NaNs (payload high, low, only below bit 11),
# quiet NaNs, infinities, a pseudo-NaN and a pseudo-infinity (integer bit clear), normals,
# 80-bit denormals and zeros.
M80 = [f80(0, 0x7FFF, (1 << 63) | (0x1234 << 40)), f80(1, 0x7FFF, (1 << 63) | 1),
       f80(0, 0x7FFF, (1 << 63) | 0x7FF), f80(0, 0x7FFF, (1 << 63) | (1 << 61)),
       f80(1, 0x7FFF, (3 << 62)), f80(0, 0x7FFF, (3 << 62) | 5), f80(0, 0x7FFF, 1 << 63),
       f80(1, 0x7FFF, 1 << 63), f80(0, 0x7FFF, (1 << 62) | 1), f80(0, 0x7FFF, 0),
       f80(0, 0x3FFF, 1 << 63), f80(1, 0x4000, 0xC90FDAA22168C235), f80(0, 1, 1 << 63),
       f80(0, 0, 1 << 40), f80(0, 0, 0), f80(1, 0, 0), f80(0, 0x43FE, (1 << 64) - 1)]

FLD_DRIVER = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "cpu_state.h"
sa2_f80 sa2_rt_get_st80(const void *state, int i);
void sa2_rt_set_st80(void *state, int i, sa2_f80 v);
const int sa2_f80_state_double = 1;                /* the double mode's marker */
unsigned char fld_snan_native(const void *val);    /* the x86-64 build's FLD m80 test */
unsigned char fld_snan(const void *val);           /* the double mode's */
static unsigned char st[1 << 16] __attribute__((aligned(16)));
static const unsigned char m80[][10] = { M80 };
int main(void) {
    sa2_f80 one; memset(&one, 0, sizeof one);
    const uint64_t im = 1ull << 63; const uint16_t ie = 0x3FFF;
    memcpy(one.b, &im, 8); memcpy(one.b + 8, &ie, 2);
    sa2_rt_set_st80(st, 5, one);
    const uint64_t d1 = 0x3FF0000000000000ull;
    long off = -1, hits = 0;
    for (long k = 0; k + 8 <= (long)sizeof st; k++) if (!memcmp(st + k, &d1, 8)) { off = k; hits++; }
    if (hits != 1) { printf("slot %ld %ld\n", off, hits); return 1; }
    for (unsigned i = 0; i < sizeof m80 / sizeof m80[0]; i++) {
        sa2_f80 v; memset(&v, 0, sizeof v); memcpy(v.b, m80[i], 10);
        uint64_t m; uint16_t se; memcpy(&m, v.b, 8); memcpy(&se, v.b + 8, 2);
        int want = (se & 0x7FFF) == 0x7FFF && (m >> 63) && !(m & (1ull << 62)) && (m & ((1ull << 62) - 1));
        unsigned char raw[16] __attribute__((aligned(16))) = {0};
        memcpy(raw, m80[i], 10);
        sa2_rt_set_st80(st, 5, v);                 /* the runtime's FLD m80 conversion */
        unsigned char slot[16] __attribute__((aligned(16))) = {0};
        memcpy(slot, st + off, 8);
        sa2_f80 back = sa2_rt_get_st80(st, 5);
        uint64_t bm; memcpy(&bm, back.b, 8);
        int back_snan = want ? !(bm & (1ull << 62)) && (bm & ((1ull << 62) - 1)) : 1;
        printf("%u %d %d %d %d\n", i, want, fld_snan_native(raw), fld_snan(slot), back_snan);
    }
    return 0;
}
"""


@needs_x87
def test_fld_m80_s_signalling_nan_test_is_the_x86_build_s_in_the_double_mode(monkeypatch, tmp_path):
    """remill's FLD m80 test, run three ways over the table of 80-bit images: the x86-64 build's
    (the module as folded, on this host's x87), the double mode's (the pass's output, its slot
    filled by the runtime's conversion, runtime/remill_runtime.cpp's register accessors linked in
    with the mode's marker) and the predicate itself. All three must agree on every image, and a
    signalling NaN must come back out of the runtime's slot still signalling."""
    rt = _runtime_object(monkeypatch, tmp_path)
    native = tmp_path / "native.ll"
    native.write_text(FLDMEM.replace("@fld_snan(", "@fld_snan_native("))
    double = tmp_path / "double.ll"
    double.write_text(D.legalize(FLDMEM)[0])
    table = ", ".join("{" + ", ".join(str(b) for b in img) + "}" for img in M80)
    drv = tmp_path / "fld.c"
    drv.write_text(FLD_DRIVER.replace("{ M80 }", "{" + table + "}"))
    exe = tmp_path / "fld"
    p = _zig("c++", "-target", "x86_64-linux-musl", "-O1", "-w", "-x", "c", str(drv), "-x", "none",
             "-I", str(ROOT / "runtime"), str(native), str(double), str(rt), "-Wl,--gc-sections",
             "-o", str(exe))
    assert p.returncode == 0, p.stderr[-3000:]
    r = subprocess.run([str(exe)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stdout + r.stderr
    rows = [list(map(int, l.split())) for l in r.stdout.splitlines()]
    assert len(rows) == len(M80)
    assert sum(row[1] for row in rows) == 4                     # the table's signalling NaNs
    for i, want, nat, dbl_, back in rows:
        assert nat == want and dbl_ == want and back == 1, (i, want, nat, dbl_, back)
