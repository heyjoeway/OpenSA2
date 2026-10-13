#!/usr/bin/env python3
"""Can the guest call WaitForSingleObject with a zero timeout?

Subtitle: every site tools/s4/xrefs.py finds for WaitForSingleObject's IAT slot,
each call it makes, and the timeout each call pushes, classified from the
instructions; and every name the guest resolved through GetProcAddress in the
runs whose records are given. One JSON document, addresses and values only.

Why it matters
--------------
`WaitForSingleObject(h, 0)` answers WAIT_TIMEOUT at once in this runtime with no
hand-off (runtime/sa2_imports.c), so a guest loop polling a handle that way
would starve a READY thread exactly as the NEW FILE wait did, and the guest-poll
rule (runtime/sa2_threads.h sa2_sched_poll) would not see it: the loop makes an
import call every pass. The 2026-10-02 review (N2) found no such call; this is
that census as a tool, so the risk register cites its output instead of a typed
list.

How a call is found and read
----------------------------
A `call [slot]` site is the call. A `mov reg,[slot]` site loads the function,
and its calls are every later `call reg` in the same decode (the site's map
entry to the next entry, as xrefs.py decodes) before reg is written again. For
each call, the two pushes before it are its arguments, nearest first: the handle,
then the timeout. A call whose two pushes are not found in straight-line code
(another call, a return, a jump or a stack adjustment comes first) is
unclassified. The timeout push is classified as:

  * `immediate`: `push imm`, or `push reg` whose last write is `mov reg,imm`;
  * `nonzero`: `push reg` where, after reg's last write, `test reg,reg` (or
    `cmp reg,0`) is followed by a je/jz whose target is not in the code from
    the branch to the push, so the straight line reaches the push only when reg
    is not 0 (a je that lands on or before the push guards nothing; the
    2026-10-02 instrument review, N8). The reading is straight-line: a branch
    from elsewhere into that code is not seen;
  * `unclassified`: anything else.

A call passes 0 if an immediate is 0. The census holds when no call passes 0, no
call is unclassified, the image holds the import's name once (its import entry:
a second copy would be a name a GetProcAddress call could take; the review's
N1), and no run record names WaitForSingleObject among its GetProcAddress
resolutions (a dynamic call no static site would show).

Usage:
  source tools/env.sh
  .venv/bin/python tools/s5/wait_census.py [--record RUN_JSON ...] [--out FILE]

Exits 1 unless the census holds. Nothing from the image is written but
addresses and the timeout values (rule 9).
"""
from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
import image as IM  # noqa: E402
import xrefs as X  # noqa: E402
from iced_x86 import (Decoder, FlowControl, InstructionInfoFactory, Mnemonic,  # noqa: E402
                      OpAccess, OpKind, Register, RegisterExt)

IMPORT = "WaitForSingleObject"
REG_NAMES = {v: k.lower() for k, v in vars(Register).items()
             if not k.startswith("_") and isinstance(v, int)}
MNEMONICS = {v: k.lower() for k, v in vars(Mnemonic).items()
             if not k.startswith("_") and isinstance(v, int)}
WRITES = (OpAccess.WRITE, OpAccess.READ_WRITE, OpAccess.COND_WRITE, OpAccess.READ_COND_WRITE)
ZERO_BRANCH = (Mnemonic.JE,)          # je/jz leave the line when the value was 0
STACK_MOVES = (Mnemonic.POP, Mnemonic.PUSHAD, Mnemonic.POPAD, Mnemonic.PUSHFD, Mnemonic.POPFD)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 22), b""):
            h.update(chunk)
    return h.hexdigest()


def git_head() -> str:
    import subprocess
    p = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def rel(p: Path) -> str:
    p = Path(p)
    if not p.is_absolute():
        return str(p)
    return str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p)


def full(reg: int) -> int:
    return RegisterExt.full_register32(reg) if reg != Register.NONE else reg


def writes(info_factory, ins, reg: int) -> bool:
    info = info_factory.info(ins)
    return any(full(u.register) == full(reg) and u.access in WRITES
               for u in info.used_registers())


def decode(img: "IM.Image", lo: int, hi: int) -> list:
    sec = img.section_of(lo)
    data = img._section_bytes[sec.name]
    hi = min(hi, sec.end)
    out = []
    for ins in Decoder(32, data[lo - sec.va:hi - sec.va], ip=lo):
        if ins.is_invalid:
            break
        out.append(ins)
    return out


def arguments(code: list, k: int) -> tuple[list, str | None]:
    """The two push instructions before code[k] (a call), nearest first, or why not."""
    pushes = []
    for j in range(k - 1, -1, -1):
        ins = code[j]
        fc = ins.flow_control
        if ins.mnemonic == Mnemonic.PUSH:
            pushes.append(j)
            if len(pushes) == 2:
                return pushes, None
            continue
        if fc in (FlowControl.CALL, FlowControl.INDIRECT_CALL, FlowControl.RETURN,
                  FlowControl.UNCONDITIONAL_BRANCH, FlowControl.INDIRECT_BRANCH):
            return pushes, (f"a {MNEMONICS.get(ins.mnemonic, ins.mnemonic)} at 0x{ins.ip:08x} "
                            "before the second push")
        if ins.mnemonic in STACK_MOVES or (
                ins.op0_kind == OpKind.REGISTER and ins.op0_register == Register.ESP):
            return pushes, f"the stack moves at 0x{ins.ip:08x} before the second push"
    return pushes, "the decode starts before the second push"


def classify(code: list, j: int, factory) -> dict:
    """The timeout push code[j]: immediate, nonzero or unclassified."""
    ins = code[j]
    at = f"0x{ins.ip:08x}"
    if ins.op0_kind in (OpKind.IMMEDIATE8, OpKind.IMMEDIATE16, OpKind.IMMEDIATE32,
                        OpKind.IMMEDIATE8TO16, OpKind.IMMEDIATE8TO32):
        return {"push": at, "kind": "immediate", "value": ins.immediate(0) & 0xFFFFFFFF}
    if ins.op0_kind != OpKind.REGISTER:
        return {"push": at, "kind": "unclassified", "why": "the timeout is pushed from memory"}
    reg = full(ins.op0_register)
    guard = None
    for i in range(j - 1, -1, -1):
        prev = code[i]
        if writes(factory, prev, reg):
            if prev.mnemonic == Mnemonic.MOV and prev.op1_kind in (OpKind.IMMEDIATE32,
                                                                    OpKind.IMMEDIATE8TO32):
                return {"push": at, "kind": "immediate", "value": prev.immediate(1) & 0xFFFFFFFF,
                        "set_at": f"0x{prev.ip:08x}"}
            if guard is not None:
                return {"push": at, "kind": "nonzero", "guard": guard,
                        "set_at": f"0x{prev.ip:08x}"}
            return {"push": at, "kind": "unclassified",
                    "why": f"the register is set at 0x{prev.ip:08x} with no zero test after it"}
        if prev.flow_control in (FlowControl.CALL, FlowControl.INDIRECT_CALL, FlowControl.RETURN,
                                 FlowControl.UNCONDITIONAL_BRANCH, FlowControl.INDIRECT_BRANCH):
            break
        if prev.mnemonic in ZERO_BRANCH and i > 0:
            test = code[i - 1]
            same = (test.op0_kind == OpKind.REGISTER and full(test.op0_register) == reg)
            # The zero path must leave the code from the branch to the push: a je
            # that lands there (on the push, or before it) still reaches the push
            # with reg 0.
            leaves = not (prev.next_ip <= prev.near_branch_target <= ins.ip)
            if same and leaves and (
                    (test.mnemonic == Mnemonic.TEST and test.op1_kind == OpKind.REGISTER
                     and full(test.op1_register) == reg)
                    or (test.mnemonic == Mnemonic.CMP and test.op1_kind in (
                        OpKind.IMMEDIATE8TO32, OpKind.IMMEDIATE32)
                        and test.immediate(1) == 0)):
                guard = {"test": f"0x{test.ip:08x}", "branch": f"0x{prev.ip:08x}",
                         "taken_to": f"0x{prev.near_branch_target:08x}"}
    return {"push": at, "kind": "unclassified",
            "why": "no write of the register found in straight-line code before the push"}


def census(img: "IM.Image", fmap: dict) -> dict:
    _, iref, _ = X.build(img, fmap)
    starts = sorted(int(f["address"], 16) for f in fmap["functions"])
    factory = InstructionInfoFactory()
    slots = [s for s, names in img.imports.items() if names[-1] == IMPORT]
    rows = []
    for slot in slots:
        for site, kind, origin in sorted(iref.get(slot, [])):
            nxt = starts[bisect.bisect_right(starts, site)] if bisect.bisect_right(
                starts, site) < len(starts) else img.section_of(site).end
            code = decode(img, origin, nxt)
            index = {ins.ip: n for n, ins in enumerate(code)}
            row = {"site": f"0x{site:08x}", "kind": kind, "decoded_from": f"0x{origin:08x}",
                   "calls": []}
            if site not in index:
                row["why"] = "the site is not an instruction of its decode"
                rows.append(row)
                continue
            k = index[site]
            calls = []
            if kind == "call":
                calls = [k]
            elif kind == "mem" and code[k].mnemonic == Mnemonic.MOV \
                    and code[k].op0_kind == OpKind.REGISTER:
                reg = full(code[k].op0_register)
                for n in range(k + 1, len(code)):
                    ins = code[n]
                    if ins.flow_control == FlowControl.INDIRECT_CALL \
                            and ins.op0_kind == OpKind.REGISTER and full(ins.op0_register) == reg:
                        calls.append(n)
                        continue
                    if writes(factory, ins, reg):
                        break
                row["register"] = REG_NAMES.get(reg, str(reg))
            else:
                row["why"] = "neither a call nor a register load of the slot"
            for n in calls:
                pushes, why = arguments(code, n)
                c = {"call": f"0x{code[n].ip:08x}"}
                if why:
                    c.update(kind="unclassified", why=why)
                else:
                    c.update(timeout=classify(code, pushes[1], factory),
                             handle_push=f"0x{code[pushes[0]].ip:08x}")
                row["calls"].append(c)
            rows.append(row)
    return {"import": IMPORT, "slots": [f"0x{s:08x}" for s in slots], "sites": rows,
            "image_name_count": name_count(img)}


def name_count(img: "IM.Image") -> int:
    """How many times the executable's file holds the import's name, NUL-ended (so
    WaitForSingleObjectEx is not counted): once is its import entry. The whole file,
    since the image reader keeps only the executable sections' bytes and the names
    live in the import directory."""
    return Path(img.path).read_bytes().count(IMPORT.encode() + b"\0")


def verdict(doc: dict, records: list[Path]) -> dict:
    calls = [c for r in doc["sites"] for c in r["calls"]]
    unread = [r["site"] for r in doc["sites"] if r.get("why")]
    zero = [c["call"] for c in calls if (c.get("timeout") or {}).get("kind") == "immediate"
            and c["timeout"]["value"] == 0]
    unclassified = [c["call"] for c in calls
                    if c.get("kind") == "unclassified"
                    or (c.get("timeout") or {}).get("kind") == "unclassified"]
    values = sorted({c["timeout"]["value"] for c in calls
                     if (c.get("timeout") or {}).get("kind") == "immediate"})
    nonzero = [c["call"] for c in calls if (c.get("timeout") or {}).get("kind") == "nonzero"]
    dynamic = {}
    for path in records:
        rec = json.loads(path.read_text())
        names = sorted(r.get("name") for r in (rec.get("getprocaddress") or {}).get("rows") or [])
        dynamic[rel(path)] = {"resolved": names, "names_the_import": IMPORT in names}
    named = doc.get("image_name_count")
    holds = (bool(calls) and not zero and not unclassified and not unread and named == 1
             and not any(d["names_the_import"] for d in dynamic.values()))
    return {"sites": len(doc["sites"]), "calls": len(calls), "immediate_values": values,
            "nonzero_register_calls": nonzero, "zero_calls": zero,
            "unclassified_calls": unclassified, "unread_sites": unread,
            "image_name_count": named, "getprocaddress": dynamic, "holds": holds}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--exe", type=Path, default=IM.DEFAULT_EXE)
    ap.add_argument("--map", type=Path, default=X.DEFAULT_MAP)
    ap.add_argument("--record", type=Path, action="append", default=[],
                    help="a run record whose getprocaddress rows to read; repeatable")
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    img = IM.Image(a.exe)
    found = census(img, json.loads(a.map.read_text()))
    doc = {"schema": "sa2-recomp-receipt/v1", "id": "s5-wait-census-v1",
           "generated_by": "tools/s5/wait_census.py", "commit": git_head(),
           "question": "Can the guest call WaitForSingleObject with a zero timeout, the poll "
                       "the runtime answers with no hand-off?",
           "inputs": {"pipeline": {f: sha256(ROOT / f) for f in (
                          "tools/s5/wait_census.py", "tools/s4/xrefs.py", "tools/s4/image.py")},
                      "exe_sha256": img.sha256, "map_sha256": sha256(a.map),
                      "records": {rel(p): sha256(p) for p in a.record}},
           "census": found, "verdict": verdict(found, a.record)}
    text = json.dumps(doc, indent=1) + "\n"
    if a.out:
        a.out.write_text(text)
    print(json.dumps(doc["verdict"], indent=1))
    return 0 if doc["verdict"]["holds"] else 1


if __name__ == "__main__":
    sys.exit(main())
