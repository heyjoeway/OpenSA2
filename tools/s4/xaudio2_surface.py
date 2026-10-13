#!/usr/bin/env python3
"""Enumerate every XAudio2 vtable slot the shipped image can call, and check
the argument counts `runtime/sa2_xaudio2.c` declares against the dwords the
image actually pushes.

Subtitle: decode 100 % of the executable bytes, follow the engine and
mastering-voice globals and the stream object's source-voice field into the
`mov r,[obj]; mov r2,[r+N]; call r2` that consumes them, and print the
(interface, slot) pairs with their call sites and push counts.

Why the counts come from the image and from Wine, never from a header
--------------------------------------------------------------------
The game uses XAudio2 2.6. The only XAudio2 header on this machine, the zig
tree's `any-windows-any/xaudio2.h`, is 2.8: its IXAudio2 has no
GetDeviceCount, GetDeviceDetails or Initialize, so every engine slot from
there on is three low, and its GetState takes a Flags argument 2.6 does not
have. A device numbered from it would pop one dword too many at every poll
of the streaming thread -- the defect class d3d9 (S4 slice 6) and Steam
(slice 9) already paid for, which corrupts a frame rather than an answer.

So three independent sources are compared:

  * the image (this scan): the slot each site calls and the arguments it
    pushes;
  * `runtime/sa2_xaudio2.c`'s declared rows, read from a run's JSON (`--argc
    RUN.json`, the `xaudio2.methods` array harness/crt_probe.c and
    harness/xaudio2_probe.c publish) or from the source itself (`--argc
    runtime/sa2_xaudio2.c`);
  * Wine's xaudio2_6.dll (`--wine DLL`), the library the native run loaded
    (native-run.log:15522): each vtable is found by the `__func__` strings
    its methods pass to Wine's trace, and each method's argument count is read
    off its own `ret imm16`.

How a push count becomes an argument count
------------------------------------------
The rule of `tools/s4/dsound_surface.py`, which is about the image and not
about a header: the last push before the call is `this`, and must carry the
interface this scan followed; walking back from it, a push is an argument
when it is an immediate, a memory operand, or a register written since the
last call, and the walk stops at the first push of a register that was not --
a save. Two additions: a `pop` retires the push it undoes, so a saved
register restored before the arguments does not count; and every interface
load is kept with the address it came from, so the global census below can
say which call consumed each load.

The roots are facts with the instruction that establishes each. The source
voice lives only in the stream object's field +0x10, so its loads are seeded
one by one, each with its evidence, and the field census proves the list is
all of them: every non-stack `[reg+0x10]` in the stream class's code is a
seed or is named as what it is. The global census does the same for the
roots: every instruction that names one is a store to it, a load of it that
an attributed call consumes, a compare of it, or a push of its address as an
out-parameter, each only in the operand shape that makes it that, and
anything else fails the census.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/xaudio2_surface.py --check \\
      --argc runtime/sa2_xaudio2.c \\
      --wine private/proton-prefix/pfx/drive_c/windows/syswow64/xaudio2_6.dll \\
      --json private/generated/s4/xaudio2/surface.json
"""
from __future__ import annotations

import argparse
import json
import re
import struct
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))

import common as C  # noqa: E402
import image as IM  # noqa: E402
from iced_x86 import Decoder, FlowControl, Mnemonic, OpKind, Register  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
RUNTIME_SOURCE = ROOT / "runtime" / "sa2_xaudio2.c"

ENGINE, MASTER, SOURCE = "IXAudio2", "IXAudio2MasteringVoice", "IXAudio2SourceVoice"

# ---------------------------------------------------------------- the roots

GLOBAL_ROOTS = {
    0x01AF0ED8: (ENGINE,
                 "0x00784127 stores the engine CoCreateInstance returned at 0x00784103, once "
                 "Initialize has succeeded"),
    0x01AF0EDC: (MASTER,
                 "CreateMasteringVoice's ppMasteringVoice: 0x00784202 pushes this address and "
                 "0x0078420E reads the voice back out of it"),
}

# The load at this address writes a register that then holds this interface.
# Each is a hand-asserted claim with its evidence; the tool checks that every
# one really is a `mov reg,[mem]`, and `--falsify` shows what is lost without
# them.
LOAD_SEEDS = {
    0x0078410D: (ENGINE,
                 "0x007840F1 `lea eax,[esp]` is pushed at 0x007840F4 as CoCreateInstance's ppv; "
                 "this reads the engine back out of that local"),
    0x0078411F: (ENGINE,
                 "the same local, [esp+4] after the `push esi` at 0x00784115: the path that "
                 "releases the engine when Initialize fails"),
    0x007843AF: (SOURCE,
                 "EDI is obj+0x10 (0x0078439B `lea edi,[esi+10h]`), pushed at 0x0078439E as "
                 "CreateSourceVoice's ppSourceVoice"),
    0x007843EB: (SOURCE, "EDI is still obj+0x10: the thread-creation failure path"),
    0x0078442B: (SOURCE, "EDI is still obj+0x10: the success path, which starts the voice"),
    0x0078448C: (SOURCE,
                 "0x00784450 is the stream object's vtable slot 0 (0x009021E4) and ESI is "
                 "`this` (0x00784451 `mov esi,ecx`); +0x10 is the source voice"),
    0x0078449F: (SOURCE, "the same field, reloaded after Stop"),
    0x007844AA: (SOURCE, "the same field, reloaded after FlushSourceBuffers"),
    0x00784588: (SOURCE,
                 "0x00784580 is the stream object's vtable slot 5 and ECX is `this`"),
    0x007846E8: (SOURCE,
                 "0x007845C0 is called with ESI = the stream object (0x00784426 inside "
                 "0x00784280, and 0x007847A0 inside the thread, whose ESI is its parameter)"),
    0x00784780: (SOURCE,
                 "0x00784760 is the streaming thread (pushed at 0x007843D0 for "
                 "_beginthreadex); ESI is its parameter, the stream object (0x0078476B)"),
}

# Every non-stack `[reg+0x10]` in the stream class's code that is not a seed,
# with what it is. The field census fails on any other.
STREAM_CODE = (0x00784140, 0x007847C3)
FIELD_NOT_A_LOAD = {
    0x0078415C: "the constructor (0x00784140) zeroing the field: `mov [esi+10h],edi` with EDI 0",
    0x0078439B: "the field's address, taken as CreateSourceVoice's out-parameter",
}

# The 2.6 vtable orders, with the argument counts of Wine's xaudio2_6.dll.
# Labels until a call site or `--wine` witnesses them.
NAMES = {
    ENGINE: [("QueryInterface", 2), ("AddRef", 0), ("Release", 0), ("GetDeviceCount", 1),
             ("GetDeviceDetails", 2), ("Initialize", 2), ("RegisterForCallbacks", 1),
             ("UnregisterForCallbacks", 1), ("CreateSourceVoice", 7),
             ("CreateSubmixVoice", 7), ("CreateMasteringVoice", 6), ("StartEngine", 0),
             ("StopEngine", 0), ("CommitChanges", 1), ("GetPerformanceData", 1),
             ("SetDebugConfiguration", 2)],
}
VOICE = [("GetVoiceDetails", 1), ("SetOutputVoices", 1), ("SetEffectChain", 1),
         ("EnableEffect", 2), ("DisableEffect", 2), ("GetEffectState", 2),
         ("SetEffectParameters", 4), ("GetEffectParameters", 3), ("SetFilterParameters", 2),
         ("GetFilterParameters", 1), ("SetOutputFilterParameters", 3),
         ("GetOutputFilterParameters", 2), ("SetVolume", 2), ("GetVolume", 1),
         ("SetChannelVolumes", 3), ("GetChannelVolumes", 2), ("SetOutputMatrix", 5),
         ("GetOutputMatrix", 4), ("DestroyVoice", 0)]
NAMES[MASTER] = list(VOICE)
NAMES[SOURCE] = VOICE + [("Start", 2), ("Stop", 2), ("SubmitSourceBuffer", 2),
                         ("FlushSourceBuffers", 0), ("Discontinuity", 0), ("ExitLoop", 1),
                         ("GetState", 1), ("SetFrequencyRatio", 2), ("GetFrequencyRatio", 1),
                         ("SetSourceSampleRate", 1)]
WIDEST_VTABLE = max(len(v) for v in NAMES.values())

# Witnesses: what has to hold at the image's own sites for the name at that
# slot to be the right one. Each is the argument count and the immediates the
# site pushes, in push order (last argument first), `None` for a non-immediate.
WITNESSES = {
    (ENGINE, 5): ("Initialize", 2, [[0xFFFFFFFF, 0]]),
    (ENGINE, 10): ("CreateMasteringVoice", 6, [[0, 0, 0, 0xBB80, 0, 0x01AF0EDC]]),
    (ENGINE, 8): ("CreateSourceVoice", 7, [[0, 0, 0, None, 0, None, None]]),
    (ENGINE, 2): ("Release", 0, None),
    (MASTER, 0): ("GetVoiceDetails", 1, [[0x01AF0EE0]]),
    (MASTER, 18): ("DestroyVoice", 0, None),
    (SOURCE, 16): ("SetOutputMatrix", 5, [[0, 0x0089E4A8, 2, 6, None]]),
    (SOURCE, 18): ("DestroyVoice", 0, None),
    (SOURCE, 19): ("Start", 2, [[0, 0]]),
    (SOURCE, 20): ("Stop", 2, [[0, 0]]),
    (SOURCE, 21): ("SubmitSourceBuffer", 2, [[0, None]]),
    (SOURCE, 22): ("FlushSourceBuffers", 0, None),
    (SOURCE, 25): ("GetState", 1, [[None]]),
    (SOURCE, 26): ("SetFrequencyRatio", 2, [[0, None]]),
}

REGNAME = {"eax": Register.EAX, "ecx": Register.ECX, "edx": Register.EDX,
           "ebx": Register.EBX, "esi": Register.ESI, "edi": Register.EDI,
           "ebp": Register.EBP}
STACK_REGS = {Register.ESP, Register.EBP}
# Instructions whose first operand is a register they do not write.
NONWRITING = {Mnemonic.TEST, Mnemonic.CMP, Mnemonic.PUSH, Mnemonic.JMP,
              Mnemonic.RET, Mnemonic.INT3, Mnemonic.NOP}


class Sym:
    __slots__ = ("kind", "iface", "slot", "origin")

    def __init__(self, kind: str, iface: str = "", slot: int = -1, origin: int = 0):
        self.kind, self.iface, self.slot, self.origin = kind, iface, slot, origin


class Push:
    __slots__ = ("ip", "imm", "reg", "fresh", "sym")

    def __init__(self, ip: int, imm: int | None, reg: int | None, fresh: bool, sym=None):
        self.ip, self.imm, self.reg, self.fresh, self.sym = ip, imm, reg, fresh, sym


def gaps(img: "IM.Image", funcs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Executable bytes no mapped function covers: a scan that reads only the
    analyzer map measures the map (S4 slice 10 found a live call site in the
    17.2 % it misses)."""
    out: list[tuple[int, int]] = []
    for sec in img.sections:
        if not sec.executable:
            continue
        lo, hi = sec.va, sec.va + len(img._section_bytes[sec.name])
        inside = sorted((a, a + s) for a, s in funcs if lo <= a < hi)
        cur = lo
        for a, b in inside:
            if a > cur:
                out.append((cur, a - cur))
            cur = max(cur, b)
        if cur < hi:
            out.append((cur, hi - cur))
    return out


def imm32(ins, op: int) -> int:
    """An immediate operand as the 32-bit value the instruction uses.
    `Instruction.immediate32` is the raw encoded field, so for `push -1`
    (6A FF) it reads 0xFF; `immediate()` sign-extends."""
    return ins.immediate(op) & 0xFFFFFFFF


def global_operand(ins, watch) -> tuple[int, int, str] | None:
    """The watched global this instruction names: (the global, the operand
    index, "memory" when the operand is the global itself as an absolute
    memory operand or "immediate" when it is the global's address), or None."""
    for i in range(ins.op_count):
        k = ins.op_kind(i)
        if (k == OpKind.MEMORY and ins.memory_base == Register.NONE
                and ins.memory_index == Register.NONE and ins.memory_displacement in watch):
            return ins.memory_displacement, i, "memory"
        if k in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32) and imm32(ins, i) in watch:
            return imm32(ins, i), i, "immediate"
    return None


def global_role(ins, operand: int, how: str) -> str | None:
    """What an instruction naming a root global does with it, or None for a
    shape the census has no name for, which fails it. Each role requires the
    operand shape that makes it that role: a store writes the global itself, a
    load reads it into a register, a compare tests it, and an out-parameter
    pushes its address. `push dword ptr [global]` passes the interface to a
    callee this scan does not follow, and `mov [esp+4],global` stores the
    address somewhere else; neither is any of the four."""
    m = ins.mnemonic
    if how == "memory":
        if m == Mnemonic.MOV and operand == 0:
            return "store"
        if m == Mnemonic.MOV and operand == 1 and ins.op0_kind == OpKind.REGISTER:
            return "load"
        if m in (Mnemonic.CMP, Mnemonic.TEST):
            return "compare"
        return None
    if m == Mnemonic.PUSH and operand == 0:
        return "out-parameter"
    return None


def scan_bytes(blob: bytes, va: int, roots: dict, seeds: dict, names: dict,
               shape: list | None = None, seed_kinds: dict | None = None,
               refs: list | None = None) -> list[dict]:
    """The attributed call sites in one linearly decoded range.

    `seed_kinds`, when given, collects what each seed address decoded as, so
    a seed that does not point at a `mov reg,[mem]` is reported rather than
    silently ignored. `refs`, when given, collects every instruction that names
    one of GLOBAL_ROOTS, whatever `roots` is, for the global census."""
    regs: dict[int, Sym] = {}
    fresh: set[int] = set()
    pushes: list[Push] = []
    saw_call = False
    out: list[dict] = []
    # A linear decode cannot see that a forward branch skips a block, so the
    # register state at each forward branch is kept under its target and
    # merged in on arrival, for registers the linear path no longer holds.
    pending: dict[int, dict[int, Sym]] = {}
    end = va + len(blob)

    def clobber(r: int) -> None:
        regs.pop(r, None)
        fresh.add(r)

    def keep(r: int, sym: Sym) -> None:
        regs[r] = sym
        fresh.add(r)

    for ins in Decoder(32, blob, ip=va):
        m = ins.mnemonic
        if refs is not None:
            named = global_operand(ins, GLOBAL_ROOTS)
            if named is not None:
                g, operand, how = named
                refs.append({"at": ins.ip, "global": g, "role": global_role(ins, operand, how)})
        snap = pending.pop(ins.ip, None)
        if snap:
            for r, sym in snap.items():
                regs.setdefault(r, sym)
        if ins.op0_kind in (OpKind.NEAR_BRANCH32, OpKind.NEAR_BRANCH16):
            tgt = ins.near_branch_target
            if ins.ip < tgt < end and tgt not in pending:
                pending[tgt] = dict(regs)
        if m == Mnemonic.PUSH:
            if ins.op0_kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                pushes.append(Push(ins.ip, imm32(ins, 0), None, True))
            elif ins.op0_kind == OpKind.REGISTER:
                r = ins.op0_register
                # The symbol is captured at the push: the image pushes `this`
                # out of EAX and then loads the method into the same EAX.
                pushes.append(Push(ins.ip, None, r, r in fresh, regs.get(r)))
            else:
                pushes.append(Push(ins.ip, None, None, True))
            continue
        if m == Mnemonic.POP:
            if pushes:
                pushes.pop()
            if ins.op0_kind == OpKind.REGISTER:
                clobber(ins.op0_register)
            continue
        seed = seeds.get(ins.ip)
        if seed_kinds is not None and seed is not None:
            seed_kinds[ins.ip] = ("load" if m == Mnemonic.MOV and ins.op0_kind == OpKind.REGISTER
                                  and ins.op1_kind == OpKind.MEMORY else "not a load")
        if m in (Mnemonic.MOV, Mnemonic.LEA) and ins.op0_kind == OpKind.REGISTER:
            dst = ins.op0_register
            if seed is not None and m == Mnemonic.MOV and ins.op1_kind == OpKind.MEMORY:
                keep(dst, Sym("iface", seed[0], origin=ins.ip))
                continue
            if m == Mnemonic.MOV and ins.op1_kind == OpKind.MEMORY:
                base, idx, disp = ins.memory_base, ins.memory_index, ins.memory_displacement
                if base == Register.NONE and idx == Register.NONE:
                    r = roots.get(disp)
                    if r:
                        keep(dst, Sym("iface", r[0], origin=ins.ip))
                    else:
                        clobber(dst)
                    continue
                if idx == Register.NONE and base in regs:
                    s = regs[base]
                    if s.kind == "iface" and disp == 0:
                        keep(dst, Sym("vtable", s.iface, origin=s.origin))
                        continue
                    if s.kind == "vtable" and disp % 4 == 0:
                        keep(dst, Sym("method", s.iface, disp // 4, origin=s.origin))
                        continue
                if (shape is not None and idx == Register.NONE and base != Register.NONE
                        and base not in STACK_REGS and disp % 4 == 0 and dst not in regs):
                    keep(dst, Sym("maybe-method", "", disp // 4))
                    continue
                clobber(dst)
                continue
            if m == Mnemonic.MOV and ins.op1_kind == OpKind.REGISTER:
                src = ins.op1_register
                if src in regs:
                    keep(dst, regs[src])
                else:
                    clobber(dst)
                continue
            clobber(dst)
            continue
        if m == Mnemonic.ADD and ins.op0_kind == OpKind.REGISTER and \
                ins.op0_register == Register.ESP and ins.op1_kind in (
                    OpKind.IMMEDIATE8TO32, OpKind.IMMEDIATE32):
            # A caller cleaning up after a cdecl call has already crossed the
            # call boundary; any other `add esp` retires pushes.
            n = imm32(ins, 1)
            if n < 0x80000000:
                for _ in range(min(len(pushes), n // 4)):
                    pushes.pop()
            continue
        if m == Mnemonic.CALL:
            target = regs.get(ins.op0_register) if ins.op0_kind == OpKind.REGISTER else None
            if target is not None and target.kind in ("method", "maybe-method"):
                table = names.get(target.iface) or []
                row = {
                    "iface": target.iface or None,
                    "slot": target.slot,
                    "name": table[target.slot][0] if target.slot < len(table) else None,
                    "site": "%#010x" % ins.ip,
                    "return_to": "%#010x" % ins.next_ip,
                    "this_from": "%#010x" % target.origin if target.origin else None,
                    "pushes_total": len(pushes),
                    "this_push_is_the_interface": False,
                    "argc_rule": None,
                    "argc_upper": max(len(pushes) - 1, 0),
                    "a_call_precedes_this_site": saw_call,
                    "pushed": [],
                    "argument_origins": [],
                }
                if pushes:
                    last = pushes[-1]
                    is_this = (last.sym is not None and last.sym.kind == "iface"
                               and last.sym.iface == target.iface)
                    row["this_push_is_the_interface"] = bool(is_this)
                    if is_this:
                        n = 0
                        args = []
                        for p in reversed(pushes[:-1]):
                            if p.imm is not None or p.fresh:
                                n += 1
                                args.append(p)
                            else:
                                break
                        row["argc_rule"] = n
                        # Push order, last argument first, as the image pushes them.
                        row["pushed"] = [a.imm for a in reversed(args)]
                        row["argument_origins"] = [
                            "%#010x" % a.sym.origin if a.sym is not None and a.sym.origin
                            else None for a in reversed(args)]
                if target.kind == "method":
                    out.append(row)
                elif shape is not None:
                    shape.append({"site": row["site"], "slot": target.slot,
                                  "beyond_widest_xaudio2_vtable": target.slot >= WIDEST_VTABLE})
            for r in (Register.EAX, Register.ECX, Register.EDX):
                regs.pop(r, None)
            pushes = []
            fresh.clear()
            saw_call = True
            continue
        if m in (Mnemonic.RET, Mnemonic.INT3):
            # A linear decode walks through a `ret` into the next block, which
            # a jump reaches with registers the `ret` did not kill, so state is
            # kept; pushes and freshness are not.
            pushes = []
            fresh.clear()
            saw_call = False
            continue
        if ins.op0_kind == OpKind.REGISTER and m not in NONWRITING:
            clobber(ins.op0_register)
    return out


def ranges(img: "IM.Image") -> tuple[list[tuple[int, int]], dict]:
    funcs = [(int(f["address"], 0) if isinstance(f["address"], str) else int(f["address"]),
              int(f.get("size", 0) or 0)) for f in C.functions()]
    gs = gaps(img, funcs)
    exec_bytes = sum(len(img._section_bytes[s.name]) for s in img.sections if s.executable)
    gap_bytes = sum(s for _, s in gs)
    cover = {"executable_bytes": exec_bytes, "mapped_functions": len(funcs),
             "gap_bytes": gap_bytes, "gap_ranges": len(gs), "scanned_fraction": 1.0,
             "map_fraction": round((exec_bytes - gap_bytes) / exec_bytes, 5)
             if exec_bytes else 0.0}
    return [(va, size) for va, size in funcs if size] + gs, cover


def scan_image(img: "IM.Image", roots: dict, seeds: dict) -> tuple[list[dict], list[dict],
                                                                    dict, dict, list[dict]]:
    spans, cover = ranges(img)
    shape: list[dict] = []
    kinds: dict[int, str] = {}
    refs: list[dict] = []
    sites: list[dict] = []
    for va, size in spans:
        sec = img.section_of(va)
        if sec is None or not sec.executable:
            continue
        blob = img._section_bytes[sec.name][va - sec.va: va - sec.va + size]
        sites += scan_bytes(blob, va, roots, seeds, NAMES, shape, kinds, refs)
    # The spans partition the executable bytes, so nothing is decoded twice;
    # this keeps that a checked property rather than an assumed one.
    keys = [(s["site"], s["iface"], s["slot"]) for s in sites]
    if len(keys) != len(set(keys)):
        raise SystemExit("a call site was attributed twice: the decoded spans overlap")
    return sorted(sites, key=lambda s: s["site"]), shape, cover, kinds, refs


# ---------------------------------------------------------------- the censuses

def global_census(refs: list[dict], sites: list[dict]) -> dict:
    """Every instruction that names a root global, and what it is. A load no
    attributed call consumed is an XAudio2 use this scan did not follow."""
    consumed = {s["this_from"] for s in sites if s["this_from"]}
    for s in sites:
        consumed.update(o for o in s["argument_origins"] if o)
    rows, bad = [], []
    for r in sorted(refs, key=lambda r: r["at"]):
        at = "%#010x" % r["at"]
        role = r["role"]
        if role == "load":
            role = "load, consumed by an attributed call" if at in consumed else None
        row = {"at": at, "global": "%#010x" % r["global"],
               "interface": GLOBAL_ROOTS[r["global"]][0], "role": role}
        rows.append(row)
        if role is None:
            bad.append(row)
    return {"references": rows, "unaccounted": bad, "ok": bool(rows) and not bad}


def field_census(img: "IM.Image", seeds: dict) -> dict:
    """Every non-stack `[reg+0x10]` in the stream class's code: a seed, or a
    named non-load. The source voice lives nowhere else."""
    lo, hi = STREAM_CODE
    sec = img.section_of(lo)
    blob = img._section_bytes[sec.name][lo - sec.va: hi - sec.va]
    rows, bad = [], []
    for ins in Decoder(32, blob, ip=lo):
        for i in range(ins.op_count):
            if (ins.op_kind(i) == OpKind.MEMORY and ins.memory_displacement == 0x10
                    and ins.memory_base not in (Register.NONE, *STACK_REGS)
                    and ins.memory_index == Register.NONE):
                why = (("seed: " + seeds[ins.ip][1]) if ins.ip in seeds
                       else FIELD_NOT_A_LOAD.get(ins.ip))
                row = {"at": "%#010x" % ins.ip, "explained": why}
                rows.append(row)
                if why is None:
                    bad.append(row)
                break
    unused = sorted("%#010x" % a for a in FIELD_NOT_A_LOAD
                    if "%#010x" % a not in {r["at"] for r in rows})
    return {"accesses": rows, "unexplained": bad, "stale_explanations": unused,
            "ok": bool(rows) and not bad and not unused}


def seed_check(kinds: dict) -> dict:
    missing = sorted("%#010x" % a for a in LOAD_SEEDS if a not in kinds)
    wrong = sorted("%#010x" % a for a, k in kinds.items() if k != "load")
    return {"seeds": len(LOAD_SEEDS), "not_decoded": missing, "not_a_load": wrong,
            "ok": not missing and not wrong}


# ---------------------------------------------------------------- the checks

def by_slot(sites: list[dict]) -> dict[tuple[str, int], list[dict]]:
    by: dict[tuple[str, int], list[dict]] = {}
    for s in sites:
        by.setdefault((s["iface"], s["slot"]), []).append(s)
    return by


def check(sites: list[dict]) -> dict:
    res: dict = {"witnesses": {}, "ok": True}
    by = by_slot(sites)
    for key, (name, want, imms) in WITNESSES.items():
        hits = by.get(key, [])
        counted = [h["argc_rule"] for h in hits if h["argc_rule"] is not None]
        held = bool(hits) and len(counted) == len(hits) and all(c == want for c in counted)
        if imms is not None:
            for h in hits:
                if not any(len(h["pushed"]) == len(pat) and
                           all(p is None or p == q for p, q in zip(pat, h["pushed"]))
                           for pat in imms):
                    held = False
        res["witnesses"]["%s:%d" % key] = {
            "expected_name": name, "held": held,
            "detail": "%d site(s), counts %s, wanted %d" % (len(hits), sorted(set(counted)), want),
            "sites": [h["site"] for h in hits],
        }
        res["ok"] = res["ok"] and held
    unwitnessed = sorted("%s:%d" % k for k in by if k not in WITNESSES)
    res["slots_without_a_witness"] = unwitnessed
    res["ok"] = res["ok"] and not unwitnessed
    return res


ROW = re.compile(r"\{\s*(\d+)\s*,\s*(\d+)\s*,\s*[^,]*,\s*\"(\w+)\"\s*,\s*(\w+)\s*\}")
TABLE = re.compile(r"static\s+xa_row\s+(ROWS_\w+)\[\]\s*=\s*\{(.*?)\n\};", re.S)
TABLE_IFACE = {"ROWS_ENGINE": ENGINE, "ROWS_MASTER": MASTER, "ROWS_SOURCE": SOURCE}


def declared_from_source(path: Path) -> dict[tuple[str, int], dict]:
    """The rows as `runtime/sa2_xaudio2.c` publishes them, before any break."""
    out: dict[tuple[str, int], dict] = {}
    for m in TABLE.finditer(path.read_text()):
        iface = TABLE_IFACE.get(m.group(1))
        if iface is None:
            continue
        for r in ROW.finditer(m.group(2)):
            out[(iface, int(r.group(1)))] = {"interface": iface, "slot": int(r.group(1)),
                                             "argc": int(r.group(2)), "name": r.group(3),
                                             "implemented": r.group(4) != "NULL"}
    return out


def declared_from_run(path: Path) -> dict[tuple[str, int], dict]:
    run = json.loads(path.read_text())
    return {(r["interface"], int(r["slot"])): r
            for r in run.get("xaudio2", {}).get("methods", [])}


def load_declared(path: Path) -> dict[tuple[str, int], dict]:
    return declared_from_source(path) if path.suffix == ".c" else declared_from_run(path)


def argc_check(sites: list[dict], declared: dict[tuple[str, int], dict], source: str) -> dict:
    """The runtime's rows against the image: every site's count, every called
    slot answered, and nothing answered that the image never calls."""
    out: dict = {"declared_from": source, "declared_slots": len(declared), "compared": [],
                 "not_compared": [], "disagreements": [], "called_but_refused": [],
                 "answered_but_never_called": [], "misnamed": []}
    by = by_slot(sites)
    for s in sites:
        d = declared.get((s["iface"], s["slot"]))
        row = {"interface": s["iface"], "slot": s["slot"], "site": s["site"],
               "argc_rule": s["argc_rule"], "argc_upper": s["argc_upper"],
               "declared_argc": d["argc"] if d else None,
               "declared_method": d["name"] if d else None}
        if s["argc_rule"] is None:
            row["why_not"] = "the last push before the call is not the interface pointer"
            out["not_compared"].append(row)
            continue
        if d is None:
            row["why_not"] = "the runtime declares no row for this slot"
            out["not_compared"].append(row)
            out["called_but_refused"].append(row)
            continue
        out["compared"].append(row)
        if d["argc"] != s["argc_rule"]:
            out["disagreements"].append(row)
        if s["name"] and d["name"] != s["name"]:
            out["misnamed"].append(row)
        if not d["implemented"]:
            out["called_but_refused"].append(row)
    for key, d in sorted(declared.items()):
        if d["implemented"] and key not in by:
            out["answered_but_never_called"].append(
                {"interface": key[0], "slot": key[1], "name": d["name"]})
    out["ok"] = (bool(out["compared"]) and not out["disagreements"] and not out["misnamed"]
                 and not out["called_but_refused"] and not out["answered_but_never_called"]
                 and not out["not_compared"])
    return out


def names_check(declared: dict[tuple[str, int], dict]) -> dict:
    """The declared rows against this tool's 2.6 table: the same name and
    count at every slot, and every slot of every interface named."""
    bad = []
    for iface, rows in NAMES.items():
        for slot, (name, argc) in enumerate(rows):
            d = declared.get((iface, slot))
            if d is None or d["name"] != name or int(d["argc"]) != argc:
                bad.append({"interface": iface, "slot": slot, "want": [name, argc],
                            "declared": [d["name"], d["argc"]] if d else None})
    extra = sorted("%s:%d" % k for k in declared if k[1] >= len(NAMES.get(k[0], [])))
    return {"mismatches": bad, "beyond_the_2_6_table": extra, "ok": not bad and not extra}


# ---------------------------------------------------------------- Wine's DLL

WINE_PREFIX = {ENGINE: "IXAudio2Impl_", MASTER: "XA2M_", SOURCE: "XA2SRC_"}


def wine_vtables(path: Path) -> dict:
    """Wine's own vtables in xaudio2_6.dll, named by the `__func__` strings
    each method passes to wine_dbg_log, with each method's stdcall pop read
    off its `ret imm16` by following its own control flow."""
    import pefile
    pe = pefile.PE(str(path))
    base = pe.OPTIONAL_HEADER.ImageBase
    secs = [(base + s.VirtualAddress, s.get_data(), s.Characteristics) for s in pe.sections]

    def sec_of(va):
        for sva, data, ch in secs:
            if sva <= va < sva + len(data):
                return sva, data, ch
        return None

    def read(va, n):
        s = sec_of(va)
        return s[1][va - s[0]: va - s[0] + n] if s else b""

    def is_code(va):
        s = sec_of(va)
        return bool(s and s[2] & 0x20000000)

    strings = {}
    for sva, data, ch in secs:
        if ch & 0x20000000:
            continue
        for m in re.finditer(rb"(?<=\0)(IXAudio2Impl_\w+|XA2\w*_\w+|get_voice_details)\0", data):
            strings[sva + m.start()] = m.group(1).decode()

    def name_of(va, depth=0):
        for ins in Decoder(32, read(va, 1200), ip=va):
            for i in range(ins.op_count):
                k = ins.op_kind(i)
                if k in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32) and imm32(ins, i) in strings:
                    return strings[imm32(ins, i)]
            if ins.mnemonic == Mnemonic.INT3:
                break
        return None

    def pops(entry):
        seen, todo, rets, tails = set(), [entry], set(), set()
        while todo:
            va = todo.pop()
            while entry <= va < entry + 0x1000 and va not in seen:
                seen.add(va)
                ins = next(iter(Decoder(32, read(va, 16), ip=va)))
                fc = ins.flow_control
                if ins.mnemonic == Mnemonic.RET:
                    rets.add(ins.immediate16 if ins.op_count else 0)
                    break
                if fc == FlowControl.CONDITIONAL_BRANCH:
                    todo.append(ins.near_branch_target)
                elif fc == FlowControl.UNCONDITIONAL_BRANCH:
                    if ins.op0_kind == OpKind.NEAR_BRANCH32 and ins.near_branch_target >= entry:
                        va = ins.near_branch_target
                        continue
                    break
                elif fc == FlowControl.INDIRECT_BRANCH:
                    # A tail call through an import slot. DestroyVoice ends in
                    # `jmp [LeaveCriticalSection]` with its critical section
                    # written over its own `this` slot, so the import pops
                    # exactly `this`.
                    tails.add("import")
                    break
                elif ins.mnemonic in (Mnemonic.INT3, Mnemonic.UD2):
                    break
                va = ins.next_ip
        if not rets and tails == {"import"}:
            rets.add(4)
        return sorted(rets)

    found: dict = {}
    for sva, data, ch in secs:
        if ch & 0x20000000:
            continue
        n = len(data) // 4
        i = 0
        while i < n:
            j = i
            while j < n and is_code(struct.unpack_from("<I", data, 4 * j)[0]):
                j += 1
            if j - i >= 8:
                entries = [struct.unpack_from("<I", data, 4 * k)[0] for k in range(i, j)]
                labels = [name_of(e) for e in entries]
                for iface, prefix in WINE_PREFIX.items():
                    hits = sum(1 for lab in labels if lab and lab.startswith(prefix))
                    if hits >= len(labels) // 2 and iface not in found:
                        rows = []
                        for slot, (e, lab) in enumerate(zip(entries, labels)):
                            p = pops(e)
                            rows.append({"slot": slot, "wine": lab,
                                         "name": (lab or "").split("_", 1)[-1]
                                         if lab != "get_voice_details" else "GetVoiceDetails",
                                         "pops": p,
                                         "argc": p[0] // 4 - 1 if len(p) == 1 else None})
                        found[iface] = {"vtable": "%#010x" % (sva + 4 * i), "rows": rows}
            i = max(j, i + 1)
    return found


def wine_check(path: Path, declared: dict[tuple[str, int], dict]) -> dict:
    tables = wine_vtables(path)
    out: dict = {"dll": str(path), "dll_sha256": C.sha256(path), "vtables": tables,
                 "mismatches": []}
    for iface in (ENGINE, MASTER, SOURCE):
        t = tables.get(iface)
        if t is None:
            out["mismatches"].append({"interface": iface, "why": "no vtable found in the DLL"})
            continue
        if len(t["rows"]) != len(NAMES[iface]):
            out["mismatches"].append({"interface": iface,
                                      "why": "the DLL's table has %d slots, 2.6 has %d"
                                             % (len(t["rows"]), len(NAMES[iface]))})
        for r in t["rows"]:
            d = declared.get((iface, r["slot"]))
            if d is None or d["name"] != r["name"] or d["argc"] != r["argc"]:
                out["mismatches"].append({"interface": iface, "slot": r["slot"],
                                          "wine": [r["name"], r["argc"]],
                                          "declared": [d["name"], d["argc"]] if d else None})
    out["ok"] = not out["mismatches"]
    return out


# ---------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, default=IM.DEFAULT_EXE)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--check", action="store_true",
                    help="the witnesses, the seed check and the two censuses")
    ap.add_argument("--argc", type=Path, default=None,
                    help="a run JSON with an xaudio2.methods array, or runtime/sa2_xaudio2.c")
    ap.add_argument("--wine", type=Path, default=None,
                    help="Wine's xaudio2_6.dll, the library the native run loaded")
    ap.add_argument("--falsify", action="store_true",
                    help="re-run with no seeds, and with no roots or seeds, and fail unless "
                         "each removal loses sites")
    args = ap.parse_args(argv)

    img = IM.Image(args.exe)
    sites, shape, cover, kinds, refs = scan_image(img, GLOBAL_ROOTS, LOAD_SEEDS)
    slots: dict[str, dict] = {}
    for s in sites:
        e = slots.setdefault("%s:%d" % (s["iface"], s["slot"]),
                             {"interface": s["iface"], "slot": s["slot"], "name": s["name"],
                              "sites": []})
        e["sites"].append(s["site"])
    doc: dict = {
        "schema": "sa2-xaudio2-surface/v1",
        "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "exe": str(args.exe), "exe_sha256": C.sha256(args.exe),
        "coverage": cover,
        "roots": {"%#010x" % k: {"interface": v[0], "why": v[1]} for k, v in GLOBAL_ROOTS.items()},
        "load_seeds": {"%#010x" % k: {"interface": v[0], "why": v[1]}
                       for k, v in LOAD_SEEDS.items()},
        "sites": sites,
        "distinct_slots": sorted(slots.values(), key=lambda e: (e["interface"], e["slot"])),
        "vtable_shaped_sites_unattributed": len(shape),
    }
    bad = False
    if args.check:
        doc["check"] = check(sites)
        doc["seed_check"] = seed_check(kinds)
        doc["global_census"] = global_census(refs, sites)
        doc["field_census"] = field_census(img, LOAD_SEEDS)
        bad |= not (doc["check"]["ok"] and doc["seed_check"]["ok"]
                    and doc["global_census"]["ok"] and doc["field_census"]["ok"])
    declared = None
    if args.argc or args.wine:
        declared = load_declared(args.argc or RUNTIME_SOURCE)
        doc["names"] = names_check(declared)
        bad |= not doc["names"]["ok"]
    if args.argc:
        doc["argc"] = argc_check(sites, declared, str(args.argc))
        bad |= not doc["argc"]["ok"]
    if args.wine:
        doc["wine"] = wine_check(args.wine, declared)
        bad |= not doc["wine"]["ok"]
    if args.falsify:
        no_seeds = scan_image(img, GLOBAL_ROOTS, {})[0]
        nothing = scan_image(img, {}, {})[0]
        doc["falsify"] = {"with_all": len(sites), "with_no_seeds": len(no_seeds),
                          "with_no_roots_or_seeds": len(nothing),
                          "seeds_are_load_bearing": len(no_seeds) < len(sites),
                          "roots_are_load_bearing": len(nothing) == 0 and len(sites) > 0}
        # A seed list or root table that attributes nothing the scan would not
        # find without it is a claim with no test behind it, so --falsify fails
        # the run rather than printing the fact.
        bad |= not (doc["falsify"]["seeds_are_load_bearing"]
                    and doc["falsify"]["roots_are_load_bearing"])

    for e in doc["distinct_slots"]:
        print("%-23s slot %2d  %-20s %s" % (e["interface"], e["slot"], e["name"] or "(unnamed)",
                                             " ".join(e["sites"])))
    print("sites: %d attributed, %d vtable-shaped unattributed"
          % (len(sites), doc["vtable_shaped_sites_unattributed"]))
    if args.check:
        for k, v in doc["check"]["witnesses"].items():
            print("  witness %-26s %-5s %s" % (k, v["held"], v["detail"]))
        print("check ok:", doc["check"]["ok"], "| seeds ok:", doc["seed_check"]["ok"],
              "| global census ok:", doc["global_census"]["ok"],
              "| field census ok:", doc["field_census"]["ok"])
    if args.argc:
        a = doc["argc"]
        for r in a["compared"]:
            print("  argc %-23s slot %2d %-20s declared %d vs %d pushed at %s%s"
                  % (r["interface"], r["slot"], r["declared_method"], r["declared_argc"],
                     r["argc_rule"], r["site"],
                     "   DISAGREES" if r["declared_argc"] != r["argc_rule"] else ""))
        for k in ("not_compared", "called_but_refused", "answered_but_never_called", "misnamed"):
            for r in a[k]:
                print("  %s: %s" % (k, json.dumps(r)))
        print("argc ok:", a["ok"], "(compared %d)" % len(a["compared"]), "| names ok:",
              doc["names"]["ok"])
    if args.wine:
        w = doc["wine"]
        for iface, t in w["vtables"].items():
            print("  wine %-23s vtable %s, %d slots" % (iface, t["vtable"], len(t["rows"])))
        for mm in w["mismatches"]:
            print("  wine mismatch:", json.dumps(mm))
        print("wine ok:", w["ok"])
    if args.falsify:
        print("falsify:", json.dumps(doc["falsify"]))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(doc, indent=1) + "\n")
        print("->", args.json)
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
