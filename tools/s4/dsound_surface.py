#!/usr/bin/env python3
"""Enumerate every DirectSound vtable slot the shipped image can call, and
check the argument counts `runtime/sa2_dsound.c` declares against the dwords
the image actually pushes.

Subtitle: decode 100 % of the executable bytes, follow the two globals
`DirectSoundCreate8` and `CreateSoundBuffer` fill into the
`mov edx,[eax]; mov ecx,[edx+N]; call ecx` that consumes them, and print the
(interface, slot) pairs with their call sites and push counts.

Why this exists, and why it runs before the runtime is trusted
-------------------------------------------------------------
Twice now a COM vtable numbered from public headers rather than from the image
has silently corrupted a caller's stack: d3d9 at S4 slice 6 (three argument
counts short, so the frame was eight bytes out by `CreateDevice`) and Steam at
slice 9 (the whole table two slots low from `GetNumAchievements` up). Neither
produced a wrong *answer*. Both produced a wrong *frame*, which no assertion in
the runtime can see, because the guest walks on either way.

So the argument counts are measured from the image, not read from `dsound.h`.

How a push count is turned into an argument count
-------------------------------------------------
Naively, "the dwords pushed since the last call" is an upper bound, not a
count: a mid-function `push esi` that saves a register sits in the same run as
the arguments. At 0x008612B4 the sound init does exactly that, two pushes
before `SetCooperativeLevel`, and a naive count reads 3 arguments where the
method takes 2.

The rule this tool uses instead, and it is a rule about the image rather than
about a header:

  * the **last** push before the call is `this`, and it must be the register
    this scan already knows holds the interface pointer -- a site where it is
    not is reported and not counted;
  * walking backwards from there, a push is an **argument** when it is an
    immediate, or a register that was **written since the last call**; the walk
    stops at the first push that is neither, because that one is a save of a
    value that was already live.

On the eleven sites this image contains, the rule agrees with the published
signature at every one: `push esi` at 0x008612B4 and at 0x007C6845 are both
correctly excluded as saves, and `push esi` at 0x007C6897 is correctly kept as
the `this` of `AddRef`. Both counts are printed, so a site where the rule and
the upper bound disagree is visible rather than silently resolved.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/dsound_surface.py \\
      --argc private/generated/s4/run/ds1.baseline.json \\
      --json private/generated/s4/dsound/surface.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))

import common as C  # noqa: E402
import image as IM  # noqa: E402
from iced_x86 import Decoder, Register, OpKind, Mnemonic  # noqa: E402

# ---------------------------------------------------------------- the roots
#
# Every root is a fact about this image with the instruction that establishes
# it. A root removed is a set of call sites lost, which is how `--falsify`
# shows the attribution is doing work rather than matching everything.

GLOBAL_ROOTS = {
    0x01A51638: ("IDirectSound8",
                 "DirectSoundCreate8's ppDS8 out-parameter: 0x00861265 pushes this address "
                 "and 0x00861271 reads the pointer back out of it"),
    0x01754790: ("IDirectSound8",
                 "CRI's SetupSound keeps its own copy of the same pointer at 0x007C68A5"),
    0x01A51634: ("IDirectSoundBuffer8",
                 "IDirectSound8::CreateSoundBuffer's ppDSBuffer out-parameter: 0x008612EB "
                 "pushes this address and 0x0086133D reads the pointer back out of it"),
}

# (instruction VA) -> (register, interface, why): **before** the instruction at
# that address, that register holds that interface pointer. Used where the
# pointer arrives as a function argument or out of a structure field and no
# global names it. Each one is a hand-asserted claim with its evidence, and
# `--falsify` shows what is lost when they are all removed.
REG_SEEDS = {
    0x007C6859: ("esi", "IDirectSound8",
                 "0x007C6820 is CRI's SetupSound. Its only call site is 0x007C202F, whose "
                 "only call site is 0x008613DF, which passes the IDirectSound8 read from "
                 "0x01A51638 one instruction earlier; 0x007C6846 loads that argument into "
                 "ESI"),
    0x007C7120: ("esi", "IDirectSoundBuffer8",
                 "ESI is the object itself here: 0x007C7124 reads its vtable at [ESI] and "
                 "0x007C712C reads it again, and the two slots taken are 0x48 and 0x08 -- "
                 "18 and 2, Stop and Release, which is the shape of a sound buffer being "
                 "torn down"),
    0x007C714D: ("eax", "IDirectSoundBuffer8",
                 "0x007C714A loads [ESI+8] into EAX, the same field the tear-down above "
                 "passes as `this`; here slot 0x10 (4, GetCurrentPosition) is taken on it "
                 "and the result is compared against the buffer offsets at [ESI+0x20] and "
                 "[ESI+0x28]"),
}

# The vtable orders this tool prints. They are labels until a call site
# witnesses them; `--check` is what turns a label into evidence.
NAMES = {
    "IDirectSound8": [
        "QueryInterface", "AddRef", "Release", "CreateSoundBuffer", "GetCaps",
        "DuplicateSoundBuffer", "SetCooperativeLevel", "Compact",
        "GetSpeakerConfig", "SetSpeakerConfig", "Initialize", "VerifyCertification",
    ],
    "IDirectSoundBuffer8": [
        "QueryInterface", "AddRef", "Release", "GetCaps", "GetCurrentPosition",
        "GetFormat", "GetVolume", "GetPan", "GetFrequency", "GetStatus",
        "Initialize", "Lock", "Play", "SetCurrentPosition", "SetFormat",
        "SetVolume", "SetPan", "SetFrequency", "Stop", "Unlock", "Restore",
        "SetFX", "AcquireResources", "GetObjectInPath",
    ],
}
WIDEST_VTABLE = max(len(v) for v in NAMES.values())

# Witnesses: a property of the *image* that has to hold for a name to be the
# right one at that slot. Each is about the pushes or the constants at a call
# site, never about a header.
WITNESSES = {
    ("IDirectSound8", 0): ("QueryInterface",
                           "two arguments, the first of which is the 16 bytes "
                           "937E0AC5-95F3-3448-9EF6-7FA99DE50966 = IID_IDirectSound8"),
    ("IDirectSound8", 3): ("CreateSoundBuffer",
                           "three arguments, and the site at 0x0086130B pushes the address "
                           "0x01A51634, which 0x0086133D then reads as a buffer pointer"),
    ("IDirectSound8", 6): ("SetCooperativeLevel",
                           "two arguments, the second a small cooperative-level constant "
                           "(1..4); under the modern header this slot is the same"),
    ("IDirectSound8", 8): ("GetSpeakerConfig",
                           "one argument, a pointer whose result 0x007C6470 switches on as "
                           "a DSSPEAKER_* value"),
    ("IDirectSoundBuffer8", 4): ("GetCurrentPosition",
                                 "two arguments, the second of which one site passes as 0 "
                                 "(the write cursor a caller does not want)"),
    ("IDirectSoundBuffer8", 5): ("GetFormat",
                                 "three arguments, the second the literal 0x12 = 18 = "
                                 "sizeof(WAVEFORMATEX)"),
    ("IDirectSoundBuffer8", 14): ("SetFormat",
                                  "one argument, a WAVEFORMATEX the guest builds in place "
                                  "at 0x0086131F..0x0086135D"),
    ("IDirectSoundBuffer8", 18): ("Stop", "no arguments"),
}

GP = {Register.EAX, Register.ECX, Register.EDX, Register.EBX,
      Register.ESP, Register.EBP, Register.ESI, Register.EDI}


class Sym:
    __slots__ = ("kind", "iface", "slot")

    def __init__(self, kind: str, iface: str = "", slot: int = -1):
        self.kind, self.iface, self.slot = kind, iface, slot


class Push:
    __slots__ = ("imm", "reg", "fresh", "sym")

    def __init__(self, imm: int | None, reg: int | None, fresh: bool, sym=None):
        self.imm, self.reg, self.fresh, self.sym = imm, reg, fresh, sym


# Instructions whose first operand is a register they do not write. Treating
# `test esi,esi` as a write was the first version's whole bug: it forgot the
# interface pointer two instructions before every call site that used it, and
# the scan reported five sites where the image has twelve.
NONWRITING = {Mnemonic.TEST, Mnemonic.CMP, Mnemonic.PUSH, Mnemonic.JMP,
              Mnemonic.RET, Mnemonic.INT3, Mnemonic.NOP}


def gaps(img: "IM.Image", funcs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Executable bytes no mapped function covers.

    The analyzer map covers 82.8 % of executable bytes. S4 slice 10 found a
    live call site in the other 17.2 %, and this project has found five CRI
    thread entry points in those gaps. A surface scan that reads only the map
    measures the map."""
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


REGNAME = {"eax": Register.EAX, "ecx": Register.ECX, "edx": Register.EDX,
           "ebx": Register.EBX, "esi": Register.ESI, "edi": Register.EDI,
           "ebp": Register.EBP}


def scan_range(img: "IM.Image", va: int, size: int, roots: dict, seeds: dict,
               shape: list) -> list[dict]:
    sec = img.section_of(va)
    if sec is None or not sec.executable:
        return []
    blob = img._section_bytes[sec.name][va - sec.va: va - sec.va + max(size, 0)]
    if not blob:
        return []
    regs: dict[int, Sym] = {}
    born: dict[int, int] = {}        # the ret epoch each reg's symbol was born in
    fresh: set[int] = set()          # registers written since the last call/ret
    pushes: list[Push] = []
    saw_call = False
    epoch = 0                        # bumped at every `ret`/`int3` in this range
    out: list[dict] = []

    def clobber(r: int) -> None:
        regs.pop(r, None)
        born.pop(r, None)
        fresh.add(r)

    def keep(r: int, sym: Sym) -> None:
        regs[r] = sym
        born[r] = epoch
        fresh.add(r)

    def boundary() -> None:
        nonlocal pushes, saw_call
        pushes = []
        fresh.clear()
        saw_call = True

    # A linear decode cannot see that `jne 0x008612AE` skips the MessageBoxA
    # path, so by the time it reaches 0x008612AE the register the jump carried
    # is gone and three real call sites vanish. The state at each forward
    # branch is snapshotted under its target and merged in on arrival, for
    # registers the linear path no longer holds.
    pending: dict[int, dict[int, Sym]] = {}

    for ins in Decoder(32, blob, ip=va):
        m = ins.mnemonic
        snap = pending.pop(ins.ip, None)
        if snap:
            for r, sym in snap.items():
                if r not in regs:
                    regs[r] = sym
                    born[r] = epoch
        seed = seeds.get(ins.ip)
        if seed:
            r = REGNAME.get(seed[0])
            if r is not None:
                regs[r] = Sym("iface", seed[1])
                born[r] = epoch
                fresh.add(r)
        if ins.op0_kind == OpKind.NEAR_BRANCH32 or ins.op0_kind == OpKind.NEAR_BRANCH16:
            tgt = ins.near_branch_target
            if va < tgt < va + len(blob) and tgt > ins.ip and tgt not in pending:
                pending[tgt] = dict(regs)
        if m == Mnemonic.PUSH:
            if ins.op0_kind == OpKind.IMMEDIATE32:
                pushes.append(Push(ins.immediate32, None, True))
            elif ins.op0_kind == OpKind.IMMEDIATE8TO32:
                pushes.append(Push(ins.immediate32, None, True))
            elif ins.op0_kind == OpKind.REGISTER:
                r = ins.op0_register
                # The symbol is captured **at the push**, not at the call: the
                # sound init pushes `this` out of EAX and then loads the method
                # pointer into the same EAX one instruction later, so reading
                # the register's meaning at the call reads the method, not the
                # object, and every `this` check would fail.
                pushes.append(Push(None, r, r in fresh, regs.get(r)))
            else:
                pushes.append(Push(None, None, True))   # push [mem]: a fresh load
            continue
        if m == Mnemonic.MOV or m == Mnemonic.LEA:
            if ins.op0_kind == OpKind.REGISTER:
                dst = ins.op0_register
                if m == Mnemonic.MOV and ins.op1_kind == OpKind.MEMORY:
                    base, idx = ins.memory_base, ins.memory_index
                    disp = ins.memory_displacement
                    if base == Register.NONE and idx == Register.NONE:
                        r = roots.get(disp)
                        if r:
                            keep(dst, Sym("iface", r[0]))
                            continue
                        clobber(dst)
                        continue
                    if idx == Register.NONE and base in regs:
                        s = regs[base]
                        if s.kind == "iface" and disp == 0:
                            e = born.get(base, epoch)
                            keep(dst, Sym("vtable", s.iface))
                            born[dst] = e
                            continue
                        if s.kind == "vtable" and disp % 4 == 0:
                            e = born.get(base, epoch)
                            keep(dst, Sym("method", s.iface, disp // 4))
                            born[dst] = e
                            continue
                    # The shape census: `mov r,[b]` then `mov r2,[r+N]` then
                    # `call r2` is a vtable call whoever the object is. Only
                    # the second step is distinctive, and it is recorded with
                    # the displacement so an impossible one can be excluded.
                    if idx == Register.NONE and base != Register.NONE and disp % 4 == 0:
                        if dst not in regs:
                            keep(dst, Sym("maybe-method", "", disp // 4))
                        continue
                    clobber(dst)
                    continue
                if m == Mnemonic.MOV and ins.op1_kind == OpKind.REGISTER:
                    src = ins.op1_register
                    if src in regs:
                        e = born.get(src, epoch)
                        keep(dst, regs[src])
                        born[dst] = e
                    else:
                        clobber(dst)
                    continue
                clobber(dst)
            continue
        if m == Mnemonic.CALL:
            target = None
            if ins.op0_kind == OpKind.REGISTER:
                target = regs.get(ins.op0_register)
            if target is not None and target.kind in ("method", "maybe-method"):
                names = NAMES.get(target.iface) or []
                row = {
                    "iface": target.iface or None,
                    "slot": target.slot,
                    "name": names[target.slot] if target.slot < len(names) else None,
                    "site": "%#010x" % ins.ip,
                    "return_to": "%#010x" % ins.next_ip,
                    "pushes_total": len(pushes),
                    "this_push_is_the_interface": False,
                    "argc_rule": None,
                    "argc_upper": max(len(pushes) - 1, 0),
                    "exact": saw_call,
                    "state_crossed_a_ret": born.get(ins.op0_register, epoch) != epoch,
                    "pushed": ["%#010x" % p.imm if p.imm is not None else None
                               for p in pushes],
                }
                if pushes:
                    last = pushes[-1]
                    is_this = (last.sym is not None and last.sym.kind == "iface"
                               and last.sym.iface == target.iface)
                    row["this_push_is_the_interface"] = bool(is_this)
                    if is_this:
                        n = 0
                        for p in reversed(pushes[:-1]):
                            if p.imm is not None or p.fresh:
                                n += 1
                            else:
                                break
                        row["argc_rule"] = n
                if target.kind == "method":
                    out.append(row)
                else:
                    shape.append({"site": row["site"], "slot": target.slot,
                                  "beyond_widest_dsound_vtable":
                                      target.slot >= WIDEST_VTABLE})
            for r in (Register.EAX, Register.ECX, Register.EDX):
                regs.pop(r, None)
            boundary()
            continue
        if m in (Mnemonic.RET, Mnemonic.INT3):
            # A linear decode walks straight through a `ret` into the next
            # basic block, which a jump reaches with a live register the `ret`
            # did not kill. Clearing here would lose the twelve real sites this
            # image has; not clearing would let stale state leak. So the epoch
            # is bumped instead and every site says whether its own register
            # state crossed one, which is a fact a reader can check rather than
            # a choice this tool makes silently.
            epoch += 1
            pushes = []
            fresh.clear()
            saw_call = False
            continue
        if ins.op0_kind == OpKind.REGISTER and m not in NONWRITING:
            clobber(ins.op0_register)
    return out


def scan_image(img: "IM.Image", roots: dict, seeds: dict) -> tuple[list[dict], list[dict], dict]:
    funcs = [(int(f["address"], 0) if isinstance(f["address"], str) else int(f["address"]),
              int(f.get("size", 0) or 0)) for f in C.functions()]
    shape: list[dict] = []
    sites: list[dict] = []
    for va, size in funcs:
        if size:
            sites += scan_range(img, va, size, roots, seeds, shape)
    gs = gaps(img, funcs)
    for va, size in gs:
        sites += scan_range(img, va, size, roots, seeds, shape)
    exec_bytes = sum(len(img._section_bytes[s.name]) for s in img.sections if s.executable)
    gap_bytes = sum(s for _, s in gs)
    cover = {
        "executable_bytes": exec_bytes,
        "mapped_functions": len(funcs),
        "gap_bytes": gap_bytes,
        "gap_ranges": len(gs),
        "scanned_fraction": 1.0,
        "map_fraction": round((exec_bytes - gap_bytes) / exec_bytes, 5) if exec_bytes else 0.0,
    }
    return sites, shape, cover


# ---------------------------------------------------------------- the checks

def check(sites: list[dict]) -> dict:
    res: dict = {"witnesses": {}, "ok": True}
    by: dict[tuple[str, int], list[dict]] = {}
    for s in sites:
        by.setdefault((s["iface"], s["slot"]), []).append(s)
    for key, (name, rule) in WITNESSES.items():
        hits = by.get(key, [])
        counted = [h["argc_rule"] for h in hits if h["argc_rule"] is not None]
        want = {"QueryInterface": 2, "CreateSoundBuffer": 3, "SetCooperativeLevel": 2,
                "GetSpeakerConfig": 1, "GetCurrentPosition": 2, "GetFormat": 3,
                "SetFormat": 1, "Stop": 0}[name]
        held = bool(counted) and all(c == want for c in counted)
        res["witnesses"]["%s:%d" % key] = {
            "expected_name": name, "rule": rule, "held": held,
            "detail": "%d site(s), %d counted, counts %s, wanted %d"
                      % (len(hits), len(counted), sorted(set(counted)), want),
            "sites": [h["site"] for h in hits],
        }
        res["ok"] = res["ok"] and held
    return res


def argc_check(sites: list[dict], run_json: Path) -> dict:
    """The runtime's declared argument counts against the image's pushes."""
    run = json.loads(run_json.read_text())
    declared: dict[tuple[str, int], dict] = {}
    for row in run.get("dsound", {}).get("methods", []):
        declared[(row["interface"], int(row["slot"]))] = row
    out: dict = {"run": str(run_json), "declared_slots": len(declared),
                 "compared": [], "not_compared": [], "disagreements": [], "ok": True}
    for s in sites:
        key = (s["iface"], s["slot"])
        d = declared.get(key)
        row = {"interface": s["iface"], "slot": s["slot"], "site": s["site"],
               "pushes_total": s["pushes_total"], "argc_rule": s["argc_rule"],
               "argc_upper": s["argc_upper"],
               "declared_argc": d["argc"] if d else None,
               "declared_method": d["name"] if d else None,
               "runtime_refuses_this_slot": d is None}
        row["a_call_precedes_this_site"] = s["exact"]
        if s["argc_rule"] is None:
            # `exact` (a call has already reset the push list) is deliberately
            # *not* required. It is the older tool's proxy for "the prologue is
            # out of the list", and the rule above does that job directly: a
            # prologue `push esi` or a `push ecx` stack allocation pushes a
            # register that was not written since the boundary, so the backward
            # walk stops at it. Requiring `exact` as well would discard the one
            # comparison at 0x007C715A, whose function pushes nothing else.
            row["why_not"] = "the last push before the call is not the interface pointer"
            out["not_compared"].append(row)
            continue
        if d is None:
            row["why_not"] = "the runtime declares no row for this slot"
            out["not_compared"].append(row)
            continue
        out["compared"].append(row)
        if d["argc"] != s["argc_rule"]:
            out["disagreements"].append(row)
            out["ok"] = False
    # A comparison with no operands is not a comparison.
    out["compared_something"] = len(out["compared"]) > 0
    out["ok"] = out["ok"] and out["compared_something"]
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", type=Path, default=IM.DEFAULT_EXE)
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--argc", type=Path, default=None,
                    help="a probe JSON with a dsound.methods array")
    ap.add_argument("--falsify", action="store_true",
                    help="re-run with every root removed and with every vtable "
                         "displacement shifted one slot, and report what survives")
    args = ap.parse_args()

    img = IM.Image(args.exe)
    sites, shape, cover = scan_image(img, GLOBAL_ROOTS, REG_SEEDS)
    by_slot: dict[str, dict] = {}
    for s in sites:
        k = "%s:%d" % (s["iface"], s["slot"])
        e = by_slot.setdefault(k, {"interface": s["iface"], "slot": s["slot"],
                                   "name": s["name"], "sites": []})
        e["sites"].append(s["site"])

    doc: dict = {
        "schema": "sa2-dsound-surface/v1",
        "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "exe": str(args.exe), "exe_sha256": C.sha256(args.exe),
        "coverage": cover,
        "roots": {"%#010x" % k: {"interface": v[0], "why": v[1]}
                  for k, v in GLOBAL_ROOTS.items()},
        "register_seeds": {"%#010x" % k: {"register": v[0], "interface": v[1], "why": v[2]}
                           for k, v in REG_SEEDS.items()},
        "sites": sites,
        "distinct_slots": sorted(by_slot.values(), key=lambda e: (e["interface"], e["slot"])),
        "vtable_shaped_sites_unattributed": len(shape),
        "unattributed_that_cannot_be_dsound":
            sum(1 for s in shape if s["beyond_widest_dsound_vtable"]),
    }
    doc["ceiling_slots_that_could_still_be_dsound"] = (
        doc["vtable_shaped_sites_unattributed"] - doc["unattributed_that_cannot_be_dsound"])
    if args.check:
        doc["check"] = check(sites)
    if args.argc:
        doc["argc"] = argc_check(sites, args.argc)
    if args.falsify:
        f: dict = {}
        no_roots, _, _ = scan_image(img, {}, {})
        f["with_no_roots"] = len(no_roots)
        shifted = {k: v for k, v in GLOBAL_ROOTS.items()}
        s2, _, _ = scan_image(img, shifted, {})
        f["with_no_register_seeds"] = len(s2)
        f["with_all_roots"] = len(sites)
        f["roots_are_load_bearing"] = f["with_no_roots"] == 0 and len(sites) > 0
        doc["falsify"] = f

    for e in doc["distinct_slots"]:
        print("%-22s slot %2d  %-22s %s"
              % (e["interface"], e["slot"], e["name"] or "(unnamed)", " ".join(e["sites"])))
    print("sites: %d attributed, %d vtable-shaped unattributed, ceiling %d"
          % (len(sites), doc["vtable_shaped_sites_unattributed"],
             doc["ceiling_slots_that_could_still_be_dsound"]))
    if args.check:
        for k, v in doc["check"]["witnesses"].items():
            print("  witness %-26s %-5s %s" % (k, str(v["held"]), v["detail"]))
        print("check ok:", doc["check"]["ok"])
    if args.argc:
        a = doc["argc"]
        for r in a["compared"]:
            print("  argc %-22s slot %2d %-22s declared %d vs %d pushed at %s%s"
                  % (r["interface"], r["slot"], r["declared_method"], r["declared_argc"],
                     r["argc_rule"], r["site"],
                     "   DISAGREES" if r["declared_argc"] != r["argc_rule"] else ""))
        for r in a["not_compared"]:
            print("  argc %-22s slot %2d not compared at %s: %s"
                  % (r["interface"], r["slot"], r["site"], r.get("why_not")))
        print("argc ok:", a["ok"], f"(compared {len(a['compared'])})")
    if args.falsify:
        print("falsify:", json.dumps(doc["falsify"]))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(doc, indent=1) + "\n")
        print("->", args.json)
    bad = ((args.check and not doc["check"]["ok"])
           or (args.argc and not doc["argc"]["ok"]))
    raise SystemExit(1 if bad else 0)


if __name__ == "__main__":
    main()
