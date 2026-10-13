#!/usr/bin/env python3
"""Does the guest's rounding control reach the arithmetic, and does it stay in its row?

remill does not carry the rounding mode in `State`. Its FLDCW semantic calls the host's
`fesetround` and its FNSTCW reads `fegetround`, so the guest's rounding-control field
(x87 control word bits 10-11) round-trips through *process-global host* state. Runtime
v0 shipped with neither half wired:

  * `sa2_rt_state_load` wrote the caller's control word into `State` and never told the
    host, so a row asking for round-toward-zero got round-to-nearest anyway; and
  * `sa2_call` never restored the host mode, so one row that moved it contaminated every
    later row in the same process.

Both are now fixed (runtime/remill_runtime.cpp, runtime/sa2_call.c). This tool is the
test, so the claim is measured rather than argued. It drives one lifted leaf,
`0x0077FC30` (`length`, sqrt(x*x + y*y + z*z), five instrumented x87 ops) over the S1
spike's own `length.bin` vectors in three plans:

  honoured      one row per rounding control, same inputs. If the guest's rounding
                control reaches the arithmetic at all, the four answers are not all
                equal -- and round-toward-zero is never above round-to-nearest.
  contaminated  a row with RC = 11 (truncate) in front of every row with RC = 00. Every
                RC = 00 row must equal its `baseline` answer.
  baseline      every row at the game's own control word 0x007F (RC = 00), alone.

Measured against both runtimes (`--binary` builds a driver from a source tree without
the fix), the discriminating numbers are:

  control_word_read_back   before {0x007f}; after {0x007f, 0x047f, 0x087f, 0x0c7f}
  answers_sha256.up        before 5148a2867783f33d; after bffe16f8b064b1d9
  answers_sha256 nearest / down / truncate   unchanged

Limit, stated rather than hidden: the `contaminated` plan reports clean on *both*
runtimes, so it does not discriminate here. `length` reaches the CRT square root, whose
tail restores the caller's control word with `fldcw` -- which calls `fesetround` again on
the way out -- and the sum before it is rounded to 24 significand bits by
`__sa2_pc_round`, which swallows the one-ulp difference the leaked mode would make. A
contamination test that bites needs a fixture function with no control-word round trip of
its own; there is none among the eleven driven today.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s2/corpus_run/rounding_probe.py [--json OUT] [--binary DRIVER]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))
import plans as P  # noqa: E402

GEN = ROOT / "private" / "generated"
OUT = GEN / "s2" / "corpus_run"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
VECTORS = GEN / "spike" / "fixtures" / "vectors" / "length.bin"

LENGTH = 0x0077FC30
CW_BASE = 0x007F                      # the game's own control word: PC24, RC = nearest
RC = {"nearest": 0x0000, "down": 0x0400, "up": 0x0800, "truncate": 0x0C00}


def vectors(limit: int) -> list[tuple[int, int, int]]:
    """The S1 spike's `length` vectors: "SA2FXV1\\0" va, rows, words, then rows x words."""
    data = VECTORS.read_bytes()
    assert data[:8] == b"SA2FXV1\0", VECTORS
    _va, rows, words = struct.unpack_from("<3I", data, 8)
    out = []
    for i in range(min(rows, limit)):
        r = struct.unpack_from(f"<{words}I", data, 20 + i * words * 4)
        out.append((r[0], r[1], r[2]))
    return out


def row(x: int, y: int, z: int, cw: int) -> bytes:
    """`length` takes the vector in eax and returns in ST(0)
    (harness/fixture_shapes.h `FN_LENGTH`, harness/fixture_marshal.c)."""
    o = P.Ops()
    o.cw(cw)
    o.fill(P.A_ARGS, 0x1000)
    o.poke(P.A_VECTOR, [x, y, z])
    o.reg(P.ESP, P.ESP0).sentinel()
    o.reg(P.EAX, P.A_VECTOR)
    o.call()
    o.emit_st0()
    o.emit_cw()
    return o.bytes()


def drive(name: str, rows: list[bytes], binary: Path) -> list[list[int]]:
    plan = P.Plan(name, LENGTH, 2, ("length", "cw_out"), rows,
                  [(0, 0)] * len(rows),
                  abi="harness/fixture_shapes.h FN_LENGTH: eax=&vector[3], result in ST(0)")
    pdir, odir = OUT / "plans", OUT / "out"
    pdir.mkdir(parents=True, exist_ok=True)
    odir.mkdir(parents=True, exist_ok=True)
    pf, of = pdir / f"{name}.plan.bin", odir / f"{name}.out.bin"
    pf.write_bytes(plan.encode())
    p = subprocess.run([str(binary), str(EXE), str(pf), str(of)],
                       capture_output=True, text=True, timeout=600)
    if p.returncode != 0 or not of.exists():
        raise SystemExit(f"{name}: driver failed rc={p.returncode}\n{p.stderr}")
    data = of.read_bytes()
    assert data[:8] == b"SA2FXO1\0"
    _va, n, words = struct.unpack_from("<3I", data, 8)
    return [list(struct.unpack_from(f"<{words}I", data, 20 + i * words * 4)) for i in range(n)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=OUT / "rounding_probe.json")
    ap.add_argument("--rows", type=int, default=64)
    ap.add_argument("--binary", type=Path, default=None,
                    help="a driver built some other way, to measure a runtime without the fix")
    args = ap.parse_args()
    binary = args.binary or (OUT / "build" / "corpus_driver_pc24")
    if not binary.exists():
        raise SystemExit(f"{binary} is not built; run tools/s2/corpus_run/build.py")
    vecs = vectors(args.rows)

    report: dict = {"function": f"0x{LENGTH:08x}", "name": "length",
                    "vectors": str(VECTORS.relative_to(ROOT)), "rows": len(vecs),
                    "control_words": {k: f"0x{CW_BASE | v:04x}" for k, v in RC.items()}}

    # 1. honoured: the same vector under all four rounding controls.
    honoured = drive("rounding-honoured", [row(*v, CW_BASE | rc) for v in vecs for rc in RC.values()],
                     binary)
    per_mode = {name: [honoured[i * len(RC) + k][2] for i in range(len(vecs))]
                for k, name in enumerate(RC)}
    differing = sum(1 for i in range(len(vecs))
                    if len({per_mode[m][i] for m in RC}) > 1)
    report["honoured"] = {
        "rows": len(honoured),
        "traps": sum(1 for r in honoured if r[0] != 0),
        # One digest per rounding control, so two builds of the runtime are comparable
        # as numbers rather than by eye.
        "answers_sha256": {m: hashlib.sha256(
            struct.pack(f"<{len(vecs)}I", *per_mode[m])).hexdigest()[:16] for m in RC},
        "vectors_whose_answer_depends_on_the_rounding_control": differing,
        "of": len(vecs),
        "control_word_read_back": sorted({f"0x{r[3]:04x}" for r in honoured}),
        "first_difference": next(
            ({"vector": [f"0x{w:08x}" for w in vecs[i]],
              **{m: f"0x{per_mode[m][i]:08x}" for m in RC}}
             for i in range(len(vecs)) if len({per_mode[m][i] for m in RC}) > 1), None),
    }

    # 2. baseline: every vector alone at the game's control word.
    baseline = drive("rounding-baseline", [row(*v, CW_BASE) for v in vecs], binary)

    # 3. contaminated: a truncate row in front of every nearest row.
    inter: list[bytes] = []
    for v in vecs:
        inter.append(row(*v, CW_BASE | RC["truncate"]))
        inter.append(row(*v, CW_BASE))
    mixed = drive("rounding-contamination", inter, binary)
    after = [mixed[2 * i + 1][2] for i in range(len(vecs))]
    leaked = [i for i in range(len(vecs)) if after[i] != baseline[i][2]]
    report["contamination"] = {
        "rows": len(mixed),
        "nearest_rows_matching_their_baseline": len(vecs) - len(leaked),
        "of": len(vecs),
        "contaminated_rows": leaked[:8],
        "verdict": "clean" if not leaked else "CONTAMINATED",
    }
    report["verdict"] = (
        "the guest rounding control reaches the arithmetic and does not survive the row"
        if differing and not leaked else
        ("the rounding control is still ignored" if not differing else
         "one row's rounding control contaminates the next"))

    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(report, indent=1) + "\n")
    print(json.dumps({k: v for k, v in report.items() if k != "control_words"}, indent=1))
    raise SystemExit(0 if differing and not leaked else 1)


if __name__ == "__main__":
    main()
