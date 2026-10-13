#!/usr/bin/env python3
"""Run the ten spike functions' lifted output against fixtures (S1 receipt 7).

For remill (LLVM IR + stub runtime, remill_harness.cpp) and RetDec (its C
under gnu89 + prelude, retdec_glue.c) this builds one 32-bit static binary
per function with zig (x86_64-linux-musl: remill's IR hardcodes its x86-64
host's long-double size, and compiled for i386 every x87 store wrote 0), runs
vector files through it, and compares every row
against an expected row:
  native      rows traced from the game (authority: directly-traced):
              0x00426CC0 from private/fixtures/.../ninja-point-transform-v1.bin
              (53 rows; the matrix and expected words are the predecessor's
              SA2NPT1/v1 layout);
  pc24        synthetic rows (seeded, gameplay ranges plus edge values) whose
              expected words come from pc24_reference.c in its pc24 model
              (authority: statically-translated reference, not native).
Every row is also run through the reference's fp80 model. A row is EXACT when
the lifted words equal the expected words, RESIDUE when they differ from the
expected words but equal the fp80 model (the difference is attributable to
80-bit intermediates without 24-bit rounding), else FAIL. Status and exit
address are compared like payload words, so wrong control flow is a FAIL.

The PC24 probe (--probe is implicit) searches the length and unit-vector rows
for inputs where the pc24 and fp80 models disagree, adds constructed ones,
and records what each lifter produced for them.

Outputs (all ignored by git): private/generated/spike/fixtures/{image.bin,
build/, vectors/, out/, results.json}. Timeline events are appended to
tools/spike/timeline.jsonl. The receipts are written by tools/spike/
write_receipt.py from results.json.

Usage: .venv/bin/python tools/spike/fixtures/run_fixtures.py [--no-build]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import spikelib as S  # noqa: E402

ROOT = S.ROOT
GEN = ROOT / "private" / "generated" / "spike"
OUTDIR = GEN / "fixtures"
REMILL_INC = ROOT / "private" / "lifters" / "remill" / "usr" / "local" / "include"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
NPT_FIXTURE = ROOT / "private" / "fixtures" / "LocalAssets-SA2" / "Precise" / "ninja-point-transform-v1.bin"
V2A_FIXTURE = ROOT / "private" / "fixtures" / "LocalAssets-SA2" / "Precise" / "vector-to-angles-v1.bin"
ZIG = str(S.ZIG)
TARGET = ["-target", "x86_64-linux-musl"]
LINK = ["-Wl,--image-base=0x10000000"]   # keep the guest range 0x400000..0x4000000 free (guest.h)
A_MSTACK = 0x03100000

# va, name, in_words, out_words, payload field names, in-ten callees (extra IR to link)
FUNCTIONS = [
    (0x004273B0, "calc_vector",   15, 3,  ["x", "y", "z"], []),
    (0x00426CC0, "calc_point",    16, 3,  ["x", "y", "z"], []),
    (0x0048B9F0, "object_tree",   21, 12, [f"m{i}" for i in range(12)], []),
    (0x00446960, "clamp_yaw",      3, 1,  ["eax"], []),
    (0x004469E0, "vector_angle",   6, 2,  ["eax", "dot"], []),
    (0x0077F3A0, "unit_vector",    3, 3,  ["x", "y", "z"], []),
    (0x0077FC30, "length",         3, 1,  ["length"], []),
    (0x00468E70, "rotate_entity",  6, 4,  ["x", "y", "z", "stack_top"], [0x0044B210, 0x004273B0]),
    (0x00468FA0, "transform_dir",  9, 4,  ["x", "y", "z", "stack_top"], [0x0044B210, 0x004273B0]),
    (0x0044B210, "push_identity", 14, 14, ["eax", "stack_top"] + [f"m{i}" for i in range(12)], []),
]
SEED = 20260919
# Callees outside the ten that no lifted IR provides (the harness traps at them).
UNLIFTED_CALLEES = {0x00427470, 0x004274E0, 0x004275B0, 0x00427680, 0x00427750, 0x0048B610, 0x00459260}


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def timeline_once(lifter: str, event: str, detail: str) -> None:
    """Append a milestone unless the timeline already records it (re-runs add nothing)."""
    for line in S.TIMELINE.read_text().splitlines():
        if line.strip():
            e = json.loads(line)
            if e["lifter"] == lifter and e["event"] == event:
                return
    S.timeline(lifter, event, detail)


def fw(x: float) -> int:
    return struct.unpack("<I", struct.pack("<f", x))[0]


def wf(w: int) -> float:
    return struct.unpack("<f", struct.pack("<I", w))[0]


# ---------------------------------------------------------------- image
def dump_image() -> Path:
    import pefile
    path = OUTDIR / "image.bin"
    if path.exists():
        return path
    pe = pefile.PE(str(EXE), fast_load=True)
    img = pe.get_memory_mapped_image()
    path.write_bytes(bytes(img))
    return path


# ---------------------------------------------------------------- build
def build(no_build: bool) -> dict:
    bdir = OUTDIR / "build"
    bdir.mkdir(parents=True, exist_ok=True)
    log = {"harness": {}, "remill": {}, "retdec": {}, "reference": {}}

    def cc(args: list[str], out: Path) -> dict:
        cmd = [ZIG] + args + ["-o", str(out)]
        p = sh(cmd, cwd=bdir)
        return {"ok": p.returncode == 0, "command": " ".join(cmd).replace(str(ROOT) + "/", ""),
                "error_head": S.error_head(p.stderr) if p.returncode else ""}

    if no_build and (bdir / "reference").exists():
        return json.loads((bdir / "build.json").read_text())
    common = ["cc"] + TARGET + ["-O1", "-w", "-fno-sanitize=undefined", "-c", "-I", str(HERE)]
    for src in ("guest.c", "vectors.c", "marshal.c"):
        log["harness"][src] = cc(common + [str(HERE / src)], bdir / (src[:-2] + ".o"))
    log["harness"]["remill_harness.cpp"] = cc(
        ["c++"] + TARGET + ["-O1", "-w", "-std=c++17", "-c", "-I", str(HERE), "-I", str(REMILL_INC), str(HERE / "remill_harness.cpp")],
        bdir / "remill_harness.o")
    log["harness"]["retdec_harness.c"] = cc(common + [str(HERE / "retdec_harness.c")], bdir / "retdec_harness.o")
    log["reference"]["pc24_reference"] = cc(
        ["cc"] + TARGET + ["-O1", "-w", "-I", str(HERE), str(HERE / "pc24_reference.c"), str(HERE / "vectors.c"), "-lm"],
        bdir / "reference")
    objs = [str(bdir / f) for f in ("guest.o", "vectors.o", "marshal.o")]
    for va, name, *_rest, callees in FUNCTIONS:
        irs = [va] + callees
        ir_objs = []
        entry = {"ir_compile": {}, "link": None}
        for cva in irs:
            ll = GEN / "remill" / f"sub_{cva:08X}.ll"
            o = bdir / f"remill_ir_{cva:08X}.o"
            if not o.exists():
                entry["ir_compile"][f"{cva:#010x}"] = cc(["cc"] + TARGET + ["-O1", "-w", "-c", str(ll)], o)
            else:
                entry["ir_compile"][f"{cva:#010x}"] = {"ok": True, "command": "cached", "error_head": ""}
            ir_objs.append(str(o))
        entry["link"] = cc(["c++"] + TARGET + LINK + [str(bdir / "remill_harness.o")] + objs + ir_objs, bdir / f"remill_{va:08X}")
        log["remill"][f"{va:#010x}"] = entry
        c = GEN / "retdec" / f"sub_{va:08X}.c"
        glue = cc(["cc"] + TARGET + ["-O0", "-w", "-fno-sanitize=undefined", "-std=gnu89", "-c", "-I", str(HERE), f"-DFN={va:#010x}",
                  f"-DRETDEC_C=\"{c}\"", str(HERE / "retdec_glue.c")], bdir / f"retdec_glue_{va:08X}.o")
        entry = {"glue_compile": glue, "link": None}
        if glue["ok"]:
            entry["link"] = cc(["cc"] + TARGET + LINK + [str(bdir / "retdec_harness.o")] + objs + [str(bdir / f"retdec_glue_{va:08X}.o")],
                               bdir / f"retdec_{va:08X}")
        log["retdec"][f"{va:#010x}"] = entry
    (bdir / "build.json").write_text(json.dumps(log, indent=2))
    return log


# ---------------------------------------------------------------- vectors
def write_vectors(path: Path, va: int, in_words: int, out_words: int, rows: list[list[int]]) -> None:
    with path.open("wb") as f:
        f.write(b"SA2FXV1\0")
        f.write(struct.pack("<4I", va, len(rows), in_words, out_words))
        for r in rows:
            assert len(r) == in_words, (va, len(r), in_words)
            f.write(struct.pack(f"<{in_words}I", *[w & 0xFFFFFFFF for w in r]))


def read_out(path: Path) -> list[list[int]]:
    data = path.read_bytes()
    assert data[:8] == b"SA2FXO1\0", path
    va, rows, words = struct.unpack_from("<3I", data, 8)
    return [list(struct.unpack_from(f"<{words}I", data, 20 + i * words * 4)) for i in range(rows)]


def rnd_float(rng: random.Random, lo_exp: float = -3, hi_exp: float = 4) -> float:
    mag = 10 ** rng.uniform(lo_exp, hi_exp)
    return fw(rng.choice((-1, 1)) * mag)


def rnd_vec(rng, lo=-3, hi=4):
    return [fw(rnd_float(rng, lo, hi)) for _ in range(3)]


def rnd_unit(rng):
    while True:
        v = [rng.gauss(0, 1) for _ in range(3)]
        n = math.sqrt(sum(x * x for x in v))
        if n > 1e-3:
            return [fw(x / n) for x in v]


def rnd_matrix(rng) -> list[int]:
    a, b, c = (rng.uniform(-math.pi, math.pi) for _ in range(3))
    ca, sa, cb, sb, cc_, sc = math.cos(a), math.sin(a), math.cos(b), math.sin(b), math.cos(c), math.sin(c)
    r = [[cb * cc_, -cb * sc, sb], [sa * sb * cc_ + ca * sc, -sa * sb * sc + ca * cc_, -sa * cb],
         [-ca * sb * cc_ + sa * sc, ca * sb * sc + sa * cc_, ca * cb]]
    t = [rng.uniform(-3000, 3000) for _ in range(3)]
    return [fw(x) for row, tt in zip(r, t) for x in (row + [tt])]


EDGE = [0.0, -0.0, 1.0, -1.0, 0.5, 1e-20, -1e-20, 3.4e38, 1e-38, 12.0, -149.0, -1593.0, 1.0000001, 0.99999994]


def edge_vecs() -> list[list[int]]:
    e = [fw(x) for x in EDGE]
    return [[e[i], e[(i + 3) % len(e)], e[(i + 7) % len(e)]] for i in range(len(e))] + [[0, 0, 0], [fw(-0.0)] * 3]


def native_calc_point() -> tuple[list[list[int]], list[list[int]], dict]:
    data = NPT_FIXTURE.read_bytes()
    assert data[:8] == b"SA2NPT1\0"
    version, rows, vertices, mwords = struct.unpack_from("<4I", data, 8)
    matrix = list(struct.unpack_from("<12I", data, 24))
    ins, exps = [], []
    off = 72
    for _ in range(rows):
        kind, index, sx, sy, sz, ex, ey, ez = struct.unpack_from("<8I", data, off)
        off += 32
        ins.append(matrix + [sx, sy, sz, 0])
        exps.append([0, 0, ex, ey, ez])
    meta = {"fixture": str(NPT_FIXTURE.relative_to(ROOT)), "schema": "SA2NPT1/v1", "rows": rows,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "mode": "point (flag 0: translation added)"}
    return ins, exps, meta


def native_angles() -> dict[tuple[int, int, int], tuple[int, int]]:
    data = V2A_FIXTURE.read_bytes()
    assert data[:8] == b"SA2V2A1\0"
    version, cases, words = struct.unpack_from("<3I", data, 8)
    table = {}
    for i in range(cases):
        x, y, z, a1, a2 = struct.unpack_from("<5I", data, 20 + i * 20)
        table[(x, y, z)] = (a1, a2)
    return table


def synthetic(va: int, rng: random.Random, n: int) -> list[list[int]]:
    rows: list[list[int]] = []
    if va in (0x004273B0, 0x00426CC0):
        mats = [rnd_matrix(rng) for _ in range(n // 8)] + [[0x3F800000, 0, 0, 0, 0, 0x3F800000, 0, 0, 0, 0, 0x3F800000, 0]]
        mats.append([fw(x) for x in [1, 0, 0, 12, 0, 0, 1, -149, 0, -1, 0, -1593]])
        mats += [[fw(rnd_float(rng, -2, 2)) for _ in range(12)] for _ in range(n // 8)]
        for m in mats:
            for _ in range(4):
                v = rnd_vec(rng) if rng.random() < 0.7 else rnd_unit(rng)
                rows.append(m + v + ([rng.choice((0, 0, 1))] if va == 0x00426CC0 else []))
            for e in edge_vecs()[:4]:
                rows.append(m + e + ([0] if va == 0x00426CC0 else []))
    elif va == 0x0048B9F0:
        for _ in range(n // 16):
            rows.append(rnd_matrix(rng) + rnd_vec(rng) + [rng.randrange(0, 0x10000) for _ in range(3)] + [fw(1.0)] * 3)
        rows.append([fw(x) for x in [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0]] + [fw(12.0), fw(-149.0), fw(-1593.0), 0xFFFFC000, 0, 0] + [fw(1.0)] * 3)
    elif va == 0x00446960:
        for _ in range(n):
            rows.append([rng.randrange(0, 0x10000), rng.randrange(0, 0x10000), rng.choice((0, 1, 0x100, 0x800, 0x2000, 0x4000, 0x7FFF, rng.randrange(0, 0x8000)))])
        rows += [[0, 0, 0], [0x8000, 0, 0x100], [0, 0x8000, 0x100], [0x1234, 0xFFFF, 0x10]]
    elif va == 0x004469E0:
        for _ in range(n):
            a = rnd_unit(rng)
            b = rnd_unit(rng) if rng.random() < 0.8 else rnd_vec(rng, -1, 1)
            if rng.random() < 0.15:
                b = [fw(-wf(w)) for w in a]  # antiparallel: dot near -1
            rows.append(a + b)
        rows += [e + e for e in edge_vecs()[:6]]
        rows.append([fw(1.0), 0, 0, fw(-1.0), 0, 0])
    elif va in (0x0077F3A0, 0x0077FC30):
        for _ in range(n):
            rows.append(rnd_vec(rng) if rng.random() < 0.6 else rnd_unit(rng))
        rows += edge_vecs()
    elif va == 0x00468E70:
        for _ in range(n):
            rows.append(rnd_vec(rng) + [0, 0, 0])
        rows += [e + [0, 0, 0] for e in edge_vecs()]
        rows += [rnd_vec(rng) + [rng.randrange(1, 0x10000), 0, 0], rnd_vec(rng) + [0, rng.randrange(1, 0x10000), 0],
                 rnd_vec(rng) + [0, 0, rng.randrange(1, 0x10000)]]
    elif va == 0x00468FA0:
        table = native_angles()
        zero = [k for k, v in table.items() if v == (0, 0)]
        nonzero = [k for k, v in table.items() if v != (0, 0)]
        for _ in range(n):
            d = rng.choice(zero)
            rows.append(list(d) + rnd_vec(rng) + [0] + list(table[d]))
        for e in edge_vecs():
            d = zero[0]
            rows.append(list(d) + e + [0] + list(table[d]))
        for _ in range(4):
            d = rng.choice(nonzero)
            rows.append(list(d) + rnd_vec(rng) + [0] + list(table[d]))
        for _ in range(2):
            d = rng.choice(zero)
            rows.append(list(d) + rnd_vec(rng) + [rng.randrange(1, 0x10000)] + list(table[d]))
    elif va == 0x0044B210:
        for _ in range(n // 4):
            k = rng.randrange(0, 15)
            rows.append([fw(rnd_float(rng, -1, 3)) for _ in range(12)] + [k, rng.choice((15, 15, k, max(k - 1, 0)))])
        rows += [[0] * 12 + [0, 15], [0] * 12 + [15, 15], [0] * 12 + [14, 15]]
    return rows


# ---------------------------------------------------------------- compare
def classify(observed: list[int], expected: list[int], fp80: list[int]) -> str:
    if observed == expected:
        return "EXACT"
    if observed == fp80:
        return "RESIDUE"
    return "FAIL"


def field_names(fields: list[str]) -> list[str]:
    return ["status", "exit_pc"] + fields


def run_binary(binary: Path, args: list[str]) -> subprocess.CompletedProcess:
    return sh([str(binary)] + args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-build", action="store_true")
    ap.add_argument("--rows", type=int, default=256)
    ap.add_argument("--only", help="run one function by name (debugging; results.json is still rewritten)")
    args = ap.parse_args()
    OUTDIR.mkdir(parents=True, exist_ok=True)
    (OUTDIR / "vectors").mkdir(exist_ok=True)
    (OUTDIR / "out").mkdir(exist_ok=True)
    image = dump_image()
    log = build(args.no_build)
    bdir = OUTDIR / "build"
    ref = bdir / "reference"
    if not ref.exists():
        raise SystemExit("reference did not build: " + json.dumps(log["reference"]))
    timeline_once("fixtures", "harness_rebuilt_x86_64",
                  "first harness build was i386 (x86-linux-musl, matching the IR's datalayout): every remill float store read 0 because "
                  "the IR memsets a 16-byte long-double union that is 12 bytes on i386, clobbering the neighbouring operand copy; "
                  "rebuilt for x86_64-linux-musl with the ELF at 0x10000000 so the guest range stays free")
    for lifter in ("remill", "retdec"):
        if any(e.get("link") and e["link"]["ok"] for e in log[lifter].values()):
            timeline_once(lifter, "fixture_harness_compiles",
                       f"{sum(1 for e in log[lifter].values() if e.get('link') and e['link']['ok'])}/10 fixture binaries linked "
                       f"(tools/spike/fixtures, zig cc -target x86_64-linux-musl)")

    rng = random.Random(SEED)
    results = {"schema": "sa2-recomp-spike-fixtures/v1", "written": S.now(), "seed": SEED, "image_sha256": hashlib.sha256(image.read_bytes()).hexdigest(),
               "build": log, "functions": {}, "pc24_probe": {}}
    first_pass = {"remill": False, "retdec": False}
    for va, name, in_words, out_words, fields, callees in FUNCTIONS:
        if args.only and name != args.only:
            continue
        fn: dict = {"va": f"{va:#010x}", "name": name, "fields": field_names(fields), "sources": {}, "lifters": {}}
        rows_in: list[list[int]] = []
        expected: list[list[int] | None] = []
        authority: list[str] = []
        if va == 0x00426CC0:
            ins, exps, meta = native_calc_point()
            rows_in += ins; expected += exps; authority += ["native"] * len(ins)
            fn["sources"]["native"] = meta
        syn = synthetic(va, rng, args.rows)
        rows_in += syn; expected += [None] * len(syn); authority += ["pc24-reference"] * len(syn)
        fn["sources"]["synthetic"] = {"rows": len(syn), "generator": "tools/spike/fixtures/run_fixtures.py synthetic()", "seed": SEED}
        vec = OUTDIR / "vectors" / f"{name}.bin"
        write_vectors(vec, va, in_words, out_words, rows_in)
        fn["vectors_sha256"] = hashlib.sha256(vec.read_bytes()).hexdigest()
        fn["rows"] = len(rows_in)
        # reference, both models
        ref_out = {}
        for model in ("pc24", "fp80"):
            o = OUTDIR / "out" / f"{name}.{model}.bin"
            p = run_binary(ref, [model, str(vec), str(o)])
            if p.returncode != 0:
                raise SystemExit(f"reference {model} failed for {name}: {p.stderr}")
            ref_out[model] = read_out(o)
        for i, e in enumerate(expected):
            if e is None:
                expected[i] = ref_out["pc24"][i]
        # does the reference reproduce the native rows?
        nat = [i for i, a in enumerate(authority) if a == "native"]
        if nat:
            agree = sum(1 for i in nat if ref_out["pc24"][i][2:] == expected[i][2:])
            fn["sources"]["native"]["pc24_reference_reproduces_rows"] = f"{agree}/{len(nat)}"
            fn["sources"]["native"]["fp80_model_reproduces_rows"] = f"{sum(1 for i in nat if ref_out['fp80'][i][2:] == expected[i][2:])}/{len(nat)}"
        fn["pc24_vs_fp80_model_differ_rows"] = sum(1 for a, b in zip(ref_out["pc24"], ref_out["fp80"]) if a != b)
        for lifter in ("remill", "retdec"):
            entry = log[lifter][f"{va:#010x}"]
            binary = bdir / f"{lifter}_{va:08X}"
            L: dict = {"binary_built": bool(entry.get("link") and entry["link"]["ok"])}
            if not L["binary_built"]:
                blocker = entry.get("glue_compile") or {}
                L["blocker"] = (blocker.get("error_head") or (entry.get("link") or {}).get("error_head") or "not built")
                L["rows"] = len(rows_in); L["passed"] = 0; L["exact"] = 0; L["residue"] = 0; L["failed"] = 0; L["not_run"] = len(rows_in)
                L["verdict"] = "NOT-RUN"
                fn["lifters"][lifter] = L
                continue
            o = OUTDIR / "out" / f"{name}.{lifter}.bin"
            p = run_binary(binary, [str(image), str(vec), str(o)])
            L["run_rc"] = p.returncode
            L["run_stderr_head"] = S.error_head(p.stderr) if p.stderr else ""
            if p.returncode != 0 or not o.exists():
                L.update({"rows": len(rows_in), "passed": 0, "exact": 0, "residue": 0, "failed": len(rows_in), "not_run": 0,
                          "verdict": "FAIL", "first_divergence": {"row": 0, "field": "process", "detail": f"harness exited {p.returncode}: {L['run_stderr_head']}"}})
                fn["lifters"][lifter] = L
                continue
            obs = read_out(o)
            verdicts = [classify(obs[i], expected[i], ref_out["fp80"][i]) for i in range(len(rows_in))]
            L["rows"] = len(rows_in)
            L["exact"] = verdicts.count("EXACT"); L["residue"] = verdicts.count("RESIDUE"); L["failed"] = verdicts.count("FAIL")
            L["passed"] = L["exact"] + L["residue"]; L["not_run"] = 0
            L["exact_by_authority"] = {a: sum(1 for i in range(len(rows_in)) if authority[i] == a and verdicts[i] == "EXACT") for a in sorted(set(authority))}
            L["rows_by_authority"] = {a: authority.count(a) for a in sorted(set(authority))}
            statuses = {}
            for r in obs:
                key = {0: "returned", 1: f"left lifted bytes at {r[1]:#010x}", 2: f"fault at {r[1]:#010x}", 3: "not runnable", 4: f"looping at {r[1]:#010x}"}[r[0]] if r[0] in (0, 1, 2, 3, 4) else str(r[0])
                statuses[key] = statuses.get(key, 0) + 1
            L["observed_status_counts"] = statuses
            L["expected_status_counts"] = {}
            for e in expected:
                key = "returned" if e[0] == 0 else f"exit {e[1]:#010x}"
                L["expected_status_counts"][key] = L["expected_status_counts"].get(key, 0) + 1
            fd = next((i for i, v in enumerate(verdicts) if v != "EXACT"), None)
            if fd is not None:
                names = field_names(fields)
                j = next(k for k in range(len(names)) if obs[fd][k] != expected[fd][k])
                L["first_divergence"] = {"row": fd, "authority": authority[fd], "field": names[j], "verdict": verdicts[fd],
                                         "expected": f"{expected[fd][j]:#010x}", "observed": f"{obs[fd][j]:#010x}",
                                         "fp80_model": f"{ref_out['fp80'][fd][j]:#010x}",
                                         "inputs": [f"{w:#010x}" for w in rows_in[fd]]}
            ff = next((i for i, v in enumerate(verdicts) if v == "FAIL"), None)
            if ff is not None and ff != fd:
                names = field_names(fields)
                j = next(k for k in range(len(names)) if obs[ff][k] != expected[ff][k])
                L["first_fail"] = {"row": ff, "field": names[j], "expected": f"{expected[ff][j]:#010x}", "observed": f"{obs[ff][j]:#010x}"}
            returned = sum(1 for r in obs if r[0] == 0)
            L["coverage"] = {"rows_returned_through_ret": returned,
                             "rows_exited_at_callee_outside_the_ten": sum(1 for r in obs if r[0] == 1 and r[1] in UNLIFTED_CALLEES),
                             "rows_exited_beyond_first_ret": sum(1 for r in obs if r[0] == 1 and r[1] not in UNLIFTED_CALLEES)}
            L["verdict"] = "EXACT" if L["failed"] == 0 and L["residue"] == 0 else ("RESIDUE-ONLY" if L["failed"] == 0 else "FAIL")
            if L["failed"] == 0 and returned == 0:
                L["verdict"] += "-PARTIAL"   # every row left at a callee outside the ten: only the head was checked
            if L["exact"] and not first_pass[lifter]:
                first_pass[lifter] = True
                fe = verdicts.index("EXACT")
                timeline_once(lifter, "first_fixture_row_passes", f"{name} {va:#010x}: row {fe} bit-identical to its {authority[fe]} expectation ({L['exact']}/{L['rows']} rows exact)")
            fn["lifters"][lifter] = L
        results["functions"][f"{va:#010x}"] = fn
        print(f"{va:#010x} {name:14} rows={len(rows_in):4} " + "  ".join(
            f"{l}: {fn['lifters'][l]['verdict']:12} exact={fn['lifters'][l]['exact']} residue={fn['lifters'][l]['residue']} fail={fn['lifters'][l]['failed']}"
            for l in ("remill", "retdec")))

    results["pc24_probe"] = pc24_probe(image, bdir, ref, rng)
    (OUTDIR / "results.json").write_text(json.dumps(results, indent=2) + "\n")
    timeline_once("fixtures", "fixtures_done", "ten functions x {remill, retdec} run against native rows / PC24 reference; results in private/generated/spike/fixtures/results.json")
    print("wrote", OUTDIR / "results.json")


# ---------------------------------------------------------------- PC24 probe
def constructed_probe_rows() -> list[tuple[str, list[int]]]:
    """Vectors where (y^2 + x^2) + z^2 rounded to 24 bits at each add differs from
    the sum kept in 64 bits and rounded once at the store, or where the 1/len and
    x*inv products round differently. Found by search over the synthetic rows and
    confirmed by the two reference models; the search is in probe_search()."""
    return [
        ("hand-built tie: x=1, y=z=2^-12, so x^2+y^2 = 1+2^-24 is a tie at 24 bits; the models agree here because sqrt halves the "
         "relative gap, which is why the receipt relies on the searched rows above",
         [fw(1.0), fw(2.0 ** -12), fw(2.0 ** -12)]),
        ("hand-built: x=1+2^-23, y=z=2^-12 (models agree; kept for the same reason)", [fw(1.0 + 2.0 ** -23), fw(2.0 ** -12), fw(2.0 ** -12)]),
    ]


def pc24_probe(image: Path, bdir: Path, ref: Path, rng: random.Random) -> dict:
    probe: dict = {"functions": {}}
    for va, name, in_words, out_words, fields, callees in FUNCTIONS:
        if va not in (0x0077FC30, 0x0077F3A0):
            continue
        # search: random rows where the two models disagree
        cand = [rnd_vec(rng, -2, 3) if rng.random() < 0.5 else rnd_unit(rng) for _ in range(4096)]
        cand += [[fw(rng.uniform(0.5, 4)), fw(rng.uniform(2 ** -14, 2 ** -10)), fw(rng.uniform(2 ** -14, 2 ** -10))] for _ in range(2048)]
        vec = OUTDIR / "vectors" / f"probe_search_{name}.bin"
        write_vectors(vec, va, in_words, out_words, cand)
        outs = {}
        for model in ("pc24", "fp80"):
            o = OUTDIR / "out" / f"probe_search_{name}.{model}.bin"
            sh([str(ref), model, str(vec), str(o)])
            outs[model] = read_out(o)
        differing = [i for i in range(len(cand)) if outs["pc24"][i] != outs["fp80"][i]]
        chosen = [("searched: a row where rounding (y^2+x^2) and then +z^2 to 24 bits at each add (PC24) lands on a different float "
                   "than keeping the sums in 64 bits and rounding once at the store (FP80)", cand[i]) for i in differing[:6]]
        chosen += constructed_probe_rows()
        rows = [r for _, r in chosen]
        vec = OUTDIR / "vectors" / f"probe_{name}.bin"
        write_vectors(vec, va, in_words, out_words, rows)
        model_out = {}
        for model in ("pc24", "fp80"):
            o = OUTDIR / "out" / f"probe_{name}.{model}.bin"
            sh([str(ref), model, str(vec), str(o)])
            model_out[model] = read_out(o)
        lifted = {}
        for lifter in ("remill", "retdec"):
            binary = bdir / f"{lifter}_{va:08X}"
            if not binary.exists():
                lifted[lifter] = None
                continue
            o = OUTDIR / "out" / f"probe_{name}.{lifter}.bin"
            p = sh([str(binary), str(image), str(vec), str(o)])
            lifted[lifter] = read_out(o) if p.returncode == 0 else None
        names = field_names(fields)
        entries = []
        for i, (why, r) in enumerate(chosen):
            e = {"why": why, "inputs": [f"{w:#010x}" for w in r], "inputs_float": [wf(w) for w in r],
                 "expected_pc24": dict(zip(names, [f"{w:#010x}" for w in model_out["pc24"][i]])),
                 "fp80_model": dict(zip(names, [f"{w:#010x}" for w in model_out["fp80"][i]])),
                 "models_differ": model_out["pc24"][i] != model_out["fp80"][i], "observed": {}}
            for lifter, out in lifted.items():
                if out is None:
                    e["observed"][lifter] = {"words": None, "verdict": "NOT-RUN"}
                    continue
                v = "matches PC24" if out[i] == model_out["pc24"][i] else ("matches FP80 model (PC24 divergence)" if out[i] == model_out["fp80"][i] else "matches neither")
                e["observed"][lifter] = {"words": dict(zip(names, [f"{w:#010x}" for w in out[i]])), "verdict": v}
            entries.append(e)
        summary = {}
        for lifter in ("remill", "retdec"):
            vs = [e["observed"][lifter]["verdict"] for e in entries if e["models_differ"]]
            summary[lifter] = {"rows_where_models_differ": len(vs), "matches_pc24": vs.count("matches PC24"),
                               "matches_fp80": vs.count("matches FP80 model (PC24 divergence)"), "neither": vs.count("matches neither"),
                               "not_run": vs.count("NOT-RUN")}
        probe["functions"][f"{va:#010x}"] = {"name": name, "search_rows": len(cand), "search_rows_where_models_differ": len(differing),
                                            "fields": names, "rows": entries, "summary": summary}
    return probe


if __name__ == "__main__":
    main()
