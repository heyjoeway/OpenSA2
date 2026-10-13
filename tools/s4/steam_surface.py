#!/usr/bin/env python3
"""Enumerate every Steam interface method the shipped image can call.

Subtitle: decode the 12 683 mapped functions, follow each `call [steam_api
accessor]` into the `mov edx,[eax]; mov eax,[edx+N]; call eax` that consumes
it, and print the (interface, slot) pairs with their call sites and the
constant strings pushed at each one.

Why a tool and not a run
------------------------
A run tells you the slot the guest reached *this time*. That is one slot of a
state machine and says nothing about the rest. The runtime's REFUSE policy
turns every other slot into a trap with an address on it, which is the right
default and a slow way to discover a surface: one slot per rebuild.

This scans instead. It is the denominator for "the Steam stats surface": every
slot any mapped function can ask for, whether or not a run has reached it. A
slot this tool does not list cannot be reached by a direct vtable call from a
mapped function, and a slot it lists but no run reaches is a stub nobody needs
yet -- both are facts a run alone cannot produce.

What it does *not* claim
------------------------
It names slots by a vtable order. That naming is checked against the image,
not assumed from a header: `--check` pins six slots by the argument shape and
the use of the result at their call sites, and it is how the first version of
this table was found to be wrong -- this build's interface has no
`GetNumAchievements` and no `GetAchievementName`, so everything from slot 14 up
sits two lower than the current Steamworks headers say. A name this tool prints
for a slot with no witness is a label, and the JSON says which slots carry one.

`--argc` is the other half and the one that pays: it compares the argument
counts `runtime/sa2_iface.c` declares against the dwords the image pushes at
each site. A __thiscall COM method pops its own arguments, so a count one dword
short leaves the caller's frame drifting and every later [esp+n] in that
function reads the wrong local -- silently. That is how the wrong vtable order
was caught, on a slot the guest had already called in a run that looked fine.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/steam_surface.py --check \
      --argc private/generated/s4/run/steam.ctl.json \
      --json private/generated/s4/steam/surface.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "s3" / "whole_image"))

import common as C  # noqa: E402
import image as IM  # noqa: E402
from iced_x86 import Decoder, Register, OpKind, Mnemonic  # noqa: E402

MAP = C.GEN / "function-map.json"

# The accessor -> interface mapping is the one `runtime/sa2_iface.c` already
# implements; repeated here because this tool reads the image, not the runtime,
# and a tool that imported the runtime's opinion could not contradict it.
ACCESSOR = {
    "SteamUserStats": "ISteamUserStats",
    "SteamUser": "ISteamUser",
    "SteamUtils": "ISteamUtils",
    "SteamApps": "ISteamApps",
    "SteamFriends": "ISteamFriends",
}

# This build's ISteamUserStats vtable order.
#
# It is NOT the order the current Steamworks headers document: this interface
# version has no `GetNumAchievements` and no `GetAchievementName`, so every
# slot from 14 up is two lower than the modern header says. The evidence is in
# the image, not in a header, and `--check` is what makes it evidence: four
# call sites each pin a different slot by its argument shape and its return,
# and all four contradict the modern order.
USERSTATS = [
    "RequestCurrentStats", "GetStatInt32", "GetStatFloat", "SetStatInt32",
    "SetStatFloat", "UpdateAvgRateStat", "GetAchievement", "SetAchievement",
    "ClearAchievement", "GetAchievementAndUnlockTime", "StoreStats",
    "GetAchievementIcon", "GetAchievementDisplayAttribute",
    "IndicateAchievementProgress", "RequestUserStats",
    "GetUserStatInt32", "GetUserStatFloat", "GetUserAchievement",
    "GetUserAchievementAndUnlockTime", "ResetAllStats",
    "FindOrCreateLeaderboard", "FindLeaderboard", "GetLeaderboardName",
    "GetLeaderboardEntryCount", "GetLeaderboardSortMethod",
    "GetLeaderboardDisplayType", "DownloadLeaderboardEntries",
    "DownloadLeaderboardEntriesForUsers", "GetDownloadedLeaderboardEntry",
    "UploadLeaderboardScore", "AttachLeaderboardUGC",
    "GetNumberOfCurrentPlayers",
]
NAMES = {"ISteamUserStats": USERSTATS}

GP = {Register.EAX, Register.ECX, Register.EDX, Register.EBX,
      Register.ESP, Register.EBP, Register.ESI, Register.EDI}


def full32(r: int) -> int:
    return r


class Sym:
    """What a register is known to hold, as far as this scan cares."""
    __slots__ = ("kind", "iface", "slot")

    def __init__(self, kind: str, iface: str = "", slot: int = -1):
        self.kind, self.iface, self.slot = kind, iface, slot

    def __repr__(self) -> str:
        return f"{self.kind}({self.iface},{self.slot})"


def gaps(img: "IM.Image", funcs: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """Executable bytes no mapped function covers.

    13 % of the game region's instructions are in no module at all (S3's M1
    companion clause), and the first draft of this tool scanned only the map --
    which is how it missed `GetAchievement` and `GetAchievementDisplayAttribute`
    entirely, both of which live at 0x0040e9b0, in a gap. A surface scan that
    reads only the function map measures the map, not the image."""
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


def scan_function(img: "IM.Image", iat: dict[int, str], va: int, size: int) -> list[dict]:
    sec = img.section_of(va)
    if sec is None or not sec.executable:
        return []
    data = img._section_bytes[sec.name]
    off = va - sec.va
    blob = data[off:off + max(size, 0)]
    if not blob:
        return []
    regs: dict[int, Sym] = {}
    # True once a `call` has reset the push list. Only then is the push count
    # at a later site the *exact* argument count: before the first call, the
    # function's own `push esi/edi/ebx` prologue is still in the list, and a
    # count that includes a prologue is an upper bound pretending to be a
    # measurement.
    saw_call = False
    # Constant pushes seen since the last call, newest first: the arguments of
    # a __thiscall method are pushed right-to-left, so index 0 is the last
    # argument and the deepest push is argument 0.
    pushes: list[int] = []
    out: list[dict] = []

    def clobber(r: int) -> None:
        regs.pop(r, None)

    for ins in Decoder(32, blob, ip=va):
        m = ins.mnemonic
        if m == Mnemonic.PUSH:
            if ins.op0_kind == OpKind.IMMEDIATE32:
                pushes.append(ins.immediate32)
            else:
                pushes.append(-1)
            continue
        if m == Mnemonic.MOV:
            # mov r32, [abs]  -- an IAT slot
            if ins.op0_kind == OpKind.REGISTER and ins.op1_kind == OpKind.MEMORY:
                dst = full32(ins.op0_register)
                base, idx = ins.memory_base, ins.memory_index
                disp = ins.memory_displacement
                if base == Register.NONE and idx == Register.NONE:
                    sym = iat.get(disp)
                    regs[dst] = Sym("accessor", ACCESSOR[sym]) if sym in ACCESSOR else None
                    if regs[dst] is None:
                        clobber(dst)
                    continue
                if idx == Register.NONE and base in regs and regs[base] is not None:
                    s = regs[base]
                    if s.kind == "iface" and disp == 0:
                        regs[dst] = Sym("vtable", s.iface)
                        continue
                    if s.kind == "vtable" and disp % 4 == 0:
                        regs[dst] = Sym("method", s.iface, disp // 4)
                        continue
                clobber(dst)
                continue
            # mov r32, r32
            if ins.op0_kind == OpKind.REGISTER and ins.op1_kind == OpKind.REGISTER:
                dst, src = full32(ins.op0_register), full32(ins.op1_register)
                if src in regs and regs[src] is not None:
                    regs[dst] = regs[src]
                else:
                    clobber(dst)
                continue
            if ins.op0_kind == OpKind.REGISTER:
                clobber(full32(ins.op0_register))
            continue
        if m == Mnemonic.CALL:
            target = None
            if ins.op0_kind == OpKind.MEMORY and ins.memory_base == Register.NONE \
               and ins.memory_index == Register.NONE:
                sym = iat.get(ins.memory_displacement)
                if sym in ACCESSOR:
                    target = Sym("accessor", ACCESSOR[sym])
            elif ins.op0_kind == OpKind.REGISTER:
                target = regs.get(full32(ins.op0_register))
            after = ins.next_ip
            if target is not None and target.kind == "accessor":
                regs[Register.EAX] = Sym("iface", target.iface)
                for r in (Register.ECX, Register.EDX):
                    clobber(r)
                pushes = []
                saw_call = True
                continue
            if target is not None and target.kind == "method":
                args = [a for a in reversed(pushes)]
                out.append({
                    "iface": target.iface,
                    "slot": target.slot,
                    "name": (NAMES.get(target.iface) or [])[target.slot]
                            if target.slot < len(NAMES.get(target.iface) or []) else None,
                    "site": "%#010x" % ins.ip,
                    "return_to": "%#010x" % after,
                    "pushed": ["%#010x" % a if a >= 0 else None for a in args],
                    "pushed_strings": [img.cstring(a, 64) if a > 0 else None for a in args],
                    "argc_exact": saw_call,
                })
            for r in (Register.EAX, Register.ECX, Register.EDX):
                clobber(r)
            pushes = []
            saw_call = True
            continue
        if m in (Mnemonic.RET, Mnemonic.INT3):
            regs.clear()
            pushes = []
            saw_call = False
            continue
        # Anything else: forget what it writes.
        if ins.op0_kind == OpKind.REGISTER:
            clobber(full32(ins.op0_register))
    return out


# (slot, expected name, the property of the *image* that has to hold for that
# name to be the right one at that slot). Each is a fact about the pushes and
# the use of the result at a call site, never about a header.
WITNESSES = {
    6:  ("GetAchievement", "two exact pushes: a name and a bool out-pointer"),
    7:  ("SetAchievement", "exactly one exact push"),
    12: ("GetAchievementDisplayAttribute", 'a site pushing the literal "name"'),
    20: ("FindOrCreateLeaderboard", "three exact pushes; under the modern order "
         "this slot would be GetLeaderboardName, which takes two"),
    22: ("GetLeaderboardName", "two exact pushes; under the modern order this "
         "slot would be FindOrCreateLeaderboard, which takes three"),
    26: ("DownloadLeaderboardEntries", "five exact pushes; under the modern "
         "order this slot would be GetLeaderboardSortMethod, which takes two"),
}


def exact_pushes(by_slot: dict[int, list[dict]], slot: int) -> list[int]:
    return [len(s["pushed"]) for s in by_slot.get(slot, []) if s["argc_exact"]]


def check(sites: list[dict]) -> dict:
    res: dict = {"witnesses": {}, "ok": True}
    by_slot: dict[int, list[dict]] = {}
    for s in sites:
        if s["iface"] == "ISteamUserStats":
            by_slot.setdefault(s["slot"], []).append(s)

    def verdict(slot: int, ok: bool, why: str) -> None:
        res["witnesses"][str(slot)] = {
            "expected_name": WITNESSES[slot][0], "rule": WITNESSES[slot][1],
            "held": ok, "detail": why,
        }
        res["ok"] = res["ok"] and ok

    # 12 is pinned by a literal, not by a count.
    hits = [s for s in by_slot.get(12, []) if "name" in (s["pushed_strings"] or [])]
    verdict(12, bool(hits),
            f'{len(hits)} of {len(by_slot.get(12, []))} sites push "name"')
    # The rest are pinned by an exact push count. "Exact" matters: a count
    # taken before the function's first call still contains its own prologue
    # pushes, so it is an upper bound, and a witness built on an upper bound
    # would hold for names that are wrong.
    for slot, want in ((6, 2), (7, 1), (20, 3), (22, 2), (26, 5)):
        got = exact_pushes(by_slot, slot)
        verdict(slot, bool(got) and all(g == want for g in got),
                f"{len(got)} exact site(s) of {len(by_slot.get(slot, []))}, "
                f"push counts {sorted(set(got))}, wanted {want}")
    return res


def argc_check(sites: list[dict], run_json: Path) -> dict:
    """The runtime's declared argument counts against the image's pushes.

    A __thiscall COM method pops its own arguments, so a declared count that is
    one dword short leaves the caller's frame drifting and every later [esp+n]
    in that function reads the wrong local. Three of the d3d9 counts were wrong
    that way for six slices and nothing noticed, because a survey that answers
    0 to everything walks on either way.

    Only sites with `argc_exact` are compared: before a function's first call
    the push list still holds its own `push esi/edi/ebx` prologue, so a count
    taken there is an upper bound, and a comparison against an upper bound is
    not a comparison. Sites that are not exact are reported as `not_compared`
    with their upper bound, rather than dropped."""
    run = json.loads(run_json.read_text())
    declared = {int(d["slot"]): d for d in
                run.get("interfaces", {}).get("declared", [])}
    out: dict = {"run": str(run_json), "declared_slots": len(declared),
                 "compared": [], "not_compared": [], "disagreements": [], "ok": True}
    for s in sites:
        if s["iface"] != "ISteamUserStats":
            continue
        d = declared.get(s["slot"])
        row = {"slot": s["slot"], "site": s["site"], "pushes": len(s["pushed"]),
               "declared_argc": d["argc"] if d else None,
               "declared_method": d["method"] if d else None,
               "runtime_refuses_this_slot": d is None}
        if not s["argc_exact"]:
            row["upper_bound_only"] = True
            out["not_compared"].append(row)
            continue
        if d is None:
            out["not_compared"].append(row)
            continue
        out["compared"].append(row)
        if d["argc"] != len(s["pushed"]):
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
                    help="a crt_probe run JSON; compares its interfaces.declared "
                         "argument counts against the image's pushes")
    args = ap.parse_args()

    img = IM.Image(args.exe)
    fmap = json.loads(MAP.read_text())
    iat = {}
    for row in fmap["imports"]:
        dll, sym = row["symbol"].split("!", 1)
        iat[int(row["iat"], 16)] = sym

    sites: list[dict] = []
    funcs = [(int(f["address"], 16), f["size"]) for f in fmap["functions"]]
    for a, s in funcs:
        for row in scan_function(img, iat, a, s):
            row["mapped"] = True
            row["owner"] = "%#010x" % a
            sites.append(row)
    gap_list = gaps(img, funcs)
    for a, s in gap_list:
        for row in scan_function(img, iat, a, s):
            row["mapped"] = False
            row["owner"] = "gap %#010x+%#x" % (a, s)
            sites.append(row)
    sites.sort(key=lambda s: (s["iface"], s["slot"], s["site"]))

    slots: dict[str, dict] = {}
    for s in sites:
        d = slots.setdefault(s["iface"], {})
        e = d.setdefault(str(s["slot"]), {"name": s["name"], "sites": [], "strings": [],
                                          "in_gap": 0})
        e["sites"].append(s["site"])
        if not s["mapped"]:
            e["in_gap"] += 1
        for t in s["pushed_strings"]:
            if t and t not in e["strings"]:
                e["strings"].append(t)

    doc = {
        "tool": "tools/s4/steam_surface.py",
        "executable_sha256": fmap["executable_sha256"],
        "functions_scanned": len(fmap["functions"]),
        "call_sites": len(sites),
        "by_interface": slots,
        "sites": sites,
    }
    if args.check:
        doc["check"] = check(sites)
    if args.argc:
        doc["argc"] = argc_check(sites, args.argc)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(doc, indent=1) + "\n")

    for iface in sorted(slots):
        print(f"{iface}:")
        for slot in sorted(slots[iface], key=int):
            e = slots[iface][slot]
            strs = (" strings=" + ", ".join(repr(x) for x in e["strings"][:6])) if e["strings"] else ""
            print(f"  slot {slot:>2} {e['name'] or '?':<34} {len(e['sites'])} site(s) "
                  f"({e['in_gap']} in unmapped gaps) {' '.join(e['sites'][:6])}{strs}")
    if args.check:
        print("check:", json.dumps(doc["check"]["witnesses"], indent=1))
        print("check ok:", doc["check"]["ok"])
    if args.argc:
        a = doc["argc"]
        for r in a["compared"]:
            print(f"  argc slot {r['slot']:>2} {r['declared_method']:<34} "
                  f"declared {r['declared_argc']} vs {r['pushes']} pushed at {r['site']}"
                  + ("   DISAGREES" if r['declared_argc'] != r['pushes'] else ""))
        for r in a["not_compared"]:
            print(f"  argc slot {r['slot']:>2} not compared at {r['site']}: "
                  + ("the runtime refuses this slot" if r["runtime_refuses_this_slot"]
                     else f"push count {r['pushes']} is an upper bound only"))
        print("argc ok:", a["ok"], f"(compared {len(a['compared'])})")
    if (args.check and not doc["check"]["ok"]) or (args.argc and not doc["argc"]["ok"]):
        raise SystemExit(3)


if __name__ == "__main__":
    main()
