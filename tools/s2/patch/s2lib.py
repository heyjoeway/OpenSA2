#!/usr/bin/env python3
"""S2 patch: shared plumbing for the four pieces (lift, pass, build, run, compare).

Every piece of the S2 patch work drives the same loop: take bytes (a hand-encoded
probe from probes.py or a real function's bytes out of the unpacked image), lift
them with remill-lift-14, run tools/spike/pc24pass/pc24pass.py over the IR, build
the generic harness (s2_harness.cpp) around the result, run vector rows through
it, and compare against the native x87 oracle (x87_oracle.c) or a reference. This
module is that loop; the drivers decide the rows and the verdicts.

Nothing here writes into tools/spike/ or private/generated/spike/: the pass and
its helper IR are read, the S1 outputs are read, and everything this module
produces lands under private/generated/s2/patch/ (ignored by git).
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
SPIKE = ROOT / "tools" / "spike"
FIX = SPIKE / "fixtures"
PASS_DIR = SPIKE / "pc24pass"
sys.path.insert(0, str(SPIKE))
import spikelib as S  # noqa: E402

GEN = ROOT / "private" / "generated" / "s2" / "patch"
SPIKE_GEN = ROOT / "private" / "generated" / "spike"
IMAGE = SPIKE_GEN / "fixtures" / "image.bin"
HELPER_LL = SPIKE_GEN / "pc24pass" / "pc_round.ll"
REMILL_PREFIX = ROOT / "private" / "lifters" / "remill" / "usr" / "local"
REMILL_INC = REMILL_PREFIX / "include"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
ZIG = str(S.ZIG)
TARGET = ["-target", "x86_64-linux-musl"]
LINK = ["-Wl,--image-base=0x10000000"]

A_IN = 0x03400000
A_OUT = 0x03500000
FLAG_ST0_FROM_ROW = 1
FLAG_OUT_IN_ARENA = 2
IMAGE_BASE = 0x400000
PREFIX_WORDS = 7           # status, exit_pc, eax, st0 x3, cw

REG_ZERO = 0xF0000000
REG_A_IN = 0xF0000001
REG_A_OUT = 0xF0000002
REG_IN_WORD = lambda i: 0xF1000000 | i            # noqa: E731
REG_A_IN_OFF = lambda off: 0xF2000000 | off       # noqa: E731

VEC_MAGIC = b"SA2S2V1\0"
OUT_MAGIC = b"SA2S2O1\0"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def zig_env() -> dict:
    """zig's cache under private/generated (a shared cache across drivers reports CacheCheckFailed on .ll input)."""
    import os
    cache = GEN / "zig-cache"
    cache.mkdir(parents=True, exist_ok=True)
    return dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(cache), ZIG_LOCAL_CACHE_DIR=str(cache))


def cmdline(cmd: list[str]) -> str:
    return " ".join(cmd).replace(str(ROOT) + "/", "")


def fw(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def dw(x: float) -> tuple[int, int]:
    lo, hi = struct.unpack("<II", struct.pack("<d", x))
    return lo, hi


# ---------------------------------------------------------------- vectors
def write_vectors(path: Path, va: int, rows: list[list[int]], out_words: int, cw: int, *,
                  stack_args: int = 0, eax: int = REG_ZERO, ecx: int = REG_ZERO, edx: int = REG_ZERO,
                  esi: int = REG_ZERO, edi: int = REG_ZERO, flags: int = 0) -> None:
    in_words = len(rows[0]) if rows else 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(VEC_MAGIC)
        f.write(struct.pack("<12I", va, len(rows), in_words, out_words, cw, stack_args, eax, ecx, edx, esi, edi, flags))
        for r in rows:
            assert len(r) == in_words, (len(r), in_words)
            f.write(struct.pack(f"<{in_words}I", *[w & 0xFFFFFFFF for w in r]))


def read_out(path: Path) -> list[list[int]]:
    data = path.read_bytes()
    assert data[:8] == OUT_MAGIC, path
    va, rows, words = struct.unpack_from("<3I", data, 8)
    return [list(struct.unpack_from(f"<{words}I", data, 20 + i * words * 4)) for i in range(rows)]


# ---------------------------------------------------------------- image
def image_bytes() -> bytes:
    """The unpacked PE mapped at its own VA, as the spike harness dumps it."""
    if IMAGE.exists():
        return IMAGE.read_bytes()
    import pefile
    pe = pefile.PE(str(EXE), fast_load=True)
    data = bytes(pe.get_memory_mapped_image())
    IMAGE.parent.mkdir(parents=True, exist_ok=True)
    IMAGE.write_bytes(data)
    return data


def function_bytes(img: bytes, va: int, max_bytes: int = 4096) -> bytes:
    """A function's bytes from its entry to its first `ret` inclusive (the spike's sizing rule)."""
    import iced_x86 as ix
    blob = img[va - IMAGE_BASE: va - IMAGE_BASE + max_bytes]
    end = 0
    for ins in ix.Decoder(32, blob, ip=va):
        end = ins.ip + ins.len - va
        if ins.mnemonic in (ix.Mnemonic.RET, ix.Mnemonic.RETF):
            break
    return blob[:end]


# ---------------------------------------------------------------- lift + pass
def lift(name: str, code: bytes, va: int, outdir: Path, *, reuse: bool = True) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    ll = outdir / f"{name}.ll"
    hexbytes = code.hex()
    inner = (f"mount --bind {REMILL_PREFIX} /usr/local && /usr/local/bin/remill-lift-14 --arch x86 --os windows "
             f"--address {va:#x} --bytes {hexbytes} --ir_out {ll}")
    shown = f"unshare -Urm --propagation unchanged sh -c '{inner.replace(hexbytes, f'<{len(code)} bytes hex>')}'"
    if reuse and ll.exists():
        return {"command": shown, "rc": 0, "reused": True, "ll": rel(ll), "path": ll, "ll_sha256": sha(ll),
                "lifted": bool(re.search(rf"^define [^\n]*@sub_{va:x}\(", ll.read_text(errors='replace'), re.M))}
    p = sh(["unshare", "-Urm", "--propagation", "unchanged", "sh", "-c", inner], timeout=900)
    text = ll.read_text(errors="replace") if ll.exists() else ""
    return {"command": shown, "rc": p.returncode, "reused": False, "ll": rel(ll), "path": ll,
            "ll_sha256": sha(ll) if ll.exists() else None,
            "lifted": bool(re.search(rf"^define [^\n]*@sub_{va:x}\(", text, re.M)),
            "error_head": S.error_head(p.stderr + p.stdout) if p.returncode else ""}


def run_pass(ll: Path, out: Path, *, extra: list[str] | None = None) -> dict:
    """tools/spike/pc24pass/pc24pass.py over one module."""
    stats = out.with_suffix(".stats.json")
    cmd = [sys.executable, str(PASS_DIR / "pc24pass.py"), "--helper-ll", str(HELPER_LL), str(ll), str(out),
           "--stats", str(stats)] + (extra or [])
    p = sh(cmd)
    return {"command": cmdline(cmd), "rc": p.returncode, "error_head": S.error_head(p.stderr) if p.returncode else "",
            "out": rel(out), "out_sha256": sha(out) if out.exists() else None,
            "stats": json.loads(stats.read_text()) if stats.exists() else None}


# ---------------------------------------------------------------- callee stubs
NATIVE_STUBS = {
    # va -> (C++ body, comment). These are the CRT helpers the game's x87 code
    # reaches; each behaves as the original does, under the guest control word.
    0x0082C215: ("""// CRT _ctrlfp-style control-word set: cw = (cw & 0x300) | 0x7F, i.e. keep the
    // guest precision-control bits and force round-to-nearest with all exceptions
    // masked (tools/spike/fixtures/pc24_reference.c records the same reading).
    s->x87.fxsave.cwd.flat = (uint16_t)((s->x87.fxsave.cwd.flat & 0x300u) | 0x7Fu);
    s2_pop_return(s);""", "0x0082C215 CRT control-word set"),
    0x008290A0: ("""// CRT _CIsqrt: st(0) = sqrt(st(0)) under the guest control word. The body at
    // 0x008290BD loads (cw & 0x300) | 0x7F, so the precision-control bits are the
    // guest's and the rounding is to nearest; that is reproduced by executing the
    // host's own fsqrt under exactly that control word (native evidence, the same
    // stub the S1 harness uses).
    long double v, r;
    memcpy(&v, &s->st.elems[0].val, 10);
    uint16_t host, guest = (uint16_t)((s->x87.fxsave.cwd.flat & 0x300u) | 0x7Fu);
    __asm__ volatile("fnstcw %0" : "=m"(host));
    __asm__ volatile("fldcw %0" : : "m"(guest));
    __asm__ volatile("fsqrt" : "=t"(r) : "0"(v));
    __asm__ volatile("fldcw %0" : : "m"(host));
    memcpy(&s->st.elems[0].val, &r, 10);
    s2_pop_return(s);""", "0x008290A0 CRT _CIsqrt under the guest control word"),
}


def stub_source(ll: Path, natives: dict[int, tuple[str, str]] | None = None) -> str:
    """A .cpp defining every `sub_*` the IR declares but does not define: native where known, else a trap."""
    text = ll.read_text(errors="replace")
    declared = set(re.findall(r"^declare [^\n]*@(sub_[0-9a-f]+)\(", text, re.M))
    defined = set(re.findall(r"^define [^\n]*@(sub_[0-9a-f]+)\(", text, re.M))
    natives = natives or NATIVE_STUBS
    lines = ["// generated by tools/s2/patch/s2lib.py from the lifted module's own declarations:",
             f"// {rel(ll)} declares {len(declared)} sub_* symbols it does not define.",
             "#include <cstdint>", "#include <cstring>",
             '#include "remill/Arch/X86/Runtime/State.h"',
             "struct Memory;", "typedef uint32_t gaddr;",
             'extern "C" void s2_callee_trap(uint32_t va);',
             'extern "C" void s2_pop_return(State *s);', 'extern "C" {']
    for name in sorted(declared - defined):
        va = int(name.split("_")[1], 16)
        if va in natives:
            body, comment = natives[va]
            lines.append(f"// {comment}")
            lines.append(f"Memory *{name}(State *s, gaddr, Memory *m) {{\n    {body}\n    return m;\n}}")
        else:
            lines.append(f"Memory *{name}(State *, gaddr, Memory *m) {{ s2_callee_trap({va:#010x}u); return m; }}")
    lines.append("}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- build + run
def build_harness(tag: str, ll: Path, va: int, bdir: Path, *, harness_src: Path | None = None,
                  natives: dict[int, tuple[str, str]] | None = None) -> dict:
    """Compile s2_harness.cpp + the IR + generated stubs into one runnable binary."""
    bdir.mkdir(parents=True, exist_ok=True)
    log: dict = {}

    def cc(args: list[str], out: Path) -> dict:
        cmd = [ZIG] + args + ["-o", str(out)]
        p = sh(cmd, cwd=bdir, env=zig_env())
        return {"ok": p.returncode == 0, "command": cmdline(cmd), "error_head": S.error_head(p.stderr) if p.returncode else ""}

    stubs = bdir / f"stubs_{tag}.cpp"
    stubs.write_text(stub_source(ll, natives))
    src = harness_src or (HERE / "s2_harness.cpp")
    log["guest.o"] = cc(["cc"] + TARGET + ["-O1", "-w", "-fno-sanitize=undefined", "-c", "-I", str(FIX), str(FIX / "guest.c")], bdir / "guest.o")
    log["harness"] = cc(["c++"] + TARGET + ["-O1", "-w", "-std=c++17", "-fno-sanitize=undefined", f"-DSUB=sub_{va:x}",
                         "-I", str(FIX), "-I", str(REMILL_INC), "-c", str(src)], bdir / f"harness_{tag}.o")
    log["stubs"] = cc(["c++"] + TARGET + ["-O1", "-w", "-std=c++17", "-I", str(REMILL_INC), "-c", str(stubs)], bdir / f"stubs_{tag}.o")
    log["ir"] = cc(["cc"] + TARGET + ["-O1", "-w", "-c", str(ll)], bdir / f"ir_{tag}.o")
    binary = bdir / f"run_{tag}"
    log["link"] = cc(["c++"] + TARGET + LINK + [str(bdir / f"harness_{tag}.o"), str(bdir / "guest.o"),
                                                str(bdir / f"stubs_{tag}.o"), str(bdir / f"ir_{tag}.o"), "-lm"], binary)
    log["binary"] = rel(binary)
    log["ok"] = all(v["ok"] for v in log.values() if isinstance(v, dict))
    log["stubs_source"] = rel(stubs)
    return log


def build_oracle(bdir: Path) -> dict:
    bdir.mkdir(parents=True, exist_ok=True)
    out = bdir / "x87_oracle"
    cmd = [ZIG, "cc"] + TARGET + ["-O1", "-w", str(HERE / "x87_oracle.c"), "-o", str(out), "-lm"]
    p = sh(cmd, cwd=bdir, env=zig_env())
    return {"ok": p.returncode == 0, "command": cmdline(cmd), "binary": rel(out),
            "error_head": S.error_head(p.stderr) if p.returncode else ""}


def run_binary(binary: Path, vectors: Path, out: Path, *, args: list[str] | None = None) -> dict:
    cmd = [str(binary)] + (args or []) + [str(IMAGE), str(vectors), str(out)]
    if "x87_oracle" in binary.name:
        cmd = [str(binary)] + (args or []) + [str(vectors), str(out)]
    p = sh(cmd, timeout=600)
    return {"command": cmdline(cmd), "rc": p.returncode, "stderr_head": S.error_head(p.stderr + p.stdout) if p.returncode else "",
            "out": rel(out)}


def compare(a: list[list[int]], b: list[list[int]]) -> dict:
    """Row-by-row equality of two output tables, with the first divergences kept."""
    n = min(len(a), len(b))
    mismatch = [i for i in range(n) if a[i] != b[i]]
    return {"rows": n, "equal": n - len(mismatch), "mismatch": len(mismatch), "first_mismatches": mismatch[:8]}
