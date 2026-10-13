#!/usr/bin/env python3
"""S1 receipt 2: SEH / C++ EH surface of the unpacked executable (design 3.4).

Counts FS:[0] frame registrations, the handlers they register, the shared
prolog helpers (`__SEH_prolog4` / `__EH_prolog3`-shaped), C++ throw sites and
the SafeSEH table, per region when the function map exists.

    .venv/bin/python Documentation/scripts/s1_exception_handling.py \
        --out Documentation/receipts/s1-exception-handling-v1.json

Registration model (MSVC x86): a frame is registered by reading the old head
(`mov reg, fs:[0]` or `push fs:[0]`) and writing the new one
(`mov fs:[0], reg`); the handler is a `push imm32` of a code address in the
same prolog. The optimiser schedules the handler push either before the
read (`push -1; push handler; mov eax, fs:[0]`) or between the read and the
write (`mov eax, fs:[0]; push -1; push handler; push eax; mov fs:[0], esp`),
so the handler search window runs from three instructions before the read
to the paired write. A read with no handler push in that window and inside
a function with direct callers is a shared prolog helper; its callers push
the handler right before `call helper` (`push offset ehhandler; call
__EH_prolog3`), and those callers are the registration users. A handler that
decodes as `... mov eax, imm32; jmp target` with a FuncInfo magic
(0x19930520..0x19930522) at imm32 is a C++ `__ehhandler$` stub and `target`
is `__CxxFrameHandler3`. `_CxxThrowException` is the RaiseException wrapper
whose body carries the MSVC C++ exception code 0xE06D7363 (as an immediate,
or as the first dword of the EXCEPTION_RECORD template it copies); its
direct callers are the throw sites. The SafeSEH handler table is the
cross-check: every handler registered statically should be in it.
"""

from __future__ import annotations

import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import s1_sweep as sw  # noqa: E402
from iced_x86 import Mnemonic, OpKind, Register  # noqa: E402

CXX_EXCEPTION_CODE = 0xE06D7363
FUNCINFO_MAGICS = (0x19930520, 0x19930521, 0x19930522)
HANDLER_WINDOW_BEFORE = 3
HANDLER_WINDOW_AFTER = 24


def is_fs0(ins) -> bool:
    return ins.segment_prefix == Register.FS and sw.abs_displacement(ins) == 0


def is_fs0_write(ins) -> bool:
    """`mov fs:[0], reg` and `pop fs:[0]`: the head pointer is the destination operand."""
    return is_fs0(ins) and ins.op0_kind == OpKind.MEMORY and ins.mnemonic != Mnemonic.PUSH


def is_fs0_read(ins) -> bool:
    """`mov reg, fs:[0]` and `push fs:[0]`: the old head is saved (a registration's first half)."""
    return is_fs0(ins) and not is_fs0_write(ins)


def code_immediate(image: sw.Image, ins, allow_register_load: bool = False) -> int | None:
    """A code address materialised by `push imm32`, `mov [mem], imm32`, or (helper callers only,
    `mov eax, offset ehhandler; call __EH_prolog3`) `mov reg, imm32`; else None."""
    imm = sw.pushed_imm32(ins)
    if imm is None and ins.mnemonic == Mnemonic.MOV and ins.op1_kind == OpKind.IMMEDIATE32 \
            and (ins.op0_kind == OpKind.MEMORY or (allow_register_load and ins.op0_kind == OpKind.REGISTER)):
        imm = ins.immediate32
    return imm if imm is not None and image.in_code(imm) else None


def find_registrations(image: sw.Image) -> list[dict]:
    """Every FS:[0] read paired with its following write and the handler materialised in the prolog.
    A read with no write before the next `ret` inspects the chain (`cmp [ecx+4], handler`) and is
    returned with write None so callers can count it separately."""
    sites = []
    for pos, ins in enumerate(image.instructions):
        if not is_fs0_read(ins):
            continue
        write_pos = None
        for j in range(1, HANDLER_WINDOW_AFTER + 1):
            if pos + j >= len(image.instructions):
                break
            nxt = image.instructions[pos + j]
            if is_fs0_write(nxt):
                write_pos = pos + j
                break
            if nxt.mnemonic == Mnemonic.RET:
                break
        handler = None
        end = write_pos if write_pos is not None else min(pos + HANDLER_WINDOW_AFTER, len(image.instructions))
        for j in range(max(0, pos - HANDLER_WINDOW_BEFORE), end):
            imm = code_immediate(image, image.instructions[j])
            if imm is not None:
                handler = imm
        sites.append({"read": ins.ip, "write": None if write_pos is None else image.instructions[write_pos].ip,
                      "handler": handler})
    return sites


def handler_shape(image: sw.Image, handler: int) -> dict:
    """Classify a handler address by following up to 12 instructions from it."""
    pos = image.at(handler)
    if pos is None:
        return {"shape": "not-an-instruction-boundary"}
    funcinfo = None
    for j in range(12):
        if pos + j >= len(image.instructions):
            break
        ins = image.instructions[pos + j]
        if ins.mnemonic == Mnemonic.MOV and ins.op0_kind == OpKind.REGISTER and ins.op0_register == Register.EAX \
                and ins.op1_kind == OpKind.IMMEDIATE32:
            funcinfo = ins.immediate32
        if ins.mnemonic == Mnemonic.JMP and ins.op0_kind == OpKind.NEAR_BRANCH32:
            magic = image.read_u32(funcinfo) if funcinfo is not None else None
            if magic in FUNCINFO_MAGICS:
                return {"shape": "cxx-ehhandler-stub", "frame_handler": ins.near_branch32,
                        "funcinfo": funcinfo, "funcinfo_magic": magic}
            return {"shape": "jmp-stub", "target": ins.near_branch32}
        if ins.mnemonic == Mnemonic.RET:
            break
    return {"shape": "direct-handler"}


def safeseh_table(image: sw.Image) -> dict:
    lc = getattr(image.pe, "DIRECTORY_ENTRY_LOAD_CONFIG", None)
    if lc is None:
        return {"load_config": False, "handler_table": None, "handler_count": 0, "handlers": []}
    s = lc.struct
    table, count = getattr(s, "SEHandlerTable", 0), getattr(s, "SEHandlerCount", 0)
    handlers = []
    if table and count:
        for k in range(count):
            rva = image.read_u32(table + 4 * k)
            if rva is not None:
                handlers.append(image.base + rva)
    return {"load_config": True, "handler_table": table or None, "handler_count": count, "handlers": handlers}


def analyse(image: sw.Image, fmap: sw.FunctionMap) -> dict:
    all_reads = find_registrations(image)
    inspections = [s for s in all_reads if s["write"] is None]
    sites = [s for s in all_reads if s["write"] is not None]
    reads = [s["read"] for s in sites]
    writes = [ins.ip for ins in image.instructions if is_fs0_write(ins)]
    paired_writes = {s["write"] for s in sites if s["write"] is not None}
    unpaired_writes = [w for w in writes if w not in paired_writes]

    # inline sites carry their own handler; the rest sit inside shared prolog helpers.
    helpers: dict[int, dict] = {}
    inline_sites, helper_sites = [], []
    for s in sites:
        f = fmap.enclosing(image, s["read"])
        s["function"] = f
        if s["handler"] is not None:
            inline_sites.append(s)
            continue
        helper_sites.append(s)
        entry = f["entry"]
        if entry is None:
            continue
        h = helpers.setdefault(entry, {"entry": entry, "region": f["region"], "registration_sites_inside": [],
                                       "callers": sorted(image.direct_calls.get(entry, [])), "caller_handlers": []})
        h["registration_sites_inside"].append(s["read"])
    for h in helpers.values():
        for call in h["callers"]:
            pos = image.at(call)
            handler = None
            for j in (1, 2):
                if pos is None or pos - j < 0:
                    break
                imm = code_immediate(image, image.instructions[pos - j], allow_register_load=True)
                if imm is not None:
                    handler = imm
                    break
            h["caller_handlers"].append({"call": call, "handler": handler, "region": fmap.region_of(call)})

    # handler catalogue: every distinct handler address with where it is registered from.
    handler_refs: dict[int, dict] = defaultdict(lambda: {"inline_sites": 0, "via_helper_callers": 0})
    for s in inline_sites:
        handler_refs[s["handler"]]["inline_sites"] += 1
    for h in helpers.values():
        for c in h["caller_handlers"]:
            if c["handler"] is not None:
                handler_refs[c["handler"]]["via_helper_callers"] += 1
    catalogue = []
    frame_handlers: Counter = Counter()
    for addr in sorted(handler_refs):
        shape = handler_shape(image, addr)
        if shape["shape"] == "cxx-ehhandler-stub":
            frame_handlers[shape["frame_handler"]] += 1
        catalogue.append({"handler": hex(addr), "region": fmap.region_of(addr), **handler_refs[addr],
                          **{k: (hex(v) if isinstance(v, int) else v) for k, v in shape.items()}})

    direct = [c for c in catalogue if c["shape"] == "direct-handler"]
    if direct:
        top = max(direct, key=lambda c: (c["inline_sites"] + c["via_helper_callers"], c["handler"]))
        top["note"] = "most-referenced C handler: _except_handler4-shaped (shared C SEH handler)"

    table = safeseh_table(image)
    table_set = set(table["handlers"])
    table_shapes = Counter(handler_shape(image, h)["shape"] for h in table["handlers"])
    found_handlers = set(handler_refs)

    # C++ throw sites: callers of the RaiseException wrapper that carries the MSVC exception code
    # (as an immediate, or as the first dword of an EXCEPTION_RECORD template it copies).
    raise_wrappers, throw_fns = [], {}
    for call in image.import_call_sites("RaiseException"):
        f = fmap.enclosing(image, call)
        pos = image.at(call)
        start = pos - 40 if f["entry"] is None or image.at(f["entry"]) is None else image.at(f["entry"])
        code_seen = False
        for j in range(max(0, start), pos):
            ins = image.instructions[j]
            for k in range(ins.op_count):
                if ins.op_kind(k) not in (OpKind.IMMEDIATE32, OpKind.IMMEDIATE8TO32):
                    continue
                imm = ins.immediate(k) & 0xFFFFFFFF
                if imm == CXX_EXCEPTION_CODE or image.read_u32(imm) == CXX_EXCEPTION_CODE:
                    code_seen = True
        callers = sorted(image.direct_calls.get(f["entry"], [])) if f["entry"] is not None else []
        row = {"call": call, "entry": f["entry"], "region": f["region"], "callers": callers,
               "kind": "cxx-throw" if code_seen else "seh-raise"}
        raise_wrappers.append(row)
        if code_seen and f["entry"] is not None:
            throw_fns.setdefault(f["entry"], row)
    throw_sites = sorted(c for t in throw_fns.values() for c in t["callers"])

    # functions using EH: own an inline registration, or call a prolog helper.
    eh_functions = {s["function"]["entry"] for s in inline_sites if s["function"]["entry"] is not None}
    for h in helpers.values():
        for c in h["caller_handlers"]:
            f = fmap.enclosing(image, c["call"])
            if f["entry"] is not None:
                eh_functions.add(f["entry"])

    def site_row(s: dict) -> dict:
        f = s["function"]
        return {"read": hex(s["read"]), "write": None if s["write"] is None else hex(s["write"]),
                "handler": None if s["handler"] is None else hex(s["handler"]),
                "function": None if f["entry"] is None else hex(f["entry"]), "region": f["region"],
                "direct_callers_of_function": len(image.direct_calls.get(f["entry"], [])) if f["entry"] is not None else 0}

    ranked = sorted(sites, key=lambda s: (-(len(helpers.get(s["function"]["entry"], {}).get("callers", []))
                                          if s["handler"] is None else 0), s["read"]))
    frame_handler = frame_handlers.most_common(1)[0] if frame_handlers else None
    return {
        "fs0_registration_sites": sw.counted(fmap, reads),
        "fs0_reads_without_write": sw.counted(fmap, [s["read"] for s in inspections]),
        "fs0_writes": sw.counted(fmap, writes),
        "fs0_unregistration_writes": sw.counted(fmap, unpaired_writes),
        "registration_sites_inline_with_handler": sw.counted(fmap, [s["read"] for s in inline_sites]),
        "registration_sites_inside_prolog_helpers": sw.counted(fmap, [s["read"] for s in helper_sites]),
        "prolog_helpers": [{"entry": hex(h["entry"]), "region": h["region"],
                            "registration_sites_inside": [hex(a) for a in h["registration_sites_inside"]],
                            "callers": sw.counted(fmap, h["callers"]),
                            "callers_with_handler_push": sum(1 for c in h["caller_handlers"] if c["handler"] is not None)}
                           for h in sorted(helpers.values(), key=lambda h: (-len(h["callers"]), h["entry"]))],
        "registration_users": sw.counted(fmap, [s["read"] for s in inline_sites]
                                         + [c["call"] for h in helpers.values() for c in h["caller_handlers"]]),
        "functions_with_registration": sw.counted(fmap, eh_functions),
        "distinct_handlers": len(catalogue),
        "handlers_by_shape": dict(sorted(Counter(c["shape"] for c in catalogue).items())),
        "cxx_frame_handler3": None if frame_handler is None else
            {"address": hex(frame_handler[0]), "region": fmap.region_of(frame_handler[0]),
             "ehhandler_stubs": frame_handler[1],
             "registrations_through_it": sum(c["inline_sites"] + c["via_helper_callers"] for c in catalogue
                                             if c.get("frame_handler") == hex(frame_handler[0]))},
        "non_cxx_handlers": [c for c in catalogue if c["shape"] != "cxx-ehhandler-stub"],
        "cxx_throw": {"RaiseException_wrappers": [{"call": hex(r["call"]), "function": None if r["entry"] is None else hex(r["entry"]),
                                                    "region": r["region"], "kind": r["kind"], "direct_callers": len(r["callers"])}
                                                   for r in raise_wrappers],
                      "throw_functions": [{"entry": hex(t["entry"]), "region": t["region"], "callers": len(t["callers"])}
                                          for t in throw_fns.values()],
                      "throw_sites": sw.counted(fmap, throw_sites),
                      "RaiseException_call_sites": sw.counted(fmap, image.import_call_sites("RaiseException"))},
        "safeseh": {"load_config_present": table["load_config"],
                    "handler_table": None if table["handler_table"] is None else hex(table["handler_table"]),
                    "handler_count": table["handler_count"],
                    "table_entries_by_region": fmap.by_region(table["handlers"]),
                    "table_entries_by_neighbour_region": dict(sorted(Counter(
                        fmap.neighbour_region(h) or "unpartitioned" for h in table["handlers"]).items())),
                    "table_entries_by_shape": dict(sorted(table_shapes.items())),
                    "found_handlers_in_table": len(found_handlers & table_set),
                    "found_handlers_not_in_table": sorted(hex(h) for h in found_handlers - table_set),
                    "table_entries_never_registered_statically": len(table_set - found_handlers)},
        "top_20_registration_sites": [site_row(s) for s in ranked[:20]],
    }


METHOD = (
    "Linear iced-x86 sweep of every executable section. A registration site is an FS:[0] read "
    "(`mov reg, fs:[0]` or `push dword ptr fs:[0]`: segment prefix FS, absolute displacement 0); it is "
    "paired with the next FS:[0] write (`mov fs:[0], reg` / `pop fs:[0]`) within 24 instructions and "
    "before any `ret`; the handler is the last `push imm32` whose value lies in a code section, "
    "searched from 3 instructions before the read to the paired write (a `mov [mem], imm32` of a code "
    "address counts too). A read with no write before the next `ret` inspects the chain and is not a "
    "registration; it is counted separately. Writes with no preceding read "
    "are unregistrations (epilogs). A read with no handler push is inside a shared prolog helper "
    "(`__SEH_prolog4` / `__EH_prolog3`-shaped): the helper's function (map entry, or nearest preceding "
    "direct-call target without the map) is looked up, its direct callers are the registration users, "
    "and each caller's handler is a code-address `push imm32` or `mov reg, imm32` within 2 instructions "
    "before the call (`push size; mov eax, offset ehhandler; call __EH_prolog3`). "
    "Handlers are classified by decoding up to 12 instructions from the address: `mov eax, imm32; jmp T` "
    "with a FuncInfo magic 0x19930520..22 at imm32 is a C++ `__ehhandler$` stub and T is "
    "`__CxxFrameHandler3`. Each RaiseException call site's function is a raise wrapper; it is "
    "`_CxxThrowException`-shaped when an immediate in the function body is 0xE06D7363 or points at a "
    "dword holding it (the EXCEPTION_RECORD template the VS2010 CRT copies), and the wrapper's direct "
    "callers are the throw sites. SafeSEH comes from pefile's load config (SEHandlerTable/Count). Regions come "
    "from the function map when present."
)

LIMITS = [
    "Linear sweep: data inside code sections decodes as instructions; an FS-prefixed garbage decode would count. "
    "Cross-check: the number of registered handlers found statically versus the SafeSEH table.",
    "Handler search is a window, not data flow: a code-address push in the prolog that is not the handler "
    "would be misattributed; a handler pushed outside the window is reported as a helper site.",
    "Without the function map the enclosing function is the nearest preceding direct-call target; "
    "functions reached only indirectly are merged into their predecessor.",
    "Registrations reached through an indirect call to a helper, or by hand-written CRT assembly that "
    "does not read FS:[0] first, are not counted.",
    "An __ehhandler$ stub is not a function in the map, so SafeSEH entries are 'unmapped' by region; the "
    "neighbour region (nearest mapped function before the stub) is the region of the function that owns it.",
    "Throw sites via function pointers or via a second-level wrapper of _CxxThrowException are not counted; "
    "the wrapper is scanned from its entry (or 40 instructions back without the map) to the RaiseException call.",
]


def main(argv=None) -> int:
    args = sw.base_parser(__doc__.splitlines()[0]).parse_args(argv)
    image = sw.Image(args.exe)
    fmap = sw.FunctionMap(args.function_map)
    outputs = analyse(image, fmap)
    doc = sw.receipt(image, fmap, "s1-exception-handling-v1",
                     "How much SEH / C++ EH does the executable use, per region, and through which handlers? (design 3.4)",
                     METHOD, outputs, LIMITS)
    sw.write_receipt(args.out, doc)
    o = outputs
    print(f"s1-exception-handling: {sw.format_counts('FS:[0] registration sites', o['fs0_registration_sites'])}; "
          f"reads without a paired write {o['fs0_reads_without_write']['total']}")
    print(f"s1-exception-handling: {sw.format_counts('FS:[0] writes', o['fs0_writes'])}; "
          f"unregistration writes {o['fs0_unregistration_writes']['total']}")
    print(f"s1-exception-handling: {sw.format_counts('inline sites with handler', o['registration_sites_inline_with_handler'])}; "
          f"{sw.format_counts('sites inside prolog helpers', o['registration_sites_inside_prolog_helpers'])}; "
          f"helpers {len(o['prolog_helpers'])}")
    print(f"s1-exception-handling: {sw.format_counts('registration users (inline + helper callers)', o['registration_users'])}")
    print(f"s1-exception-handling: {sw.format_counts('functions with a registration', o['functions_with_registration'])}")
    print(f"s1-exception-handling: distinct handlers {o['distinct_handlers']} by shape {o['handlers_by_shape']}; "
          f"__CxxFrameHandler3 {o['cxx_frame_handler3']}")
    print(f"s1-exception-handling: non-C++ handlers {len(o['non_cxx_handlers'])}: "
          + ", ".join(f"{c['handler']} inline {c['inline_sites']} via-helper {c['via_helper_callers']} ({c['shape']})"
                      for c in o['non_cxx_handlers']))
    print(f"s1-exception-handling: {sw.format_counts('C++ throw sites', o['cxx_throw']['throw_sites'])} via "
          f"{[t['entry'] for t in o['cxx_throw']['throw_functions']]}; RaiseException wrappers "
          f"{[(r['function'], r['kind']) for r in o['cxx_throw']['RaiseException_wrappers']]}; "
          f"{sw.format_counts('RaiseException call sites', o['cxx_throw']['RaiseException_call_sites'])}")
    s = o["safeseh"]
    print(f"s1-exception-handling: SafeSEH load config {s['load_config_present']}, table {s['handler_table']} "
          f"count {s['handler_count']} by region {s['table_entries_by_region']} by neighbour region "
          f"{s['table_entries_by_neighbour_region']} by shape {s['table_entries_by_shape']}; "
          f"found handlers in table {s['found_handlers_in_table']}, not in table {len(s['found_handlers_not_in_table'])}, "
          f"table entries never registered statically {s['table_entries_never_registered_statically']}")
    print(f"s1-exception-handling: receipt {sw.rel(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
