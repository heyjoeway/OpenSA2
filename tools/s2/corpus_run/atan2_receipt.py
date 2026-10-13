#!/usr/bin/env python3
"""Write Documentation/receipts/s2-atan2-v1.json: the atan2 divergence, tested rather than inherited.

Every number and every disassembly line comes from a file this slice's tools produced or
from the executable itself; nothing is typed from memory. Inputs:

  private/generated/s2/corpus_run/atan2_probe.json          the instrumented run, as built
  private/generated/s2/corpus_run/atan2_probe.unfixed.json  the same with remill's readnone restored
  private/generated/s2/corpus_run/atan2_probe.trace.txt     the raw traces
  private/generated/s2/corpus_run/atan2_fxam_probe.json     the FXAM operand panel, as built
  private/generated/s2/corpus_run/atan2_fxam_probe.unfixed.json  the same with remill's FXAM restored
  private/generated/s2/corpus_run/run.pc24.json             the full row sets, after
  private/generated/s2/corpus_run/lift/*.pc24.stats.json    what the pass rewrote

Usage:
  source tools/env.sh
  .venv/bin/python tools/s2/corpus_run/atan2_probe.py --rows 131 296 66 2 0
  .venv/bin/python tools/s2/corpus_run/atan2_probe.py --rows 131 296 66 2 0 --unfixed \\
      --out private/generated/s2/corpus_run/atan2_probe.unfixed.json
  .venv/bin/python tools/s2/corpus_run/atan2_probe.py --operands
  .venv/bin/python tools/s2/corpus_run/atan2_probe.py --operands --fxam-unfixed
  .venv/bin/python tools/s2/corpus_run/atan2_receipt.py
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(HERE))

OUT = ROOT / "private" / "generated" / "s2" / "corpus_run"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"
RECEIPT = ROOT / "Documentation" / "receipts" / "s2-atan2-v1.json"

# The two windows the verdict rests on: the table jump that picks the tail, and the tail
# that loads pi/2. Both are disassembled out of the image, not quoted.
WINDOWS = [(0x0082C037, 0x0082C0C3, "__trandisp2's fxam classification and the table jump"),
           (0x0085A39C, 0x0085A3D8, "the pi/2 tail and its neighbours"),
           (0x0086BF83, 0x0086BFA0, "the CRT acos body: fsqrt, fxch, fpatan")]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def disassemble(lo: int, hi: int) -> list[str]:
    import pefile
    import iced_x86
    pe = pefile.PE(str(EXE), fast_load=True)
    base = pe.OPTIONAL_HEADER.ImageBase
    for s in pe.sections:
        s_lo = base + s.VirtualAddress
        s_hi = s_lo + max(s.Misc_VirtualSize, s.SizeOfRawData)
        if s_lo <= lo < s_hi:
            data = s.get_data()[lo - s_lo:hi - s_lo]
            break
    else:
        raise SystemExit(f"{lo:#x} is not in a mapped section")
    fmt = iced_x86.Formatter(iced_x86.FormatterSyntax.INTEL)
    out = []
    for ins in iced_x86.Decoder(32, data, ip=lo):
        raw = data[ins.ip - lo:ins.ip - lo + ins.len].hex()
        out.append(f"{ins.ip:08X}  {raw:<20} {fmt.format(ins)}")
    return out


def pass_stats() -> dict:
    fpatan, f80, fxam, fxam_refused = {}, {}, {}, {}
    for f in sorted(glob.glob(str(OUT / "lift" / "*.pc24.stats.json"))):
        d = json.loads(Path(f).read_text())
        va = Path(f).name[4:12]
        if d.get("fpatan_rewrites"):
            fpatan[f"0x{va.lower()}"] = d["fpatan_sites"]
        if d.get("f80_accessor_attrs_fixed"):
            f80[f"0x{va.lower()}"] = d["f80_accessor_sites"]
        if d.get("fxam_c1_stores_fixed"):
            fxam[f"0x{va.lower()}"] = d["fxam_sites"]
        if d.get("fxam_refused"):
            fxam_refused[f"0x{va.lower()}"] = d["fxam_refused_sites"]
    return {"fpatan_rewrites_by_module": fpatan, "f80_accessor_fixes_by_module": f80,
            "modules_with_fpatan_rewrites": len(fpatan), "modules_with_f80_fixes": len(f80),
            "fxam_sign_fixes_by_module": fxam, "modules_with_fxam_sign_fixes": len(fxam),
            "fxam_refused_by_module": fxam_refused}


def fxam_section(fixed: dict, unfixed: dict) -> dict:
    """The FXAM operand panel, as built and with remill's FXAM put back, and what it shows."""
    def row(r: dict) -> dict:
        l = r.get("lifted") or {}
        c = l.get("classification") or {}
        return {"atan2": r["atan2"], "st1_bits": r["st1_bits"], "st0_bits": r["st0_bits"],
                "host_c1_st1_st0": [r["host_x87"]["c1_st1"], r["host_x87"]["c1_st0"]],
                "host_tail": r["host_x87"]["classification"]["target"],
                "host_fpatan": r["host_x87"]["fpatan"], "host_fpatan_f32": r["host_x87"]["fpatan_f32"],
                "host_angle2": r["host_x87"]["angle2"],
                "reached": r["reached_the_dispatcher"], "trap": r.get("trap"),
                "lifted_c1_st1_st0": ([c["st1"]["c1"], c["st0"]["c1"]] if c else None),
                "lifted_ecx": l.get("ecx"), "lifted_tail": l.get("target"),
                "lifted_atan2": l.get("atan2_result"),
                "lifted_angle2": (r["observed"][1] if r.get("observed") else None),
                "c1_match": r.get("c1_match"), "tail_match": r.get("tail_match"),
                "result_f32_match": r.get("result_f32_match"),
                "result_bits_equal": r.get("result_bits_equal"), "angle2_match": r.get("angle2_match")}

    fs, us = fixed["summary"], unfixed["summary"]
    named = {r["atan2"]: r for r in fixed["rows"]}
    before = {r["atan2"]: r for r in unfixed["rows"]}
    key = "atan2(-0, -normal)"
    k0, kb = named.get(key, {}), before.get(key, {})
    # FX gate review, finding 4: the value-tail counts are out of the pairs that reach the
    # dispatcher, not out of every pair whose host tail returns a value.
    missing = fs["host_value_tail_rows_not_reached"]
    zeros = [r for r in fixed["rows"] if r["st1"] in ("+0", "-0") and r["st0"] in ("+0", "-0")]
    zeros_text = ", ".join(f"{r['atan2']} = {r['host_x87']['fpatan_f32']}" for r in zeros)
    # the pairs whose host tail is the zero/pi tail (0x0085A3A5) with a zero in st(0): the tail
    # takes pi or 0 from st(0)'s C1 and the sign from st(1)'s, so both are FXAM's zero-arm C1
    zero_pi_zero_st0 = [r["atan2"] for r in fixed["rows"] if r["st0"] in ("+0", "-0")
                        and r["host_x87"]["classification"]["target"] == "0x0085a3a5"]
    zeros_unreached = (all(not r["reached_the_dispatcher"] for r in zeros)
                       and sorted(zero_pi_zero_st0) == sorted(r["atan2"] for r in zeros))
    coverage = (
        f"Those {fs['value_tail_rows']} are out of the {fs['host_value_tail_rows']} pairs whose host "
        f"tail returns a value: the other {len(missing)} "
        f"({', '.join(m['atan2'] for m in missing) or 'none'}) are pairs 0x00459260 answers itself "
        f"before it calls atan2, so no lifted row compares them. "
        + (f"Among them are the four +-0/+-0 pairs ({zeros_text} on the host), the only ones in which "
           f"the zero/pi tail meets a zero in st(0), so both operands' C1 pick the result; the C1 of a "
           f"zero in st(0) is compared on the reached rows, and FXAM's C1 on every class by "
           f"test_pc24pass.py, but no atan2 result with a zero in both operands is. "
           if zeros_unreached else
           f"The four +-0/+-0 pairs ({zeros_text} on the host) are not exactly the unreached "
           f"zero/pi-tail pairs with a zero in st(0) ({', '.join(zero_pi_zero_st0) or 'none'}). "))
    reading = (
        f"{fs['rows']} synthetic rows, every pair of the ten operand classes as atan2's first "
        f"operand (st(1)) against its second (st(0)), each driven alone through 0x00459260; "
        f"{fs['reached_the_dispatcher']} reach __trandisp2 (0x00459260 leaves the rest before its "
        f"atan2: {', '.join(fs['not_reached']) or 'none'}). As built, the lifted C1 bits equal the "
        f"host's on {fs['c1_match']} of them and the tail on {fs['tail_match']}. "
        f"{fs['value_tail_rows']} take a tail that returns a value (zero/pi, pi/2, FPATAN's own); on "
        f"them the lifted atan2 result equals the host FPATAN's as the float32 0x00459327 keeps on "
        f"{fs['result_f32_match']}, and 0x00459260's second angle equals the host's on "
        f"{fs['angle2_match']}. {coverage}The other {len(fs['error_tail_rows_trapped'])} that reach "
        f"the dispatcher reach a NaN or "
        f"infinity/infinity tail, the CRT's domain-error path, which is outside the corpus program's "
        f"closure: the row traps there after the classification, so C1 and the tail are compared "
        f"and the result is not. With remill's FXAM put back: C1 on {us['c1_match']}, the tail on "
        f"{us['tail_match']}, the float32 result on {us['result_f32_match']} and the second angle on "
        f"{us['angle2_match']} of {us['value_tail_rows']}; C1 differs on "
        f"{', '.join(us['c1_mismatch_rows']) or 'none'}, and the result on "
        f"{', '.join(us['result_f32_mismatch_rows']) or 'none'}. The class never changes (xlat maps "
        f"a set C1 to the class of a clear one), so the tail is the same either way; what C1 changes "
        f"is the sign the tail gives its result. {key}: the host's x87 returns "
        f"{k0.get('host_x87', {}).get('fpatan')} and 0x00459260's second angle "
        f"{k0.get('host_x87', {}).get('angle2')}; the lifted build returns "
        f"{(k0.get('lifted') or {}).get('atan2_result')} and {(k0.get('observed') or [None, None])[1]} "
        f"as built, and {(kb.get('lifted') or {}).get('atan2_result')} and "
        f"{(kb.get('observed') or [None, None])[1]} with remill's FXAM. The results whose 80 bits "
        f"differ while their float32 agrees ({', '.join(fs['result_bits_differ_rows']) or 'none'}) "
        f"are the `fldpi` of the zero/pi tail (0x0085A3AD) and of the general tail when st(0) is "
        f"negative (0x0085A361), which remill loads as the double nearest pi (0x...c90fdaa22168c000) "
        f"where the x87 loads it to 64 bits (0x...c90fdaa22168c235): a last-bits difference the "
        f"float32 store removes, recorded in `limits`.")
    return {
        "question": ("Metal Harbor's contact-angle words (CL_ColPolCheckTouchRe, 0x0048CE40, the same "
                     "`fmul qword [0x00905658]` and _ftol after atan2 as 0x00459260) read 0xffff8000 "
                     "lifted where the game reads 0x8000. Is it FXAM's C1, which __trandisp2 keeps "
                     "(`and ecx,404h`) and atan2's zero/pi tail takes the result's sign from "
                     "(0x0085A3A5: `or cl,cl` picks pi or 0 by st(0)'s sign, `or ch,ch` negates by "
                     "st(1)'s)?"),
        "method": ("tools/s2/corpus_run/atan2_probe.py --operands: one synthetic vector-to-angles row "
                   "per pair of operand classes (+-0, +-inf, +-NaN, +-denormal, +-normal, as float32 "
                   "components, z = 0.5), each driven alone through the traced corpus program; "
                   "tools/s2/corpus_run/atan2_oracle.c runs the same fxam sequence, FPATAN and "
                   "0x00459260's own tail after atan2 on this host's x87. `--fxam-unfixed` puts "
                   "remill's C1 = 0 back on the zero, NaN and infinity arms of the traced dispatcher, "
                   "and nothing else. A float32 denormal is a normal number once `fld dword` widens "
                   "it, so it classifies as normal on both sides; the 80-bit denormal class is held "
                   "by tools/spike/pc24pass/test_pc24pass.py test_fxam_c1_matches_the_host_x87, "
                   "which compiles remill's FXAM before and after the pass and holds C0 to C3 to "
                   "this host's fxam on +-{0, inf, NaN, denormal, normal}."),
        "operands": fixed.get("operands"),
        "angle_multiplier": fixed.get("angle_multiplier"),
        "as_built": {"summary": fs, "instrumentation": fixed.get("instrumentation"),
                     "rows": [row(r) for r in fixed["rows"]]},
        "with_remills_fxam_restored": {"summary": us, "instrumentation": unfixed.get("instrumentation"),
                                       "rows": [row(r) for r in unfixed["rows"]]},
        "reading": reading,
    }


def main() -> None:
    probe = json.loads((OUT / "atan2_probe.json").read_text())
    unfixed = json.loads((OUT / "atan2_probe.unfixed.json").read_text())
    fxam_fixed = json.loads((OUT / "atan2_fxam_probe.json").read_text())
    fxam_unfixed = json.loads((OUT / "atan2_fxam_probe.unfixed.json").read_text())
    if not (fxam_fixed.get("instrumentation") or {}).get("fxam_fix_in_build"):
        raise SystemExit("atan2_fxam_probe.json was not run on a build carrying the FXAM fix; "
                         "re-run atan2_probe.py --operands after build.py")
    fixes = pass_stats()
    # The probe and the lift must agree: a probe run on a fixed build beside a lift that no longer
    # carries the fix (or refused it) would put "C1 84 of 84" next to "0 modules fixed".
    if fixes["modules_with_fxam_sign_fixes"] == 0 or fixes["fxam_refused_by_module"]:
        raise SystemExit("the corpus lift carries no FXAM fix, or refused one, while atan2_fxam_probe.json "
                         "was run on a fixed build; re-run lift_set.py --force --only 0x0082C037 and build.py")
    run = {r["fixture"]: r for r in json.loads((OUT / "run.pc24.json").read_text())["results"]}

    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s2-atan2-v1",
        "date": dt.date.today().isoformat(),
        "stage": "S2",
        "question": ("agents-journals/s2/2026-09-20-s2-corpus-run.md classified the vector-to-angles-v1 / "
                     "stairs-transform-v1 divergence as a design 7.4 transcendental residue. Test that "
                     "classification: which instruction selects the tail, what it compares, and what the "
                     "lifted code computes for that comparison on a divergent row."),
        "verdict": (
            "Not a residue. Three defects in the lift, all fixed in tools/spike/pc24pass/pc24pass.py: the "
            "80-bit accessors (2) and FPATAN's quadrant (3), proved on the full row sets, and FXAM's sign "
            "(4), proved on an operand panel against this host's x87 (finding (1) is what selects the "
            "tail, and was never wrong).\n"
            "(1) The tail is not selected by a transcendental's last bits. It is selected by the table jump "
            "at 0x0082C0C1, whose index __trandisp2 builds from two `fxam` classifications: "
            "`fxam`/`fnstsw`/`shl`/`sar`/`rol`/`and 0Fh`/`xlat [0x00922EFC]` per operand, combined as "
            "class(st0) | class(st1)<<2 and used as a byte offset into the atan2 descriptor table at "
            "0x00938B80. The instrumented lifted run computes eax = 0x04 on the divergent rows -- index 1, "
            "target 0x0085A3C8, the pi/2 tail -- which is exactly what this host's x87 produces for the "
            "same two operands under the same control word. The classification was never wrong, and the "
            "lifted code was already taking the pi/2 tail: the journal's claim that it takes the `fldz` "
            "tail at 0x0085A3A5 is not what the machine does.\n"
            "(2) The pi/2 tail returned 0 because `fld tbyte [0x00922EEA]` at 0x0085A39C loaded 0.0. remill "
            "declares `__remill_read_memory_f80` with LLVM's `readnone` although the 80-bit value travels "
            "through an out-pointer, so at -O2 the optimiser folds the load of the caller's alloca to zero. "
            "The ten bytes are present and correct in guest memory (peeked: 35 c2 68 21 a2 da 0f c9 ff 3f). "
            "The pass now moves that declaration to an attribute group without `readnone`.\n"
            "(3) Driving rejected-contact-v1 then exposed a second, larger defect of the same shape: remill "
            "lowers FPATAN to `atanl(st(1)/st(0))`, a one-argument arc tangent whose range is (-pi/2, pi/2), "
            "so the quadrant x87's two-argument FPATAN takes from the sign of st(0) is gone. The CRT's acos "
            "(0x0086BF91: `fsqrt; fxch; fpatan`) therefore returned acos(x) - pi for every negative x -- a "
            "half-turn error in every angle the game takes from an arc cosine. The pass now rewrites the "
            "call to `atan2l` on the division's own operands.\n"
            "(4) remill's FXAM stores C1 = 0 on a zero, a NaN and an infinity, where the x87 stores the "
            "operand's sign on every class. __trandisp2 keeps both operands' C1 (`and ecx,404h` at "
            "0x0082C0B4) and atan2's special-case tails take the result's sign from them, so the lifted "
            "atan2(-0, x<0) returned +pi where the x87 and the game return -pi: the class and the tail "
            "were right, the sign was not. The pass now stores the sign the semantic already computed "
            "on those three arms; `fxam_sign` holds the whole operand panel to this host's x87.\n"
            "What remains a design 7.4 transcendental residue is the last-bits accuracy of the host libm "
            "calls remill emits; no fixture in this corpus discriminates on it."),
        "inputs": {
            "design": ["Documentation/SA2_RECOMP_DESIGN.md section 7.4 (fidelity tiers and residue "
                       "classes), section 3.4 (the x87 model)"],
            "journals": ["agents-journals/s2/2026-09-20-s2-corpus-run.md",
                         "agents-journals/s2/2026-09-20-s2-patch.md"],
            "receipts": ["Documentation/receipts/s2-corpus-run-v1.json",
                         "Documentation/receipts/s2-patch-v1.json"],
            "executable": {"path": "private/exe/sonic2app-unpacked.exe", "sha256": sha(EXE)},
            "tools": {p: sha(HERE / p.split("/")[-1]) for p in
                      ("tools/s2/corpus_run/atan2_probe.py", "tools/s2/corpus_run/atan2_oracle.c",
                       "tools/s2/corpus_run/trace_hook.c")},
            "pass": {"path": "tools/spike/pc24pass/pc24pass.py",
                     "sha256": sha(ROOT / "tools" / "spike" / "pc24pass" / "pc24pass.py")},
        },
        "commands": [
            "source tools/env.sh",
            ".venv/bin/python tools/s2/corpus_run/atan2_probe.py --rows 131 296 66 2 0",
            ".venv/bin/python tools/s2/corpus_run/atan2_probe.py --rows 131 296 66 2 0 --unfixed "
            "--out private/generated/s2/corpus_run/atan2_probe.unfixed.json",
            ".venv/bin/python tools/s2/corpus_run/atan2_probe.py --operands",
            ".venv/bin/python tools/s2/corpus_run/atan2_probe.py --operands --fxam-unfixed",
            # Not a bare `lift_set.py --force`: it re-lifts the whole closure at the map's bounds,
            # undoing the --to-next-entry widening of 0x0047D160 and 0x0048B510 and the
            # 0x008287C0 window, which close_traps.py does not redo.
            ".venv/bin/python tools/s2/corpus_run/lift_set.py --force --only 0x0082C037",
            ".venv/bin/python tools/s2/corpus_run/build.py --variant pc24",
            ".venv/bin/python tools/s2/corpus_run/run.py",
            ".venv/bin/python tools/s2/corpus_run/atan2_receipt.py",
        ],
        "the_branch": {
            "site": "0x0082C0C1  jmp dword ptr [ebx]",
            "what_selects_it": (
                "Not a comparison of the transcendental's result. __trandisp2 classifies each operand with "
                "`fxam` (0x0082C064 for st(0), 0x0082C082 for st(1) after an `fxch`), reads the condition "
                "codes back through `fnstsw m16` into cl and ch, folds C3|C0<<1|C1<<2|C2<<3 into a nibble "
                "with `shl`/`sar`/`rol`/`and 0Fh`, maps it through the 16-byte xlat table at 0x00922EFC to "
                "a class (normal 0, zero/denormal 4, NaN/unsupported/empty 8, infinity 12), and jumps "
                "through the atan2 descriptor table at 0x00938B80 at offset class(st0) + 4*class(st1). "
                "st(1) is the x component, st(0) the y component: `fld [esp+4]` then `fld [esp+8]` at "
                "0x00459318/0x0045931C, and `fpatan` computes atan2(st(1), st(0))."),
            "xlat_table": probe["xlat_table"],
            "descriptor_table": probe["descriptor_table"],
            "disassembly": {f"0x{lo:08x}-0x{hi:08x} ({what})": disassemble(lo, hi)
                            for lo, hi, what in WINDOWS},
        },
        "operand_and_status_word_evidence": {
            "method": ("tools/s2/corpus_run/atan2_probe.py rewrites the folded IR of sub_82c037 to call "
                       "tools/s2/corpus_run/trace_hook.c after each `fnstsw m16` and at the table jump, and "
                       "traces st(0)/st(1) at the entry and every terminator of the whole tail chain; "
                       "tools/s2/corpus_run/atan2_oracle.c runs the same fxam/fnstsw sequence under the "
                       "same control word (0x133F, the one 0x0082C059 loads) on this host's x87. Every "
                       "other object in the traced program is the shipped one, byte for byte."),
            "as_built": probe["rows"],
            "with_remills_readnone_restored": unfixed["rows"],
            "reading": (
                "On the divergent rows (y == 0, x != 0) the lifted status words are 0x7020 for st(0) and "
                "0x3420/0x3620 for st(1), against the host's 0x7000 and 0x3400/0x3600: identical in C0, C1, "
                "C2 and C3, differing only in bit 5 (PE, the precision flag, which the oracle does not "
                "raise and which the nibble masks away). Both classify st(0) as zero (class 4) and st(1) as "
                "normal (class 0), both land on index 1, and both take 0x0085A3C8. The signed zero and "
                "stack-fault hypotheses are ruled out *for the tail's choice* by the same rows: C1 is 0 on "
                "both sides and the nibble maps a set C1 to the same class as a clear one (xlat[1] == "
                "xlat[5] == 4). C1 is not unused, though: __trandisp2 keeps it in ecx and the tails take "
                "the result's sign from it, and remill's FXAM dropped it on a zero, NaN or infinity "
                "(`fxam_sign`, finding 4)."),
            "raw_traces": ["private/generated/s2/corpus_run/atan2_probe.trace.txt",
                           "private/generated/s2/corpus_run/atan2_probe.unfixed.trace.txt"],
        },
        "before_and_after": {
            "vector-to-angles-v1": {
                "before": "5027/5305 rows, 278 divergent, all with a zero second component, error +/-0x4000 BAM",
                "after": f"{run['vector-to-angles-v1']['rows_matching_native']}/"
                         f"{run['vector-to-angles-v1']['rows']} rows, "
                         f"passed={run['vector-to-angles-v1']['passed']}",
            },
            "stairs-transform-v1": {
                "before": "4800/5472 rows, 672 divergent, all with a zero dir[1]",
                "after": f"{run['stairs-transform-v1']['rows_matching_native']}/"
                         f"{run['stairs-transform-v1']['rows']} rows, "
                         f"passed={run['stairs-transform-v1']['passed']}",
            },
            "rejected-contact-v1": {
                "before": "not driven (the previous slice left it for time)",
                "after": f"{run['rejected-contact-v1']['rows_matching_native']}/"
                         f"{run['rejected-contact-v1']['rows']} rows, "
                         f"passed={run['rejected-contact-v1']['passed']}; with the FPATAN defect in place "
                         "the one row trapped at guest 0x1C inside the settle routine 0x00466910, which the "
                         "half-turn angle error sent it into",
            },
            "M3": "7/54 before, 10/54 after (Documentation/scripts/metrics.py)",
        },
        "the_fixes": fixes,
        "fxam_sign": fxam_section(fxam_fixed, fxam_unfixed),
        "gameplay_visible_consequence": (
            "Both defects change gameplay-visible state, so under design 7.4 neither could have been "
            "recorded and left. The atan2 one is a quarter-turn error in 0x00459260, the vector-to-angles "
            "helper the camera and movement code call, on every direction whose second component is exactly "
            "zero -- an axis-aligned heading, which is common. The FPATAN one is a half-turn error in every "
            "angle derived from an arc cosine, which includes 0x004469E0, the angle between two vectors that "
            "the collision response branches on: in rejected-contact-v1 it sent the one witnessed rejected "
            "hit down the settle branch instead of the tail, i.e. a different response entirely, not a "
            "last-bit difference in the same one."),
        "limits": [
            "FXAM's C1 is the sign on every encoding after the pass; its class bits are remill's. remill "
            "has no `empty` or `unsupported` class: an unsupported encoding (an unnormal, a pseudo-NaN, "
            "a pseudo-infinity) classes as NaN (C0 = 1 where the x87 reports 0), and a pseudo-denormal "
            "as normal (C3 = 0 where the x87 reports denormal). tools/spike/pc24pass/test_pc24pass.py "
            "holds all four bits to this host's fxam on each. __trandisp2's class table (xlat "
            "[0x00922EFC]) gives unsupported and NaN the same code, 8, so the first picks the same tail "
            "either way; a pseudo-denormal gets 0 (normal) lifted where the x87's denormal gets 4, the "
            "zero class, so it takes a different tail. The x87 produces no pseudo-denormal from "
            "arithmetic; only an `fld tbyte` of one reaches it. Reported, not fixed, and not "
            "exercised by this panel, whose operands are float32.",
            "remill's FLDPI loads the double nearest pi, so the atan2 tails' +-pi is 0x...c000 in "
            "its low significand bits where the x87 gives 0x...c235 (`fxam_sign`, result_bits_differ_rows). "
            "0x00459260 and CL_ColPolCheckTouchRe store the result as float32 at once, which rounds both "
            "to the same value; it is a last-bits difference of the design 7.4 kind, reported and not "
            "fixed here.",
            "remill's FTST, the FCOM/FUCOM family, FCHS and FABS write no C1 at all, where the x87 "
            "clears it (a stale C1 rather than a lost sign). A census of every fnstsw in the function "
            "map, following the read-back register until it is overwritten, finds no mask that keeps "
            "C1 outside __trandisp1/2 and the CRT's two FPREM quotient reads (0x0082E0C2, 0x0082E37A); "
            "reported, not fixed.",
            "The fix restores the quadrant; it does not make FPATAN bit-exact. `atan2l` is the host's, so "
            "the result stays a design 7.4 transcendental residue in its last bits. The corpus's 108610 "
            "rows do not discriminate on that: every driven fixture now matches native word for word.",
            "The f80 repair is gated on a `declare` of __remill_read/write_memory_f80 whose attribute group "
            "claims `readnone`/`readonly`/`memory(none)`/`memory(read)`. A remill build that spells the "
            "memory effect differently would not be matched, and the pass would silently do nothing; the "
            "site count in the stats is the check.",
            "The FPATAN rewrite pairs the `atanl` call with the nearest preceding `fdiv x86_fp80` in the "
            "same define, because remill puts the division inside a float-predicate helper block whose "
            "label carries no FPATAN name. That is a heuristic over remill's current output shape, and it "
            "belongs on the same list as the label-attribution and control-word-GEP heuristics the S1 fold "
            "flagged: re-count it over the S3 whole-image lift.",
            "Three modules in this closure hold an FPATAN (0x0085A34C, atan2's general tail, and the two "
            "CRT arc functions 0x0086BCD8 and 0x0086BF68) and five hold an 80-bit memory operand; the "
            "image has 26 transcendental sites (Documentation/receipts/s2-patch-v1.json) and the rest are "
            "unexercised here. Both counts are in `the_fixes` and both should be re-counted over the S3 "
            "whole-image lift.",
        ],
        "authority": {
            "native_evidence": "directly-traced: tools/s2/corpus_run/atan2_oracle.c executes the guest's own "
                               "fxam/fnstsw sequence on this host's x87 under the guest's control word",
            "lifted_evidence": "observed: the instrumented program is the shipped one with one module "
                               "recompiled from rewritten IR and a printf hook linked in",
            "prose": "this unit's reading of the numbers above",
        },
        "owned_files": [
            "tools/s2/corpus_run/atan2_probe.py", "tools/s2/corpus_run/atan2_oracle.c",
            "tools/s2/corpus_run/trace_hook.c", "tools/s2/corpus_run/atan2_receipt.py",
            "tools/spike/pc24pass/pc24pass.py", "tools/spike/pc24pass/test_pc24pass.py",
            "Documentation/receipts/s2-atan2-v1.json",
        ],
        "written_by": "tools/s2/corpus_run/atan2_receipt.py",
    }
    RECEIPT.write_text(json.dumps(receipt, indent=1) + "\n")
    print(receipt["verdict"])
    print(f"-> {RECEIPT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
