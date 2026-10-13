#!/usr/bin/env python3
"""Ground truth for a switch that dispatches into a direct-flow arm.

Subtitle: interpret the shipped executable's own bytes, on the host, and say
what EAX must hold when the guest function returns -- and which arm it must
have gone through to get there.

`tools/s3/arms/oracle.py` does this for `FUN_004015f0`'s six arms by decoding
each arm and reading off the EBX it must leave for a shared tail. That function
takes its selector from a global and every arm differs in one instruction, so a
decode is enough. The functions this slice needs are not that shape: the answer
is a *computation* -- `inc eax`, a bounds test, a byte-indexed remap, an
indirect jump through a dword table, and then an arm that materialises a
constant -- so the oracle interprets it.

The interpreter is deliberately small and refuses rather than guessing. It
decodes with iced-x86 and implements the handful of x86 forms these functions
use; anything else stops the walk with a refusal, and a refused case is
reported as refused and never as an expected value. That is what keeps this an
oracle instead of a second opinion: it shares no code, no semantics and no
lifter with remill, and the only thing it reads is
`private/exe/sonic2app-unpacked.exe` as `common.memory_image()` loads it.

What it emits per case:

  va        the guest function to call
  setup     `eax:<hex>` -- start with that value in EAX, or
            `mem:<addr>:<hex>` -- poke that dword into guest memory first
  expect    the EAX the function must return
  arm       the address the indirect jump selected, or `-` if none ran
  through   1 if an indirect jump through a jump table chose the arm, 0 if the
            arm was reached by a direct branch or no arm ran at all
  dfarm     1 if that arm is one of the arms remill's direct flow already
            decodes inside the owner's module -- the population slice 4 left
            without a `sub_<arm>` symbol. Read from `armlist.<tag>.json`, so
            the grouping does not change when the binary does

The `through 0` cases matter as much as the `through 1` ones: in both functions
the same arm is also the target of a direct conditional branch, which is lifted
*inside* the owner's module and needs no table row. They reach the same arm by
a path the function table never sees, so they must answer correctly in every
binary and they are the probe's within-function control.

`--negative-control` corrupts one expected value, so the probe that consumes
this file can be seen disagreeing. A comparison nobody has seen fail is not a
comparison.

Output (git-ignored):
  private/generated/s3/arms/switch_oracle.<tag>.json
  private/generated/s3/arms/switch_oracle.<tag>.txt

Usage:
  source tools/env.sh
  .venv/bin/python tools/s3/arms/switch_oracle.py --tag next
  .venv/bin/python tools/s3/arms/switch_oracle.py --tag next --negative-control
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
M32 = 0xFFFFFFFF

# The functions driven, and how each takes its input. Both are call-free, so a
# case runs entirely inside one lifted module plus whatever its jump table
# dispatches to; nothing here needs an import, a stub or a native.
CASES: list[dict] = [
    {
        "va": 0x00439570,
        "name": "FUN_00439570",
        "why": "inc eax; cmp eax,7; ja 00439592; jmp dword [eax*4+439598h]. "
               "Table slot 0 is 0x00439592, which direct flow also reaches through "
               "the `ja`, so it is one of the 343 with no sub_<arm> symbol.",
        "setup": "eax",
        "inputs": [0x00000000, 0x00000001, 0x00000002, 0x00000003, 0x00000004,
                   0x00000005, 0x00000006, 0x00000007, 0x00000008, 0x00000009,
                   0x0000000A, 0x7FFFFFFF, 0xFFFFFFFF],
    },
    {
        "va": 0x004529c0,
        "name": "FUN_004529c0",
        "why": "mov eax,[1934B80h]; cmp eax,6; ja 004529DB; "
               "movzx eax,byte [eax+4529ECh]; jmp dword [eax*4+4529E4h]. "
               "Table slot 1 is 0x004529DB, the `ja` target, so odd selectors "
               "0..6 dispatch into one of the 343 and even ones do not.",
        "setup": "mem:0x01934b80",
        "inputs": [0, 1, 2, 3, 4, 5, 6, 7, 8, 0xFFFFFFFF],
    },
]


class Refused(Exception):
    pass


def _regnames() -> dict:
    """Register -> the name this machine tracks. Only the eight 32-bit GPRs are
    tracked; an 8- or 16-bit operand is refused rather than silently widened."""
    from iced_x86 import Register as R
    return {R.EAX: "eax", R.ECX: "ecx", R.EDX: "edx", R.EBX: "ebx",
            R.ESP: "esp", R.EBP: "ebp", R.ESI: "esi", R.EDI: "edi"}


def _conditions() -> dict:
    """Mnemonic -> condition code, built once. iced's `Mnemonic` members are
    plain ints with no usable `str`, so the mapping is spelled out."""
    from iced_x86 import Mnemonic as M
    return {M.JA: "a", M.JAE: "ae", M.JB: "b", M.JBE: "be", M.JE: "e",
            M.JNE: "ne", M.JG: "g", M.JGE: "ge", M.JL: "l", M.JLE: "le",
            M.JS: "s", M.JNS: "ns"}


def _memsizes() -> dict:
    from iced_x86 import MemorySize as MS
    return {MS.UINT8: 1, MS.INT8: 1, MS.UINT16: 2, MS.INT16: 2,
            MS.UINT32: 4, MS.INT32: 4}


CONDITION = _conditions()
REGNAME = _regnames()
MEMSIZE_BYTES = _memsizes()


class Machine:
    """Just enough 32-bit x86 to run these functions, and no more.

    Every unimplemented form raises, so a case either has a value this
    interpreter computed instruction by instruction or no value at all.
    """

    def __init__(self, img: bytes, pokes: dict[int, int]) -> None:
        self.img = img
        self.pokes = pokes                     # guest address -> dword
        self.reg = {r: 0 for r in ("eax", "ecx", "edx", "ebx", "esp",
                                   "ebp", "esi", "edi")}
        self.cf = self.zf = self.sf = self.of = False
        self.arm: int | None = None
        self.through_table = False
        self.trail: list[str] = []

    # ---------------------------------------------------------------- memory
    def read(self, addr: int, size: int) -> int:
        if size == 4 and addr in self.pokes:
            return self.pokes[addr] & M32
        for a in self.pokes:                   # a poked dword read back in parts
            if a <= addr < a + 4 and addr + size <= a + 4:
                return (self.pokes[a] >> (8 * (addr - a))) & ((1 << (8 * size)) - 1)
        off = addr - C.IMAGE_BASE
        if off < 0 or off + size > len(self.img):
            raise Refused(f"read of {size} bytes at 0x{addr:08x} is outside the image")
        return int.from_bytes(self.img[off:off + size], "little")

    # ------------------------------------------------------------- operands
    def ea(self, ins) -> int:
        from iced_x86 import Register
        if ins.memory_segment not in (Register.DS, Register.SS, Register.NONE):
            raise Refused(f"segment {ins.memory_segment} is not DS")
        addr = ins.memory_displacement & M32
        if ins.memory_base != Register.NONE:
            addr += self.get(ins.memory_base)
        if ins.memory_index != Register.NONE:
            addr += self.get(ins.memory_index) * ins.memory_index_scale
        return addr & M32

    def get(self, reg) -> int:
        if reg not in REGNAME:
            raise Refused(f"register {reg} is not one of the eight 32-bit GPRs")
        return self.reg[REGNAME[reg]]

    def put(self, reg, value: int) -> None:
        if reg not in REGNAME:
            raise Refused(f"register {reg} is not one of the eight 32-bit GPRs")
        self.reg[REGNAME[reg]] = value & M32

    def src(self, ins, i: int, size: int = 4) -> int:
        from iced_x86 import OpKind
        k = ins.op_kind(i)
        if k == OpKind.REGISTER:
            return self.get(ins.op_register(i))
        if k == OpKind.MEMORY:
            return self.read(self.ea(ins), size)
        if k in (OpKind.IMMEDIATE8, OpKind.IMMEDIATE8TO32, OpKind.IMMEDIATE16,
                 OpKind.IMMEDIATE32, OpKind.IMMEDIATE32TO64, OpKind.IMMEDIATE8TO16):
            return ins.immediate(i) & M32
        raise Refused(f"operand kind {k} is not supported")

    # --------------------------------------------------------------- flags
    def flags_sub(self, a: int, b: int) -> None:
        r = (a - b) & M32
        self.cf = a < b
        self.zf = r == 0
        self.sf = bool(r >> 31)
        self.of = bool(((a ^ b) & (a ^ r)) >> 31)

    def flags_logic(self, r: int) -> None:
        self.cf = self.of = False
        self.zf = (r & M32) == 0
        self.sf = bool((r & M32) >> 31)

    def taken(self, cc: str) -> bool:
        return {
            "a": not self.cf and not self.zf, "ae": not self.cf,
            "b": self.cf, "be": self.cf or self.zf,
            "e": self.zf, "ne": not self.zf,
            "g": not self.zf and self.sf == self.of, "ge": self.sf == self.of,
            "l": self.sf != self.of, "le": self.zf or self.sf != self.of,
            "s": self.sf, "ns": not self.sf,
        }[cc]

    # ----------------------------------------------------------------- run
    def run(self, entry: int, limit: int = 400) -> int:
        from iced_x86 import (Decoder, Formatter, FormatterSyntax, Mnemonic,
                              OpKind, Register)
        fmt = Formatter(FormatterSyntax.INTEL)
        pc = entry
        for _ in range(limit):
            off = pc - C.IMAGE_BASE
            if off < 0 or off + 16 > len(self.img):
                raise Refused(f"pc 0x{pc:08x} is outside the image")
            ins = Decoder(32, self.img[off:off + 16], ip=pc).decode()
            if ins.is_invalid:
                raise Refused(f"0x{pc:08x} does not decode")
            self.trail.append(f"{pc:08x}  {fmt.format(ins)}")
            m = ins.mnemonic
            nxt = ins.next_ip

            if m == Mnemonic.RET:
                return self.reg["eax"]
            if m in (Mnemonic.NOP, Mnemonic.RESERVEDNOP):
                pc = nxt
                continue
            if m == Mnemonic.JMP:
                if ins.op_kind(0) == OpKind.NEAR_BRANCH32:
                    pc = ins.near_branch32
                    continue
                if ins.op_kind(0) == OpKind.MEMORY:
                    target = self.read(self.ea(ins), 4)
                    self.arm = target
                    self.through_table = True
                    pc = target
                    continue
                raise Refused(f"0x{pc:08x}: indirect jmp through a register")
            if m in CONDITION:
                if ins.op_kind(0) != OpKind.NEAR_BRANCH32:
                    raise Refused(f"0x{pc:08x}: indirect conditional branch")
                if self.taken(CONDITION[m]):
                    if self.arm is None:
                        self.arm = ins.near_branch32
                        self.through_table = False
                    pc = ins.near_branch32
                else:
                    pc = nxt
                continue
            if m == Mnemonic.MOV:
                if ins.op_kind(0) != OpKind.REGISTER:
                    raise Refused(f"0x{pc:08x}: mov to a non-register destination")
                self.put(ins.op_register(0), self.src(ins, 1))
                pc = nxt
                continue
            if m in (Mnemonic.MOVZX, Mnemonic.MOVSX):
                if ins.op_kind(1) != OpKind.MEMORY:
                    raise Refused(f"0x{pc:08x}: {m} from a register is not supported")
                size = MEMSIZE_BYTES.get(ins.memory_size)
                if not size:
                    raise Refused(f"0x{pc:08x}: memory size {ins.memory_size} "
                                  f"is not supported")
                value = self.read(self.ea(ins), size)
                if m == Mnemonic.MOVSX and value >> (8 * size - 1):
                    value -= 1 << (8 * size)
                self.put(ins.op_register(0), value)
                pc = nxt
                continue
            if m == Mnemonic.LEA:
                self.put(ins.op_register(0), self.ea(ins))
                pc = nxt
                continue
            if m in (Mnemonic.INC, Mnemonic.DEC):
                if ins.op_kind(0) != OpKind.REGISTER:
                    raise Refused(f"0x{pc:08x}: inc/dec of memory")
                a = self.get(ins.op_register(0))
                r = (a + 1 if m == Mnemonic.INC else a - 1) & M32
                cf = self.cf                      # inc/dec leave CF alone
                self.flags_sub(a, M32 if m == Mnemonic.INC else 1)
                self.cf = cf
                self.zf, self.sf = r == 0, bool(r >> 31)
                self.put(ins.op_register(0), r)
                pc = nxt
                continue
            if m in (Mnemonic.ADD, Mnemonic.SUB, Mnemonic.XOR, Mnemonic.OR,
                     Mnemonic.AND, Mnemonic.CMP, Mnemonic.TEST):
                if ins.op_kind(0) != OpKind.REGISTER:
                    raise Refused(f"0x{pc:08x}: {m} with a memory destination")
                a = self.get(ins.op_register(0))
                b = self.src(ins, 1)
                if m == Mnemonic.CMP:
                    self.flags_sub(a, b)
                elif m == Mnemonic.TEST:
                    self.flags_logic(a & b)
                elif m == Mnemonic.ADD:
                    r = (a + b) & M32
                    self.cf = a + b > M32
                    self.zf, self.sf = r == 0, bool(r >> 31)
                    self.of = bool((~(a ^ b) & (a ^ r)) >> 31)
                    self.put(ins.op_register(0), r)
                elif m == Mnemonic.SUB:
                    self.flags_sub(a, b)
                    self.put(ins.op_register(0), (a - b) & M32)
                else:
                    r = {Mnemonic.XOR: a ^ b, Mnemonic.OR: a | b,
                         Mnemonic.AND: a & b}[m] & M32
                    self.flags_logic(r)
                    self.put(ins.op_register(0), r)
                pc = nxt
                continue
            if m == Mnemonic.IMUL and ins.op_count == 3:
                self.put(ins.op_register(0),
                         (self.src(ins, 1) * self.src(ins, 2)) & M32)
                pc = nxt
                continue
            raise Refused(f"0x{pc:08x}: {fmt.format(ins)} is not implemented")
        raise Refused(f"no `ret` within {limit} instructions from 0x{entry:08x}")


def run_case(img: bytes, spec: dict, value: int) -> dict:
    pokes: dict[int, int] = {}
    mach = Machine(img, pokes)
    if spec["setup"] == "eax":
        setup = f"eax:0x{value:08x}"
        mach.reg["eax"] = value & M32
    elif spec["setup"].startswith("mem:"):
        addr = int(spec["setup"].split(":", 1)[1], 16)
        pokes[addr] = value & M32
        setup = f"mem:0x{addr:08x}:0x{value:08x}"
    else:
        raise SystemExit(f"unknown setup {spec['setup']}")
    row = {"va": f"0x{spec['va']:08x}", "name": spec["name"], "setup": setup,
           "input": f"0x{value:08x}"}
    try:
        row["expect_eax"] = f"0x{mach.run(spec['va']):08x}"
        row["arm"] = f"0x{mach.arm:08x}" if mach.arm is not None else "-"
        row["through_table"] = 1 if mach.through_table else 0
        row["instructions"] = len(mach.trail)
        row["refusal"] = None
    except Refused as exc:
        row.update(expect_eax=None, arm=None, through_table=None,
                   instructions=len(mach.trail), refusal=str(exc))
    row["trail"] = mach.trail
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", default="next")
    ap.add_argument("--negative-control", action="store_true",
                    help="corrupt one expected value so the probe consuming this "
                         "file can be seen to disagree")
    args = ap.parse_args()

    img = C.memory_image()
    rows: list[dict] = []
    for spec in CASES:
        for value in spec["inputs"]:
            rows.append(run_case(img, spec, value))

    refused = [r for r in rows if r["refusal"]]
    usable = [r for r in rows if not r["refusal"]]
    if args.negative_control and usable:
        victim = usable[0]
        victim["expect_eax"] = f"0x{(int(victim['expect_eax'], 16) ^ 1) & M32:08x}"
        victim["negative_control"] = True

    # Which of the arms these cases select have no symbol in the whole-image
    # build, read from the same armlist the lifter consumes -- so the claim
    # "this case exercises one of the 343" is not this file's own opinion.
    arms_in_work: set[int] = set()
    direct_flow_arms: set[int] = set()
    listing = OUT / f"armlist.{args.tag}.json"
    if listing.is_file():
        doc = json.loads(listing.read_text())
        arms_in_work = {int(r["arm"], 16) for r in doc["work"]}
        direct_flow_arms = {int(a, 16)
                            for a in doc.get("arms_reached_by_direct_flow_list", [])}

    tag_suffix = ".negcontrol" if args.negative_control else ""
    txt = OUT / f"switch_oracle.{args.tag}{tag_suffix}.txt"
    txt.write_text(
        "# <va> <setup> <expect_eax> <arm> <through_table> <direct_flow_arm>; "
        "tools/s3/arms/switch_oracle.py\n"
        + "".join(f"{r['va']} {r['setup']} {r['expect_eax']} {r['arm']} "
                  f"{r['through_table']} "
                  f"{1 if r['arm'] != '-' and int(r['arm'], 16) in direct_flow_arms else 0}"
                  f"\n" for r in usable))

    doc = {
        "tag": args.tag,
        "negative_control": args.negative_control,
        "functions": [dict({k: v for k, v in spec.items() if k != "inputs"},
                           va=f"0x{spec['va']:08x}") for spec in CASES],
        "cases": len(rows),
        "usable": len(usable),
        "refused": len(refused),
        "refusals": [{"va": r["va"], "input": r["input"], "refusal": r["refusal"]}
                     for r in refused],
        "through_the_jump_table": sum(1 for r in usable if r["through_table"]),
        "reached_the_arm_by_a_direct_branch":
            sum(1 for r in usable if r["arm"] != "-" and not r["through_table"]),
        "distinct_expected_eax": sorted({r["expect_eax"] for r in usable}),
        "arms_selected": sorted({r["arm"] for r in usable if r["arm"] != "-"}),
        "arms_selected_that_need_a_seeded_trace": sorted(
            {r["arm"] for r in usable
             if r["arm"] != "-" and int(r["arm"], 16) in arms_in_work}),
        "cases_dispatching_through_the_table_into_a_seeded_arm": sum(
            1 for r in usable
            if r["through_table"] and int(r["arm"], 16) in arms_in_work),
        # The population this slice exists for: arms remill's direct flow
        # already decodes inside the owner's module, which slice 4 therefore
        # gave no trace and no table row.
        "arms_selected_that_are_direct_flow_arms": sorted(
            {r["arm"] for r in usable
             if r["arm"] != "-" and int(r["arm"], 16) in direct_flow_arms}),
        "cases_dispatching_through_the_table_into_a_direct_flow_arm": sum(
            1 for r in usable
            if r["through_table"] and int(r["arm"], 16) in direct_flow_arms),
        "cases_reaching_a_direct_flow_arm_by_a_direct_branch": sum(
            1 for r in usable
            if not r["through_table"] and r["arm"] != "-"
            and int(r["arm"], 16) in direct_flow_arms),
        "oracle": "a 32-bit x86 interpreter over private/exe/sonic2app-unpacked.exe, "
                  "written here, sharing nothing with remill or the lifted image; it "
                  "refuses on any instruction form it does not implement",
        "out_txt": str(txt),
        "rows": rows,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"switch_oracle.{args.tag}{tag_suffix}.json"
    out.write_text(json.dumps(doc, indent=1) + "\n")
    print(json.dumps({k: v for k, v in doc.items() if k != "rows"}, indent=1))
    print(f"-> {out}\n-> {txt}")
    if refused:
        sys.exit(f"REFUSED: {len(refused)} cases hit an instruction form the "
                 f"interpreter does not implement; they have no expected value")


if __name__ == "__main__":
    main()
