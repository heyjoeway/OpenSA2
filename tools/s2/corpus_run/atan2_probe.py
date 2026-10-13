#!/usr/bin/env python3
"""Instrument the CRT transcendental dispatcher and read out how it picks the atan2 tail.

`vector-to-angles-v1` and `stairs-transform-v1` diverge on exactly the rows whose
second operand is zero, by exactly a quarter turn. The tail is not chosen by a
transcendental's last bits: it is chosen by a *table jump*, at 0x0082C0C1, whose
index the CRT builds out of two `fxam` classifications. This tool proves what the
lifted code computes there, against what this host's x87 computes for the same two
operands.

What it does, in one command:

1. rewrites the folded IR of `sub_82c037` (the block lifted at 0x0082C037) to call
   `tools/s2/corpus_run/trace_hook.c` after each `fnstsw m16` and at the
   `jmp dword ptr [ebx]`;
2. compiles that one module and links a `corpus_driver_trace` beside the shipped
   `corpus_driver_pc24` (every other object is reused, byte for byte);
3. drives the chosen fixture rows through it and parses the trace;
4. runs `tools/s2/corpus_run/atan2_oracle.c` -- this host's x87, same control word,
   same instruction sequence -- on the same operands;
5. applies the *image's own* xlat table (0x00922EFC) and atan2 descriptor table
   (0x00938B80) to both status-word pairs, and prints the tail address each picks.

With `--operands` it drives, instead of fixture rows, one synthetic vector-to-angles row
per pair of operand classes FXAM reports -- +-0, +-inf, +-NaN, +-denormal and +-normal as
float32 vector components, each as atan2's first operand (st(1)) against each as its
second (st(0)) -- one driver run per row, and holds the lifted C1 bits, tail, atan2
result and second angle to this host's x87 on the same operands (the oracle's FPATAN
and the helper's own tail after it). `--fxam-unfixed` puts remill's FXAM back (C1 = 0 on
a zero, NaN or infinity) in the traced dispatcher, for contrast.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s2/corpus_run/atan2_probe.py [--fixture ID] [--rows N ...]
  .venv/bin/python tools/s2/corpus_run/atan2_probe.py --operands [--fxam-unfixed]
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tools" / "spike"))
import fixture_rows as FR   # noqa: E402
import plans as P           # noqa: E402
import spikelib as S        # noqa: E402

GEN = ROOT / "private" / "generated"
OUT = GEN / "s2" / "corpus_run"
FOLD = OUT / "fold"
BUILD = OUT / "build"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
TARGET = ["-target", "x86_64-linux-musl"]

DISPATCHER_VA = 0x0082C037          # __trandisp2's classification block
XLAT_TABLE = 0x00922EFC             # `mov ebx,922EFCh` at 0x0082C05F
ATAN2_DESC = 0x00938B70             # `mov edx,938B70h` at 0x00859B6A
DESC_TABLE = ATAN2_DESC + 0x10      # `add ebx,10h` at 0x0082C0BE

TAIL_NAMES = {
    0x0085A34C: "general fpatan (0x0085A34C)",
    0x0085A3A5: "zero/pi tail (0x0085A3A5: fldz or fldpi)",
    0x0085A3C8: "pi/2 tail (0x0085A3C8 -> 0x0085A39C fld tbyte [0x00922EEA])",
    0x0082C10D: "NaN tail (0x0082C10D)",
    0x0082C10F: "NaN tail (0x0082C10F)",
    0x0082C137: "inf/inf tail (0x0082C137)",
    0x0082C176: "inf tail (0x0082C176)",
}

DECL = """
declare void @__sa2_trace_sw(i32, i16, i64, i16)
declare void @__sa2_trace_disp(i32, i32, i32, i32, i64, i16, i64, i16)
declare void @__sa2_trace_st(ptr, i32, i32)
"""

# The whole tail chain the pi/2 case walks, in the order the guest walks it.
CHAIN = [0x0085A3C8, 0x0085A39C, 0x0082C193, 0x00859E50, 0x00859F1A, 0x00459260,
         0x0085A3A5, 0x0085A34C]   # and the zero/pi and general tails

# Where 0x00459260 resumes after _CIatan2 returns: the trace at 0x00859e50's return
# terminator carries the atan2 result the helper goes on to scale (0x00459327).
ATAN2_RETURN = (0x00859E50, 0x00459327)
VEC_ANGLES = 0x00459260
ANGLE_MUL = 0x00905658              # `fmul qword [905658h]` at 0x0045932F

# The FXAM classes, as a float32 vector component. A float32 denormal is a normal number
# once `fld dword` widens it to 80 bits, so it classifies as normal on both sides; the
# 80-bit denormal class is held by tools/spike/pc24pass/test_pc24pass.py instead.
OPERANDS = {"+0": 0x00000000, "-0": 0x80000000, "+inf": 0x7F800000, "-inf": 0xFF800000,
            "+nan": 0x7FC00000, "-nan": 0xFFC00000, "+denormal": 0x00000001,
            "-denormal": 0x80000001, "+normal": 0x3F800000, "-normal": 0xBF800000}
Z_HALF = 0x3F000000                 # z = 0.5 keeps 0x00459260 off its vertical branch

# Put remill's FXAM back: the pass's C1 stores on the zero, NaN and infinity arms become
# the constant 0 remill wrote.
RE_FXAM_FIXED = re.compile(r"^(\s+store i8) %[^,]+(, ptr [^,]+, align \d+)   ; sa2-pc24pass fxam c1$", re.M)


def revert_fxam_fix(text: str) -> tuple[str, int]:
    return RE_FXAM_FIXED.subn(r"\1 0\2", text)

# Undo pc24pass's f80-accessor repair, so the same command can show the defect and the
# fix from one build tree. `--unfixed` puts remill's `readnone` back on the out-pointer
# accessors of every module the probe compiles, and nothing else.
RE_F80_DECL_FIXED = re.compile(r"^(declare .*\)) #(\d+)   ; sa2-pc24pass f80$", re.M)
RE_F80_GROUP = re.compile(r"^attributes #(\d+) = \{ (.*) \}   ; sa2-pc24pass f80 \(was #(\d+)\)$", re.M)


def revert_f80_fix(text: str) -> tuple[str, int]:
    was = {m.group(1): m.group(3) for m in RE_F80_GROUP.finditer(text)}
    if not was:
        return text, 0
    n = [0]

    def back(m):
        old = was.get(m.group(2))
        if old is None:
            return m.group(0)
        n[0] += 1
        return f"{m.group(1)} #{old}"
    text = RE_F80_DECL_FIXED.sub(back, text)
    text = RE_F80_GROUP.sub("", text)
    return text, n[0]

DEFINE_RE = re.compile(r"^define ptr @sub_[0-9a-f.]+\(ptr noalias %state, i32 %program_counter, "
                       r"ptr noalias %memory\) \{$")
TERM_RE = re.compile(r"^(\s*)%\S+ = tail call ptr @__remill_(?:jump|function_return|function_call|"
                     r"missing_block|error|async_hyper_call)\(ptr %state, i32 (%\S+), ptr %\S+\)$")

SW_RE = re.compile(
    r"^(\s*)(%\S+ = call ptr @__remill_write_memory_16\(ptr noundef %\S+, i32 noundef %\S+, "
    r"i16 noundef zeroext (%bf\.set63[^)]*)\))( #\d+)?$", re.M)
JUMP_RE = re.compile(r"^(\s*)(%\S+ = tail call ptr @__remill_jump\(ptr %state, i32 (%\S+), ptr %\S+\))$", re.M)


# ---------------------------------------------------------------- image reads
def image_reader():
    import pefile
    pe = pefile.PE(str(EXE), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    secs = [(base + s.VirtualAddress,
             base + s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData), s)
            for s in pe.sections]

    def read(va: int, n: int) -> bytes:
        for lo, hi, s in secs:
            if lo <= va < hi:
                return s.get_data()[va - lo:va - lo + n]
        raise SystemExit(f"{va:#x} is not in a mapped section")
    return read


def classify(sw: int, xlat: bytes) -> dict:
    """The dispatcher's own transform, 0x0082C093..0x0082C09D, on one status word."""
    ah = (sw >> 8) & 0xFF
    t = (ah << 1) & 0xFF                                  # shl ah,1
    u = ((t >> 1) | 0x80) & 0xFF if t & 0x80 else t >> 1  # sar ah,1
    v = ((u << 1) | (u >> 7)) & 0xFF                      # rol ah,1
    nib = v & 0x0F                                        # and al,0Fh
    return {"sw": f"0x{sw:04x}", "ah": f"0x{ah:02x}",
            "c0": (ah >> 0) & 1, "c1": (ah >> 1) & 1, "c2": (ah >> 2) & 1, "c3": (ah >> 6) & 1,
            "nibble": nib, "class": xlat[nib],
            "fxam_class": FXAM_CLASS.get(((ah >> 6) & 1, (ah >> 2) & 1, (ah >> 0) & 1), "?")}


FXAM_CLASS = {(0, 0, 0): "unsupported", (0, 0, 1): "nan", (0, 1, 0): "normal", (0, 1, 1): "infinity",
              (1, 0, 0): "zero", (1, 0, 1): "empty", (1, 1, 0): "denormal", (1, 1, 1): "empty"}


def tail_for(sw_st0: int, sw_st1: int, xlat: bytes, desc: list[int]) -> dict:
    a = classify(sw_st0, xlat)
    b = classify(sw_st1, xlat)
    off = (a["class"] | (b["class"] << 2)) & 0xFF
    target = desc[off // 4]
    return {"st0": a, "st1": b, "table_offset": off, "index": off // 4,
            "target": f"0x{target:08x}", "tail": TAIL_NAMES.get(target, "?")}


# ---------------------------------------------------------------- instrument
def instrument(text: str) -> tuple[str, dict]:
    n = [0]

    def sw_sub(m):
        i = n[0]
        n[0] += 1
        ind, call, val = m.group(1), m.group(2) + (m.group(4) or ""), m.group(3)
        return (f"{ind}{call}\n"
                f"{ind}%tr.sw{i}.lo = load i64, ptr %ST0, align 1\n"
                f"{ind}%tr.sw{i}.hp = getelementptr inbounds %struct.float80_t, ptr %ST0, i64 0, i32 0, i64 8\n"
                f"{ind}%tr.sw{i}.hi = load i16, ptr %tr.sw{i}.hp, align 1\n"
                f"{ind}call void @__sa2_trace_sw(i32 {i}, i16 {val}, i64 %tr.sw{i}.lo, i16 %tr.sw{i}.hi)")

    text, n_sw = SW_RE.subn(sw_sub, text)

    def jmp_sub(m):
        ind, call, pc = m.group(1), m.group(2), m.group(3)
        return (f"{ind}%tr.d.eax = load i32, ptr %EAX, align 4\n"
                f"{ind}%tr.d.ecx = load i32, ptr %ECX, align 4\n"
                f"{ind}%tr.d.ebx = load i32, ptr %EBX, align 4\n"
                f"{ind}%tr.d.st0lo = load i64, ptr %ST0, align 1\n"
                f"{ind}%tr.d.st0hp = getelementptr inbounds %struct.float80_t, ptr %ST0, i64 0, i32 0, i64 8\n"
                f"{ind}%tr.d.st0hi = load i16, ptr %tr.d.st0hp, align 1\n"
                f"{ind}%tr.d.st1lo = load i64, ptr %ST1, align 1\n"
                f"{ind}%tr.d.st1hp = getelementptr inbounds %struct.float80_t, ptr %ST1, i64 0, i32 0, i64 8\n"
                f"{ind}%tr.d.st1hi = load i16, ptr %tr.d.st1hp, align 1\n"
                f"{ind}call void @__sa2_trace_disp(i32 {pc}, i32 %tr.d.ebx, i32 %tr.d.eax, i32 %tr.d.ecx, "
                f"i64 %tr.d.st0lo, i16 %tr.d.st0hi, i64 %tr.d.st1lo, i16 %tr.d.st1hi)\n"
                f"{ind}{call}")

    text, n_jmp = JUMP_RE.subn(jmp_sub, text)
    return text + DECL, {"fnstsw_sites_traced": n_sw, "indirect_jumps_traced": n_jmp}


def instrument_chain(text: str, va: int) -> tuple[str, int]:
    """Trace st(0)/st(1) at every define's entry and at every lifted terminator."""
    out, n, in_entry = [], 0, False
    call = f"  call void @__sa2_trace_st(ptr %state, i32 {{pc}}, i32 {va})"
    for line in text.splitlines():
        if DEFINE_RE.match(line):
            in_entry = True
            out.append(line)
            continue
        if in_entry and line.startswith("  br label %"):
            out.append(call.format(pc="%program_counter"))
            n += 1
            in_entry = False
            out.append(line)
            continue
        m = TERM_RE.match(line)
        if m:
            out.append(call.format(pc=m.group(2)))
            n += 1
        out.append(line)
    return "\n".join(out) + "\n" + DECL, n


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def build_traced(variant: str = "pc24", unfixed: bool = False,
                 fxam_unfixed: bool = False) -> tuple[Path, dict]:
    src = FOLD / f"sub_{DISPATCHER_VA:08X}.{variant}.ll"
    if not src.exists():
        raise SystemExit(f"{src} is missing; run tools/s2/corpus_run/build.py first")
    reverted = 0
    fxam_reverted = 0

    def prep(text: str) -> str:
        nonlocal reverted, fxam_reverted
        if unfixed:
            text, n = revert_f80_fix(text)
            reverted += n
        if fxam_unfixed:
            text, n = revert_fxam_fix(text)
            fxam_reverted += n
        return text

    text, stats = instrument(prep(src.read_text()))
    dst = FOLD / f"sub_{DISPATCHER_VA:08X}.trace.ll"
    dst.write_text(text)
    traced = {DISPATCHER_VA: dst}
    stats["chain_sites"] = {}
    for va in CHAIN:
        s = FOLD / f"sub_{va:08X}.{variant}.ll"
        if not s.exists():
            continue
        t, n = instrument_chain(prep(s.read_text()), va)
        d = FOLD / f"sub_{va:08X}.trace.ll"
        d.write_text(t)
        traced[va] = d
        stats["chain_sites"][f"0x{va:08x}"] = n
    if unfixed:
        # every other module the repair touched, so the "before" build is faithful
        for s in sorted(FOLD.glob(f"sub_*.{variant}.ll")):
            va = int(s.stem.split(".")[0][4:], 16)
            if va in traced or "; sa2-pc24pass f80" not in s.read_text():
                continue
            d = FOLD / f"sub_{va:08X}.trace.ll"
            d.write_text(prep(s.read_text()))
            traced[va] = d
    stats["f80_declarations_reverted"] = reverted
    stats["fxam_c1_stores_reverted"] = fxam_reverted
    stats["fxam_fix_in_build"] = "; sa2-pc24pass fxam c1" in src.read_text()
    if fxam_unfixed and not fxam_reverted:
        raise SystemExit("--fxam-unfixed reverted nothing: the dispatcher's IR carries no FXAM fix to "
                         "revert, so the contrast would be the build itself")
    stats["variant"] = ("unfixed (remill's readnone put back)" if unfixed else
                        "remill's FXAM put back (C1 = 0 on zero, NaN, infinity)" if fxam_unfixed else
                        "as built")

    objs = []
    for va, ll in traced.items():
        o = BUILD / f"ir_trace_{va:08X}.o"
        r = sh([str(S.ZIG), "cc"] + TARGET + ["-O2", "-w", "-c", str(ll), "-o", str(o)])
        if r.returncode:
            raise SystemExit(f"traced module {va:#010x} did not compile:\n{r.stderr[:3000]}")
        objs.append(o)
    hook = BUILD / "trace_hook.o"
    r = sh([str(S.ZIG), "cc"] + TARGET + ["-O2", "-w", "-c", str(HERE / "trace_hook.c"), "-o", str(hook)])
    if r.returncode:
        raise SystemExit(f"trace_hook.c did not compile:\n{r.stderr[:3000]}")
    objs.append(hook)

    replaced = {f"ir_{variant}_{va:08X}.o" for va in traced}
    keep = sorted(p for p in BUILD.glob("*.o")
                  if p.name not in replaced and not p.name.startswith("ir_trace_")
                  and p.name != "trace_hook.o")
    binary = BUILD / "corpus_driver_trace"
    r = sh([str(S.ZIG), "c++"] + TARGET + ["-O2"] + [str(p) for p in keep + objs] + ["-o", str(binary)])
    if r.returncode:
        raise SystemExit(f"traced driver did not link:\n{r.stderr[:3000]}")
    stats["objects"] = len(keep) + len(objs)
    stats["binary"] = str(binary.relative_to(ROOT))
    return binary, stats


def run_oracle(oracle: Path, pairs: list[tuple[int, int]], mul_bits: int | None = None) -> list[dict]:
    """This host's x87 on (st(1), st(0)) float32 pairs: both fxam status words, FPATAN, angle2."""
    op = sh([str(oracle)] + ([f"{mul_bits:016x}"] if mul_bits is not None else []),
            input="".join(f"{a:08x} {b:08x}\n" for a, b in pairs))
    if op.returncode:
        raise SystemExit(f"atan2_oracle failed: {op.stderr[-2000:]}")
    out = []
    for ln in op.stdout.splitlines():
        f = ln.split()
        if len(f) < 3:
            continue
        out.append({"sw0": int(f[0], 16), "sw1": int(f[1], 16), "fpatan": f[2],
                    "angle2": None if len(f) < 4 or f[3] == "-" else int(f[3], 16)})
    if len(out) != len(pairs):
        raise SystemExit(f"atan2_oracle answered {len(out)} of {len(pairs)} pairs")
    return out


def build_oracle() -> Path:
    exe = BUILD / "atan2_oracle"
    r = sh([str(S.ZIG), "cc", "-O2", "-w", str(HERE / "atan2_oracle.c"), "-o", str(exe)])
    if r.returncode:
        raise SystemExit(f"atan2_oracle.c did not compile:\n{r.stderr[:3000]}")
    return exe


TRACE_SW = re.compile(r"SA2TRACE sw site=(\d+) sw=([0-9a-f]+) st0=([0-9a-f]{4})([0-9a-f]{16})")
TRACE_DISP = re.compile(r"SA2TRACE disp target=([0-9a-f]+) table=([0-9a-f]+) eax=([0-9a-f]+) "
                        r"ecx=([0-9a-f]+) st0=([0-9a-f]{4})([0-9a-f]{16}) st1=([0-9a-f]{4})([0-9a-f]{16})")


def fp80(hi: str, lo: str) -> float:
    """Decode an 80-bit x87 value (for reporting only)."""
    h, l = int(hi, 16), int(lo, 16)
    sign = -1.0 if h & 0x8000 else 1.0
    exp = h & 0x7FFF
    if exp == 0 and l == 0:
        return sign * 0.0
    try:
        return sign * l * 2.0 ** (exp - 16383 - 63)
    except OverflowError:
        return float("inf") * sign


TRACE_ST = re.compile(r"SA2TRACE st site=([0-9a-f]+) pc=([0-9a-f]+) st0=([0-9a-f]{20}) st1=([0-9a-f]{20})")


def row_segments(lines: list[str]) -> list[list[str]]:
    """The trace split per row: each row starts where 0x00459260 is entered."""
    segs: list[list[str]] = []
    for line in lines:
        m = TRACE_ST.search(line)
        if m and int(m.group(1), 16) == VEC_ANGLES and int(m.group(2), 16) == VEC_ANGLES:
            segs.append([])
        if segs and line.startswith("SA2TRACE"):
            segs[-1].append(line)
    return segs


def lifted_of(seg: list[str], xlat: bytes, desc: list[int]) -> dict | None:
    """What one row's trace shows: the two fnstsw words, the table jump and the atan2 result."""
    sws = [TRACE_SW.search(l) for l in seg]
    sws = [m for m in sws if m]
    disp = next((m for m in (TRACE_DISP.search(l) for l in seg) if m), None)
    if disp is None:
        return None
    result = None
    for l in seg:
        m = TRACE_ST.search(l)
        if m and (int(m.group(1), 16), int(m.group(2), 16)) == ATAN2_RETURN:
            result = m.group(3)
    target = int(disp.group(1), 16)
    rec = {"fnstsw": [{"sw": f"0x{int(m.group(2), 16):04x}", "st0": f"0x{m.group(3)}{m.group(4)}",
                       "st0_value": fp80(m.group(3), m.group(4))} for m in sws],
           "eax": f"0x{disp.group(3)}", "ecx": f"0x{disp.group(4)}", "table": f"0x{disp.group(2)}",
           "target": f"0x{target:08x}", "tail": TAIL_NAMES.get(target, "?"),
           "atan2_result": None if result is None else f"0x{result}",
           "atan2_result_value": None if result is None else fp80(result[:4], result[4:])}
    if len(sws) == 2:
        rec["classification"] = tail_for(int(sws[0].group(2), 16), int(sws[1].group(2), 16), xlat, desc)
    return rec


def is_nan80(h: str) -> bool:
    v = int(h, 16)
    return (v >> 64) & 0x7FFF == 0x7FFF and (v & ((1 << 63) - 1)) != 0


def f80_to_f32(h: str) -> str:
    """An 80-bit value, as 20 hex digits, rounded to float32 (nearest, ties to even) the way
    0x00459327's `fstp dword` rounds the atan2 result: the value the helper goes on with."""
    v = int(h, 16)
    sign, exp, mant = (v >> 79) & 1, (v >> 64) & 0x7FFF, v & ((1 << 64) - 1)
    if exp == 0x7FFF:
        return "nan" if mant & ((1 << 63) - 1) else ("-inf" if sign else "+inf")
    if mant == 0:
        return f"0x{sign << 31:08x}"
    e = exp - 16383                      # value = mant * 2^(e - 63), mant's top bit set
    q, rem = mant >> 40, mant & ((1 << 40) - 1)
    if rem > 1 << 39 or (rem == 1 << 39 and q & 1):
        q += 1
        if q == 1 << 24:
            q, e = q >> 1, e + 1
    if not -126 <= e <= 127:
        return "out of float32's normal range"
    return f"0x{(sign << 31) | ((e + 127) << 23) | (q & 0x7FFFFF):08x}"


VALUE_TAILS = {0x0085A3A5, 0x0085A3C8, 0x0085A34C}   # zero/pi, pi/2 and FPATAN's own: a value
TRAP_RE = re.compile(r"first trap at row \d+: (.*)$")


def operands_mode(args, xlat: bytes, desc: list[int]) -> None:
    """One synthetic vector-to-angles row per (st(1), st(0)) pair of operand classes."""
    import concurrent.futures as cf
    read = image_reader()
    mul_bits = struct.unpack("<Q", read(ANGLE_MUL, 8))[0]
    binary, stats = build_traced(args.variant, False, args.fxam_unfixed)
    oracle = build_oracle()
    fixture = "vector-to-angles-v1"
    header, _ = FR.sections(FR.find(fixture))
    pairs = [(a, b) for a in OPERANDS for b in OPERANDS]
    host = run_oracle(oracle, [(OPERANDS[a], OPERANDS[b]) for a, b in pairs], mul_bits)
    pdir = OUT / "plans" / "fxam"
    odir = OUT / "out" / "fxam"
    pdir.mkdir(parents=True, exist_ok=True)
    odir.mkdir(parents=True, exist_ok=True)

    def drive(k: int) -> tuple[list[str], list[int] | None, int]:
        a, b = pairs[k]
        plan = P.BUILDERS[fixture]([[OPERANDS[a], OPERANDS[b], Z_HALF, 0, 0]], header)
        pf = pdir / f"row{k:03d}.plan.bin"
        of = odir / f"row{k:03d}.out.bin"
        of.unlink(missing_ok=True)
        pf.write_bytes(plan.encode())
        proc = sh([str(binary), str(EXE), str(pf), str(of)])
        words = None
        if of.exists():
            data = of.read_bytes()
            _, nr, nw = struct.unpack_from("<3I", data, 8)
            if nr == 1:
                words = list(struct.unpack_from(f"<{nw}I", data, 20))[2:]
        return proc.stderr.splitlines(), words, proc.returncode

    with cf.ThreadPoolExecutor(8) as ex:
        runs = list(ex.map(drive, range(len(pairs))))

    rows = []
    for k, ((a, b), h, (lines, words, rc)) in enumerate(zip(pairs, host, runs)):
        segs = row_segments(lines)
        lifted = lifted_of(segs[0], xlat, desc) if len(segs) == 1 else None
        native = tail_for(h["sw0"], h["sw1"], xlat, desc)
        trap = next((m.group(1) for m in (TRAP_RE.search(l) for l in lines) if m), None)
        row = {"row": k, "atan2": f"atan2({a}, {b})", "st1": a, "st0": b,
               "st1_bits": f"0x{OPERANDS[a]:08x}", "st0_bits": f"0x{OPERANDS[b]:08x}",
               "host_x87": {"classification": native, "c1_st0": native["st0"]["c1"],
                            "c1_st1": native["st1"]["c1"], "fpatan": f"0x{h['fpatan']}",
                            "fpatan_f32": f80_to_f32(h["fpatan"]),
                            "fpatan_value": None if is_nan80(h["fpatan"]) else fp80(h["fpatan"][:4], h["fpatan"][4:]),
                            "angle2": None if h["angle2"] is None else f"0x{h['angle2']:08x}"},
               "driver_rc": rc, "reached_the_dispatcher": lifted is not None, "trap": trap,
               "observed": None if words is None else [f"0x{w:08x}" for w in words]}
        if lifted is not None:
            c = lifted.get("classification")
            row["lifted"] = lifted
            row["c1_match"] = bool(c) and (c["st0"]["c1"], c["st1"]["c1"]) == (row["host_x87"]["c1_st0"],
                                                                               row["host_x87"]["c1_st1"])
            row["tail_match"] = lifted["target"] == native["target"]
            # A value tail's result is FPATAN's for the same operands (signed zeros, pi, pi/2:
            # Intel SDM vol. 2A, FPATAN's special-case table); the NaN and infinity/infinity tails
            # are the CRT's domain-error paths, which the corpus program does not hold.
            res = lifted["atan2_result"]
            if native["target"] in (f"0x{t:08x}" for t in VALUE_TAILS) and res is not None and not trap:
                row["result_f32_match"] = f80_to_f32(res[2:]) == f80_to_f32(h["fpatan"])
                row["result_bits_equal"] = res[2:] == h["fpatan"]
                row["angle2_match"] = words is not None and h["angle2"] is not None and words[1] == h["angle2"]
        rows.append(row)

    reached = [r for r in rows if r["reached_the_dispatcher"]]
    valued = [r for r in reached if "result_f32_match" in r]
    # What the value-tail counts are out of: every pair whose *host* tail returns a value,
    # reached or not. 0x00459260 answers the pairs of two (float32) zeros or denormals before it
    # calls atan2, so those have no lifted row at all: they are the host's value-tail pairs the
    # panel does not compare, not rows that matched.
    host_valued = [r for r in rows
                   if int(r["host_x87"]["classification"]["target"], 16) in VALUE_TAILS]
    summary = {"rows": len(rows), "reached_the_dispatcher": len(reached),
               "c1_match": sum(1 for r in reached if r.get("c1_match")),
               "tail_match": sum(1 for r in reached if r.get("tail_match")),
               "value_tail_rows": len(valued),
               "host_value_tail_rows": len(host_valued),
               "host_value_tail_rows_not_reached": [
                   {"atan2": r["atan2"], "host_tail": r["host_x87"]["classification"]["target"],
                    "host_fpatan_f32": r["host_x87"]["fpatan_f32"]}
                   for r in host_valued if not r["reached_the_dispatcher"]],
               "result_f32_match": sum(1 for r in valued if r["result_f32_match"]),
               "result_bits_equal": sum(1 for r in valued if r["result_bits_equal"]),
               "angle2_match": sum(1 for r in valued if r["angle2_match"]),
               "c1_mismatch_rows": [r["atan2"] for r in reached if not r.get("c1_match")],
               "tail_mismatch_rows": [r["atan2"] for r in reached if not r.get("tail_match")],
               "result_f32_mismatch_rows": [r["atan2"] for r in valued if not r["result_f32_match"]],
               "result_bits_differ_rows": [r["atan2"] for r in valued if not r["result_bits_equal"]],
               "angle2_mismatch_rows": [r["atan2"] for r in valued if not r["angle2_match"]],
               "error_tail_rows_trapped": [r["atan2"] for r in reached if r["trap"]],
               "not_reached": [r["atan2"] for r in rows if not r["reached_the_dispatcher"]]}
    report = {"mode": "operands", "fixture_plan": fixture, "helper": f"0x{VEC_ANGLES:08x}",
              "z": f"0x{Z_HALF:08x}", "operands": {k: f"0x{v:08x}" for k, v in OPERANDS.items()},
              "angle_multiplier": {"address": f"0x{ANGLE_MUL:08x}", "bits": f"0x{mul_bits:016x}"},
              "dispatcher": f"0x{DISPATCHER_VA:08x}",
              "xlat_table": {"address": f"0x{XLAT_TABLE:08x}", "bytes": xlat.hex()},
              "descriptor_table": {"address": f"0x{DESC_TABLE:08x}", "entries": [f"0x{d:08x}" for d in desc]},
              "instrumentation": stats, "summary": summary, "rows": rows}
    out = args.out if args.out != OUT / "atan2_probe.json" else (
        OUT / ("atan2_fxam_probe.unfixed.json" if args.fxam_unfixed else "atan2_fxam_probe.json"))
    out.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps(summary, indent=1))
    for r in rows:
        l = r.get("lifted") or {}
        h = r["host_x87"]
        lres = l.get("atan2_result")
        print(f"{r['atan2']:<22} host c1 {h['c1_st1']}{h['c1_st0']} {h['classification']['target']} "
              f"{h['fpatan_f32']:<10} a2 {h['angle2']} | lifted "
              + (f"c1 {l['classification']['st1']['c1']}{l['classification']['st0']['c1']} {l['target']} "
                 f"{f80_to_f32(lres[2:]) if lres else '-':<10} a2 {r['observed'][1] if r['observed'] else None}"
                 + (f"  trap: {r['trap']}" if r["trap"] else "") if l else "not reached"))
    print(f"-> {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fixture", default="vector-to-angles-v1")
    ap.add_argument("--rows", nargs="*", type=int, default=None,
                    help="fixture row indices to drive (default: a divergent row and three passing ones)")
    ap.add_argument("--variant", default="pc24")
    ap.add_argument("--out", type=Path, default=OUT / "atan2_probe.json")
    ap.add_argument("--raw", action="store_true", help="print the raw trace lines")
    ap.add_argument("--unfixed", action="store_true",
                    help="put remill's `readnone` back on the 80-bit accessors (the defect, for contrast)")
    ap.add_argument("--operands", action="store_true",
                    help="drive one synthetic row per pair of FXAM operand classes instead of fixture rows")
    ap.add_argument("--fxam-unfixed", action="store_true",
                    help="put remill's FXAM back in the traced dispatcher (C1 = 0 on zero, NaN, infinity)")
    args = ap.parse_args()

    read = image_reader()
    xlat = read(XLAT_TABLE, 16)
    desc = list(struct.unpack("<16I", read(DESC_TABLE, 64)))
    if args.operands:
        operands_mode(args, xlat, desc)
        return

    header, secs = FR.sections(FR.find(args.fixture))
    rows = secs["cases"]
    picks = args.rows or [131, 296, 66, 2, 0]
    picks = [i for i in picks if i < len(rows)]

    binary, stats = build_traced(args.variant, args.unfixed, args.fxam_unfixed)
    oracle = build_oracle()

    plan = P.BUILDERS[args.fixture]([rows[i] for i in picks], header)
    pdir = OUT / "plans"
    pdir.mkdir(parents=True, exist_ok=True)
    pf = pdir / f"{args.fixture}.probe.plan.bin"
    of = OUT / "out" / f"{args.fixture}.probe.out.bin"
    of.parent.mkdir(parents=True, exist_ok=True)
    pf.write_bytes(plan.encode())
    proc = sh([str(binary), str(EXE), str(pf), str(of)])

    # one (sw, sw, disp) triple per row that reached the dispatcher
    events, cur = [], []
    for line in proc.stderr.splitlines():
        m = TRACE_SW.search(line)
        if m:
            cur.append(("sw", int(m.group(2), 16), m.group(3), m.group(4)))
            continue
        m = TRACE_DISP.search(line)
        if m:
            events.append(cur + [("disp",) + m.groups()])
            cur = []

    data = of.read_bytes()
    va, nr, nw = struct.unpack_from("<3I", data, 8)
    obs = [struct.unpack_from(f"<{nw}I", data, 20 + i * nw * 4) for i in range(nr)]

    def f32(w):
        return struct.unpack("<f", struct.pack("<I", w))[0]

    # native x87, same control word, same sequence, same operands
    pairs = [(rows[i][0], rows[i][1]) for i in picks]     # st(1)=x=[esp+4], st(0)=y=[esp+8]
    native_sw = [(h["sw0"], h["sw1"]) for h in run_oracle(oracle, pairs)]

    report = {"fixture": args.fixture, "dispatcher": f"0x{DISPATCHER_VA:08x}",
              "branch_site": "0x0082c0c1 jmp dword ptr [ebx]",
              "xlat_table": {"address": f"0x{XLAT_TABLE:08x}", "bytes": xlat.hex()},
              "descriptor_table": {"address": f"0x{DESC_TABLE:08x}",
                                   "entries": [f"0x{d:08x}" for d in desc]},
              "instrumentation": stats, "driver_stderr_tail": proc.stderr.splitlines()[-3:],
              "rows": []}

    for k, i in enumerate(picks):
        r = rows[i]
        entry = {"row": i,
                 "x_st1": f"0x{r[0]:08x}", "y_st0": f"0x{r[1]:08x}", "z": f"0x{r[2]:08x}",
                 "x": f32(r[0]), "y": f32(r[1]), "z": f32(r[2]),
                 "expected": [f"0x{r[3]:08x}", f"0x{r[4]:08x}"],
                 "observed": [f"0x{w:08x}" for w in obs[k][2:]],
                 "matches": tuple(obs[k][2:]) == (r[3], r[4])}
        if k < len(native_sw):
            sw0, sw1 = native_sw[k]
            entry["native"] = tail_for(sw0, sw1, xlat, desc)
        ev = events[k] if k < len(events) else None
        if ev:
            sws = [e for e in ev if e[0] == "sw"]
            disp = next(e for e in ev if e[0] == "disp")
            entry["lifted"] = {
                "fnstsw": [{"sw": f"0x{e[1]:04x}", "st0": f"0x{e[2]}{e[3]}",
                            "st0_value": fp80(e[2], e[3])} for e in sws],
                "eax": f"0x{disp[3]}", "ecx": f"0x{disp[4]}", "table": f"0x{disp[2]}",
                "target": f"0x{int(disp[1], 16):08x}",
                "tail": TAIL_NAMES.get(int(disp[1], 16), "?"),
                "st0_at_jump": f"0x{disp[5]}{disp[6]}", "st1_at_jump": f"0x{disp[7]}{disp[8]}",
                "st0_value": fp80(disp[5], disp[6]), "st1_value": fp80(disp[7], disp[8]),
            }
            if len(sws) == 2:
                entry["lifted"]["classification"] = tail_for(sws[0][1], sws[1][1], xlat, desc)
        report["rows"].append(entry)

    raw = [ln for ln in proc.stderr.splitlines() if ln.startswith("SA2TRACE")]
    report["raw_trace_lines"] = len(raw)
    args.out.with_suffix(".trace.txt").write_text("\n".join(raw) + "\n")
    if args.raw:
        for ln in raw:
            print(ln)
    args.out.write_text(json.dumps(report, indent=1) + "\n")
    for e in report["rows"]:
        print(f"row {e['row']:5d} x={e['x']:<12g} y={e['y']:<12g} z={e['z']:<12g} "
              f"{'MATCH' if e['matches'] else 'DIVERGE'}")
        if "native" in e:
            n = e["native"]
            print(f"   native x87: st0 {n['st0']['sw']} {n['st0']['fxam_class']:>10} class {n['st0']['class']:2d} | "
                  f"st1 {n['st1']['sw']} {n['st1']['fxam_class']:>10} class {n['st1']['class']:2d} "
                  f"-> idx {n['index']:2d} {n['target']} {n['tail']}")
        if "lifted" in e:
            l = e["lifted"]
            sw = " | ".join(f"{x['sw']} st0={x['st0_value']:g}" for x in l["fnstsw"])
            print(f"   lifted    : {sw}")
            c = l.get("classification")
            if c:
                print(f"               st0 {c['st0']['fxam_class']:>10} class {c['st0']['class']:2d} | "
                      f"st1 {c['st1']['fxam_class']:>10} class {c['st1']['class']:2d} "
                      f"-> idx {c['index']:2d}")
            print(f"               eax {l['eax']} target {l['target']} {l['tail']}")
            print(f"               st0 at jump {l['st0_value']:g}  st1 at jump {l['st1_value']:g}")
    print(f"-> {args.out}")


if __name__ == "__main__":
    main()
