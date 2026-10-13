#!/usr/bin/env python3
"""Ground truth for "which arm ran", read out of the shipped image.

Subtitle: decode each arm from the executable and say what the guest code
must put in EBX before the shared tail calls 0x004026E0.

`FUN_004015f0`'s six arms differ in exactly one instruction: each loads a
different 32-bit global into EBX and jumps to a tail the six share. The tail
does not write EBX before `call 0x004026E0`, so the value that call sees is a
witness for which arm executed -- and it is a witness the lift cannot fake,
because it comes from decoding `private/exe/sonic2app-unpacked.exe`.

The walk: start at the arm, follow `jmp rel`, fall through a conditional
branch, record every `mov ebx, imm32`, and stop at the direct `call` to the
witness address. It refuses (non-zero exit) if any instruction on the walk
writes EBX other than a `mov ebx, imm32`, or if the witness call is not
reached, because then EBX is not a witness and this file would be a decoration.

Also emitted: the selector-to-arm mapping read out of the jump table itself,
so the probe can check its own guest-memory read of the table against it.

Output (git-ignored):
  private/generated/s3/arms/oracle.<tag>.json
  private/generated/s3/arms/oracle.<tag>.txt   "<selector> <arm> <expected ebx>"

Usage:
  .venv/bin/python tools/s3/arms/oracle.py --owner 0x004015f0 --tag fun4015f0 \
      --witness-call 0x004026e0
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "whole_image"))
import common as C  # noqa: E402

OUT = C.GEN / "s3" / "arms"


def walk(img: bytes, start: int, witness: int, limit: int = 200) -> dict:
    """Decode from `start` to the call at `witness`, tracking EBX."""
    from iced_x86 import Decoder, Formatter, FormatterSyntax, Register, OpKind, Mnemonic
    fmt = Formatter(FormatterSyntax.INTEL)
    pc, ebx, trail, refusal = start, None, [], None
    for _ in range(limit):
        off = pc - C.IMAGE_BASE
        ins = Decoder(32, img[off:off + 16], ip=pc).decode()
        trail.append(f"{pc:08x} {fmt.format(ins)}")
        writes_ebx = any(ins.op0_register == r or ins.op1_register == r
                         for r in ()) or False
        # EBX written?
        if ins.op_count and ins.op0_kind == OpKind.REGISTER and \
                ins.op0_register in (Register.EBX, Register.BX, Register.BL, Register.BH):
            if ins.mnemonic == Mnemonic.MOV and ins.op1_kind == OpKind.IMMEDIATE32:
                ebx = ins.immediate32
            else:
                refusal = f"{pc:08x} writes EBX and is not `mov ebx, imm32`: {fmt.format(ins)}"
                break
        if ins.mnemonic == Mnemonic.POP and ins.op0_register == Register.EBX:
            refusal = f"{pc:08x} pops EBX"
            break
        if ins.mnemonic == Mnemonic.CALL and ins.op0_kind == OpKind.NEAR_BRANCH32:
            if ins.near_branch32 == witness:
                return {"start": f"0x{start:08x}", "expected_ebx": ebx,
                        "witness_call_at": f"0x{pc:08x}", "ok": ebx is not None,
                        "instructions": len(trail), "trail": trail, "refusal": None}
            pc = ins.next_ip
            continue
        if ins.mnemonic == Mnemonic.RET:
            refusal = f"{pc:08x} returns before reaching the witness call"
            break
        if ins.mnemonic == Mnemonic.JMP and ins.op0_kind == OpKind.NEAR_BRANCH32:
            pc = ins.near_branch32
            continue
        if ins.mnemonic == Mnemonic.JMP:          # indirect: the arm's own table
            refusal = f"{pc:08x} is an indirect jump; the walk cannot follow it"
            break
        pc = ins.next_ip                          # conditional branches fall through
    else:
        refusal = f"no witness call to {witness:#010x} within {limit} instructions"
    return {"start": f"0x{start:08x}", "expected_ebx": ebx, "ok": False,
            "instructions": len(trail), "trail": trail, "refusal": refusal}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--witness-call", required=True)
    ap.add_argument("--default-arm", default=None,
                    help="the address the bounds check branches to when the selector is "
                         "out of range. Emitted as selector 0xffffffff, so the probe has "
                         "an oracle for the one arm direct control flow already reaches.")
    args = ap.parse_args()
    owner = int(args.owner, 16)
    witness = int(args.witness_call, 16)
    img = C.memory_image()
    fn = C.by_address()[owner]
    jts = fn.get("jump_tables") or []
    if len(jts) != 1:
        sys.exit(f"{owner:#010x} has {len(jts)} jump tables; this tool handles one")
    table_at = int(jts[0]["at"], 16)

    # The table's own bytes, read where the guest reads them, so the probe's
    # `sa2_peek` of the same slots has something to be checked against.
    rows = []
    for sel, t in enumerate(jts[0]["targets"]):
        arm = int(t, 16)
        rows.append({"selector": sel, "arm": f"0x{arm:08x}", "walk": walk(img, arm, witness)})

    if args.default_arm:
        da = int(args.default_arm, 16)
        rows.append({"selector": 0xffffffff, "arm": f"0x{da:08x}",
                     "note": "the out-of-range arm; direct flow reaches it, so the "
                             "whole-image lift already contains it",
                     "walk": walk(img, da, witness)})
    bad = [r for r in rows if not r["walk"]["ok"]]
    doc = {"owner": f"0x{owner:08x}", "jump_table_at": f"0x{table_at:08x}",
           "witness_call": f"0x{witness:08x}",
           "witness_rule": "EBX at the call to the witness address identifies the arm: "
                           "each arm's only distinguishing instruction is `mov ebx, imm32` "
                           "and nothing on the shared tail writes EBX before that call.",
           "distinct_expected_ebx": len({r["walk"]["expected_ebx"] for r in rows}),
           "rows": rows, "refused": [r["walk"]["refusal"] for r in bad]}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"oracle.{args.tag}.json").write_text(json.dumps(doc, indent=1) + "\n")
    (OUT / f"oracle.{args.tag}.txt").write_text(
        f"{len(rows)}\n" + "".join(f"{r['selector']} {r['arm']} "
                                   f"0x{r['walk']['expected_ebx']:08x}\n" for r in rows))
    for r in rows:
        w = r["walk"]
        print(f"  sel {r['selector']} arm {r['arm']} -> ebx "
              f"{w['expected_ebx'] and hex(w['expected_ebx'])} "
              f"({w['instructions']} instructions to {w['witness_call_at'] if w['ok'] else w['refusal']})")
    if bad:
        sys.exit(f"REFUSED: {len(bad)} arms have no EBX witness")
    if doc["distinct_expected_ebx"] != len(rows):
        sys.exit("REFUSED: the arms do not have distinct EBX witnesses; "
                 "this oracle could not tell them apart")
    print(f"-> {OUT / f'oracle.{args.tag}.json'}")


if __name__ == "__main__":
    main()
