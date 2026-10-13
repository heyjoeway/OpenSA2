#!/usr/bin/env python3
"""Shape (b): is a captured mid-function window drivable by lifting at its entry address?

The brief's hypothesis: `sa2_call` takes a guest address and remill can lift starting at
an arbitrary address, so a window captured at, say, 0x0047D0EC inside
0x0047CD60 ListGroundForCollision ought to be drivable by lifting there and setting up
the state the fixture's rows record. This tool tests it in two halves.

  --lift    lift a block at the window address (through tools/s2/corpus_run/lift_set.py
            --block) and report whether remill produced a callable define, whether it
            links into the corpus driver, and whether `sa2_call` can enter it.
  --livein  the machine state the window reads before it writes it, taken from the
            image with iced-x86: registers read-before-written, stack-frame slots,
            x87 status-word bits, and absolute guest globals. That census is what has
            to be compared against the fixture's recorded columns.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s2/corpus_run/window_entry.py --window 0x0047D0EC --end 0x0047D160
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import pefile
from iced_x86 import (Decoder, FlowControl, Formatter, FormatterSyntax,
                      InstructionInfoFactory, Mnemonic, OpAccess, OpKind, Register,
                      RegisterExt)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
GEN = ROOT / "private" / "generated"
OUT = GEN / "s2" / "corpus_run"
EXE = ROOT / "private" / "exe" / "sonic2app-unpacked.exe"


def image() -> bytes:
    return bytes(pefile.PE(str(EXE), fast_load=True).get_memory_mapped_image())


# The x87 stack moves under the register names, and iced-x86 reports no ST write at all
# for `fld`/`fldz`: this window pushes four values and then names st(2) and st(3), which
# the old census read as three live x87 registers when all four slots are the window's
# own. `depth` is the net number of pushes the window has made, so a reference to ST(i)
# is the caller's register only when `i >= depth`.
X87_PUSH = {Mnemonic.FLD, Mnemonic.FILD, Mnemonic.FBLD, Mnemonic.FLD1, Mnemonic.FLDZ,
            Mnemonic.FLDPI, Mnemonic.FLDL2E, Mnemonic.FLDL2T, Mnemonic.FLDLG2,
            Mnemonic.FLDLN2}
X87_POP1 = {Mnemonic.FSTP, Mnemonic.FISTP, Mnemonic.FBSTP, Mnemonic.FISTTP,
            Mnemonic.FCOMP, Mnemonic.FICOMP, Mnemonic.FUCOMP, Mnemonic.FCOMIP,
            Mnemonic.FUCOMIP, Mnemonic.FADDP, Mnemonic.FSUBP, Mnemonic.FSUBRP,
            Mnemonic.FMULP, Mnemonic.FDIVP, Mnemonic.FDIVRP, Mnemonic.FYL2X,
            Mnemonic.FYL2XP1, Mnemonic.FPATAN, Mnemonic.FSTPNCE}
X87_POP2 = {Mnemonic.FCOMPP, Mnemonic.FUCOMPP}
SEGMENTS = {Register.ES, Register.CS, Register.SS, Register.DS, Register.FS, Register.GS}


def _decode(img: bytes, start: int, end: int) -> dict[int, object]:
    """Every instruction in [start, end), by address. A linear decode: these ranges are
    function bodies and windows inside them, not mixed code and data."""
    decoder = Decoder(32, img[start - 0x400000:end - 0x400000], ip=start)
    out = {}
    for ins in decoder:
        if ins.ip >= end:
            break
        out[ins.ip] = ins
    return out


# A `call` inside the window returns with the caller-saved registers clobbered, so a read
# of one after a call is not a read of the window's own entry value. Modelling it is a
# choice, so the census reports the count both ways.
CALL_CLOBBERS = {Register.RAX, Register.RCX, Register.RDX}


def _successors(ins, start: int, end: int, code: dict) -> list[int]:
    fc = ins.flow_control
    nxt = ins.next_ip
    if fc in (FlowControl.RETURN, FlowControl.INTERRUPT, FlowControl.EXCEPTION):
        return []
    if fc == FlowControl.UNCONDITIONAL_BRANCH:
        t = ins.near_branch_target
        return [t] if t in code else []
    if fc == FlowControl.INDIRECT_BRANCH:
        return []
    if fc == FlowControl.CONDITIONAL_BRANCH:
        t = ins.near_branch_target
        return ([t] if t in code else []) + ([nxt] if nxt in code else [])
    return [nxt] if nxt in code else []


HIGH8 = {Register.AH, Register.CH, Register.DH, Register.BH}


def _mask(reg: int) -> int:
    """The bytes of the full register this name covers, as a bitmask, one bit per byte.

    Liveness over whole registers is wrong in both directions here. `0x004620CA` starts
    `mov dx,[1DE9484h]`: counting that as a def of `edx` loses the top half, which the
    window does read, and not counting it as a def at all reports `edx` live when only two
    of its bytes are. Masks make the answer exact and let the census say which bytes.
    """
    size = min(RegisterExt.size(reg), 4)
    off = 1 if reg in HIGH8 else 0
    return ((1 << size) - 1) << off


def _is_nop(ins) -> bool:
    """A padding instruction that reads nothing.

    `0x0046235A` inside the 0x004622EE window is `lea ebx,[ebx]` -- the six-byte NOP form
    a compiler emits to align a loop head. iced reports it as a read of `ebx`, and it was
    the whole reason that window's census claimed a live-in no plan supplies.
    """
    if ins.mnemonic == Mnemonic.NOP:
        return True
    if ins.mnemonic != Mnemonic.LEA:
        return False
    return (ins.op0_kind == OpKind.REGISTER
            and ins.memory_base == ins.op0_register
            and ins.memory_index == Register.NONE
            and ins.memory_displacement == 0)


def _uses_defs(ins, info, model_call_clobbers: bool) -> tuple[dict, dict, dict]:
    """(use masks, def masks, read_as) for one instruction, per full general register."""
    uses: dict[int, int] = {}
    defs: dict[int, int] = {}
    read_as: dict[int, int] = {}
    if _is_nop(ins):
        return uses, defs, read_as
    i = info.info(ins)
    for ri in i.used_registers():
        base, acc = ri.register, ri.access
        if Register.ST0 <= base <= Register.ST7:
            continue          # the x87 stack rotates under these names; see _x87_live_in
        full = _full(base)
        m = _mask(base)
        if acc in (OpAccess.READ, OpAccess.READ_WRITE, OpAccess.COND_READ,
                   OpAccess.READ_COND_WRITE):
            uses[full] = uses.get(full, 0) | m
            read_as.setdefault(full, base)
        # A conditional write kills nothing: the value survives when the condition is false.
        if acc == OpAccess.WRITE:
            defs[full] = defs.get(full, 0) | m
    if model_call_clobbers and ins.flow_control == FlowControl.CALL:
        for r in CALL_CLOBBERS:
            defs[r] = 0xF
            uses.pop(r, None)
    return uses, defs, read_as


def _liveness(code: dict, start: int, end: int, model_call_clobbers: bool) -> tuple[dict, dict]:
    """Backward liveness over the window's own CFG, per byte of each register.

    Returns `({full register: live byte mask at `start`}, {register: first-read site})`.
    """
    info = InstructionInfoFactory()
    fmt = Formatter(FormatterSyntax.INTEL)
    uses, defs, read_as, succ = {}, {}, {}, {}
    for ip, ins in code.items():
        u, d, ra = _uses_defs(ins, info, model_call_clobbers)
        uses[ip], defs[ip], read_as[ip] = u, d, ra
        succ[ip] = _successors(ins, start, end, code)
    preds: dict[int, list[int]] = {ip: [] for ip in code}
    for ip, ss in succ.items():
        for s in ss:
            preds[s].append(ip)

    live_in: dict[int, dict[int, int]] = {ip: {} for ip in code}
    work = list(code)
    while work:
        ip = work.pop()
        out: dict[int, int] = {}
        for s in succ[ip]:
            for r, m in live_in[s].items():
                out[r] = out.get(r, 0) | m
        new: dict[int, int] = {}
        for r, m in out.items():
            m &= ~defs[ip].get(r, 0)
            if m:
                new[r] = m
        for r, m in uses[ip].items():
            new[r] = new.get(r, 0) | m
        if new != live_in[ip]:
            live_in[ip] = new
            work.extend(preds[ip])

    entry = live_in.get(start, {})
    sites = {}
    for reg, want in entry.items():
        seen, stack, best = set(), [start], None
        while stack:
            ip = stack.pop(0)
            if ip in seen:
                continue
            seen.add(ip)
            if uses[ip].get(reg, 0) & want:
                if best is None or ip < best[0]:
                    best = (ip, read_as[ip].get(reg, reg))
                continue
            if defs[ip].get(reg, 0) & want == want:
                continue
            stack += succ[ip]
        if best:
            sites[reg] = {"at": f"0x{best[0]:08x}", "read_as": _name(best[1]),
                          "insn": fmt.format(code[best[0]])}
    return entry, sites


X87_FXCH = {Mnemonic.FXCH}


def _x87_live_in(code: dict, start: int) -> tuple[list[dict], list[dict], set]:
    """Which of the caller's x87 registers the window reads, by symbolic stack.

    A push depth counter is not enough, and the counter this tool used to keep was wrong in
    the direction that hides a live-in. `0x00462F83` is the case: `fstp [esp+0Ch], st` reads
    the caller's ST0 and pops, so the caller's ST1 becomes the physical ST0; a later `fld`
    pushes the window's own value back on top, and the `fcompp` at `0x00463006` -- whose
    result steers control flow -- then reads physical ST1, which is the caller's *second*
    register. A counter clamped at zero reports one live x87 register there; the truth is
    two.

    So the stack is modelled by identity: eight caller slots at entry, `push` inserts the
    window's own value, `pop` removes the top and shifts an unknown in at the bottom,
    `fxch` swaps. A read of a slot still holding a caller identity, before anything has
    written it, is a live-in.

    Limits: paths are walked forward and the first stack that reaches an instruction wins,
    with every disagreement recorded in `x87_stack_conflicts`; and a `call` inside the
    window is assumed to leave the stack as it found it, which is false for a callee that
    returns a float in ST0. Every live-in therefore carries `after_a_call`, and one that is
    true may be the callee's return value rather than the caller's register. The G2b fold
    settled which by measurement instead of argument: `SA2_X87_ENTRY=<value>` puts that
    value in all eight entry registers (harness/plan_driver.c) and the whole corpus was
    re-run at 1.5 and at -7777.25. Not one fixture's verdict moved, and every x87 live-in
    the nine windows have is `after_a_call`.
    """
    CALLER = [("caller", j) for j in range(8)]
    info = InstructionInfoFactory()
    fmt = Formatter(FormatterSyntax.INTEL)
    stacks = {start: (tuple(CALLER), False)}
    conflicts: list[dict] = []
    live: dict[str, dict] = {}
    written: set[str] = set()
    work = [start]
    order = []
    while work:
        ip = work.pop(0)
        order.append(ip)
        ins = code[ip]
        st_t, after_call = stacks[ip]
        st = list(st_t)
        i = info.info(ins)
        text = fmt.format(ins)
        refs = [(ri.register - Register.ST0, ri.access) for ri in i.used_registers()
                if Register.ST0 <= ri.register <= Register.ST7]
        for idx, acc in refs:
            if idx >= len(st):
                continue
            tag = st[idx]
            if tag[0] == "caller":
                key = f"st{tag[1]}"
                if acc in (OpAccess.READ, OpAccess.READ_WRITE, OpAccess.COND_READ,
                           OpAccess.READ_COND_WRITE) and key not in written:
                    live.setdefault(key, {"caller_slot": key, "read_as": f"st{idx}",
                                          "at": f"0x{ip:08x}", "insn": text,
                                          "after_a_call": after_call})
        if ins.mnemonic in X87_FXCH and len(refs) >= 2:
            a, b = refs[0][0], refs[1][0]
            if a < len(st) and b < len(st):
                st[a], st[b] = st[b], st[a]
        else:
            for idx, acc in refs:
                if idx < len(st) and acc in (OpAccess.WRITE, OpAccess.READ_WRITE,
                                             OpAccess.COND_WRITE, OpAccess.READ_COND_WRITE):
                    if st[idx][0] == "caller":
                        written.add(f"st{st[idx][1]}")
                    st[idx] = ("own", ip)
        if ins.mnemonic in X87_PUSH:
            st = [("own", ip)] + st[:7]
        elif ins.mnemonic in X87_POP2:
            st = st[2:] + [("unknown", 0), ("unknown", 0)]
        elif ins.mnemonic in X87_POP1:
            st = st[1:] + [("unknown", 0)]
        nxt = (tuple(st), after_call or ins.flow_control == FlowControl.CALL)
        for s in _successors(ins, min(code), max(code) + 1, code):
            if s not in stacks:
                stacks[s] = nxt
                work.append(s)
            elif stacks[s][0] != nxt[0]:
                conflicts.append({"at": f"0x{s:08x}", "from": f"0x{ip:08x}",
                                  "kept": [f"{k}{v:x}" if k == "own" else f"{k}{v}"
                                           for k, v in stacks[s][0]],
                                  "other": [f"{k}{v:x}" if k == "own" else f"{k}{v}"
                                            for k, v in nxt[0]]})
    return [live[k] for k in sorted(live)], conflicts, set(order)


def live_in(img: bytes, start: int, end: int) -> dict:
    """Registers, x87 slots and memory the window reads before defining them.

    **This follows branches.** Until the G2b fold the walk was straight-line, and its
    docstring claimed it could "only over-report, never under-report". That is false in the
    direction that matters: a linear walk treats a write on one path as killing the value on
    every path, so a register written in a branch the entry never takes and read on the
    branch it does take was reported as dead. The tool had been run on exactly two windows,
    both rejected; the nine that ship carry 22 of the 52 corpus passes and had no census at
    all.

    What it does now: decode the window, build its control-flow graph, and run a backward
    liveness fixpoint over it. The live-in set is `LiveIn(start)`; `first_read` is where
    each one is first read on some path from the entry.

    Two corrections the G2 fold made, both of which inflated the earlier census, still hold:
    sub-registers are counted once through `RegisterExt.full_register` (the old code called
    a method that does not exist in this decoder build and swallowed the error with a bare
    `except`), and x87 pushes are not counted as reads.

    Remaining limits, recorded rather than argued:

      * a `call` inside the window is modelled as clobbering eax/ecx/edx and reading
        nothing beyond its operands. `registers_read_before_written_no_call_model` is the
        same analysis without that assumption, and is the conservative number.
      * the callee of such a call is not entered, so a global *it* reads is not in
        `guest_globals_read`.
      * an indirect branch ends its path; the window's own targets of one are not followed.
    """
    code = _decode(img, start, end)
    if not code:
        return {"window": f"0x{start:08x}", "end": f"0x{end:08x}", "instructions": 0,
                "error": "nothing decoded"}
    fmt = Formatter(FormatterSyntax.INTEL)
    info = InstructionInfoFactory()

    entry, sites = _liveness(code, start, end, True)
    entry_raw, _ = _liveness(code, start, end, False)
    x87_live, x87_conflicts, reachable = _x87_live_in(code, start)

    reads, segment_reads = [], []
    for reg in sorted(entry, key=_name):
        site = sites.get(reg, {})
        mask = entry[reg]
        item = {"register": _name32(reg), "read_as": site.get("read_as", _name(reg)),
                "at": site.get("at"), "insn": site.get("insn"),
                "live_bytes": f"0x{mask:x}",
                "whole_register": mask == 0xF}
        (segment_reads if reg in SEGMENTS else reads).append(item)

    # memory, over every reachable instruction.
    mem_reads, listing = [], []
    globals_read: dict[str, dict] = {}
    globals_written: dict[str, dict] = {}
    for ip in sorted(code):
        if ip not in reachable:
            continue                      # unreachable from the entry
        ins = code[ip]
        text = fmt.format(ins)
        listing.append({"ip": f"0x{ip:08x}", "text": text})
        i = info.info(ins)
        for mi in i.used_memory():
            absolute = mi.base == Register.NONE and mi.index == Register.NONE
            key = f"0x{mi.displacement & 0xFFFFFFFF:08x}"
            if mi.access in (OpAccess.READ, OpAccess.READ_WRITE, OpAccess.COND_READ):
                mem_reads.append({"at": f"0x{ip:08x}", "insn": text,
                                  "base": _name(mi.base), "index": _name(mi.index),
                                  "displacement": f"0x{mi.displacement:x}"})
                if absolute:
                    globals_read.setdefault(key, {"address": key, "at": f"0x{ip:08x}",
                                                  "insn": text, "memory_size": int(mi.memory_size)})
            if absolute and mi.access in (OpAccess.WRITE, OpAccess.READ_WRITE,
                                          OpAccess.COND_WRITE, OpAccess.READ_COND_WRITE):
                globals_written.setdefault(key, {"address": key, "at": f"0x{ip:08x}",
                                                 "insn": text, "memory_size": int(mi.memory_size)})
    return {"window": f"0x{start:08x}", "end": f"0x{end:08x}",
            "instructions": len(listing), "decoded": len(code), "listing": listing,
            "walk": "control-flow graph, backward liveness fixpoint",
            "registers_read_before_written": reads,
            "registers_read_before_written_no_call_model":
                sorted(_name32(r) for r in entry_raw if r not in SEGMENTS),
            "segments_read": segment_reads,
            "x87_read_before_written": x87_live,
            "x87_stack_conflicts": x87_conflicts,
            "guest_globals_read": sorted(globals_read.values(), key=lambda g: g["address"]),
            "guest_globals_written": sorted(globals_written.values(), key=lambda g: g["address"]),
            "memory_reads": mem_reads,
            "census": {"registers": len(reads), "segments": len(segment_reads),
                       "x87_registers": len(x87_live),
                       "guest_globals": len(globals_read),
                       "guest_globals_written": len(globals_written),
                       "memory_reads": len(mem_reads),
                       "reachable_instructions": len(listing),
                       "decoded_instructions": len(code)}}


def _full(reg: int) -> int:
    """The full register a sub-register belongs to (`ah` -> `rax`), so it is counted once."""
    return RegisterExt.full_register(reg)


_NAMES = {getattr(Register, n): n.lower() for n in dir(Register)
          if n.isupper() and isinstance(getattr(Register, n), int)}


def _name(reg: int) -> str:
    return _NAMES.get(reg, str(reg))


def _name32(reg: int) -> str:
    """The 32-bit name of a full register, which is what a plan's OP_REG index means."""
    try:
        return _NAMES.get(RegisterExt.full_register32(reg), _name(reg))
    except Exception:                                   # noqa: BLE001
        return _name(reg)


def drive_experiment(binary: Path) -> dict:
    """Enter 0x0047D0EC twice with identical state but for `ah`, and watch the answer flip.

    The window's first instruction is `test ah, 41h`: `ah` holds the x87 condition codes
    `fnstsw` copied from the `fcompp` two instructions earlier, and that bit decides
    whether the COL entry is admitted. Everything else is set to the same plausible
    frame in both rows. If the two rows differ, the window's answer is a function of a
    live-in the fixture's columns do not carry.
    """
    sys.path.insert(0, str(HERE))
    import plans as P

    FRAME = 0x03E00000              # inside the runtime's stack arena
    COL = P.A_ARGS + 0x1000         # a COL entry for esi
    NEAR = P.A_ARGS + 0x2000        # the near-set cursor for ebp
    COUNT_GLOBAL = 0x01DE9484
    SENTINEL = 0xDEAD0000
    rows, expected = [], []
    for ah in (0x00, 0x41):
        o = P.Ops()
        o.fill(P.A_ARGS, 0x10000)
        o.fill(FRAME - 0x100, 0x200)
        o.poke(COL + 0x10, [0x11223344])          # [esi+0x10]: the COL slot word copied out
        o.poke(COUNT_GLOBAL, [0])                 # the near-set count the window increments
        o.reg(P.ESP, FRAME)
        o.poke(FRAME + 0x2C, [SENTINEL])          # the epilogue pops 4 regs then adds 0x1C
        o.poke(FRAME + 0x1C, [1])                 # [esp+0x1C]: loop counter, 1 -> the loop ends
        o.reg(P.EAX, ah << 8)
        o.reg(P.EBP, NEAR).reg(P.EDI, 0xAAAA0001).reg(P.EBX, 0)
        o.reg(P.ESI, COL)
        o.call()
        o.peek(NEAR, 12)
        o.peek(COUNT_GLOBAL, 4)
        rows.append(o.bytes())
        expected.append((0, 0, 0, 0))
    plan = P.Plan("window-0047d0ec-ah-probe", 0x0047D0EC, 4,
                  ("near[0]", "near[1]", "near[2]", "near_count"), rows, expected,
                  abi="entered at the window address with a synthetic ListGroundForCollision frame")
    pdir = OUT / "plans"
    pdir.mkdir(parents=True, exist_ok=True)
    pf = pdir / "window-0047d0ec-ah-probe.plan.bin"
    of = OUT / "out" / "window-0047d0ec-ah-probe.out.bin"
    of.parent.mkdir(parents=True, exist_ok=True)
    pf.write_bytes(plan.encode())
    proc = subprocess.run([str(binary), str(EXE), str(pf), str(of)], capture_output=True, text=True)
    import struct
    data = of.read_bytes() if of.exists() else b""
    obs = []
    if data[:8] == b"SA2FXO1\0":
        _va, nrows, words = struct.unpack_from("<3I", data, 8)
        obs = [list(struct.unpack_from(f"<{words}I", data, 20 + i * words * 4)) for i in range(nrows)]
    return {"rc": proc.returncode, "stderr": proc.stderr.strip().splitlines()[-3:],
            "rows": [{"ah": hex(ah), "status": r[0], "exit_pc": f"0x{r[1]:08x}",
                      "near": [f"0x{w:08x}" for w in r[2:5]], "near_count": r[5]}
                     for ah, r in zip((0x00, 0x41), obs)],
            "answer_depends_on_ah": bool(len(obs) == 2 and obs[0][2:] != obs[1][2:])}


# ---------------------------------------------------------------- the census set
# The windows the corpus ships: an address inside a mapped function that `lift_set.py
# --block START:END` lifted as its own module. Read from the lift log rather than typed,
# so a window that is added or removed shows up here without this file being edited.
def shipping_windows() -> dict[int, int]:
    """{window address: end address} for every lifted block that a plan or the runtime can
    enter, from `lift.json`."""
    log = json.loads((OUT / "lift.json").read_text())["functions"]
    out = {}
    for key, e in log.items():
        if e.get("region") != "unmapped" or not e.get("lifted") or not e.get("end"):
            continue
        out[int(key, 16)] = int(e["end"], 16)
    return out


# The nine the G2 interrogation named: the windows that carry corpus passes. Anything else
# `lift.json` holds is a CRT orphan block or a window a survey rejected.
SHIPPING = [0x004620CA, 0x00462133, 0x004622EE, 0x004624C0, 0x00462526, 0x00462F83,
            0x0048B6C0, 0x0048DA51, 0x0048DBD7]

# harness/plan_ops.h. {opcode: fixed argument words}; OP_POKE is variable and handled below.
OP_ARGS = {0: 0, 1: 2, 2: 3, 3: 2, 4: 2, 5: 2, 6: 1, 7: 0, 8: 1, 9: 0, 10: 0, 11: 0,
           12: 2, 13: 1, 14: 0, 15: 3, 16: 4, 17: 1, 18: 1}
OP_POKE, OP_FILL, OP_REG, OP_CALL, OP_ST, OP_ST_F80, OP_ST_UNSUPPLIED = 1, 2, 3, 4, 15, 16, 17
REG_NAMES = ["eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi", "eip"]


def scan_ops(blob: bytes) -> dict:
    """What one op stream supplies: registers set, x87 slots set or declared unsupplied,
    call targets, and the guest ranges it writes."""
    import struct as _s
    words = _s.unpack(f"<{len(blob) // 4}I", blob[:len(blob) // 4 * 4])
    regs, sts, unsupplied, calls, writes = set(), set(), set(), [], []
    k = 0
    while k < len(words):
        op = words[k]
        k += 1
        if op == 0:
            break
        if op == OP_POKE:
            addr, nbytes = words[k], words[k + 1]
            writes.append((addr, nbytes))
            k += 2 + (nbytes + 3) // 4
            continue
        n = OP_ARGS.get(op)
        if n is None:
            break                       # an op this reader does not know: stop, honestly
        args = words[k:k + n]
        k += n
        if op == OP_FILL:
            writes.append((args[0], args[1]))
        elif op == OP_REG:
            regs.add(int(args[0]))
        elif op == OP_CALL:
            calls.append(int(args[0]))
        elif op in (OP_ST, OP_ST_F80):
            sts.add(int(args[0]))
        elif op == OP_ST_UNSUPPLIED:
            unsupplied.add(int(args[0]))
    return {"registers": regs, "x87": sts, "x87_unsupplied": unsupplied,
            "calls": calls, "writes": writes}


def read_plan(path: Path) -> dict:
    import struct as _s
    data = path.read_bytes()
    if data[:8] != b"SA2PLN1\0":
        raise ValueError(f"{path}: not a plan file")
    _ver, entry_va, rows, _out_words, global_bytes = _s.unpack_from("<5I", data, 8)
    glob = data[28:28 + global_bytes]
    cur = 28 + global_bytes
    row = b""
    if rows:
        (n,) = _s.unpack_from("<I", data, cur)
        row = data[cur + 4:cur + 4 + n]
    g, r = scan_ops(glob), scan_ops(row)
    return {"plan": path.name, "entry_va": entry_va, "rows": rows,
            "registers": g["registers"] | r["registers"],
            "x87": g["x87"] | r["x87"],
            "x87_unsupplied": g["x87_unsupplied"] | r["x87_unsupplied"],
            "calls": sorted(set(g["calls"] + r["calls"])),
            "writes": g["writes"] + r["writes"]}


def census_all(img: bytes, windows: list[int], ends: dict[int, int]) -> dict:
    """Run the live-in census on every shipping window and store it beside the lift log."""
    d = OUT / "livein"
    d.mkdir(parents=True, exist_ok=True)
    out = {}
    for va in windows:
        end = ends.get(va)
        if end is None:
            out[f"0x{va:08x}"] = {"error": "not a lifted block in lift.json"}
            continue
        rep = live_in(img, va, end)
        (d / f"window_{va:08x}.livein.json").write_text(json.dumps(rep, indent=1) + "\n")
        out[f"0x{va:08x}"] = rep
    index = {"tool": "tools/s2/corpus_run/window_entry.py --census-all",
             "windows": {k: {"end": v.get("end"), **(v.get("census") or {}),
                             "registers_read": [r["register"] for r in v.get("registers_read_before_written", [])],
                             "x87_read": [x["caller_slot"] for x in v.get("x87_read_before_written", [])]}
                         for k, v in out.items()},
             "stored_in": str(d.relative_to(ROOT))}
    (d / "index.json").write_text(json.dumps(index, indent=1) + "\n")
    return out


def sections() -> list[dict]:
    pe = pefile.PE(str(EXE), fast_load=True)
    out = []
    for s in pe.sections:
        out.append({"name": s.Name.rstrip(b"\0").decode("ascii", "replace"),
                    "va": 0x400000 + s.VirtualAddress,
                    "virtual_size": s.Misc_VirtualSize,
                    "raw_size": s.SizeOfRawData,
                    "writable": bool(s.Characteristics & 0x80000000)})
    return out


def globals_scan(img: bytes, censuses: dict, plans: list[dict]) -> list[dict]:
    """Every absolute guest global the shipping windows read, with what answers it.

    G2 finding 6 asks for this to be recorded so S3 need not redo it. For each address:
    which section holds it, whether the PE's file image initialises it or it is the
    zero-filled tail past `SizeOfRawData`, the four file-initial bytes, whether the section
    is writable, whether any plan writes over it, and which windows read it. An address
    that is *not* file-initialised, *not* written by a plan and *is* in a writable section
    is mutable runtime state the corpus is answering with a zero nobody chose.
    """
    secs = sections()
    writes: list[tuple[int, int]] = []
    for p in plans:
        writes += [(a, n) for a, n in p["writes"]]

    def written_by_plan(addr: int) -> bool:
        return any(a <= addr < a + n for a, n in writes)

    seen: dict[int, dict] = {}
    for key, c in censuses.items():
        for g in c.get("guest_globals_read", []):
            a = int(g["address"], 16)
            row = seen.setdefault(a, {"address": g["address"], "windows": [], "at": g["at"],
                                      "insn": g["insn"]})
            row["windows"].append(key)
    out = []
    for a in sorted(seen):
        row = seen[a]
        sec = next((s for s in secs if s["va"] <= a < s["va"] + max(s["virtual_size"], s["raw_size"])), None)
        file_initialised = bool(sec and a < sec["va"] + sec["raw_size"])
        off = a - 0x400000
        row.update({
            "section": sec["name"] if sec else None,
            "section_writable": sec["writable"] if sec else None,
            "file_initialised": file_initialised,
            "file_initial_dword": (f"0x{int.from_bytes(img[off:off + 4], 'little'):08x}"
                                   if 0 <= off < len(img) - 4 else None),
            "written_by_a_plan": written_by_plan(a),
        })
        row["verdict"] = (
            "written by a plan" if row["written_by_a_plan"] else
            "file-initial constant" if file_initialised and not (sec and sec["writable"]) else
            "file-initial, in a writable section: could be mutable runtime state"
            if file_initialised else
            "zero-filled .bss tail, nothing writes it: the corpus answers this read with a "
            "zero no record chose")
        out.append(row)
    return out


def gate(img: bytes) -> int:
    """Fail when a shipping window's live-in set is not accounted for by its plan.

    Before the G2b fold no shipping window had a census at all, and the plan vocabulary had
    no way to set an x87 register -- so four windows read caller x87 registers that the
    driver had zeroed, and nothing could notice. This is the check that notices.

    A window no plan enters directly is reached through lifted code, so its live-ins are
    the producer's own outputs; it is reported and not failed. A window a plan does enter
    must have every general-register live-in set by an `OP_REG` and every caller x87 slot
    set by an `OP_ST` or declared with `OP_ST_UNSUPPLIED`.
    """
    ends = shipping_windows()
    censuses = census_all(img, SHIPPING, ends)
    plans = sorted((OUT / "plans").glob("*.plan.bin"))
    if not plans:
        print("live-in gate: no plan files under private/generated/s2/corpus_run/plans; "
              "run tools/s2/corpus_run/run.py first", file=sys.stderr)
        return 1
    decoded = []
    for pf in plans:
        try:
            decoded.append(read_plan(pf))
        except Exception as exc:                      # noqa: BLE001
            print(f"live-in gate: {pf.name}: {exc}", file=sys.stderr)
            return 1

    failures, report = [], {}
    for va in SHIPPING:
        c = censuses[f"0x{va:08x}"]
        if "error" in c:
            failures.append(f"0x{va:08x}: {c['error']}")
            continue
        regs = [r["register"].replace("r", "e", 1) for r in c["registers_read_before_written"]]
        x87 = [x["caller_slot"] for x in c["x87_read_before_written"]
               if not x.get("after_a_call")]
        x87_after_call = [x["caller_slot"] for x in c["x87_read_before_written"]
                          if x.get("after_a_call")]
        # A plan enters the window when it calls it: `OP_CALL 0` means the plan's own
        # entry_va. The `.nocall` probes `run.py` drives have every OP_CALL stripped, so
        # they enter nothing and are not judged here.
        #
        # Only the baseline plan of each fixture is judged -- the file named exactly
        # `<fixture>.plan.bin`. Every other plan on disk is a deliberate variant:
        # `.control.*` perturbs or withholds one input to see whether the answer moves
        # (`pushed-ecx-is-dead` withholds ECX on purpose, and judging it against "every
        # live-in must be supplied" would fail the gate for the control doing its job),
        # and the `.sw.*` and `.y.*` files are sweep probes. A variant is not what the
        # fixture passes on, so it is not what must account for the window's live-ins.
        # The controls are judged instead by Documentation/scripts/check_controls.py,
        # which requires each to move a column the lift decides.
        entered = [p for p in decoded
                   if p["plan"].count(".") == 2          # <fixture>.plan.bin
                   and (va in p["calls"] or (0 in p["calls"] and p["entry_va"] == va))]
        row = {"window": f"0x{va:08x}", "end": c["end"], "registers": regs, "x87": x87,
               # A caller x87 register first read after a `call` may be that callee's
               # float return rather than the window's live-in; see _x87_live_in. Reported,
               # not failed, and the SA2_X87_ENTRY perturbation is what settles it.
               "x87_after_a_call": x87_after_call,
               "entered_by": [p["plan"] for p in entered]}
        if not entered:
            row["verdict"] = ("entered only through lifted control flow; its live-ins are "
                              "the producing window's own outputs")
            report[f"0x{va:08x}"] = row
            continue
        bad: dict[tuple, dict] = {}
        for p in entered:
            have = {REG_NAMES[i] for i in p["registers"] if i < len(REG_NAMES)}
            missing_r = tuple(r for r in regs if r not in have and r != "esp")
            missing_x = tuple(x for x in x87
                              if int(x[2:]) not in (p["x87"] | p["x87_unsupplied"]))
            if not missing_r and not missing_x:
                continue
            key = (missing_r, missing_x)
            slot = bad.setdefault(key, {"registers_unaccounted": list(missing_r),
                                        "x87_unaccounted": list(missing_x),
                                        "plans": 0, "example": p["plan"]})
            slot["plans"] += 1
        row["unaccounted"] = list(bad.values())
        row["verdict"] = "accounted" if not bad else "NOT accounted"
        report[f"0x{va:08x}"] = row
        for b in bad.values():
            failures.append(f"0x{va:08x}: "
                            + "; ".join(filter(None, [
                                "registers " + ",".join(b["registers_unaccounted"]) if b["registers_unaccounted"] else "",
                                "x87 " + ",".join(b["x87_unaccounted"]) if b["x87_unaccounted"] else ""]))
                            + f" -- {b['plans']} plan(s), e.g. {b['example']}")

    scan = globals_scan(img, censuses, decoded)
    js = OUT / "livein" / "gate.json"
    js.write_text(json.dumps({"windows": report, "failures": failures,
                              "plans_read": len(decoded),
                              "guest_globals_scan": scan}, indent=1) + "\n")
    by_verdict: dict[str, int] = {}
    for g in scan:
        by_verdict[g["verdict"]] = by_verdict.get(g["verdict"], 0) + 1
    print(f"  guest globals the nine windows read: {len(scan)}")
    for v, n in sorted(by_verdict.items(), key=lambda kv: -kv[1]):
        print(f"    {n:>3}  {v}")
    for va, row in report.items():
        print(f"  {va} {row['verdict']}: {len(row['registers'])} registers "
              f"({', '.join(row['registers']) or 'none'}), "
              f"{len(row['x87'])} x87 ({', '.join(row['x87']) or 'none'}"
              + (f", {len(row['x87_after_a_call'])} more only after a call" if row['x87_after_a_call'] else "")
              + "), "
              f"{len(row['entered_by'])} plan(s)")
    if failures:
        print(f"live-in gate FAIL: {len(failures)} shipping window/plan pair(s) read state "
              f"the plan does not supply:", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    print(f"live-in gate OK: {len(SHIPPING)} shipping windows, every live-in accounted for "
          f"by the plans that enter them -> {js.relative_to(ROOT)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--window")
    ap.add_argument("--end")
    ap.add_argument("--census-all", action="store_true",
                    help="run the live-in census on every shipping window and store each "
                         "beside the lift log, under private/generated/s2/corpus_run/livein")
    ap.add_argument("--gate", action="store_true",
                    help="--census-all, then fail when a shipping window's live-in set is "
                         "not accounted for by the plans that enter it")
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--drive", action="store_true",
                    help="also enter the window through sa2_call and probe one live-in bit")
    args = ap.parse_args()
    if args.gate:
        return gate(image())
    if args.census_all:
        censuses = census_all(image(), SHIPPING, shipping_windows())
        for va, c in censuses.items():
            k = c.get("census", {})
            print(f"  {va}: {k.get('registers', 0)} registers, {k.get('x87_registers', 0)} x87, "
                  f"{k.get('guest_globals', 0)} globals, {k.get('reachable_instructions', 0)}"
                  f"/{k.get('decoded_instructions', 0)} instructions reachable")
        return 0
    if not args.window or not args.end:
        ap.error("--window and --end are required unless --census-all or --gate is given")
    start, end = int(args.window, 16), int(args.end, 16)
    img = image()

    report = {"window": f"0x{start:08x}", "end": f"0x{end:08x}"}
    report["live_in"] = live_in(img, start, end)

    lift = subprocess.run([sys.executable, str(HERE / "lift_set.py"), "--block", f"0x{start:08x}"],
                          capture_output=True, text=True, cwd=ROOT)
    log = json.loads((OUT / "lift.json").read_text())["functions"].get(f"0x{start:08x}", {})
    report["lift"] = {"stdout": lift.stdout.strip().splitlines()[-2:], "lifted": log.get("lifted"),
                      "ll_lines": log.get("ll_lines"), "missing_blocks": log.get("missing_blocks"),
                      "error_head": log.get("error_head")}
    build = subprocess.run([sys.executable, str(HERE / "build.py"), "--variant", "pc24"],
                           capture_output=True, text=True, cwd=ROOT)
    bl = json.loads((OUT / "build.pc24.json").read_text())
    report["build"] = {"link_ok": bl["link"]["ok"], "functions": bl["functions"],
                       "undefined_sub_symbols": bl.get("undefined_sub_symbols"),
                       "stdout": build.stdout.strip().splitlines()[-1:]}
    print(json.dumps({k: v for k, v in report.items() if k != "live_in"}, indent=1))
    c = report["live_in"]["census"]
    print(f"live-in: {c['registers']} registers read before written "
          f"({', '.join(r['register'] for r in report['live_in']['registers_read_before_written'])}), "
          f"{c['x87_registers']} x87 registers, {c['guest_globals']} guest globals, "
          f"{c['memory_reads']} memory reads")
    if args.drive and bl["link"]["ok"]:
        report["drive"] = drive_experiment(OUT / "build" / "corpus_driver_pc24")
        print(json.dumps(report["drive"], indent=1))
    js = args.json or (OUT / f"window_{start:08x}.json")
    js.write_text(json.dumps(report, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
