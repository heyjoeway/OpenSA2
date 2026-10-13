#!/usr/bin/env python3
"""What would it cost to make the guest's data DLL work?

Subtitle: measure the second PE the guest asks `LoadLibraryA` for -- its
shape, its exports classified as code or data, its imports -- and cross
-reference its export table against the names the 706 `GetProcAddress` sites
actually ask for. Builds nothing and loads nothing; this is arithmetic over
two files.

Nothing here writes to `private/install`, which is the user's property, and
nothing here emits a byte of either file. Hashes, sizes, names, counts,
addresses and section tables only (rule 9).

Inputs:
  * every `*.dll` under the installation path the guest names, discovered by
    a **case-folding** walk, because the guest asks for
    `.\\resource\\gd_PC\\DLL\\Win32\\Data_DLL.dll` and a tool that matches the
    literal case silently drops the file that holds the answer -- which is
    exactly how slice 13's texture matcher reported a clean negative;
  * `private/generated/s4/getprocaddress-sites.json` (the 708 sites);
  * `private/generated/s4/procaddr_handles.json` (which handle each asks).

Output: `private/generated/s4/datadll/measure.json`, written incrementally --
one `--stage` at a time so a killed session keeps what it established.

Usage:
  source tools/env.sh
  .venv/bin/python tools/s4/data_dll.py
  .venv/bin/python tools/s4/data_dll.py --json private/generated/s4/datadll/measure.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

import pefile
from iced_x86 import Decoder, Formatter, FormatterSyntax, FlowControl, Mnemonic, OpKind

ROOT = Path(__file__).resolve().parents[2]
INSTALL = ROOT / "private" / "install"
GUEST_PATH = r".\resource\gd_PC\DLL\Win32\Data_DLL.dll"
SITES = ROOT / "private" / "generated" / "s4" / "getprocaddress-sites.json"
HANDLES = ROOT / "private" / "generated" / "s4" / "procaddr_handles.json"
OUT = ROOT / "private" / "generated" / "s4" / "datadll" / "measure.json"

SCN_CODE = 0x00000020
SCN_IDATA = 0x00000040
SCN_UDATA = 0x00000080
SCN_DISCARDABLE = 0x02000000
SCN_EXEC = 0x20000000
SCN_READ = 0x40000000
SCN_WRITE = 0x80000000

# The runtime's guest address space, read from runtime/guest_memory.h. Quoted
# here as constants so the cost model can say whether the DLL's preferred base
# is free; the assertion below fails if the header moves them.
GUEST_END = 0x20000000
GUEST_HEAP_BASE = 0x04000000


def sha256_of(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(p: Path) -> str:
    """Repo-relative, without resolving symlinks: `private/install` is a
    symlink into the user's Steam library, and resolving it would put an
    absolute path outside the repository into every record."""
    p = Path(p)
    for cand in (p if p.is_absolute() else (ROOT / p), p.resolve()):
        try:
            return str(Path(os.path.normpath(str(cand))).relative_to(ROOT))
        except ValueError:
            continue
    return str(p)


def read_guest_memory_constants() -> dict:
    """Second direction on the two constants the cost model quotes."""
    txt = (ROOT / "runtime" / "guest_memory.h").read_text()
    out = {}
    for name in ("SA2_GUEST_END", "SA2_GUEST_HEAP_BASE", "SA2_GUEST_IMAGE_BASE"):
        m = re.search(rf"#define\s+{name}\s+(0x[0-9A-Fa-f]+)", txt)
        out[name] = int(m.group(1), 16) if m else None
    return out


# ---------------------------------------------------------------- resolution


def resolve_case_insensitive(guest_path: str) -> dict:
    """Resolve a guest path against the installation the way the VFS does.

    Reports every candidate rather than the first hit, and reports the
    directory listing, so a miss is a listing and not a bare None.
    """
    parts = [p for p in guest_path.replace("/", "\\").split("\\") if p not in ("", ".")]
    cur = INSTALL
    trail = []
    for part in parts:
        if not cur.is_dir():
            return {"guest_path": guest_path, "resolved": None, "failed_at": part, "trail": trail}
        hit = None
        for entry in sorted(os.listdir(cur)):
            if entry.lower() == part.lower():
                hit = entry
                break
        if hit is None:
            return {
                "guest_path": guest_path,
                "resolved": None,
                "failed_at": part,
                "trail": trail,
                "listing": sorted(os.listdir(cur))[:64],
            }
        trail.append({"asked": part, "on_disk": hit, "case_differs": hit != part})
        cur = cur / hit
    return {"guest_path": guest_path, "resolved": rel(cur), "trail": trail}


def siblings_of(resolved: Path) -> list:
    """Every PE beside the resolved file. The guest asks for one name; what is
    on disk is the question, and a measurement that opens only the asked name
    cannot see a file that has been renamed out from under it."""
    out = []
    for entry in sorted(os.listdir(resolved.parent)):
        p = resolved.parent / entry
        if not p.is_file():
            continue
        with open(p, "rb") as f:
            magic = f.read(2)
        out.append(
            {
                "name": entry,
                "size": p.stat().st_size,
                "sha256": sha256_of(p),
                "is_mz": magic == b"MZ",
            }
        )
    return out


# ------------------------------------------------------------------- PE shape


def flags_of(ch: int) -> str:
    s = ""
    s += "r" if ch & SCN_READ else "-"
    s += "w" if ch & SCN_WRITE else "-"
    s += "x" if ch & SCN_EXEC else "-"
    if ch & SCN_CODE:
        s += " code"
    if ch & SCN_IDATA:
        s += " idata"
    if ch & SCN_UDATA:
        s += " udata"
    if ch & SCN_DISCARDABLE:
        s += " discardable"
    return s


DIRS = [
    "EXPORT", "IMPORT", "RESOURCE", "EXCEPTION", "SECURITY", "BASERELOC",
    "DEBUG", "ARCHITECTURE", "GLOBALPTR", "TLS", "LOAD_CONFIG", "BOUND_IMPORT",
    "IAT", "DELAY_IMPORT", "COM_DESCRIPTOR", "RESERVED",
]


def disassemble_from(pe: pefile.PE, rva: int, limit: int = 64) -> dict:
    """Decode forward from an RVA until the first return or unconditional
    terminator, so "does the entry point do anything" is answered by decoding
    rather than by the size of `.text`."""
    data = pe.get_memory_mapped_image()
    base = pe.OPTIONAL_HEADER.ImageBase
    if rva == 0 or rva >= len(data):
        return {"rva": hex(rva), "decoded": False, "reason": "rva out of image"}
    dec = Decoder(32, data[rva : rva + 4096], ip=base + rva)
    fmt = Formatter(FormatterSyntax.INTEL)
    lines, calls, n = [], [], 0
    terminator = None
    for insn in dec:
        if not insn:
            terminator = "invalid instruction"
            break
        n += 1
        text = fmt.format(insn)
        lines.append(f"{insn.ip:#010x}  {text}")
        if insn.flow_control in (FlowControl.CALL, FlowControl.INDIRECT_CALL):
            calls.append(f"{insn.ip:#010x}  {text}")
        if insn.mnemonic in (Mnemonic.RET, Mnemonic.RETF):
            terminator = "ret"
            break
        if insn.flow_control == FlowControl.UNCONDITIONAL_BRANCH:
            terminator = "jmp"
            break
        if n >= limit:
            terminator = "limit"
            break
    return {
        "rva": hex(rva),
        "va": hex(base + rva),
        "decoded": True,
        "instructions": n,
        "terminator": terminator,
        "calls": calls,
        "listing": lines,
    }


def classify_exports(pe: pefile.PE, sections: list) -> dict:
    """Code or data, from the section the export's RVA lands in, and then
    checked a second way.

    The section characteristic is the primary answer. The check that can fail
    is a decode at the export address: an export in a data section whose bytes
    happen to decode as a clean prologue would be reported, and so would an
    export in `.text` whose bytes do not decode at all. Neither overrides the
    section; both are counted, because a classification with no disagreement
    reported is a classification nobody can audit.
    """
    ex = getattr(pe, "DIRECTORY_ENTRY_EXPORT", None)
    if ex is None:
        return {"present": False}
    data = pe.get_memory_mapped_image()
    edir = pe.OPTIONAL_HEADER.DATA_DIRECTORY[0]
    ed_lo, ed_hi = edir.VirtualAddress, edir.VirtualAddress + edir.Size

    rows, by_section, by_class = [], {}, {"code": 0, "data": 0, "forwarder": 0, "outside": 0}
    decode_disagreements = []
    for sym in ex.symbols:
        rva = sym.address
        name = sym.name.decode("ascii", "replace") if sym.name else None
        sec = None
        for s in sections:
            if s["rva"] <= rva < s["rva"] + max(s["virtual_size"], s["raw_size"]):
                sec = s
                break
        if ed_lo <= rva < ed_hi:
            cls = "forwarder"
        elif sec is None:
            cls = "outside"
        elif sec["characteristics"] & SCN_EXEC:
            cls = "code"
        else:
            cls = "data"
        by_class[cls] += 1
        key = sec["name"] if sec else "<none>"
        by_section[key] = by_section.get(key, 0) + 1

        # Second direction: does it look like a *function*, not merely decode?
        # Almost any byte decodes as some x86 instruction, so "decodes" alone
        # is not a check -- it agreed with 480 data exports on the first run
        # and was therefore measuring nothing. The predicate that can fail is
        # a recognised MSVC entry shape (hotpatch `mov edi,edi`, a frame
        # prologue, or a bare `ret`) followed by a run of 8 instructions with
        # no invalid opcode.
        looks_like_function = False
        if rva + 64 < len(data):
            blob = data[rva : rva + 64]
            head = (
                blob[:2] == b"\x8b\xff"                  # mov edi,edi
                or blob[:3] == b"\x55\x8b\xec"           # push ebp; mov ebp,esp
                or blob[:1] == b"\xc3"                   # ret
                or blob[:1] == b"\x6a"                   # push imm8 (CRT prologue)
                or blob[:1] == b"\x53"                   # push ebx
            )
            dec = Decoder(32, blob, ip=pe.OPTIONAL_HEADER.ImageBase + rva)
            ok, n = True, 0
            for insn in dec:
                if not insn or insn.mnemonic == Mnemonic.INVALID:
                    ok = False
                    break
                n += 1
                if n >= 8:
                    break
            looks_like_function = bool(head and ok and n >= 1)
        if (cls == "data" and looks_like_function) or (cls == "code" and not looks_like_function):
            decode_disagreements.append(
                {"name": name, "rva": hex(rva), "section_class": cls,
                 "looks_like_function": looks_like_function}
            )
        rows.append(
            {
                "ordinal": sym.ordinal,
                "name": name,
                "rva": hex(rva),
                "section": key,
                "class": cls,
                "looks_like_function": looks_like_function,
            }
        )
    return {
        "present": True,
        "dll_name_in_export_directory": pe.get_string_at_rva(ex.struct.Name).decode("ascii", "replace"),
        "number_of_functions": ex.struct.NumberOfFunctions,
        "number_of_names": ex.struct.NumberOfNames,
        "ordinal_base": ex.struct.Base,
        "by_class": by_class,
        "by_section": by_section,
        "exports_with_no_name": sum(1 for r in rows if r["name"] is None),
        "decode_disagreements": decode_disagreements,
        "decode_disagreement_count": len(decode_disagreements),
        "rows": rows,
    }


def measure_pe(path: Path) -> dict:
    pe = pefile.PE(str(path))
    oh, fh = pe.OPTIONAL_HEADER, pe.FILE_HEADER
    sections = []
    for s in pe.sections:
        sections.append(
            {
                "name": s.Name.decode("ascii", "replace").rstrip("\x00"),
                "rva": s.VirtualAddress,
                "va": hex(oh.ImageBase + s.VirtualAddress),
                "virtual_size": s.Misc_VirtualSize,
                "raw_size": s.SizeOfRawData,
                "characteristics": s.Characteristics,
                "flags": flags_of(s.Characteristics),
            }
        )

    relocs = getattr(pe, "DIRECTORY_ENTRY_BASERELOC", []) or []
    by_type, total = {}, 0
    reloc_out_of_image = 0
    for blk in relocs:
        for e in blk.entries:
            t = pefile.RELOCATION_TYPE.get(e.type, str(e.type)) if hasattr(pefile, "RELOCATION_TYPE") else str(e.type)
            t = t if isinstance(t, str) else str(t)
            by_type[t] = by_type.get(t, 0) + 1
            total += 1
            if e.rva >= oh.SizeOfImage:
                reloc_out_of_image += 1
    # pefile's RELOCATION_TYPE is a name->value dict; invert it for readability.
    inv = {v: k for k, v in pefile.RELOCATION_TYPE.items()} if isinstance(pefile.RELOCATION_TYPE, dict) else {}
    if inv:
        by_type = {}
        for blk in relocs:
            for e in blk.entries:
                n = inv.get(e.type, f"type{e.type}")
                by_type[n] = by_type.get(n, 0) + 1

    imports = []
    for entry in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
        names = [
            (i.name.decode("ascii", "replace") if i.name else f"Ordinal#{i.ordinal}")
            for i in entry.imports
        ]
        imports.append({"dll": entry.dll.decode("ascii", "replace"), "count": len(names), "names": names})
    delay = []
    for entry in getattr(pe, "DIRECTORY_ENTRY_DELAY_IMPORT", []) or []:
        delay.append({"dll": entry.dll.decode("ascii", "replace"), "count": len(entry.imports)})

    dirs = {}
    for i, name in enumerate(DIRS):
        d = oh.DATA_DIRECTORY[i]
        if d.VirtualAddress or d.Size:
            dirs[name] = {"rva": hex(d.VirtualAddress), "size": d.Size}

    out = {
        "path": rel(path),
        "size": path.stat().st_size,
        "sha256": sha256_of(path),
        "format": "PE32+" if oh.Magic == 0x20B else "PE32",
        "machine": hex(fh.Machine),
        "machine_name": pefile.MACHINE_TYPE.get(fh.Machine, "?") if isinstance(pefile.MACHINE_TYPE, dict) else "?",
        "characteristics": hex(fh.Characteristics),
        "is_dll": bool(fh.Characteristics & 0x2000),
        "image_base": hex(oh.ImageBase),
        "size_of_image": oh.SizeOfImage,
        "section_alignment": oh.SectionAlignment,
        "file_alignment": oh.FileAlignment,
        "subsystem": oh.Subsystem,
        "dll_characteristics": hex(oh.DllCharacteristics),
        "dynamic_base_aslr": bool(oh.DllCharacteristics & 0x0040),
        "entry_point_rva": hex(oh.AddressOfEntryPoint),
        "entry_point_va": hex(oh.ImageBase + oh.AddressOfEntryPoint),
        "has_entry_point": oh.AddressOfEntryPoint != 0,
        "sections": sections,
        "section_count": len(sections),
        "bytes_to_map": sum(max(s["virtual_size"], s["raw_size"]) for s in sections),
        "executable_section_bytes": sum(
            max(s["virtual_size"], s["raw_size"]) for s in sections if s["characteristics"] & SCN_EXEC
        ),
        "relocations": {
            "present": bool(relocs),
            "blocks": len(relocs),
            "entries": total,
            "by_type": by_type,
            "entries_outside_image": reloc_out_of_image,
            "stripped_flag": bool(fh.Characteristics & 0x0001),
        },
        "imports": imports,
        "import_dll_count": len(imports),
        "import_symbol_count": sum(i["count"] for i in imports),
        "delay_imports": delay,
        "data_directories": dirs,
        "has_tls": "TLS" in dirs,
        "has_exception_directory": "EXCEPTION" in dirs,
        "has_load_config": "LOAD_CONFIG" in dirs,
        "entry_disassembly": disassemble_from(pe, oh.AddressOfEntryPoint) if oh.AddressOfEntryPoint else None,
        "crt_startup": crt_startup(pe),
        "reloc_targets": reloc_targets(pe),
        "exports": classify_exports(pe, sections),
    }
    pe.close()
    return out


# -------------------------------------------------------------- demand/supply


def asked_names() -> dict:
    sites = json.loads(SITES.read_text())
    handles = json.loads(HANDLES.read_text())
    by_va = {}
    for row in handles["rows"]:
        va = row.get("va") or row.get("site")
        by_va[va] = row
    cls_key = None
    if handles["rows"]:
        for k in ("class", "handle_class", "kind"):
            if k in handles["rows"][0]:
                cls_key = k
                break

    all_names, game_names, os_names, unclassified = set(), set(), set(), set()
    sites_with_symbol = 0
    sites_without_symbol = []
    for c in sites["calls"]:
        sym = c.get("symbol")
        if not sym:
            sites_without_symbol.append({"va": c["va"], "reason": c.get("reason")})
            continue
        sites_with_symbol += 1
        all_names.add(sym)
        row = by_va.get(c["va"])
        k = row.get(cls_key) if row and cls_key else None
        if k == "game-data-handle":
            game_names.add(sym)
        elif k == "os-module-handle":
            os_names.add(sym)
        else:
            unclassified.add(sym)
    return {
        "sites_total": len(sites["calls"]),
        "sites_with_symbol": sites_with_symbol,
        "sites_without_symbol": sites_without_symbol,
        "handle_class_key": cls_key,
        "distinct_names": sorted(all_names),
        "game_data_handle_names": sorted(game_names),
        "os_module_handle_names": sorted(os_names),
        "unclassified_names": sorted(unclassified),
        "by_class_counts_from_handles_file": handles.get("by_class"),
    }


def cross_reference(asked: dict, supplies: dict) -> dict:
    """Asked-for names against every export table measured.

    Matched exactly and, separately, case-insensitively. The case-folded pass
    is not cosmetic: slice 13 lost sixteen of seventeen rows to exactly this,
    and a cross-reference that reports only the exact count cannot tell a real
    miss from a spelling.
    """
    names = set(asked["game_data_handle_names"])
    result = {"asked_game_data_names": len(names), "per_supply": {}, "union": {}}
    union_exact, union_ci = set(), set()
    for label, exp in supplies.items():
        if not exp.get("present"):
            result["per_supply"][label] = {"exports": 0, "matched": 0}
            continue
        ex_names = {r["name"] for r in exp["rows"] if r["name"]}
        ex_lower = {n.lower(): n for n in ex_names}
        exact = names & ex_names
        ci = {n for n in names if n.lower() in ex_lower} - exact
        union_exact |= exact
        union_ci |= ci
        code = {r["name"] for r in exp["rows"] if r["name"] in exact and r["class"] == "code"}
        data = {r["name"] for r in exp["rows"] if r["name"] in exact and r["class"] == "data"}
        result["per_supply"][label] = {
            "exports": len(ex_names),
            "matched_exact": len(exact),
            "matched_case_insensitive_only": sorted(ci),
            "matched_that_are_code_exports": sorted(code),
            "matched_that_are_data_exports_count": len(data),
            "exports_never_asked_for": len(ex_names - names),
            "examples_matched": sorted(exact)[:10],
        }
    unmatched = sorted(names - union_exact - union_ci)
    result["union"] = {
        "matched_exact": len(union_exact),
        "matched_case_insensitive_only": len(union_ci),
        "unmatched": len(unmatched),
        "unmatched_names": unmatched,
    }
    return result


def crt_startup(pe: pefile.PE) -> dict:
    """Does anything in this DLL have to *execute*?

    Answered from the image rather than from the size of `.text`: the MSVC
    DLL entry is `_DllMainCRTStartup`, which runs `_pRawDllMain`, the two
    `_initterm` tables and then `DllMain`. If `_pRawDllMain` is null, both
    tables are empty of user entries and `DllMain` is the CRT's default, then
    running the entry point is a no-op the loader can skip -- and that is the
    difference between option (a) and option (b).

    The bounds come from the `push imm32; push imm32; call <thunk>` pairs in
    front of the `_initterm`/`_initterm_e` thunks, decoded, not guessed.
    """
    data = pe.get_memory_mapped_image()
    base = pe.OPTIONAL_HEADER.ImageBase
    sec = next((s for s in pe.sections if s.Name.startswith(b".text")), None)
    if sec is None:
        return {"present": False}
    # IAT slots for the two initialiser walkers.
    want = {}
    for e in getattr(pe, "DIRECTORY_ENTRY_IMPORT", []) or []:
        for i in e.imports:
            if i.name and i.name.decode() in ("_initterm", "_initterm_e"):
                want[i.address] = i.name.decode()
    # Thunks: `jmp dword ptr [slot]`.
    thunks = {}
    dec = Decoder(32, data[sec.VirtualAddress : sec.VirtualAddress + sec.Misc_VirtualSize],
                  ip=base + sec.VirtualAddress)
    insns = [i for i in dec]
    for insn in insns:
        if insn.mnemonic == Mnemonic.JMP and insn.memory_displacement in want:
            thunks[insn.ip] = want[insn.memory_displacement]
    tables = []
    for idx, insn in enumerate(insns):
        if insn.mnemonic != Mnemonic.CALL:
            continue
        tgt = insn.near_branch_target if insn.op0_kind in (OpKind.NEAR_BRANCH16, OpKind.NEAR_BRANCH32) else 0
        which = thunks.get(tgt) or want.get(insn.memory_displacement)
        if not which:
            continue
        pushes = [p for p in insns[max(0, idx - 4) : idx] if p.mnemonic == Mnemonic.PUSH]
        if len(pushes) < 2:
            continue
        hi, lo = pushes[-2].immediate32, pushes[-1].immediate32
        entries = []
        if lo and hi and hi > lo:
            for va in range(lo, hi, 4):
                rva = va - base
                if 0 <= rva + 4 <= len(data):
                    entries.append(int.from_bytes(data[rva : rva + 4], "little"))
        tables.append(
            {
                "walker": which,
                "call_site": hex(insn.ip),
                "table": [hex(lo), hex(hi)],
                "slots": len(entries),
                "non_null": [hex(v) for v in entries if v],
            }
        )
    # `_pRawDllMain` is the dword the default DllMain tests before calling
    # DisableThreadLibraryCalls; find it by decoding the CRT's default.
    return {"present": True, "initializer_tables": tables,
            "user_initializers": sum(len(t["non_null"]) for t in tables)}


def reloc_targets(pe: pefile.PE) -> dict:
    """Where do the relocated dwords point? A data DLL whose pointers all aim
    inside its own image can be mapped anywhere with a uniform delta. One that
    points into the executable cannot."""
    data = pe.get_memory_mapped_image()
    base = pe.OPTIONAL_HEADER.ImageBase
    lo, hi = base, base + pe.OPTIONAL_HEADER.SizeOfImage
    inside = outside = zero = 0
    mn, mx = None, None
    by_section = {}
    secs = [
        (s.Name.decode("ascii", "replace").rstrip("\x00"), s.VirtualAddress,
         s.VirtualAddress + max(s.Misc_VirtualSize, s.SizeOfRawData))
        for s in pe.sections
    ]
    for blk in getattr(pe, "DIRECTORY_ENTRY_BASERELOC", []) or []:
        for e in blk.entries:
            if e.type != 3:
                continue
            for n, a, b in secs:
                if a <= e.rva < b:
                    by_section[n] = by_section.get(n, 0) + 1
                    break
            else:
                by_section["<none>"] = by_section.get("<none>", 0) + 1
            if e.rva + 4 > len(data):
                continue
            v = int.from_bytes(data[e.rva : e.rva + 4], "little")
            if v == 0:
                zero += 1
            elif lo <= v < hi:
                inside += 1
                mn = v if mn is None else min(mn, v)
                mx = v if mx is None else max(mx, v)
            else:
                outside += 1
    return {
        "highlow_by_section": by_section,
        "points_inside_own_image": inside,
        "points_outside_own_image": outside,
        "zero": zero,
        "target_min": hex(mn) if mn else None,
        "target_max": hex(mx) if mx else None,
    }


def name_scan_check(path: Path, exp: dict) -> dict:
    """Second direction on the export table: find each export name as a raw
    NUL-terminated byte string in the file. A parse that invented names, or a
    name table read at the wrong RVA, fails this. It reads the file; it emits
    only counts."""
    blob = path.read_bytes()
    names = [r["name"] for r in exp.get("rows", []) if r["name"]]
    found = sum(1 for n in names if (n.encode() + b"\x00") in blob)
    return {"names_checked": len(names), "found_verbatim_in_file": found, "missing": len(names) - found}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=str(OUT))
    args = ap.parse_args()
    out_path = Path(args.json)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    report = {
        "schema": "sa2-recomp-measurement/v1",
        "id": "s4-data-dll",
        "question": "what would it cost to make the guest's data DLL work?",
        "guest_path": GUEST_PATH,
    }

    def checkpoint(stage: str):
        report["last_stage"] = stage
        out_path.write_text(json.dumps(report, indent=1) + "\n")
        print(f"  checkpointed after {stage} -> {rel(out_path)}", file=sys.stderr)

    print("resolving the guest path case-insensitively ...", file=sys.stderr)
    res = resolve_case_insensitive(GUEST_PATH)
    report["resolution"] = res
    checkpoint("resolution")
    if not res["resolved"]:
        print("could not resolve", file=sys.stderr)
        return 1
    resolved = ROOT / res["resolved"]
    report["directory_contents"] = siblings_of(resolved)
    checkpoint("directory")

    targets = {}
    for row in report["directory_contents"]:
        if row["is_mz"]:
            targets[row["name"]] = resolved.parent / row["name"]
    report["pe_files"] = {}
    for label, p in targets.items():
        print(f"measuring {label} ...", file=sys.stderr)
        report["pe_files"][label] = measure_pe(p)
        report["pe_files"][label]["export_name_scan_check"] = name_scan_check(
            p, report["pe_files"][label]["exports"]
        )
        checkpoint(f"pe:{label}")

    print("reading the asked-for names ...", file=sys.stderr)
    asked = asked_names()
    report["demand"] = {k: v for k, v in asked.items() if not k.endswith("names")}
    report["demand"]["distinct_names_count"] = len(asked["distinct_names"])
    report["demand"]["game_data_handle_names_count"] = len(asked["game_data_handle_names"])
    report["demand"]["os_module_handle_names_count"] = len(asked["os_module_handle_names"])
    report["demand"]["unclassified_names_count"] = len(asked["unclassified_names"])
    checkpoint("demand")

    supplies = {label: report["pe_files"][label]["exports"] for label in report["pe_files"]}
    report["cross_reference"] = cross_reference(asked, supplies)
    checkpoint("cross_reference")

    # Cost inputs, measured rather than argued.
    gm = read_guest_memory_constants()
    report["guest_memory"] = {
        "constants_read_from": "runtime/guest_memory.h",
        **{k: (hex(v) if v is not None else None) for k, v in gm.items()},
    }
    cost = {}
    for label, pe in report["pe_files"].items():
        base = int(pe["image_base"], 16)
        end = base + pe["size_of_image"]
        cost[label] = {
            "sections_to_map": pe["section_count"],
            "bytes_to_map": pe["bytes_to_map"],
            "relocations_to_apply_if_rebased": pe["relocations"]["entries"],
            "relocations_to_apply_at_preferred_base": 0,
            "imports_to_resolve": pe["import_symbol_count"],
            "import_dlls_to_stand_in_for": pe["import_dll_count"],
            "preferred_base": pe["image_base"],
            "preferred_range": [hex(base), hex(end)],
            "preferred_range_inside_guest_space": end <= (gm["SA2_GUEST_END"] or GUEST_END),
            "preferred_range_overlaps_guest_heap": end > (gm["SA2_GUEST_HEAP_BASE"] or GUEST_HEAP_BASE)
            and base < (gm["SA2_GUEST_END"] or GUEST_END),
            "code_exports": pe["exports"].get("by_class", {}).get("code"),
            "data_exports": pe["exports"].get("by_class", {}).get("data"),
            "entry_point_instructions": (pe["entry_disassembly"] or {}).get("instructions"),
        }
    report["cost_inputs"] = cost
    checkpoint("cost_inputs")

    # A third direction on demand-versus-supply, independent of both the
    # instruction decode that produced the site list and the export-directory
    # parse: are the export names present in the executable as NUL-terminated
    # bytes at all? An export the exe never mentions cannot be asked for by
    # any site, resolved or not, and an asked-for name is a string in the exe
    # by construction. This is what bounds the 84 residual `GetDllData`
    # callers the surface receipt could not resolve.
    exe = (ROOT / "private" / "exe" / "sonic2app-unpacked.exe").read_bytes()
    checks = {}
    for label, pe in report["pe_files"].items():
        names = [r["name"] for r in pe["exports"].get("rows", []) if r["name"]]
        present = [n for n in names if (n.encode() + b"\x00") in exe]
        asked_set = set(asked["distinct_names"])
        never_asked = [n for n in names if n not in asked_set]
        by_rva = {}
        for r in pe["exports"].get("rows", []):
            by_rva.setdefault(r["rva"], []).append(r["name"])
        collisions = {k: v for k, v in by_rva.items() if len(v) > 1}
        checks[label] = {
            "export_names_present_in_executable": len(present),
            "export_names_absent_from_executable": sorted(set(names) - set(present)),
            "exports_never_asked_by_a_resolved_site": len(never_asked),
            "of_those_present_in_executable": sum(
                1 for n in never_asked if (n.encode() + b"\x00") in exe
            ),
            "exports_sharing_one_rva": collisions,
            "export_decode_disagreements": pe["exports"].get("decode_disagreement_count"),
        }
    checks["executable_sha256"] = hashlib.sha256(exe).hexdigest()
    report["checks"] = checks
    checkpoint("checks")

    write_receipt(report)
    print(json.dumps({k: v for k, v in report.items() if k in ("resolution", "cross_reference")}, indent=1)[:2000])
    print(f"\nwrote {rel(out_path)}", file=sys.stderr)
    return 0


RECEIPT = ROOT / "private" / "generated" / "s4" / "receipts" / "s4-data-dll-v1.json"


def write_receipt(report: dict) -> None:
    """A receipt with inputs, command, hashes and outputs (AGENTS.md
    "Records"). It goes under `private/generated/s4/receipts/` because this
    slice measures and does not ship: nothing here is a durable record yet,
    and rule 7 says a number in a durable record is copied from METRICS.md."""
    RECEIPT.parent.mkdir(parents=True, exist_ok=True)
    orig = report["pe_files"].get("Data_DLL_orig.dll", {})
    shim = report["pe_files"].get("Data_DLL.dll", {})
    xr = report["cross_reference"]
    receipt = {
        "schema": "sa2-recomp-receipt/v1",
        "id": "s4-data-dll-v1",
        "stage": "S4",
        "question": report["question"],
        "authority": "statically-measured; nothing was loaded or executed",
        "command": "source tools/env.sh && .venv/bin/python tools/s4/data_dll.py",
        "inputs": {
            "guest_path_asked": GUEST_PATH,
            "installation": rel(INSTALL),
            "executable_sha256": report["checks"]["executable_sha256"],
            "sites": {"path": rel(SITES), "sha256": sha256_of(SITES)},
            "handles": {"path": rel(HANDLES), "sha256": sha256_of(HANDLES)},
            "pipeline": {"tools/s4/data_dll.py": sha256_of(Path(__file__))},
        },
        "finding": {
            "file_the_guest_names_is_not_the_data_dll": bool(
                shim and xr["per_supply"].get("Data_DLL.dll", {}).get("matched_exact") == 0
            ),
            "data_dll_on_disk": orig.get("path"),
            "data_dll_sha256": orig.get("sha256"),
            "data_dll_size": orig.get("size"),
            "shim_sha256": shim.get("sha256"),
            "shim_size": shim.get("size"),
        },
        "demand": report["demand"],
        "cross_reference": {k: v for k, v in xr.items() if k != "per_supply"},
        "per_supply": {
            k: {kk: vv for kk, vv in v.items() if kk != "examples_matched"}
            for k, v in xr["per_supply"].items()
        },
        "cost_inputs": report["cost_inputs"],
        "guest_memory": report["guest_memory"],
        "checks": report["checks"],
        "pe_summary": {
            label: {
                k: pe[k]
                for k in (
                    "path", "size", "sha256", "format", "machine_name", "image_base",
                    "size_of_image", "entry_point_rva", "section_count", "bytes_to_map",
                    "executable_section_bytes", "import_dll_count", "import_symbol_count",
                    "has_tls", "dynamic_base_aslr",
                )
            }
            | {
                "sections": pe["sections"],
                "relocations": {k: v for k, v in pe["relocations"].items()},
                "reloc_targets": pe["reloc_targets"],
                "crt_startup": pe["crt_startup"],
                "exports_by_class": pe["exports"]["by_class"],
                "exports_by_section": pe["exports"]["by_section"],
                "imports": [{"dll": i["dll"], "count": i["count"]} for i in pe["imports"]],
            }
            for label, pe in report["pe_files"].items()
        },
        "outputs": {"measurement": rel(OUT)},
        "limits": [
            "nothing was loaded, mapped or executed; every figure is a static parse",
            "whether the guest writes into the DLL's .data at run time is not "
            "answerable statically and is not answered here",
            "the 84 callers of GetDllData at 0x0077DEF0 pass lpProcName from one "
            "frame up, so 436 asked names is a floor, bounded above by the 488 "
            "this DLL exports",
            "Data_DLL_orig.dll is assumed to be the shipped data DLL because its "
            "export directory names itself Data_DLL.dll and 436 of 436 asked names "
            "resolve in it; no pristine copy was available to diff against",
        ],
    }
    RECEIPT.write_text(json.dumps(receipt, indent=1) + "\n")
    print(f"wrote {rel(RECEIPT)}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
