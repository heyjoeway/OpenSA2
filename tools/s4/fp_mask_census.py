#!/usr/bin/env python3
"""Can lifted code unmask an x87 or SSE floating-point exception?

Subtitle: the evidence behind the refused row {0x0082ccc5 from 0x008294d8} in
Documentation/dispatch-exceptions.json, as a command.

Why this exists
---------------
The CRT's SSE2 `modf` (0x008294a0) leaves for __modf_default (0x0082ccc5), a
function-table entry the edge table refuses, by two branches. The first, `je`
at 0x008294a7, is the SSE2-available test. The second, `jne` at 0x008294d8,
is taken when `MXCSR & 0x1F80 != 0x1F80` or `x87CW & 0x7F != 0x7F`: when any FP
exception is unmasked. Until 2026-10-18 the refused census kept one source per
target and the second branch had no recorded reason (the review of issue #16's
fix found it). The reason it may stay refused is that nothing unmasks an
exception: every state the runtime gives a thread has all of them masked
(cpu_state_reset 0x007F and 0x1F80, a new thread 0x027F, Direct3D's
`(cw & ~0xF3F) | 0x3F`, the emulated fnsave's FNINIT 0x037F), and this census
checks the other half, that no lifted instruction loads a word that clears a
mask bit.

What it checks
--------------
Every module the whole-image build lifts (the lift log, the arms and
tools/s4/lift_extra.py's extras, as tools/s4/mb_edges.py reads them) is decoded
linearly, and every instruction that loads the x87 control word or MXCSR
(fldcw, ldmxcsr, fldenv, frstor, fxrstor) and every call to the CRT's
control-word routines is a site. A site passes when the word it loads is

  * derived from a word stored by fnstcw/stmxcsr, with only OR or mask-keeping
    AND applied (the compiler's float-to-int `or ah,0Ch` sequence and the
    restores after it: the mask bits are whatever they were);
  * a constant, from an immediate or the image, with every mask bit set
    (x87 bits 0-5; bit 6 is reserved and reads 1, so the `and ax,7Fh` test sees
    0x7F; MXCSR bits 7-12); or
  * a site in REVIEWED, read by hand, with its reason.

A linear decode over-approximates: data after a `ret` decodes as instructions
(four REVIEWED entries are switch tables), so the census can only list too
many sites, never skip a lifted one that starts where the decode is aligned.
Any other site fails by address.

Usage:
  .venv/bin/python tools/s4/fp_mask_census.py
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mb_edges as MB  # noqa: E402

from iced_x86 import (Decoder, FlowControl, Formatter, FormatterSyntax,  # noqa: E402
                      InstructionInfoFactory, Mnemonic as M, OpAccess, OpKind, Register as R)

X87_MASKS, SSE_MASKS = 0x003F, 0x1F80
LOADS = {M.FLDCW: X87_MASKS, M.LDMXCSR: SSE_MASKS, M.VLDMXCSR: SSE_MASKS}
ENV_LOADS = {M.FLDENV: (M.FNSTENV, M.FSTENV), M.FRSTOR: (M.FNSAVE, M.FSAVE),
             M.FXRSTOR: (M.FXSAVE,), M.XRSTOR: (M.XSAVE,)}
SAVES = (M.FNSTCW, M.FSTCW, M.STMXCSR, M.VSTMXCSR)
CONTROL_ROUTINES = {0x007BFF13: "__control87", 0x007BEEF8: "__controlfp_s",
                    0x007BDC77: "__ctrlfp", 0x007BDCF8: "___set_fpsr_sse2"}

_QUERY_RESTORE = ("restores, with mask 0xFFFF, the word an earlier __ctrlfp(0, 0) in the same "
                  "routine returned")
_CALLER_WORD = ("restores, with mask 0xFFFF, the control word its caller saved (the math "
                "entry's fnstcw, carried in the exception record)")
_DEFAULT = "__ctrlfp(word at {}, 0xFFFF): the CRT's working word {}, every mask set"
_ENTRY_SAVED = ("restores the word the CRT math entry saved with `push edx; fnstcw [esp]` "
                "(FUN_00829008 and its siblings) or `fnstcw` into this slot at its own entry")
_TABLE = "switch-table data after a `ret` or `jmp`, decoded linearly; not code"
REVIEWED = {
    # __ctrlfp(new, mask) loads (new & mask) | (old & ~mask): the callers decide.
    0x007B49D7: _DEFAULT.format("0x00917dd0", "0x1B3F"),
    0x007B4A23: _QUERY_RESTORE, 0x007B4A65: _QUERY_RESTORE,
    0x0082CC05: _DEFAULT.format("0x00922f10", "0x173F"),
    0x0082CC51: _QUERY_RESTORE, 0x0082CC93: _QUERY_RESTORE,
    0x0082CCD1: "__ctrlfp(0, 0): mask 0, reads the control word and changes nothing",
    0x0082CCE4: _DEFAULT.format("0x00922f14", "0x1F3F"),
    0x0082CD64: _QUERY_RESTORE, 0x0082CDC5: _QUERY_RESTORE,
    0x007BD997: _CALLER_WORD, 0x007BD9CA: _CALLER_WORD, 0x007BDA28: _CALLER_WORD,
    0x007BDAE2: _CALLER_WORD, 0x0082E59F: _CALLER_WORD,
    0x007BDC97: "__ctrlfp's own fldcw of (new & mask) | (old & ~mask); its callers are above",
    # __controlfp_s and __control87, which alone call ___set_fpsr_sse2 (MXCSR).
    0x007BA651: ("__setdefaultprecision: __controlfp_s(0, 0x10000, 0x30000), the precision "
                 "field only"),
    0x007BEF1F: "__controlfp_s querying with __control87(0, 0)",
    0x007BEF4F: "__controlfp_s passing its caller's (new, mask) on; its only caller is above",
    0x007BEF58: "__controlfp_s passing its caller's (new, mask) on; its only caller is above",
    0x007BFFE1: "__control87's own fldcw of its merged word; its callers are listed here",
    0x007C0161: "__control87 handing its merged word to ___set_fpsr_sse2; callers as above",
    0x007BDD1F: "___set_fpsr_sse2's ldmxcsr; called only by __control87 (0x007c0161)",
    0x007BDD4F: "___set_fpsr_sse2's ldmxcsr; called only by __control87 (0x007c0161)",
    0x007BDD60: "___set_fpsr_sse2's ldmxcsr; called only by __control87 (0x007c0161)",
    0x0083366B: "FUN_00833580 saving the word: _control87(0, 0)",
    0x0083367E: ("FUN_00833580: _control87(0x20300, 0x30300), precision and rounding only "
                 "(_MCW_PC | _MCW_RC); no mask bit is in the mask"),
    0x00833765: ("FUN_00833580 restoring, with mask 0xFFFF, the word its _control87(0, 0) "
                 "returned (saved at [esp+30h] before `add esp,10h`, read back at [esp+20h])"),
    # The CRT math exits: a word saved on entry, restored on exit.
    0x0082C1EE: _ENTRY_SAVED, 0x0082C2A6: _ENTRY_SAVED, 0x0082C2D0: _ENTRY_SAVED,
    0x0082C335: _ENTRY_SAVED,
    0x0082D7A3: "FUN_0082d710 restoring its own `fnstcw [esp+1Ch]` (0x0082d754, 0x0082d7c2)",
    0x0082D817: "FUN_0082d710 restoring its own `fnstcw [esp+1Ch]` (0x0082d754, 0x0082d7c2)",
    0x0082D820: "FUN_0082d710 restoring its own `fnstcw [esp+1Ch]` (0x0082d754, 0x0082d7c2)",
    0x00859F72: ("restores the word __cintrindisp1/2 saved with `fnstcw [ebp-0A4h]` "
                 "(0x00859e5b, 0x00859e99)"),
    0x004722FC: _TABLE, 0x00562320: _TABLE, 0x0066EC69: _TABLE, 0x00722AA4: _TABLE,
}

WIDE = {R.AX: R.EAX, R.CX: R.ECX, R.DX: R.EDX, R.BX: R.EBX, R.SI: R.ESI, R.DI: R.EDI,
        R.BP: R.EBP, R.AH: R.EAX, R.CH: R.ECX, R.DH: R.EDX, R.BH: R.EBX, R.AL: R.EAX,
        R.CL: R.ECX, R.DL: R.EDX, R.BL: R.EBX}
HIGH8 = (R.AH, R.CH, R.DH, R.BH)
LOW8 = (R.AL, R.CL, R.DL, R.BL)
WORD16 = (R.AX, R.CX, R.DX, R.BX, R.SI, R.DI, R.BP)
IMMS = (OpKind.IMMEDIATE8, OpKind.IMMEDIATE16, OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32,
        OpKind.IMMEDIATE8TO16)
WRITES = (OpAccess.WRITE, OpAccess.COND_WRITE, OpAccess.READ_WRITE, OpAccess.READ_COND_WRITE)
STOPS = (FlowControl.RETURN, FlowControl.UNCONDITIONAL_BRANCH, FlowControl.INDIRECT_BRANCH,
         FlowControl.INTERRUPT)
SAVED, SET = "saved", "set"   # a word with the masks it was read with; one with all masks set


def image_view() -> bytes:
    import pefile  # noqa: PLC0415
    pe = pefile.PE(str(MB.C.EXE), fast_load=True)
    view = bytearray(0x02375000)
    for s in pe.sections:
        raw = s.get_data()
        view[s.VirtualAddress:s.VirtualAddress + len(raw)] = raw
    return bytes(view)


def lifted_ranges() -> dict[int, tuple[int, int]]:
    ranges, _ = MB.module_ranges("next", "next")
    extras = MB.OUT / "extra_functions.next+allarms.json"
    for r in json.loads(extras.read_text())["rows"]:
        if r.get("compiled") and not r.get("rejected"):
            va = int(r["va"], 16)
            lo = int(r["anchor"], 16) if r.get("anchor") else va
            ranges.setdefault(va, (min(lo, va), int(r["end"], 16)))
    return ranges


def signed(d: int) -> int:
    return d - (1 << 32) if d >= 0x80000000 else d


def slot(i, esp):
    """A memory operand as a key: absolute, ESP-relative (normalised by the tracked ESP
    delta) or base-register-relative; None when it cannot be named."""
    if i.memory_index != R.NONE:
        return None
    if i.memory_base == R.NONE:
        return ("abs", i.memory_displacement)
    if i.memory_base == R.ESP:
        return None if esp is None else ("esp", signed(i.memory_displacement) + esp)
    return (i.memory_base, signed(i.memory_displacement))


def word_class(image: bytes, ins: list, k: int, need: int) -> str:
    """What ins[k] loads: walk forward from the start of its straight-line run (at most 600
    instructions), tracking registers and stack slots as SAVED, SET, a constant or unknown."""
    lo = k
    while lo > 0 and k - lo < 600 and ins[lo - 1].next_ip == ins[lo].ip \
            and ins[lo - 1].flow_control not in STOPS:
        lo -= 1
    base = MB.C.IMAGE_BASE
    regs: dict = {}
    mem: dict = {}
    esp = 0
    info = InstructionInfoFactory()

    def imm_of(i, n):
        return i.immediate(n) & 0xFFFFFFFF

    for i in ins[lo:k]:
        m, n = i.mnemonic, i.op_count
        k0 = i.op0_kind if n else None
        if m in SAVES and k0 == OpKind.MEMORY:
            mem[slot(i, esp)] = SAVED
            continue
        if m in (M.MOV, M.MOVZX, M.MOVSX) and n == 2:
            if k0 == OpKind.REGISTER:
                dst = WIDE.get(i.op0_register, i.op0_register)
                if i.op1_kind == OpKind.MEMORY:
                    s = slot(i, esp)
                    if s in mem:
                        regs[dst] = mem[s]
                    elif s and s[0] == "abs":
                        a = s[1] - base
                        regs[dst] = int.from_bytes(image[a:a + 4], "little")
                    else:
                        regs[dst] = None
                elif i.op1_kind == OpKind.REGISTER:
                    regs[dst] = regs.get(WIDE.get(i.op1_register, i.op1_register))
                elif i.op1_kind in IMMS:
                    regs[dst] = imm_of(i, 1)
                else:
                    regs[dst] = None
                if dst == R.ESP:
                    esp = None
            elif k0 == OpKind.MEMORY:
                s = slot(i, esp)
                if i.op1_kind == OpKind.REGISTER:
                    mem[s] = regs.get(WIDE.get(i.op1_register, i.op1_register))
                elif i.op1_kind in IMMS:
                    mem[s] = imm_of(i, 1)
                else:
                    mem[s] = None
            continue
        if m in (M.OR, M.AND) and k0 == OpKind.REGISTER and i.op1_kind in IMMS:
            r0 = i.op0_register
            dst = WIDE.get(r0, r0)
            imm = imm_of(i, 1)
            if r0 in HIGH8:
                imm = (imm << 8) | (0xFFFF00FF if m == M.AND else 0)
            elif r0 in LOW8 and m == M.AND:
                imm |= 0xFFFFFF00
            elif r0 in WORD16 and m == M.AND:
                imm |= 0xFFFF0000
            v = regs.get(dst)
            if isinstance(v, int):
                regs[dst] = (v | imm) if m == M.OR else (v & imm)
            elif m == M.OR and (imm & need) == need:
                regs[dst] = SET
            elif m == M.AND and (imm & need) != need:
                regs[dst] = None
            continue                      # OR keeps SAVED and SET; so does a mask-keeping AND
        if m == M.PUSH and esp is not None:
            esp -= 4
            continue
        if m == M.POP and esp is not None:
            esp += 4
        if m in (M.SUB, M.ADD) and k0 == OpKind.REGISTER and i.op0_register == R.ESP:
            esp = None if (esp is None or i.op1_kind not in IMMS) else \
                esp + (signed(imm_of(i, 1)) if m == M.ADD else -signed(imm_of(i, 1)))
            continue
        if m == M.LEA and k0 == OpKind.REGISTER and i.op0_register == R.ESP:
            ok = i.memory_base == R.ESP and i.memory_index == R.NONE and esp is not None
            esp = esp + signed(i.memory_displacement) if ok else None
            continue
        for u in info.info(i).used_registers():
            if u.access in WRITES:
                regs[WIDE.get(u.register, u.register)] = None
        if k0 == OpKind.MEMORY and m not in (M.CMP, M.TEST, M.FLD, M.FILD, M.FCOM, M.FCOMP,
                                              M.FLDCW, M.LDMXCSR, M.PUSH):
            mem.pop(slot(i, esp), None)
    i = ins[k]
    s = slot(i, esp)
    if s is None:
        return "unnamed address"
    if s in mem:
        v = mem[s]
    elif s[0] == "abs":
        a = s[1] - base
        v = int.from_bytes(image[a:a + 4], "little")
    else:
        v = None
    if v in (SAVED, SET):
        return v
    if isinstance(v, int):
        return "constant, masks set" if (v & need) == need else f"constant {v:#06x} UNMASKS"
    return "unknown"


def census() -> tuple[collections.Counter, list[str], set[int]]:
    image = image_view()
    base = MB.C.IMAGE_BASE
    fmt = Formatter(FormatterSyntax.INTEL)
    seen: dict[int, tuple[int, str, str]] = {}
    for va, (start, end) in sorted(lifted_ranges().items()):
        ins = [i for i in Decoder(32, image[start - base:end - base], ip=start)
               if not i.is_invalid]
        for k, i in enumerate(ins):
            if i.ip in seen:
                continue
            if i.mnemonic in LOADS:
                cls = word_class(image, ins, k, LOADS[i.mnemonic])
            elif i.mnemonic in ENV_LOADS:
                saved = any(x.mnemonic in ENV_LOADS[i.mnemonic] for x in ins[max(0, k - 400):k])
                cls = "restores a saved environment" if saved else "environment with no save"
            elif i.flow_control == FlowControl.CALL and i.near_branch_target in CONTROL_ROUTINES:
                cls = f"calls {CONTROL_ROUTINES[i.near_branch_target]}"
            else:
                continue
            seen[i.ip] = (va, cls, fmt.format(i))
    counts: collections.Counter = collections.Counter()
    failures = []
    for ip, (va, cls, text) in sorted(seen.items()):
        ok = cls in (SAVED, SET, "constant, masks set", "restores a saved environment")
        if ip in REVIEWED:
            counts["reviewed"] += 1
        elif ok:
            counts[cls] += 1
        else:
            failures.append(f"{ip:#010x} in module {va:#010x}: `{text}` loads a word the census "
                            f"cannot show keeps every exception masked ({cls}); read it and add "
                            "it to REVIEWED with its reason, or the 0x008294d8 refusal is wrong")
    stale = set(REVIEWED) - set(seen)
    return counts, failures, stale


def main() -> int:
    counts, failures, stale = census()
    for f in failures:
        print(f"  {f}")
    for s in sorted(stale):
        print(f"  {s:#010x} is in REVIEWED but no lifted site is there any more: drop it")
    total = sum(counts.values()) + len(failures)
    detail = " ".join(f"{k.replace(' ', '_').replace(',', '')}={v}" for k, v in sorted(counts.items()))
    if failures or stale:
        print(f"FP_MASK_CENSUS_FAILED sites={total} unexplained={len(failures)} "
              f"stale_reviewed={len(stale)}")
        return 1
    print(f"FP_MASK_CENSUS_OK sites={total} {detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
