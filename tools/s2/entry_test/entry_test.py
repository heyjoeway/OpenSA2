#!/usr/bin/env python3
"""S2 entry test: native rows that discriminate PC24 from the 80-bit model.

Decision 2026-09-19-G1 section 1.2: the spike's 53 native rows (0x00426CC0,
the natural axis-permutation matrix) are reproduced by both the PC24 and the
fp80 model, so no native row separated them. Route 1 of the entry test looks
for such rows in the inherited fixture corpus (design 3.7) before any new
capture is taken. Every fixture under private/fixtures/LocalAssets-SA2 is
listed with a disposition; the two whose native words are outputs of a leaf
x87 function the spike harness can lift and run are decoded:

  SA2NPT1  ninja-point-transform-v1   0x00426CC0 calc_point, 53 native rows
  SA2TXC1  contact-transform-corpus-v1 0x00426D50, 95728 native calls
           (matrix, source, destination) of the point helper that reads its
           matrix from the matrix-stack top; its 42 x87 instructions are, after
           renaming the base registers, the same sequence as 0x00426CC0's
           mode-0 body. This tool asserts that identity from the executable
           (iced-x86) and runs the rows through the lifted 0x00426CC0; it also
           lifts 0x00426D50 itself (remill-lift-14, then the pc24 pass) and
           drives it through a harness variant derived at build time from the
           spike's sources (three anchored insertions), so the rows are also
           checked against the lifted function they came from.

For every decoded row both reference models (tools/spike/fixtures/
pc24_reference.c, unchanged) are computed and the row is classed by which
model the native words equal; a row is discriminating when the models differ
and native equals exactly one. The lifted binaries are run on every row:
remill's original IR (fp80 semantics) and the pc24pass-patched IR, both from
the S1 builds, plus the two 0x00426D50 builds this tool makes.

Outputs (ignored by git) under private/generated/s2/entry_test/: vectors,
model and lifted outputs, the derived harness, the 0x00426D50 IR, and
results.json. The receipt is Documentation/receipts/s2-pc24-native-rows-v1.json
(sa2-recomp-receipt/v1; at most 32 discriminating rows inline, the full set
as a vector file whose sha256 the receipt carries).

Usage: source tools/env.sh && .venv/bin/python tools/s2/entry_test/entry_test.py [--skip-lift]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
SPIKE = ROOT / "tools" / "spike"
FIX = SPIKE / "fixtures"
PASS = SPIKE / "pc24pass"
sys.path.insert(0, str(SPIKE))
import spikelib as S  # noqa: E402

GEN = ROOT / "private" / "generated"
OUT = GEN / "s2" / "entry_test"
SPIKE_BUILD = GEN / "spike" / "fixtures" / "build"
PASS_BUILD = GEN / "spike" / "pc24pass" / "root" / "fixtures" / "build"
PASS_IR = GEN / "spike" / "pc24pass" / "root" / "remill"
ORIG_IR = GEN / "spike" / "remill"
HELPER_LL = GEN / "spike" / "pc24pass" / "pc_round.ll"
IMAGE = GEN / "spike" / "fixtures" / "image.bin"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
REMILL_PREFIX = ROOT / "private" / "lifters" / "remill" / "usr" / "local"
REMILL_INC = REMILL_PREFIX / "include"
CORPUS = [ROOT / "private" / "fixtures" / "LocalAssets-SA2" / "Precise",
          ROOT / "private" / "fixtures" / "LocalAssets-SA2" / "Fixtures"]
RECEIPT = ROOT / "Documentation" / "receipts" / "s2-pc24-native-rows-v1.json"
ZIG = str(S.ZIG)
TARGET = ["-target", "x86_64-linux-musl"]
LINK = ["-Wl,--image-base=0x10000000"]
A_MSTACK = 0x03100000
CALC_POINT = 0x00426CC0
CALC_POINT_ACTIVE = 0x00426D50
INLINE_ROWS = 32
HEX8 = lambda w: f"{w:#010x}"  # noqa: E731

# Every fixture magic in the corpus, the kernel it belongs to, and why it is or is
# not decoded. A fixture is decoded only when its native words are the outputs of
# a leaf x87 function the harness can drive; the rest are kernel windows whose
# words come from functions outside the ten (or from no single function).
DISPOSITIONS = {
    "SA2NPT1": ("0x00426CC0 calc_point (Ninja point transform)", "decoded"),
    "SA2TXC1": ("0x00426D50 point helper (matrix from the stack top; same 42 x87 ops as 0x00426CC0 mode 0)", "decoded"),
    "SA2ECC3": ("reduction of ninja-point-transform-v1 and the strip composition", "not decoded: the same 53 point rows, re-grouped"),
    "SA2ECC4": ("reduction of ninja-object-matrix-v1 and the point composition", "not decoded: the same rows, re-grouped"),
    "SA2NOM1": ("Ninja object matrix: composition of the five Ninja helpers 0x00427470..0x00427750 on the natural object", "not decoded: the helpers are not lifted (S2 fixture work; the harness traps at them)"),
    "SA2CCB1": ("candidate cache builder 0x0048B510 and its tree", "not decoded: whole-kernel words, not a leaf function's"),
    "SA2ECC1": ("candidate cache composition", "not decoded: whole-kernel words"),
    "SA2ECC2": ("candidate cache strip composition", "not decoded: whole-kernel words"),
    "SA2BSD1": ("Basic strip decoder", "not decoded: integer decoding, no x87"),
    "SA2CSE1": ("candidate enumeration", "not decoded: integer selection, no x87"),
    "SA2V2A1": ("0x00459260 direction -> two angles (5305 oracle rows)", "not decoded: the function is served natively by the harness (transcendental, design 7.4 residue class); not lifted"),
    "SA2GAC1": ("GroundCollisionStep acceptance block 0x00462C19.. (word 10 = the angle 0x004469E0 returns)", "not decoded: the spike lifts 0x004469E0 only to its first ret; the angle comes from the tail past it"),
    "SA2GPW1": ("GroundCollisionStep policy-word window 0x00462526..0x00462C14 (calls 0x0077FC30 and 0x0077F3A0 inline)", "not decoded: only the integer policy word and angle are captured; the length and unit-vector words are not in the fixture"),
    "SA2STF1": ("Stairs transform oracle sweep (sine-table rotations)", "not decoded: function outside the ten, needs its own lift and reference"),
    "SA2PRW1": ("PResetPosition windowed corpus", "not decoded: whole-routine words"),
    "SA2PRS1": ("PResetPosition six-case fixture", "not decoded: whole-routine words"),
    "SA2CRI1": ("compact contact corpus", "not decoded: whole-kernel records"),
    "SA2CFN1": ("contact face records", "not decoded: whole-kernel records"),
    "SA2MHC1": ("Metal Harbor collision", "not decoded: level data"),
    "SA2GMS1": ("Metal Harbor source objects", "not decoded: level data"),
    "SA2SLC1": ("selection corpus", "not decoded: whole-kernel records"),
    "SA2SLO1": ("selection output", "not decoded: whole-kernel records"),
    "SA2GRC1": ("rejected contact", "not decoded: whole-kernel records"),
}
GROUND = ("GroundCollisionStep window kernel", "not decoded: whole-kernel words from functions outside the ten")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def rel(p: Path) -> str:
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def cmdline(cmd: list[str]) -> str:
    return " ".join(cmd).replace(str(ROOT) + "/", "")


def write_vectors(path: Path, va: int, in_words: int, out_words: int, rows: list[list[int]]) -> None:
    with path.open("wb") as f:
        f.write(b"SA2FXV1\0")
        f.write(struct.pack("<4I", va, len(rows), in_words, out_words))
        for r in rows:
            assert len(r) == in_words
            f.write(struct.pack(f"<{in_words}I", *r))


def read_out(path: Path) -> list[list[int]]:
    data = path.read_bytes()
    assert data[:8] == b"SA2FXO1\0", path
    va, rows, words = struct.unpack_from("<3I", data, 8)
    return [list(struct.unpack_from(f"<{words}I", data, 20 + i * words * 4)) for i in range(rows)]


# ---------------------------------------------------------------- corpus decoders
def decode_npt1(data: bytes) -> tuple[list[list[int]], list[list[int]], dict]:
    version, rows, vertices, mwords = struct.unpack_from("<4I", data, 8)
    matrix = list(struct.unpack_from("<12I", data, 24))
    ins, nat = [], []
    off = 72
    for _ in range(rows):
        kind, index, sx, sy, sz, ex, ey, ez = struct.unpack_from("<8I", data, off)
        off += 32
        ins.append(matrix + [sx, sy, sz, 0])
        nat.append([ex, ey, ez])
    return ins, nat, {"schema": "SA2NPT1/v1", "version": version, "rows": rows, "mode": "point (flag 0: translation added)"}


def decode_txc1(data: bytes) -> tuple[list[list[int]], list[list[int]], dict]:
    version, calls, accepts, cw, aw = struct.unpack_from("<5I", data, 8)
    assert cw == 19 and aw == 32 and len(data) == 28 + calls * cw * 4 + accepts * aw * 4
    ins, nat, sites = [], [], {}
    off = 28
    for _ in range(calls):
        r = struct.unpack_from(f"<{cw}I", data, off)
        off += cw * 4
        site, src, mat, dst = r[0], list(r[1:4]), list(r[4:16]), list(r[16:19])
        sites[site] = sites.get(site, 0) + 1
        ins.append(mat + src + [0])
        nat.append(dst)
    return ins, nat, {"schema": "SA2TXC1/v1", "version": version, "calls": calls, "accepts_not_decoded": accepts,
                      "calls_per_site": {str(k): v for k, v in sorted(sites.items())},
                      "note": "section A (every call: site, source, matrix, destination); section B regroups the per-accept calls of A and adds nothing"}


DECODERS = {"SA2NPT1": (decode_npt1, CALC_POINT), "SA2TXC1": (decode_txc1, CALC_POINT_ACTIVE)}


def survey() -> list[dict]:
    table = []
    for d in CORPUS:
        for p in sorted(d.glob("*.bin")):
            magic = p.read_bytes()[:7].decode("ascii", "replace")
            kernel, disposition = DISPOSITIONS.get(magic, GROUND if magic.startswith("SA2G") else ("unknown", "not decoded: no stager identifies it"))
            table.append({"fixture": rel(p), "magic": magic, "bytes": p.stat().st_size, "kernel": kernel, "disposition": disposition})
    return table


# ---------------------------------------------------------------- op-sequence identity
def x87_sequence(img: bytes, va: int) -> tuple[list[str], int]:
    from iced_x86 import Decoder, Formatter, FormatterSyntax
    f = Formatter(FormatterSyntax.INTEL)
    ops, end = [], va
    for ins in Decoder(32, img[va - 0x400000: va - 0x400000 + 0x400], ip=va):
        s = f.format(ins)
        end = ins.ip + ins.len
        if s.startswith("f"):
            ops.append(s)
        if s == "ret" or s.startswith("ret "):
            break
    return ops, end


def op_identity(img: bytes) -> dict:
    a, end_a = x87_sequence(img, CALC_POINT)
    b, end_b = x87_sequence(img, CALC_POINT_ACTIVE)
    rename = {"eax": "[M", "ecx": "[P", "edx": "[O", "esp": "[S"}
    norm = lambda seq: [re.sub(r"\[(eax|ecx|edx|esp)", lambda m: rename[m.group(1)], s) for s in seq]  # noqa: E731
    same = norm(a) == norm(b)
    return {"0x00426cc0_x87_ops": len(a), "0x00426d50_x87_ops": len(b), "identical_after_base_register_renaming": same,
            "renaming": "0x00426CC0 matrix eax / point ecx / out edx; 0x00426D50 matrix eax (loaded from [0x1A557FC]) / point ecx / out edx",
            "0x00426d50_bytes": end_b - CALC_POINT_ACTIVE, "0x00426d50_bytes_sha256": hashlib.sha256(img[CALC_POINT_ACTIVE - 0x400000: end_b - 0x400000]).hexdigest(),
            "0x00426d50_calls": 0, "check": "iced-x86 over the unpacked image, x87 mnemonics from the entry to the first ret"}, same


# ---------------------------------------------------------------- 0x00426D50: lift, pass, derived harness
def derive(src: Path, dst: Path, insertions: list[tuple[str, str]]) -> None:
    """Copy a spike harness source with exact-anchor insertions (fail if an anchor is missing or ambiguous)."""
    text = src.read_text()
    for anchor, addition in insertions:
        if text.count(anchor) != 1:
            raise SystemExit(f"anchor not unique in {rel(src)}: {anchor!r}")
        text = text.replace(anchor, anchor + addition)
    dst.write_text(f"/* derived by tools/s2/entry_test/entry_test.py from {rel(src)}: {len(insertions)} insertion(s) for 0x00426D50 */\n" + text)


def build_active(img: bytes, nbytes: int, skip_lift: bool) -> dict:
    hd = OUT / "harness"
    hd.mkdir(parents=True, exist_ok=True)
    log: dict = {"lift": {}, "pass": {}, "derived_sources": {}, "build": {}}
    ll = OUT / "sub_00426D50.ll"
    if not (skip_lift and ll.exists()):
        hexbytes = img[CALC_POINT_ACTIVE - 0x400000: CALC_POINT_ACTIVE - 0x400000 + nbytes].hex()
        inner = (f"mount --bind {REMILL_PREFIX} /usr/local && /usr/local/bin/remill-lift-14 --arch x86 --os windows "
                 f"--address {CALC_POINT_ACTIVE:#x} --bytes {hexbytes} --ir_out {ll}")
        p = sh(["unshare", "-Urm", "--propagation", "unchanged", "sh", "-c", inner], timeout=600)
        log["lift"] = {"command": f"unshare -Urm --propagation unchanged sh -c '{inner.replace(hexbytes, f'<{nbytes} bytes hex>')}'",
                       "rc": p.returncode, "error_head": S.error_head(p.stderr + p.stdout) if p.returncode else ""}
    else:
        log["lift"] = {"command": "reused", "rc": 0, "error_head": ""}
    text = ll.read_text(errors="replace") if ll.exists() else ""
    log["lift"]["lifted"] = bool(re.search(r"^define [^\n]*@sub_426d50\(", text, re.M))
    log["lift"]["ll_lines"] = text.count("\n")
    log["lift"]["missing_blocks"] = len(re.findall(r"__remill_missing_block", text))
    log["lift"]["ll_sha256"] = sha(ll) if ll.exists() else None
    if not log["lift"]["lifted"]:
        return log
    patched = OUT / "sub_00426D50.pc24pass.ll"
    stats = OUT / "sub_00426D50.pc24pass.stats.json"
    cmd = [sys.executable, str(PASS / "pc24pass.py"), "--helper-ll", str(HELPER_LL), str(ll), str(patched), "--stats", str(stats)]
    p = sh(cmd)
    log["pass"] = {"command": cmdline(cmd), "rc": p.returncode, "error_head": S.error_head(p.stderr) if p.returncode else "",
                   "stats": json.loads(stats.read_text()) if stats.exists() else None, "ll_sha256": sha(patched) if patched.exists() else None}
    if p.returncode:
        return log
    derive(FIX / "shapes.h", hd / "shapes.h", [
        ("#define FN_PUSH_IDENTITY 0x0044B210u /* matrix stack push then identity; calls memset (0x7acde0) */\n",
         "#define FN_CALC_POINT_ACTIVE 0x00426D50u /* ecx=point, edx=out; matrix = *[0x1A557FC] (stack top); translation added */\n"),
        ("    {FN_PUSH_IDENTITY, \"push_identity\",  14, 14},  /* frame[12] k limit_k -> eax, stack-top word, frame[12] */\n",
         "    {FN_CALC_POINT_ACTIVE, \"calc_point_active\", 15, 3}, /* m[12] p[3] -> out[3] */\n")])
    derive(FIX / "marshal.c", hd / "marshal.c", [
        ("    case FN_CALC_POINT:\n        words_to(A_MATRIX, in, 12); words_to(A_POINT, in + 12, 3);\n        *gp32(c->esp0 + 4) = in[15];\n        c->eax = A_MATRIX; c->ecx = A_POINT; c->edx = A_OUT; break;\n",
         "    case FN_CALC_POINT_ACTIVE:\n        words_to(A_MSTACK, in, 12); words_to(A_POINT, in + 12, 3);\n        matrix_stack(0, A_MSTACK_FRAMES - 1);\n        c->ecx = A_POINT; c->edx = A_OUT; break;\n"),
        ("    case FN_CALC_VECTOR: case FN_CALC_POINT:\n        words_from(A_OUT, out, 3); break;\n",
         "    case FN_CALC_POINT_ACTIVE:\n        words_from(A_OUT, out, 3); break;\n")])
    derive(FIX / "remill_harness.cpp", hd / "remill_harness.cpp", [
        ("Memory *sub_44b210(State *, gaddr, Memory *) __attribute__((weak));\n",
         "Memory *sub_426d50(State *, gaddr, Memory *) __attribute__((weak));\n"),
        ("    case FN_TRANSFORM_DIR: return sub_468fa0; case FN_PUSH_IDENTITY: return sub_44b210;\n",
         "    case FN_CALC_POINT_ACTIVE: return sub_426d50;\n")])
    for f in ("shapes.h", "marshal.c", "remill_harness.cpp"):
        log["derived_sources"][f] = {"from": rel(FIX / f), "sha256": sha(hd / f)}

    def cc(args: list[str], out: Path) -> dict:
        cmd = [ZIG] + args + ["-o", str(out)]
        p = sh(cmd, cwd=hd)
        return {"ok": p.returncode == 0, "command": cmdline(cmd), "error_head": S.error_head(p.stderr) if p.returncode else ""}

    common = ["cc"] + TARGET + ["-O1", "-w", "-fno-sanitize=undefined", "-c", "-I", str(hd), "-I", str(FIX)]
    b = log["build"]
    b["guest.c"] = cc(common + [str(FIX / "guest.c")], hd / "guest.o")
    b["vectors.c"] = cc(common + [str(FIX / "vectors.c")], hd / "vectors.o")
    b["marshal.c"] = cc(common + [str(hd / "marshal.c")], hd / "marshal.o")
    b["remill_harness.cpp"] = cc(["c++"] + TARGET + ["-O1", "-w", "-std=c++17", "-c", "-I", str(hd), "-I", str(FIX), "-I", str(REMILL_INC),
                                  str(hd / "remill_harness.cpp")], hd / "remill_harness.o")
    for tag, ir in (("fp80", ll), ("pc24pass", patched)):
        b[f"ir_{tag}"] = cc(["cc"] + TARGET + ["-O1", "-w", "-c", str(ir)], hd / f"ir_{tag}.o")
        b[f"link_{tag}"] = cc(["c++"] + TARGET + LINK + [str(hd / f) for f in ("remill_harness.o", "guest.o", "vectors.o", "marshal.o", f"ir_{tag}.o")],
                              hd / f"remill_{tag}_00426D50")
    return log


# ---------------------------------------------------------------- classification
def classify(native: list[int], pc24: list[int], fp80: list[int]) -> str:
    differ = pc24 != fp80
    eq_pc, eq_fp = native == pc24, native == fp80
    if not differ:
        return "agree_native_equals_both" if eq_pc else "agree_native_equals_neither"
    if eq_pc:
        return "native_equals_pc24_only"
    if eq_fp:
        return "native_equals_fp80_only"
    return "differ_native_equals_neither"


def lifted_summary(obs: list[list[int]], native: list[list[int]], pc24: list[list[int]], fp80: list[list[int]], idx: list[int]) -> dict:
    n = len(idx)
    ok = sum(1 for i in idx if obs[i][0] == 0)
    m_nat = sum(1 for i in idx if obs[i][0] == 0 and obs[i][2:] == native[i])
    m_fp = sum(1 for i in idx if obs[i][0] == 0 and obs[i][2:] == fp80[i][2:])
    m_pc = sum(1 for i in idx if obs[i][0] == 0 and obs[i][2:] == pc24[i][2:])
    return {"rows": n, "returned": ok, "matches_native": m_nat, "matches_pc24_model": m_pc, "matches_fp80_model": m_fp,
            "matches_neither_model": sum(1 for i in idx if obs[i][0] != 0 or (obs[i][2:] != pc24[i][2:] and obs[i][2:] != fp80[i][2:]))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-lift", action="store_true", help="reuse the 0x00426D50 IR from an earlier run")
    ap.add_argument("--no-receipt", action="store_true")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for need in (IMAGE, SPIKE_BUILD / "reference", SPIKE_BUILD / "remill_00426CC0", PASS_BUILD / "remill_00426CC0", HELPER_LL):
        if not need.exists():
            raise SystemExit(f"missing S1 build output {rel(need)}: run tools/spike/fixtures/run_fixtures.py and tools/spike/pc24pass/run_pc24pass.py first")
    img = IMAGE.read_bytes()
    ref = SPIKE_BUILD / "reference"
    results: dict = {"schema": "sa2-recomp-s2-entry-test/v1", "written": S.now(), "route": 1,
                     "image_sha256": hashlib.sha256(img).hexdigest(), "corpus": survey(), "fixtures": {}}
    identity, same = op_identity(img)
    results["op_sequence_identity"] = identity
    if not same:
        raise SystemExit("0x00426D50's x87 sequence is not 0x00426CC0's; the corpus rows cannot be run through the lifted 0x00426CC0")
    active = build_active(img, identity["0x00426d50_bytes"], args.skip_lift)
    results["active_lift"] = active
    active_ok = all(active["build"].get(k, {}).get("ok") for k in ("link_fp80", "link_pc24pass"))

    binaries = {"remill_fp80_0x00426cc0": SPIKE_BUILD / "remill_00426CC0", "remill_pc24pass_0x00426cc0": PASS_BUILD / "remill_00426CC0"}
    if active_ok:
        binaries["remill_fp80_0x00426d50"] = OUT / "harness" / "remill_fp80_00426D50"
        binaries["remill_pc24pass_0x00426d50"] = OUT / "harness" / "remill_pc24pass_00426D50"
    results["binaries_sha256"] = {k: sha(v) for k, v in binaries.items()}

    for entry in results["corpus"]:
        if entry["magic"] not in DECODERS:
            continue
        p = ROOT / entry["fixture"]
        decode, va = DECODERS[entry["magic"]]
        data = p.read_bytes()
        ins, native, meta = decode(data)
        name = p.stem
        fx: dict = {"fixture": entry["fixture"], "sha256": sha(p), "bytes": len(data), "function": HEX8(va), "meta": meta,
                    "rows": len(ins), "distinct_rows": len({tuple(r) for r in ins}), "authority": "directly-traced"}
        vec = OUT / f"{name}.calc_point.bin"        # 0x00426CC0 shape: m[12] p[3] flag
        write_vectors(vec, CALC_POINT, 16, 3, ins)
        fx["vectors_sha256"] = sha(vec)
        model = {}
        for m in ("pc24", "fp80"):
            o = OUT / f"{name}.{m}.bin"
            r = sh([str(ref), m, str(vec), str(o)])
            if r.returncode:
                raise SystemExit(f"reference {m} failed on {name}: {r.stderr}")
            model[m] = read_out(o)
        classes = [classify(native[i], model["pc24"][i][2:], model["fp80"][i][2:]) for i in range(len(ins))]
        counts = {k: classes.count(k) for k in ("agree_native_equals_both", "agree_native_equals_neither", "native_equals_pc24_only",
                                                 "native_equals_fp80_only", "differ_native_equals_neither")}
        disc = [i for i, c in enumerate(classes) if c == "native_equals_pc24_only"]
        fx["classes"] = counts
        fx["rows_where_models_differ"] = sum(1 for i in range(len(ins)) if model["pc24"][i] != model["fp80"][i])
        fx["discriminating_rows"] = len(disc)
        fx["discriminating_distinct_rows"] = len({tuple(ins[i]) for i in disc})
        fx["exit_status_all_zero_in_models"] = all(model[m][i][0] == 0 for m in model for i in range(len(ins)))
        # lifted runs on every row
        fx["lifted"] = {}
        outs = {}
        for tag, binary in binaries.items():
            bva = CALC_POINT_ACTIVE if tag.endswith("0x00426d50") else CALC_POINT
            v = vec
            if bva == CALC_POINT_ACTIVE:
                v = OUT / f"{name}.calc_point_active.bin"
                write_vectors(v, CALC_POINT_ACTIVE, 15, 3, [r[:15] for r in ins])
            o = OUT / f"{name}.{tag}.bin"
            r = sh([str(binary), str(IMAGE), str(v), str(o)])
            if r.returncode:
                fx["lifted"][tag] = {"error": f"harness exited {r.returncode}: {S.error_head(r.stderr)}"}
                continue
            obs = read_out(o)
            outs[tag] = obs
            fx["lifted"][tag] = {"binary": rel(binary), "all_rows": lifted_summary(obs, native, model["pc24"], model["fp80"], list(range(len(ins)))),
                                 "discriminating_rows": lifted_summary(obs, native, model["pc24"], model["fp80"], disc)}
        if disc:
            dvec = OUT / f"{name}.discriminating.calc_point.bin"
            write_vectors(dvec, CALC_POINT, 16, 3, [ins[i] for i in disc])
            fx["discriminating_vectors"] = {"path": rel(dvec), "sha256": sha(dvec), "rows": len(disc), "layout": "SA2FXV1: m[12] p[3] flag=0 per row, native words in the fixture at the same index order"}
            seen, inline = set(), []
            for i in disc:
                key = tuple(ins[i])
                if key in seen:
                    continue
                seen.add(key)
                row = {"index": i, "matrix": [HEX8(w) for w in ins[i][:12]], "point": [HEX8(w) for w in ins[i][12:15]],
                       "native": [HEX8(w) for w in native[i]], "pc24_model": [HEX8(w) for w in model["pc24"][i][2:]],
                       "fp80_model": [HEX8(w) for w in model["fp80"][i][2:]],
                       "lifted": {tag: [HEX8(w) for w in outs[tag][i][2:]] for tag in outs}}
                inline.append(row)
                if len(inline) >= INLINE_ROWS:
                    break
            fx["discriminating_rows_inline"] = inline
        results["fixtures"][name] = fx
        print(f"{name}: rows={len(ins)} differ={fx['rows_where_models_differ']} pc24only={counts['native_equals_pc24_only']} "
              f"fp80only={counts['native_equals_fp80_only']} both={counts['agree_native_equals_both']} "
              f"neither={counts['agree_native_equals_neither'] + counts['differ_native_equals_neither']}")
        for tag, L in fx["lifted"].items():
            if "error" in L:
                print(f"   {tag}: {L['error']}")
            else:
                d = L["discriminating_rows"]
                print(f"   {tag}: discriminating {d['matches_native']}/{d['rows']} native, all rows {L['all_rows']['matches_native']}/{L['all_rows']['rows']} native")

    # verdict
    total_disc = sum(f["discriminating_rows"] for f in results["fixtures"].values())
    fp_only = sum(f["classes"]["native_equals_fp80_only"] for f in results["fixtures"].values())
    neither = sum(f["classes"]["agree_native_equals_neither"] + f["classes"]["differ_native_equals_neither"] for f in results["fixtures"].values())
    m = sum(f["lifted"].get("remill_pc24pass_0x00426cc0", {}).get("discriminating_rows", {}).get("matches_native", 0) for f in results["fixtures"].values())
    if total_disc:
        verdict = (f"entry test passed: {total_disc} native rows discriminate PC24 and the lifted output matches native on {m} of {total_disc}")
        if fp_only or neither:
            verdict += f" (rows where native equals fp80 only: {fp_only}; neither model: {neither})"
    else:
        verdict = f"entry test failed: the inherited corpus has no native row that discriminates PC24 (native equals fp80 only: {fp_only}; neither: {neither}); route 2 is required"
    results["verdict"] = verdict
    (OUT / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    print(verdict)
    if not args.no_receipt:
        write_receipt(results)
        print("wrote", rel(RECEIPT))


def write_receipt(R: dict) -> None:
    head = sh(["git", "-C", str(ROOT), "rev-parse", "HEAD"]).stdout.strip()
    fx = R["fixtures"]
    table = []
    for name, f in fx.items():
        c = f["classes"]
        table.append({"fixture": f["fixture"], "sha256": f["sha256"], "function": f["function"], "rows": f["rows"], "distinct_rows": f["distinct_rows"],
                      "rows_where_models_differ": f["rows_where_models_differ"], "native_equals_pc24_only": c["native_equals_pc24_only"],
                      "native_equals_fp80_only": c["native_equals_fp80_only"], "native_equals_both": c["agree_native_equals_both"],
                      "native_equals_neither": c["agree_native_equals_neither"] + c["differ_native_equals_neither"],
                      "discriminating_distinct_rows": f["discriminating_distinct_rows"]})
    lifted = {name: f["lifted"] for name, f in fx.items()}
    inline = {name: f.get("discriminating_rows_inline", []) for name, f in fx.items() if f.get("discriminating_rows_inline")}
    active = R["active_lift"]
    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s2-pc24-native-rows-v1",
        "date": dt.date.today().isoformat(),
        "stage": "S2",
        "question": "S2 entry test (decision 2026-09-19-G1 section 1.2): does the inherited fixture corpus hold native rows on which the PC24 model and the 80-bit model give different words, which model do the native words equal, and does the lifted remill IR with the pc24 pass reproduce them?",
        "route": "1: inherited corpus (private/fixtures/LocalAssets-SA2); no capture taken",
        "inputs": {
            "exe": "private/exe/sonic2app-unpacked.exe", "exe_sha256": sha(EXE), "image_sha256": R["image_sha256"],
            "commit": head,
            "tool": {"path": "tools/s2/entry_test/entry_test.py", "sha256": sha(HERE / "entry_test.py")},
            "reference": {"source": "tools/spike/fixtures/pc24_reference.c", "sha256": sha(FIX / "pc24_reference.c"), "binary": rel(SPIKE_BUILD / "reference"),
                          "binary_sha256": sha(SPIKE_BUILD / "reference"),
                          "models": "pc24: every basic op in double then RoundSignificand24; fp80: long double, rounded at the float stores (S1 receipts, unchanged)"},
            "pass": {"source": "tools/spike/pc24pass/pc24pass.py", "sha256": sha(PASS / "pc24pass.py"), "helper_ll_sha256": sha(HELPER_LL)},
            "lifted_ir": {"0x00426cc0_fp80": {"path": rel(ORIG_IR / "sub_00426CC0.ll"), "sha256": sha(ORIG_IR / "sub_00426CC0.ll")},
                          "0x00426cc0_pc24pass": {"path": rel(PASS_IR / "sub_00426CC0.ll"), "sha256": sha(PASS_IR / "sub_00426CC0.ll")},
                          "0x00426d50_fp80": {"path": rel(OUT / "sub_00426D50.ll"), "sha256": active["lift"].get("ll_sha256")},
                          "0x00426d50_pc24pass": {"path": rel(OUT / "sub_00426D50.pc24pass.ll"), "sha256": active["pass"].get("ll_sha256")}},
            "binaries_sha256": R["binaries_sha256"],
            "harness": "tools/spike/fixtures (S1, unchanged) for 0x00426CC0; for 0x00426D50 a variant derived at build time by three anchored insertions (shape, marshal cases, symbol lookup), sources and hashes under active_lift.derived_sources",
            "corpus_provenance": "private/fixtures/LocalAssets-SA2/Precise/contact-transform-corpus-v1.bin was staged by the predecessor's stage_precise_contact_transform_corpus_fixture.py from the transform-call-v4 capture (private/predecessor-native-run/transform-call-v4/), which wrapped all eight call sites of 0x00426D50 inside CL_ColPolCheckTouchRe and recorded source, matrix and destination per call; the stager required destination == matrix * source + translation under its own float32 model on every row",
        },
        "command": "source tools/env.sh && .venv/bin/python tools/s2/entry_test/entry_test.py",
        "corpus_survey": R["corpus"],
        "op_sequence_identity": R["op_sequence_identity"],
        "active_lift": {"lift": active["lift"],
                        "pass": {k: v for k, v in active["pass"].items() if k != "stats"}
                        | {k: (active["pass"].get("stats") or {}).get(k) for k in ("ops_instrumented", "by_op", "by_semantic")},
                        "derived_sources": active["derived_sources"], "build_ok": {k: v.get("ok") for k, v in active["build"].items()}},
        "per_fixture": table,
        "lifted": lifted,
        "discriminating_rows_inline": inline,
        "discriminating_rows_full": {name: f["discriminating_vectors"] for name, f in fx.items() if "discriminating_vectors" in f},
        "which_model_native_matches": "PC24 on every discriminating row; the fp80 model on none" if all(f["classes"]["native_equals_fp80_only"] == 0 for f in fx.values()) else "see per_fixture",
        "verdict": R["verdict"],
        "authority": "directly-traced (native rows); the model words are statically-translated reference output; the lifted words are measured",
        "limits": [
            "The discriminating rows are inputs of 0x00426D50, not 0x00426CC0; they are run through the lifted 0x00426CC0 on the strength of op_sequence_identity (42 x87 instructions identical after base-register renaming, checked from the image by this tool) and through the lifted 0x00426D50 itself via the derived harness.",
            "All rows use control word 0x007F and one op class (fmul, fadd, faddp); no fsub, fdiv, fsqrt, fistp or fldcw is exercised by this corpus.",
            "Only two of the corpus's fixtures decode to a leaf function the harness can drive; the dispositions of the rest are in corpus_survey.",
        ],
    }
    RECEIPT.write_text(json.dumps(receipt, indent=2) + "\n")


if __name__ == "__main__":
    main()
