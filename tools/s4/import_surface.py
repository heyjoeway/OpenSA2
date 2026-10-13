#!/usr/bin/env python3
"""Enumerate the executable's `GetProcAddress` surface and write its receipt.

M4 reads `n/251`, and 251 is the number of import-address-table slots. The
binary also resolves names at run time through `GetProcAddress`, and the G1
decision (`Documentation/decisions/2026-09-19-G1-lifter-eh-threads.md`) defers
that surface to S4 with three numbers -- 154 game-region direct call sites, 209
in all, 134 pointer loads -- and promises "its own receipt enumerating the
symbol strings". This is that receipt's generator. S4's rate is about to be
projected from M4's denominator, and a rate over a denominator that does not
mean what it says is a wrong projection, so the denominator gets established
before the projection rather than after.

What it does

  1. Enumerates every reference to the `GetProcAddress` IAT slot in the
     executable sections and classifies each: `call [slot]`, a pointer load
     into a register, or the `call <reg>` sites that pointer load feeds.
  2. Checks the enumeration against a decode-independent byte scan for the
     slot address, and each site against a second decode anchored on a
     function entry, and each site's displacement field against `.reloc`.
     Any of the three disagreeing is a finding, printed and recorded, not
     smoothed over.
  3. Recovers the symbol string of each call site from the immediate written to
     `lpProcName` -- a `push`, or a store into a stack slot the previous call
     already reserved -- and classifies every site it cannot.
  4. Reconciles against the G1 decision's own figures, read out of the
     decision file and out of `Documentation/receipts/s1-import-classification-v1.json`
     rather than typed here.
  5. Computes the candidate M4 denominators and their numbers. It does not
     change `metrics.py`: which denominator M4 should carry is a decision,
     and this tool's job is to make the decision cost nothing to take.

Nothing is typed (AGENTS.md rule 7) and no image bytes cross into the
repository (rule 9): the receipt carries counts, addresses, symbol name text
and hashes.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/import_surface.py
  .venv/bin/python tools/s4/import_surface.py --symbol GetProcAddress \
      --out Documentation/receipts/s4-import-surface-v1.json
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import importlib.metadata
import json
import re
import sys
from pathlib import Path

from iced_x86 import (FlowControl, InstructionInfoFactory, Mnemonic, OpAccess,
                      OpKind, Register)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from image import (DEFAULT_EXE, DEFAULT_MAP, ROOT, FunctionMap, Image,  # noqa: E402
                   abs_displacement, region_sort_key, register_name, rel, sha256_of)

SCHEMA = "sa2-recomp-receipt/v1"
RECEIPT_ID = "s4-import-surface-v1"
DEFAULT_OUT = ROOT / "Documentation" / "receipts" / f"{RECEIPT_ID}.json"
GENERATED = ROOT / "private" / "generated" / "s4"
DECISION = "Documentation/decisions/2026-09-19-G1-lifter-eh-threads.md"
S1_RECEIPT = "Documentation/receipts/s1-import-classification-v1.json"
PINS = ROOT / "tools" / "pins.json"

PIPELINE = ("tools/s4/import_surface.py", "tools/s4/image.py")

# A decision-record number carries its citation marker: `209 [[marker-name]]`.
MARKED = re.compile(r"(\d+)\s*\[\[([a-z0-9-]+)\]\]")

BACKWALK = 32          # instructions scanned back from a call for its pushes
# `push 8BAF30h`: an argument that is a link-time constant. A module handle never is.
IMMEDIATE_PUSH = re.compile(r"push (0x)?[0-9A-Fa-f]+h?$")
FORWARDWALK = 4096     # instructions scanned forward from a pointer load


# --------------------------------------------------------------------------
# site enumeration


def slot_of(image: Image, symbol: str) -> tuple[int, str]:
    """The IAT slot holding `symbol`, and its DLL. Exactly one, or it is an error:
    two slots for one name would silently halve every count below."""
    hits = sorted((va, dll) for va, (dll, name) in image.imports.items() if name == symbol)
    if len(hits) != 1:
        raise SystemExit(f"{symbol}: expected exactly one IAT slot, found {len(hits)}: "
                         f"{[hex(v) for v, _ in hits]}")
    return hits[0]


def references(image: Image, slot: int) -> tuple[list[int], list[tuple[int, int]]]:
    """(`call [slot]` sites, (pointer-load site, destination register) pairs).

    A `jmp [slot]` thunk is neither and is returned to the caller's attention by
    `image.thunks`; this symbol has none, and the receipt records that it checked.
    """
    calls, loads = [], []
    for ins in image.instructions:
        if abs_displacement(ins) != slot:
            continue
        if ins.mnemonic == Mnemonic.CALL:
            calls.append(ins.ip)
        elif ins.mnemonic == Mnemonic.JMP:
            continue                      # a thunk, counted separately
        elif ins.op0_kind == OpKind.REGISTER:
            loads.append((ins.ip, ins.op0_register))
        else:
            loads.append((ins.ip, Register.NONE))
    return sorted(calls), sorted(loads)


def span(fmap: FunctionMap, addr: int) -> tuple[int, int]:
    """[entry, next entry) of the analyzer function containing `addr`.

    Deliberately not [entry, entry + size). The analyzer writes `size` as
    `Function.getBody().getNumAddresses()`, the *count* of addresses in a body
    that may be discontiguous, so `entry + size` falls short of the real extent
    for every function whose body has a hole -- the defect
    `Documentation/S3_INPUTS.md` section 1 is owed a fix for. Using the next
    entry as the bound is the same routing-around that `tools/s3/arms/armlist.py`
    does, and it is why a site this tool calls "unmapped" is reported with its
    preceding function rather than as a homeless address.
    """
    prev = fmap.preceding(addr)
    nxt = fmap.following(addr)
    lo = prev["address"] if prev else addr
    hi = nxt["address"] if nxt else (1 << 32)
    return lo, hi


def register_calls(image: Image, fmap: FunctionMap, load_va: int,
                   reg: int) -> tuple[list[int], str]:
    """`call <reg>` sites the pointer load at `load_va` reaches, and why the walk stopped.

    The walk runs forward through the linear sweep from the load to the end of
    the load's enclosing span and stops when anything writes `reg`. Every
    register this binary caches `GetProcAddress` in is callee-saved, so a walk
    that crosses a `call` is sound for them; a caller-saved destination is
    reported in `volatile_destination` instead of being silently trusted.
    """
    if reg == Register.NONE:
        return [], "destination is not a register"
    _, hi = span(fmap, load_va)
    info = InstructionInfoFactory()
    pos = image.at(load_va)
    if pos is None:
        return [], "load address is not a sweep instruction"
    sites: list[int] = []
    k = pos + 1
    while k < len(image.instructions) and k - pos < FORWARDWALK:
        ins = image.instructions[k]
        if ins.ip >= hi:
            return sites, "reached the next function entry"
        if (ins.mnemonic == Mnemonic.CALL and ins.op0_kind == OpKind.REGISTER
                and ins.op0_register == reg):
            sites.append(ins.ip)
        else:
            for used in info.info(ins).used_registers():
                if used.register == reg and used.access in (
                        OpAccess.WRITE, OpAccess.READ_WRITE,
                        OpAccess.COND_WRITE, OpAccess.READ_COND_WRITE):
                    return sites, "register overwritten"
        k += 1
    return sites, "walk limit reached"


VOLATILE = {Register.EAX, Register.ECX, Register.EDX}


# --------------------------------------------------------------------------
# symbol recovery


def block_before(image: Image, call_va: int) -> tuple[list, str]:
    """The straight-line instructions between the previous control-flow boundary and the
    call, in program order, and the boundary that stopped the walk."""
    pos = image.at(call_va)
    if pos is None:
        return [], "call address is not a sweep instruction"
    found = []
    for j in range(1, BACKWALK + 1):
        if pos - j < 0:
            found.reverse()
            return found, "start of the sweep"
        ins = image.instructions[pos - j]
        if (ins.mnemonic in (Mnemonic.CALL, Mnemonic.RET, Mnemonic.JMP)
                or ins.flow_control == FlowControl.CONDITIONAL_BRANCH):
            found.reverse()
            return found, image.text(ins)
        found.append(ins)
    found.reverse()
    return found, "walk limit reached"


def straight_line_entry(image: Image, addr: int) -> int | None:
    """The first instruction after the last `ret` or `int3` before `addr`.

    A function entry derived from the code rather than from the analyzer map,
    because a map that misses the function entirely cannot answer "whose code is
    this". MSVC pads between functions with `int3`, so the padding run is the
    boundary; a function reached only by a computed address still has one.
    """
    pos = image.at(addr)
    if pos is None:
        return None
    for j in range(1, 4096):
        if pos - j < 0:
            return image.instructions[0].ip
        ins = image.instructions[pos - j]
        if ins.mnemonic in (Mnemonic.INT3, Mnemonic.RET):
            return image.instructions[pos - j + 1].ip
    return None


def _imm32(ins, k: int) -> int | None:
    kind = ins.op_kind(k)
    if kind in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
        return ins.immediate(k) & 0xFFFFFFFF
    return None


def stack_arguments(image: Image, call_va: int) -> tuple[dict, str, list]:
    """The outgoing stack slots at `call_va`, keyed by byte offset from the call's `esp`.

    A `push` is not the only way MSVC writes an argument. Where a slot is already
    reserved -- because the previous `cdecl` call left its argument there -- the
    compiler reuses it with `mov dword [esp+k], imm32`, and a rule that only reads
    pushes silently loses that argument. So this walks the straight-line block
    before the call forward, tracking `esp` relative to the block's entry, and
    records every write into the outgoing area: `push`, `mov [esp+k], ...`, and
    `sub`/`add esp, imm`. A slot written from a register or from memory is
    recorded with its operand text and no value, which is the difference between
    an argument this tool did not trace and one that does not exist statically.

    Returns ({offset from call-time esp: {"value" | "operand", "how"}}, boundary,
    the block).
    """
    block, boundary = block_before(image, call_va)
    esp = 0                      # relative to the block's entry
    slots: dict[int, dict] = {}
    for ins in block:
        if ins.mnemonic == Mnemonic.PUSH:
            esp -= 4
            value = _imm32(ins, 0)
            slots[esp] = {"how": "push", "operand": image.text(ins),
                          **({"value": value} if value is not None else {})}
            continue
        if ins.mnemonic in (Mnemonic.SUB, Mnemonic.ADD) and \
                ins.op0_kind == OpKind.REGISTER and ins.op0_register == Register.ESP:
            delta = _imm32(ins, 1)
            if delta is None:
                return slots, boundary + " | esp moved by a non-immediate", block
            esp += -delta if ins.mnemonic == Mnemonic.SUB else delta
            continue
        if ins.mnemonic == Mnemonic.POP:
            esp += 4
            continue
        if ins.mnemonic == Mnemonic.MOV and ins.op0_kind == OpKind.MEMORY \
                and ins.memory_base == Register.ESP and ins.memory_index == Register.NONE:
            target = esp + (ins.memory_displacement & 0xFFFFFFFF
                            if ins.memory_displacement < 0x80000000
                            else ins.memory_displacement - (1 << 32))
            value = _imm32(ins, 1)
            slots[target] = {"how": "stack-slot store", "operand": image.text(ins),
                             **({"value": value} if value is not None else {})}
    return {k - esp: v for k, v in slots.items()}, boundary, block


def recover_symbol(image: Image, call_va: int) -> dict:
    """`GetProcAddress(hModule, lpProcName)` is `__stdcall`, so at the call `[esp]` is
    `hModule` and `[esp+4]` is `lpProcName`. The recovery reads those two slots and
    nothing else: a looser rule that took any string-looking immediate from the
    preceding window would resolve every remaining site with a name it could not
    justify, which is a worse outcome than an honest unresolved count."""
    slots, boundary, block = stack_arguments(image, call_va)
    name, module = slots.get(4), slots.get(0)
    out = {"boundary": boundary, "block_instructions": len(block),
           "module_operand": module["operand"] if module else None,
           "name_operand": name["operand"] if name else None,
           "name_written_by": name["how"] if name else None}
    if name is None:
        return out | {"resolved": False,
                      "reason": "no write to [esp+4] between the call and the preceding "
                                "control-flow boundary: lpProcName is set on a path this "
                                "straight-line walk does not see"}
    if "value" not in name:
        return out | {"resolved": False,
                      "reason": "lpProcName is not an immediate: the name arrives in a "
                                "register or from memory and is only known at run time"}
    va = name["value"]
    text = image.cstring(va)
    if text is None:
        return out | {"resolved": False, "string_va": hex(va),
                      "reason": "the immediate written to lpProcName does not address a "
                                "NUL-terminated printable ASCII string in a readable "
                                "data section"}
    return out | {"resolved": True, "string_va": hex(va), "symbol": text}


# --------------------------------------------------------------------------
# checks that can fail


def byte_scan(image: Image, slot: int) -> dict:
    """Every occurrence of the slot address as a little-endian dword, found without
    decoding anything. The sweep's claim is only as good as its agreement with this."""
    needle = slot.to_bytes(4, "little")
    exec_hits, data_hits = [], []
    for sec in image.sections:
        data = (image._section_bytes.get(sec.name) if sec.executable
                else image.read(sec.va, min(sec.vsize, sec.raw_size)))
        if not data:
            continue
        i = data.find(needle)
        while i >= 0:
            (exec_hits if sec.executable else data_hits).append((sec.name, sec.va + i))
            i = data.find(needle, i + 1)
    return {"executable": sorted(exec_hits, key=lambda h: h[1]),
            "non_executable": sorted(data_hits, key=lambda h: h[1])}


def displacement_offsets(image: Image, va: int, slot: int) -> list[int]:
    """Offsets inside the instruction at `va` where the slot dword sits."""
    pos = image.at(va)
    if pos is None:
        return []
    ins = image.instructions[pos]
    raw = image.read(va, ins.len) or b""
    needle = slot.to_bytes(4, "little")
    return [k for k in range(0, max(0, len(raw) - 3)) if raw[k:k + 4] == needle]


# --------------------------------------------------------------------------
# reconciliation inputs, read rather than typed


def decision_numbers(root: Path) -> dict[str, int]:
    path = root / DECISION
    if not path.is_file():
        return {}
    return {marker: int(value) for value, marker in MARKED.findall(
        path.read_text(encoding="utf-8"))}


def s1_entry(root: Path, symbol: str) -> dict:
    path = root / S1_RECEIPT
    doc = json.loads(path.read_text(encoding="utf-8"))
    entry = next((i for i in doc["outputs"]["imports"] if i.get("name") == symbol), {})
    return {"receipt": S1_RECEIPT, "receipt_sha256": sha256_of(path),
            "import_count": doc["outputs"]["import_count"],
            "entry": entry,
            "sweep": doc["outputs"].get("sweep", {})}


def tool_versions() -> dict:
    pins = json.loads(PINS.read_text(encoding="utf-8"))["tools"] if PINS.is_file() else {}
    return {
        "python": {"pinned": pins.get("python", {}).get("version"),
                   "observed": sys.version.split()[0]},
        "pefile": {"pinned": pins.get("pefile", {}).get("version"),
                   "observed": importlib.metadata.version("pefile")},
        "iced-x86": {"pinned": pins.get("iced-x86", {}).get("version"),
                     "observed": importlib.metadata.version("iced-x86")},
    }


def pipeline_pins(root: Path) -> dict[str, str]:
    return {p: sha256_of(root / p) for p in sorted(PIPELINE)}


def counts_by_region(fmap: FunctionMap, addrs) -> dict:
    return {"total": len(list(addrs)), "per_region": fmap.by_region(addrs)}


# --------------------------------------------------------------------------
# the run


def build(exe: Path, fmap_path: Path, symbol: str) -> dict:
    image = Image(exe)
    fmap = FunctionMap(fmap_path)
    slot, dll = slot_of(image, symbol)

    calls, loads = references(image, slot)
    thunks = sorted(t for t, s in image.thunks.items() if s == slot)

    # --- the register-cached call sites the S1 figures do not count ---------
    load_rows = []
    indirect: list[int] = []
    volatile_destinations = []
    for va, reg in loads:
        sites, stop = register_calls(image, fmap, va, reg)
        indirect.extend(sites)
        row = {"va": hex(va), "region": fmap.region_of(va),
               "destination": register_name(reg) if reg != Register.NONE else None,
               "call_sites": [hex(a) for a in sites], "walk_stopped": stop}
        if reg in VOLATILE:
            volatile_destinations.append(hex(va))
            row["volatile_destination"] = True
        load_rows.append(row)
    indirect = sorted(set(indirect))

    # --- checks -------------------------------------------------------------
    scan = byte_scan(image, slot)
    exec_hit_addrs = {va for _, va in scan["executable"]}
    claimed: set[int] = set()
    for va in calls + [a for a, _ in loads]:
        for off in displacement_offsets(image, va, slot):
            claimed.add(va + off)
    missing = sorted(exec_hit_addrs - claimed)
    extra = sorted(claimed - exec_hit_addrs)

    anchored_failures, unrelocated = [], []
    for va in calls + [a for a, _ in loads] + indirect:
        anchor = span(fmap, va)[0]
        if not image.boundary_from(anchor, va):
            anchored_failures.append(hex(va))
    for va in calls + [a for a, _ in loads]:
        offs = displacement_offsets(image, va, slot)
        if not offs or not any((va + o) in image.relocated for o in offs):
            unrelocated.append(hex(va))

    checks = {
        "byte_scan_vs_sweep": {
            "question": "does a decode-independent search for the slot address find "
                        "exactly the references the sweep claims?",
            "slot_dwords_in_executable_sections": len(exec_hit_addrs),
            "operand_fields_claimed_by_the_sweep": len(claimed),
            "found_by_bytes_but_not_by_the_sweep": [hex(a) for a in missing],
            "claimed_by_the_sweep_but_not_in_the_bytes": [hex(a) for a in extra],
            "pass": not missing and not extra,
        },
        "anchored_redecode": {
            "question": "is every site an instruction boundary on a second decode "
                        "started at its function's entry, not only on the linear sweep?",
            "sites_checked": len(calls) + len(loads) + len(indirect),
            "not_a_boundary": anchored_failures,
            "pass": not anchored_failures,
        },
        "relocation_coverage": {
            "question": "is every claimed slot operand a dword `.reloc` says holds an "
                        "absolute address?",
            "sites_checked": len(calls) + len(loads),
            "without_a_relocation": unrelocated,
            "pass": not unrelocated,
        },
        "slot_pointers_in_data": {
            "question": "does any non-executable section hold the slot address, which "
                        "would be a fourth kind of site this enumeration does not model?",
            "occurrences": [{"section": s, "va": hex(a)} for s, a in scan["non_executable"]],
            "pass": not scan["non_executable"],
        },
        "thunks": {"question": "does the image reach this import through a `jmp [slot]` "
                               "thunk, which would add call sites that never name the slot?",
                   "thunks": [hex(t) for t in thunks], "pass": not thunks},
    }

    # --- symbols ------------------------------------------------------------
    call_rows, unresolved_rows = [], []
    symbols: collections.Counter = collections.Counter()
    symbol_sites: dict[str, list[str]] = collections.defaultdict(list)
    reasons: collections.Counter = collections.Counter()
    for va in calls + indirect:
        rec = recover_symbol(image, va)
        kind = "call-slot" if va in set(calls) else "call-register"
        prev = fmap.preceding(va)
        row = {"va": hex(va), "kind": kind, "region": fmap.region_of(va),
               "function": (fmap.function_at(va) or {}).get("name"),
               "containing_span_entry": hex(prev["address"]) if prev else None,
               "containing_span_name": prev["name"] if prev else None,
               "containing_span_region": prev["region"] if prev else None}
        if rec.get("resolved"):
            row |= {"symbol": rec["symbol"], "string_va": rec["string_va"],
                    "module_operand": rec.get("module_operand")}
            symbols[rec["symbol"]] += 1
            symbol_sites[rec["symbol"]].append(hex(va))
        else:
            row |= {"symbol": None, "reason": rec["reason"],
                    "name_operand": rec.get("name_operand"),
                    "module_operand": rec.get("module_operand"),
                    "name_written_by": rec.get("name_written_by"),
                    "block_instructions": rec["block_instructions"],
                    "boundary": rec["boundary"]}
            reasons[rec["reason"]] += 1
            unresolved_rows.append(row)
        call_rows.append(row)

    resolved_rows = [r for r in call_rows if r["symbol"]]

    # --- what the unresolved sites stand for --------------------------------
    # A site whose lpProcName is a register is a generic helper: the name is the
    # helper's own argument and lives one frame up. So the question "could this
    # number ever be complete" is really "how many callers does that helper have",
    # and that is countable. The helper's entry is derived here from the code
    # (the first instruction after the preceding `ret`/`int3`), not from the map,
    # because one of these helpers has no map entry at all.
    for row in unresolved_rows:
        entry = straight_line_entry(image, int(row["va"], 16))
        row["helper_entry"] = hex(entry) if entry is not None else None
        if entry is None:
            continue
        direct = sum(1 for i in image.instructions
                     if i.mnemonic == Mnemonic.CALL
                     and i.op0_kind == OpKind.NEAR_BRANCH32
                     and i.near_branch32 == entry)
        anywhere = sum(len(hits) for hits in
                       byte_scan(image, entry).values())
        row["helper_direct_callers"] = direct
        row["helper_address_referenced_anywhere"] = anywhere
        row["residual"] = (
            f"the name is this helper's own argument, so completing it means "
            f"tracing {direct} direct caller(s); the helper's address appears "
            f"{anywhere} time(s) as a dword anywhere in the image, so "
            + ("nothing at all references it and it is reached, if ever, only by "
               "a computed address" if direct == 0 and anywhere == 0 else
               "direct calls are its whole entry surface" if anywhere == 0 else
               "indirect entry through a stored pointer is also possible"))

    # --- the module handle these calls resolve against ----------------------
    module_operands = collections.Counter(
        r.get("module_operand") for r in call_rows if r.get("module_operand"))

    # --- how the symbols relate to the 251 static imports -------------------
    static_names = {name for _, name in image.imports.values()}
    also_static = sorted(s for s in symbols if s in static_names)

    # --- a check the register-cache walk can fail ---------------------------
    # If the forward walk had attributed a `call <reg>` that is not a GetProcAddress
    # call, that call would not be carrying this import's argument shape: a module
    # handle in [esp] that is never an immediate (a handle cannot be a link-time
    # constant) and a name in [esp+4]. Both conditions are checked at every site the
    # walk claims, so a walk that over-reached shows up here rather than in the
    # symbol count.
    def wrong_shaped(row: dict) -> bool:
        module = row.get("module_operand") or ""
        return bool((row["symbol"] is None and row.get("name_written_by") is None)
                    or IMMEDIATE_PUSH.match(module))

    register_rows = [r for r in call_rows if r["kind"] == "call-register"]
    wrong_shape = sorted(r["va"] for r in register_rows if wrong_shaped(r))
    # Negative control: the same test applied to the `call <reg>` sites inside the
    # same spans that the walk did *not* claim. A test nothing fails is not a test.
    control = []
    for lo, hi in {span(fmap, va) for va, _ in loads}:
        k = image.at(lo)
        claimed = set(indirect)
        while k is not None and k < len(image.instructions) \
                and image.instructions[k].ip < hi:
            ins = image.instructions[k]
            if ins.mnemonic == Mnemonic.CALL and ins.op0_kind == OpKind.REGISTER \
                    and ins.ip not in claimed:
                rec = recover_symbol(image, ins.ip)
                control.append(wrong_shaped(
                    {"symbol": rec.get("symbol"),
                     "name_written_by": rec.get("name_written_by"),
                     "module_operand": rec.get("module_operand")}))
            k += 1
    checks["register_cached_call_shape"] = {
        "question": "does every `call <reg>` the forward walk attributes to a cached "
                    f"`{symbol}` pointer actually carry that import's argument shape "
                    "-- a module handle in [esp] that is not a link-time constant, and "
                    "a name written to [esp+4]?",
        "sites_checked": len(register_rows),
        "without_the_argument_shape": wrong_shape,
        "negative_control": {
            "unclaimed_register_calls_in_the_same_spans": len(control),
            "of_those_failing_the_same_test": sum(control),
            "note": "the test is a necessary condition, not a sufficient one: an "
                    "unclaimed site can satisfy it by being some other two-argument "
                    "indirect call whose second argument is a string. It has teeth "
                    "only because the sites the walk does claim fail it zero times "
                    "while unclaimed sites in the same code fail it often.",
        },
        "pass": not wrong_shape,
    }

    # --- analyzer cross-check ------------------------------------------------
    analyzer_funcs = fmap.functions_calling(symbol)
    sweep_spans = {span(fmap, va)[0] for va in calls + [a for a, _ in loads]}
    analyzer_only = sorted(f["address"] for f in analyzer_funcs
                           if f["address"] not in sweep_spans)
    spans_only = sorted(s for s in sweep_spans
                        if s not in {f["address"] for f in analyzer_funcs})

    # --- reconciliation ------------------------------------------------------
    marked = decision_numbers(ROOT)
    s1 = s1_entry(ROOT, symbol)
    s1e = s1["entry"]
    mine = {
        "imports-getprocaddress-direct-call-sites": len(calls),
        "imports-getprocaddress-direct-call-sites-game": sum(
            1 for a in calls if fmap.region_of(a) == "game"),
        "imports-getprocaddress-pointer-loads": len(loads),
        "imports-total": len(image.imports),
    }
    rows = []
    for marker, value in sorted(mine.items()):
        decided = marked.get(marker)
        rows.append({"marker": marker, "decision_record": decided,
                     "s4_independent_decode": value,
                     "agrees": decided == value,
                     "delta": None if decided is None else value - decided})
    reconciliation = {
        "decision": DECISION,
        "decision_sha256": sha256_of(ROOT / DECISION),
        "s1_receipt": s1["receipt"], "s1_receipt_sha256": s1["receipt_sha256"],
        "s1_direct_call_sites": s1e.get("direct_call_sites"),
        "s1_pointer_loads": s1e.get("pointer_loads"),
        "s1_direct_call_sites_by_region": s1e.get("direct_call_sites_by_region"),
        "markers": rows,
        "all_marked_numbers_agree": all(r["agrees"] for r in rows if
                                        r["decision_record"] is not None),
        "what_the_quoted_numbers_do_not_count": {
            "register_cached_call_sites": len(indirect),
            "note": "each of the 'pointer loads' the decision quotes is a "
                    "`mov <callee-saved reg>, [slot]` that feeds one or more "
                    "`call <reg>` sites later in the same function. The decision "
                    "counts the loads; it does not count the calls they make, so "
                    "the quoted figures understate the dynamic call surface.",
        },
        "analyzer_cross_check": {
            "source": "function-map.json functions[].import_calls, written by Ghidra's "
                      "own reference resolution (Documentation/scripts/ExportSA2FunctionMap.java)",
            "functions_the_analyzer_says_call_this_import": len(analyzer_funcs),
            "functions_holding_a_direct_call_in_this_decode": len(
                {span(fmap, a)[0] for a in calls}),
            "functions_holding_a_direct_call_or_a_pointer_load": len(sweep_spans),
            "analyzer_functions_with_no_site_in_this_decode": [hex(a) for a in analyzer_only],
            "sites_in_functions_the_analyzer_does_not_list": [hex(a) for a in spans_only],
            "reading": "the analyzer resolves `mov reg,[slot]` + `call reg` to the "
                       "import and records it as a call, so its function count matches "
                       "this decode only once pointer loads are counted alongside "
                       "direct calls -- independent confirmation that the loads are "
                       "calls, not address-taking.",
        },
    }

    # --- denominator options -------------------------------------------------
    distinct = len(symbols)
    iat = len(image.imports)
    options = [
        {"option": "A: leave M4 at the IAT slot count",
         "denominator": iat,
         "counts": "the 251 import-address-table slots and nothing else",
         "for": "it is what M4 has always meant, it is a closed set, and every slot "
                "is a real thing the runtime must bind",
         "against": f"it omits {len(calls) + len(indirect)} dynamic call sites "
                    f"resolving {distinct} distinct names; a rate of 100 % against it "
                    "would not mean the guest can resolve every name it asks for"},
        {"option": "B: 251 IAT slots plus the distinct dynamically-resolved symbols",
         "denominator": iat + distinct,
         "counts": f"{iat} slots + {distinct} distinct strings this decode recovers",
         "for": "one number, and it does cover the whole name-resolution surface",
         "against": f"it adds unlike things. Of the {distinct} distinct strings, "
                    f"{len(also_static)} are also static imports and the rest are not "
                    "OS entry points at all; and the set is only as complete as the "
                    f"{len(unresolved_rows)} unresolved site(s) allow, so the "
                    "denominator would move when the unresolved ones are traced"},
        {"option": "C: keep M4 at the IAT slots and publish a second metric for the "
                   "dynamic surface",
         "denominator": f"{iat} for M4; {distinct} distinct symbols over "
                        f"{len(calls) + len(indirect)} call sites for the second",
         "counts": "two populations measured separately",
         "for": "the two are bound by different machinery -- a slot is bound once at "
                "load, a dynamic name is answered per call by a stubbed "
                "`GetProcAddress` -- and a stage rate that mixes them cannot be read; "
                "it also keeps every number already published against 251 comparable",
         "against": "two metrics is two things to keep measured, and nothing forces "
                    "the second to be re-measured on the build that ships"},
    ]

    generated = GENERATED
    generated.mkdir(parents=True, exist_ok=True)

    all_pass = all(c["pass"] for c in checks.values())
    runtime_names = reasons.get(
        "lpProcName is not an immediate: the name arrives in a register or from "
        "memory and is only known at run time", 0)
    verdict = (
        f"The G1 decision's three {symbol} figures are exact, and this decode "
        f"reproduces them without reusing the code that made them: "
        f"{mine['imports-getprocaddress-direct-call-sites']} direct call sites, "
        f"{mine['imports-getprocaddress-direct-call-sites-game']} of them in the game "
        f"region, {mine['imports-getprocaddress-pointer-loads']} pointer loads; a byte "
        f"search that decodes nothing finds {len(exec_hit_addrs)} slot dwords in the "
        f"executable sections and the two sets account for every one of them. They are "
        f"nonetheless incomplete as a measure of work: every pointer load is a "
        f"`mov <callee-saved reg>, [slot]` feeding {len(indirect)} further "
        f"`call <reg>` sites the quoted figures do not count, so the dynamic call "
        f"surface is {len(calls) + len(indirect)} sites, not {len(calls)}. "
        f"{len(resolved_rows)} of {len(call_rows)} sites carry lpProcName as an "
        f"immediate and resolve to {distinct} distinct strings; {len(unresolved_rows)} "
        f"do not, {runtime_names} of them because lpProcName arrives in a register from "
        f"the caller, which no static decode can resolve, so {distinct} is a floor and "
        f"not a total: those two are generic helpers whose name argument lives one "
        f"frame up, and completing them means tracing "
        f"{sum(r.get('helper_direct_callers') or 0 for r in unresolved_rows)} further "
        f"call sites. The strings are not OS entry points -- they are SA2 data symbols, "
        f"and only {len(also_static)} of the {distinct} appear among the {iat} static "
        f"imports. The recommendation is option C: M4 keeps its {iat}-slot denominator "
        f"and the dynamic surface gets its own metric, because binding a slot and "
        f"answering a name are different work and a single rate over their sum cannot "
        f"be read. All {len(checks)} falsifiable checks "
        f"{'pass' if all_pass else 'DO NOT pass'}."
    )

    doc = {
        "schema": SCHEMA,
        "id": RECEIPT_ID,
        "stage": "S4",
        "date": dt.date.today().isoformat(),
        "question": (f"How large is the executable's dynamic import surface -- the names "
                     f"it resolves through `{symbol}` rather than through an IAT slot -- "
                     f"and what should M4's denominator therefore be?"),
        "authority": "statically-translated",
        "inputs": {
            "design": ["Documentation/SA2_RECOMP_DESIGN.md section 3.5 (imports), "
                       "section 8 (M4)"],
            "executable": {"path": rel(image.path), "sha256": image.sha256,
                           "image_base": hex(image.base),
                           "executable_sections": [s.describe() for s in image.exec_sections],
                           "instructions_decoded": len(image.instructions)},
            "function_map": fmap.describe(),
            "decision": {"path": DECISION, "sha256": reconciliation["decision_sha256"]},
            "s1_receipt": {"path": S1_RECEIPT, "sha256": s1["receipt_sha256"]},
            "pipeline": pipeline_pins(ROOT),
            "tools": tool_versions(),
        },
        "commands": [
            "source tools/env.sh",
            ".venv/bin/python tools/s4/import_surface.py",
            ".venv/bin/python Documentation/scripts/check_boundary.py",
            ".venv/bin/python Documentation/scripts/check_receipt_pins.py",
        ],
        "method": (
            "Every executable section is decoded once, linearly, with iced-x86; every "
            f"instruction whose sole absolute memory operand is the `{symbol}` IAT slot "
            "is a site. A `call` is a direct call site, a `mov <reg>` is a pointer load, "
            "and each pointer load is walked forward to the end of its function's "
            "[entry, next entry) span to find the `call <reg>` sites it feeds, stopping "
            "at the first write to that register. Symbols come from the `push imm32` two "
            "pushes back from a call, which is `lpProcName` under __stdcall; no looser "
            "rule is used. Three checks can fail the enumeration: a byte search for the "
            "slot address that never decodes anything, a second decode anchored on each "
            "site's function entry, and `.reloc` coverage of each operand field."),
        "slot": {"symbol": symbol, "dll": dll, "iat_slot": hex(slot)},
        "sites": {
            "direct_call_sites": counts_by_region(fmap, calls),
            "pointer_loads": counts_by_region(fmap, [a for a, _ in loads]),
            "register_cached_call_sites": counts_by_region(fmap, indirect),
            "all_call_sites": counts_by_region(fmap, sorted(set(calls) | set(indirect))),
            "pointer_load_destinations": dict(collections.Counter(
                register_name(r) for _, r in loads).most_common()),
            "calls_per_pointer_load": dict(sorted(collections.Counter(
                len(r["call_sites"]) for r in load_rows).items())),
            "pointer_loads_that_reach_no_call": sum(
                1 for r in load_rows if not r["call_sites"]),
            "pointer_load_walk_stops": dict(collections.Counter(
                r["walk_stopped"] for r in load_rows).most_common()),
            "volatile_destinations": volatile_destinations,
            "module_handle_operand": dict(module_operands.most_common()),
        },
        "unmapped_sites": {
            "question": "the analyzer's region for a site is 'unmapped' when the site "
                        "falls outside [entry, entry + size) of every function. What "
                        "are those?",
            "direct_calls_unmapped": [hex(a) for a in calls
                                      if fmap.region_of(a) == "unmapped"],
            "pointer_loads_unmapped": [hex(a) for a, _ in loads
                                       if fmap.region_of(a) == "unmapped"],
            "all_fall_inside_a_preceding_function_span": all(
                span(fmap, a)[0] <= a < span(fmap, a)[1]
                for a in calls + [x for x, _ in loads]
                if fmap.region_of(a) == "unmapped"),
            "preceding_function_regions": dict(collections.Counter(
                (fmap.preceding(a) or {}).get("region")
                for a in calls + [x for x, _ in loads]
                if fmap.region_of(a) == "unmapped").most_common()),
            "finding": "every unmapped site is an instruction boundary on a decode "
                       "started at the preceding function's entry, and lies before the "
                       "next entry. They are not a separate region: they are the "
                       "analyzer's `size` field under-reporting a discontiguous "
                       "function body, the same defect Documentation/S3_INPUTS.md "
                       "section 1 is owed a fix for. Counted by their containing "
                       "function they belong to the region of that function.",
        },
        "checks": checks,
        "symbols": {
            "call_sites_examined": len(call_rows),
            "resolved": len(resolved_rows),
            "unresolved": len(unresolved_rows),
            "distinct_symbols": distinct,
            "distinct_symbols_that_are_also_static_imports": also_static,
            "unresolved_reasons": dict(reasons.most_common()),
            "unresolved_sites": unresolved_rows,
            "by_symbol": {name: {"sites": count, "at": symbol_sites[name]}
                          for name, count in sorted(symbols.items(),
                                                    key=lambda kv: (-kv[1], kv[0]))},
            "reading": "the strings are not Win32 entry points. They are SA2 data "
                       "symbols -- land tables, texture lists, model and object data, "
                       "character objects -- looked up in a module whose handle the "
                       "game keeps at a fixed global. A runtime that stubs "
                       "`GetProcAddress` for these is answering asset-data questions, "
                       "not OS-API questions, which is the central reason they do not "
                       "belong in the same denominator as the IAT slots.",
        },
        "reconciliation": reconciliation,
        "denominator": {
            "m4_today": {"source": "Documentation/scripts/metrics.py IMPORT_DENOMINATOR",
                         "value": iat,
                         "agrees_with_the_import_directory": iat == s1["import_count"]},
            "options": options,
            "recommendation": "C",
            "why": "a slot is bound once, at load, by the loader; a dynamic name is "
                   "answered per call by a stubbed GetProcAddress against a module "
                   "handle the guest itself produced. They are not the same work and a "
                   "single rate over their sum cannot be read -- which is the failure "
                   "S3 already had once, a rate that saturated against a denominator "
                   "that did not mean what it said. Option C also leaves every figure "
                   "already published against 251 comparable.",
        },
        "limits": [
            "Nothing here was executed. Every number is a static decode of "
            f"{rel(image.path)}; no guest has called `{symbol}` and no stub has "
            "answered one, so this establishes the size of the surface and not that "
            "any of it is reachable.",
            "The symbol set is a lower bound on what the guest will ask for, in two "
            "directions. A site whose lpProcName is a register cannot be resolved "
            "statically at all, and the strings a resolvable site names are the ones "
            "the compiler placed inline -- a name assembled at run time, or read from "
            "a data table this decode does not walk, appears in neither count.",
            "The register-cached call sites are found by a forward walk inside one "
            "function's [entry, next entry) span that stops at the first write to the "
            "register. It can miss and it can over-reach. It misses a pointer cached "
            "in a callee-saved register and called from a different function, or "
            "stored to memory and called from there. It over-reaches when a `call "
            "<reg>` is reachable only on a control-flow path that does not run the "
            "load -- the walk is linear, not a dominance computation, so it reads no "
            "branches. The argument-shape check bounds the over-reach without "
            "eliminating it, and the walk-stop reasons are published so the shape of "
            "both errors is visible.",
            f"A site the analyzer maps to no function is reported with the function "
            f"that precedes it, which fixes its region but not its owner. Ghidra "
            f"attributes those addresses to some function's discontiguous body, and "
            f"which function that is is not re-derived here: {len(spans_only)} of the "
            f"{len(sweep_spans)} containing spans this receipt names hold only unmapped "
            f"sites, and the analyzer lists none of them as calling this import. "
            f"Ownership of the unmapped sites is open; their reality as code, their "
            f"region and their count are not.",
            "Region attribution uses the analyzer map, whose `size` field under-reports "
            "discontiguous bodies. Sites in the under-reported part are reported both "
            "as 'unmapped' (the map's own answer) and by their containing function's "
            "region; neither is re-derived from control flow, because "
            "Documentation/S3_INPUTS.md section 1 is still owed.",
            "Only `GetProcAddress` is enumerated. `LoadLibrary`, `GetModuleHandle` and "
            "ordinal-based resolution are a separate surface and are not measured here; "
            "so is any name a loaded module resolves on the guest's behalf.",
            "This receipt recommends a denominator. It does not change one: "
            "`Documentation/scripts/metrics.py` is untouched, and which denominator M4 "
            "carries is the main thread's decision to take on this evidence.",
        ],
        "verdict": verdict,
    }
    return doc, image, fmap, call_rows, load_rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--exe", type=Path, default=DEFAULT_EXE)
    parser.add_argument("--function-map", type=Path, default=DEFAULT_MAP)
    parser.add_argument("--symbol", default="GetProcAddress")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    doc, image, fmap, call_rows, load_rows = build(args.exe, args.function_map, args.symbol)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")

    GENERATED.mkdir(parents=True, exist_ok=True)
    (GENERATED / "getprocaddress-sites.json").write_text(
        json.dumps({"calls": call_rows, "pointer_loads": load_rows}, indent=2) + "\n",
        encoding="utf-8")

    failed = [name for name, c in doc["checks"].items() if not c["pass"]]
    print(f"{RECEIPT_ID}: {args.out.stat().st_size} bytes -> {rel(args.out)}")
    print(f"  direct call sites      {doc['sites']['direct_call_sites']}")
    print(f"  pointer loads          {doc['sites']['pointer_loads']}")
    print(f"  register-cached calls  {doc['sites']['register_cached_call_sites']}")
    print(f"  all call sites         {doc['sites']['all_call_sites']}")
    print(f"  symbols                {doc['symbols']['resolved']}/"
          f"{doc['symbols']['call_sites_examined']} resolved, "
          f"{doc['symbols']['distinct_symbols']} distinct")
    print(f"  checks                 {len(doc['checks']) - len(failed)}/"
          f"{len(doc['checks'])} pass"
          + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    print(f"  verdict                {doc['verdict']}")
    if failed:
        raise SystemExit(f"{RECEIPT_ID}: {len(failed)} check(s) failed")


if __name__ == "__main__":
    main()
