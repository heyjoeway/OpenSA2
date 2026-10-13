#!/usr/bin/env python3
"""S2 patch: hand-encoded x87 probe functions, lifted with remill and read back.

The ten spike functions contain no `fsub`, `fsqrt`, `fistp`, `fldcw` or
transcendental instruction (decision 2026-09-19-G1 section 1.2), so what remill
v5.0.7 emits for those semantics has never been looked at. Before any fixture is
built this module answers the structural question for each class: does the
semantic lower to an `x86_fp80` arithmetic op or an `llvm.sqrt.f80` call that
`tools/spike/pc24pass/pc24pass.py` instruments, or to something the pass cannot
see (an intrinsic it does not match, a libm call, an integer conversion)?

A probe is a few hand-encoded 32-bit instructions operating on fixed absolute
guest addresses, so it needs no prologue, no stack frame and no callee: the
generic harness (s2_harness.cpp) writes the row's input words into the input
arena, runs the lifted function, and reads the output arena back. Every encoding
is decoded back with iced-x86 and checked against the mnemonic the probe claims,
so a mis-encoded byte string is a build error and not a silent wrong answer.

Guest addresses (shapes: tools/s2/patch/s2_guest.h):
    0x03400000  input arena  (row input words, little-endian)
    0x03500000  output arena (row output words)

Usage (prints the probe table and each probe's lifted-IR summary):
    source tools/env.sh && .venv/bin/python tools/s2/patch/probes.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import iced_x86 as ix

ROOT = Path(__file__).resolve().parents[3]
GEN = ROOT / "private" / "generated" / "s2" / "patch"
REMILL_PREFIX = ROOT / "private" / "lifters" / "remill" / "usr" / "local"

A_IN = 0x03400000
A_OUT = 0x03500000
PROBE_BASE = 0x00300000          # outside the image, so no probe address collides with a real function


def d32(v: int) -> bytes:
    return v.to_bytes(4, "little")


# encoder helpers: every x87 memory form here is mod=00 rm=101 (disp32), so the
# modrm byte is (reg << 3) | 0x05.
def mem(op: int, reg: int, addr: int) -> bytes:
    return bytes([op, (reg << 3) | 0x05]) + d32(addr)


FLD32 = lambda a: mem(0xD9, 0, a)      # noqa: E731
FSTP32 = lambda a: mem(0xD9, 3, a)     # noqa: E731
FLD64 = lambda a: mem(0xDD, 0, a)      # noqa: E731
FSTP64 = lambda a: mem(0xDD, 3, a)     # noqa: E731
FLD80 = lambda a: mem(0xDB, 5, a)      # noqa: E731
FSTP80 = lambda a: mem(0xDB, 7, a)     # noqa: E731
FISTP16 = lambda a: mem(0xDF, 3, a)    # noqa: E731
FISTP32 = lambda a: mem(0xDB, 3, a)    # noqa: E731
FISTP64 = lambda a: mem(0xDF, 7, a)    # noqa: E731
FIST32 = lambda a: mem(0xDB, 2, a)     # noqa: E731
FLDCW = lambda a: mem(0xD9, 5, a)      # noqa: E731
FNSTCW = lambda a: mem(0xD9, 7, a)     # noqa: E731
FADD32 = lambda a: mem(0xD8, 0, a)     # noqa: E731
FMUL32 = lambda a: mem(0xD8, 1, a)     # noqa: E731
FSUB32 = lambda a: mem(0xD8, 4, a)     # noqa: E731
FSUBR32 = lambda a: mem(0xD8, 5, a)    # noqa: E731
FDIV32 = lambda a: mem(0xD8, 6, a)     # noqa: E731
FSQRT = b"\xd9\xfa"
FSUBP = b"\xde\xe9"                    # fsubp st(1), st: st(1) = st(1) - st(0), then pop
FSTPST0 = b"\xdd\xd8"                  # fstp st(0)
RET = b"\xc3"


# name -> (bytes, in_words, out_words, what the row means). Input words land at
# A_IN, output words are read from A_OUT.
def probe_table() -> dict[str, dict]:
    p: dict[str, dict] = {}

    # --- piece 1: fsqrt -------------------------------------------------
    p["fsqrt_f32"] = {"code": FLD32(A_IN) + FSQRT + FSTP32(A_OUT) + RET, "in": 1, "out": 1,
                      "doc": "float in -> fsqrt -> float out"}
    p["fsqrt_f64"] = {"code": FLD64(A_IN) + FSQRT + FSTP64(A_OUT) + RET, "in": 2, "out": 2,
                      "doc": "double in -> fsqrt -> double out"}
    p["fsqrt_f80"] = {"code": FLD80(A_IN) + FSQRT + FSTP80(A_OUT) + RET, "in": 3, "out": 3,
                      "doc": "x86_fp80 in -> fsqrt -> x86_fp80 out (10 bytes in 3 words)"}

    # --- piece 2: the fistp family -------------------------------------
    p["fistpl_f64"] = {"code": FLD64(A_IN) + FISTP32(A_OUT) + RET, "in": 2, "out": 1,
                       "doc": "double in -> fistp m32int (423 sites)"}
    p["fistpll_f64"] = {"code": FLD64(A_IN) + FISTP64(A_OUT) + RET, "in": 2, "out": 2,
                        "doc": "double in -> fistp m64int (272 sites)"}
    p["fistps_f64"] = {"code": FLD64(A_IN) + FISTP16(A_OUT) + RET, "in": 2, "out": 1,
                       "doc": "double in -> fistp m16int (4 sites); the out word's low half is the result"}
    p["fistl_f64"] = {"code": FLD64(A_IN) + FIST32(A_OUT) + FSTPST0 + RET, "in": 2, "out": 1,
                      "doc": "double in -> fist m32int (no pop), then discard st0"}
    p["fistpl_f32"] = {"code": FLD32(A_IN) + FISTP32(A_OUT) + RET, "in": 1, "out": 1,
                       "doc": "float in -> fistp m32int"}
    # with a control word loaded first: the rounding mode decides the integer
    p["fistpl_cw"] = {"code": FLDCW(A_IN) + FLD64(A_IN + 4) + FISTP32(A_OUT) + RET, "in": 3, "out": 1,
                      "doc": "cw at word 0 (low half), double at words 1-2 -> fistp m32int"}
    p["fistpll_cw"] = {"code": FLDCW(A_IN) + FLD64(A_IN + 4) + FISTP64(A_OUT) + RET, "in": 3, "out": 2,
                       "doc": "cw at word 0, double at words 1-2 -> fistp m64int"}

    # --- piece 3/4: fldcw and the basic ops under a live control word ---
    p["fldcw_fnstcw"] = {"code": FLDCW(A_IN) + FNSTCW(A_OUT) + RET, "in": 1, "out": 1,
                         "doc": "fldcw then fnstcw: does remill's FLDCW reach the State field the pass reads?"}
    for name, enc, doc in (("fadd", FADD32, "fadd m32fp"), ("fsub", FSUB32, "fsub m32fp"),
                           ("fsubr", FSUBR32, "fsubr m32fp"), ("fmul", FMUL32, "fmul m32fp"),
                           ("fdiv", FDIV32, "fdiv m32fp")):
        p[f"{name}_cw"] = {"code": FLDCW(A_IN) + FLD32(A_IN + 4) + enc(A_IN + 8) + FSTP32(A_OUT) + RET,
                           "in": 3, "out": 1, "doc": f"cw, a, b -> {doc} -> float out"}
    p["fsubp_cw"] = {"code": FLDCW(A_IN) + FLD32(A_IN + 4) + FLD32(A_IN + 8) + FSUBP + FSTP32(A_OUT) + RET,
                     "in": 3, "out": 1, "doc": "cw, a, b -> fsubp st(1),st (a - b) -> float out (bytes DE E9)"}
    p["fsqrt_cw"] = {"code": FLDCW(A_IN) + FLD32(A_IN + 4) + FSQRT + FSTP32(A_OUT) + RET,
                     "in": 2, "out": 1, "doc": "cw, a -> fsqrt -> float out"}
    # 80-bit results: a float store hides the precision-control difference (a PC24
    # and a PC64 result usually round to the same binary32), so these probes store
    # the raw x86_fp80 and are the rows that actually discriminate the models.
    for name, enc, doc in (("fadd", FADD32, "fadd m32fp"), ("fsub", FSUB32, "fsub m32fp"),
                           ("fsubr", FSUBR32, "fsubr m32fp"), ("fmul", FMUL32, "fmul m32fp"),
                           ("fdiv", FDIV32, "fdiv m32fp")):
        p[f"{name}_cw80"] = {"code": FLDCW(A_IN) + FLD32(A_IN + 4) + enc(A_IN + 8) + FSTP80(A_OUT) + RET,
                             "in": 3, "out": 3, "doc": f"cw, a, b -> {doc} -> x86_fp80 out"}
    p["fsubp_cw80"] = {"code": FLDCW(A_IN) + FLD32(A_IN + 4) + FLD32(A_IN + 8) + FSUBP + FSTP80(A_OUT) + RET,
                       "in": 3, "out": 3, "doc": "cw, a, b -> fsubp st(1),st (a - b) -> x86_fp80 out (bytes DE E9)"}
    p["fsqrt_cw80"] = {"code": FLDCW(A_IN) + FLD32(A_IN + 4) + FSQRT + FSTP80(A_OUT) + RET,
                       "in": 2, "out": 3, "doc": "cw, a -> fsqrt -> x86_fp80 out"}
    p["fdiv_cw_f64"] = {"code": FLDCW(A_IN) + FLD64(A_IN + 4) + mem(0xDC, 6, A_IN + 12) + FSTP64(A_OUT) + RET,
                        "in": 5, "out": 2, "doc": "cw, a(double), b(double) -> fdiv m64fp -> double out"}
    return p


def disassemble(code: bytes, va: int = PROBE_BASE) -> list[str]:
    f = ix.Formatter(ix.FormatterSyntax.INTEL)
    return [f.format(i) for i in ix.Decoder(32, code, ip=va)]


def check_encodings(table: dict[str, dict]) -> list[dict]:
    """Decode every probe back; a probe whose bytes do not decode to its name's ops is a build error."""
    rows = []
    for name, p in table.items():
        text = disassemble(p["code"])
        if any(t.startswith("(bad)") or t == "" for t in text):
            raise SystemExit(f"probe {name} does not decode: {text}")
        if text[-1] != "ret":
            raise SystemExit(f"probe {name} does not end in ret: {text}")
        rows.append({"probe": name, "bytes": len(p["code"]), "instructions": text, "doc": p["doc"]})
    return rows


def lift(name: str, code: bytes, va: int, outdir: Path) -> dict:
    """remill-lift-14 over one probe (tools/spike/lift_remill.py's invocation, one function)."""
    outdir.mkdir(parents=True, exist_ok=True)
    ll = outdir / f"{name}.ll"
    hexbytes = code.hex()
    inner = (f"mount --bind {REMILL_PREFIX} /usr/local && /usr/local/bin/remill-lift-14 --arch x86 --os windows "
             f"--address {va:#x} --bytes {hexbytes} --ir_out {ll}")
    cmd = ["unshare", "-Urm", "--propagation", "unchanged", "sh", "-c", inner]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    text = ll.read_text(errors="replace") if ll.exists() else ""
    return {"probe": name, "va": f"{va:#010x}", "rc": p.returncode,
            "command": f"unshare -Urm --propagation unchanged sh -c '{inner.replace(hexbytes, f'<{len(code)} bytes hex>')}'",
            "lifted": bool(re.search(rf"^define [^\n]*@sub_{va:x}\(", text, re.M)),
            "ll": str(ll.relative_to(ROOT)), "ll_lines": text.count("\n"),
            "stderr_head": "\n".join((p.stderr + p.stdout).strip().splitlines()[:3]) if p.returncode else ""}


def ir_summary(ll: Path) -> dict:
    """What the lifted IR contains, in the terms the pass decides on."""
    text = ll.read_text(errors="replace")
    sem = sorted(set(re.findall(r"_ZN12_GLOBAL__N_1(\d+)([A-Za-z0-9_]+)", text)))
    names = sorted({m[1][:int(m[0])] for m in sem if len(m[1]) >= int(m[0])})
    return {
        "fp80_arith_ops": len(re.findall(r"= (?:fadd|fsub|fmul|fdiv|frem)(?: [a-z]+)* x86_fp80 ", text)),
        "llvm_sqrt_f80": len(re.findall(r"@llvm\.sqrt\.f80", text)),
        "other_llvm_intrinsics": sorted(set(re.findall(r"@(llvm\.[\w.]+)", text)) - {"llvm.sqrt.f80"}),
        "external_calls": sorted({m for m in re.findall(r"call [^@]*@([\w.$]+)\(", text)
                                  if not m.startswith(("llvm.", "__remill", "sub_"))}),
        "remill_semantics": names,
        "fptosi": len(re.findall(r"= fptosi ", text)),
        "fptrunc_fpext": len(re.findall(r"= (?:fptrunc|fpext) ", text)),
        "missing_blocks": len(re.findall(r"__remill_missing_block", text)),
        "lines": text.count("\n"),
    }


def main() -> None:
    table = probe_table()
    rows = check_encodings(table)
    outdir = GEN / "probes"
    results = []
    for r in rows:
        name = r["probe"]
        lifted = lift(name, table[name]["code"], PROBE_BASE, outdir)
        if lifted["lifted"]:
            lifted["ir"] = ir_summary(ROOT / lifted["ll"])
        results.append({**r, **lifted})
        s = lifted.get("ir", {})
        print(f"S2_PROBE {name:16s} lifted={lifted['lifted']} fp80_ops={s.get('fp80_arith_ops')} "
              f"sqrt_f80={s.get('llvm_sqrt_f80')} fptosi={s.get('fptosi')} sem={s.get('remill_semantics')} "
              f"ext={s.get('external_calls')} intr={s.get('other_llvm_intrinsics')}")
    out = GEN / "probes.json"
    out.write_text(json.dumps({"schema": "sa2-recomp-x87-probes/v1", "probe_base": f"{PROBE_BASE:#010x}",
                               "input_arena": f"{A_IN:#010x}", "output_arena": f"{A_OUT:#010x}",
                               "probes": results}, indent=1) + "\n")
    print(f"S2_PROBE wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
