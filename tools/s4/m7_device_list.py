#!/usr/bin/env python3
"""The M7 list the recording d3d9 device holds its closed world to.

Subtitle: M7's called set, turned into the (interface, vtable slot) table
`runtime/sa2_d3d9.c` traps every other method by, and checked against both.

Command:
  source tools/env.sh && .venv/bin/python tools/s4/m7_device_list.py          # write
  source tools/env.sh && .venv/bin/python tools/s4/m7_device_list.py --check  # exit 1 when stale

What it does
------------
The user's ruling of 2026-10-09 (Documentation/decisions/2026-10-09-S5-user-rulings.md,
item 1) closes the device's world: every Direct3D method not on the M7 list traps
by default and names the method. The list is the `called` set of
`Documentation/receipts/d3d9-methods.json` (tools/s4/m7_inputs.py writes it). This
tool reads that set and writes `runtime/sa2_d3d9_m7_list.h`, one row per device
method: the interface's index in the device's `g_types` table, the vtable slot its
row has there, and both names, so the device can check at start-up that every row
still names the method it was generated from.

The vtable layout is the device's own, parsed by `tools/s4/d3d9_methods.py`
`parse_vtables`, the one reader the scanner uses too. Nothing about Direct3D is
typed here.

The `d3dx9_42!` names on the list are entry points, not methods: the import table
closes them (an import with no row in runtime/sa2_imports.c `g_stubs` traps by
name). This tool checks that every d3dx9 row there is on the list, so no D3DX
name outside it can reach the device, and writes them into the header's comment
only.

`--check` exits 1 when the header on disk is not what this tool would write now
(stale against the receipt or the table), when a listed name is one the device
table lacks, or when a d3dx9 import answers a name the list does not hold. The
output is deterministic: rows sorted by (interface index, slot), no clock. A write
leaves the file alone (its mtime too, which tools/s4/link_s4.py rebuilds every
runtime object by) when its text would not change.

The header's digest is the sha256 of its own rows (`rows_digest`), which the device
recomputes from the rows it was compiled with and refuses to run on when they
disagree; the row count is a compile-time assertion. A row added or dropped by hand
therefore stops the build or the run, and the digest a run record carries names the
rows that run held.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import d3d9_methods as DM  # noqa: E402  the device table's one parser

ROOT = HERE.parents[1]
RECEIPT = ROOT / "Documentation" / "receipts" / "d3d9-methods.json"
D3D9_C = ROOT / "runtime" / "sa2_d3d9.c"
IMPORTS_C = ROOT / "runtime" / "sa2_imports.c"
HEADER = ROOT / "runtime" / "sa2_d3d9_m7_list.h"
D3DX_PREFIX = "d3dx9_42!"

TYPES_RE = re.compile(r"static d3d_type g_types\[\]\s*=\s*\{(.*?)\n\};", re.S)
TYPE_ROW_RE = re.compile(r"\{\s*\"(IDirect3D[A-Za-z0-9]*|ID3DXConstantTable)\"\s*,\s*(ROWS_[A-Z0-9_]+)")
STUB_RE = re.compile(r"\{\s*\"d3dx9_42\.dll\"\s*,\s*\"([A-Za-z0-9_]+)\"")


class ListError(Exception):
    """A list this tool refuses to write or to pass."""


def rel(p: Path) -> str:
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def device_table(d3d9_c: Path) -> tuple[list[str], dict[str, dict[int, dict]]]:
    """(interfaces in `g_types` order, {interface: {slot: {"name", "argc"}}}) of the
    device. The order is the type index the device dispatches by."""
    ifaces, _ = DM.parse_vtables(d3d9_c)
    m = TYPES_RE.search(d3d9_c.read_text(encoding="utf-8"))
    if not m:
        raise ListError(f"{rel(d3d9_c)}: cannot find g_types[]")
    order = [name for name, _rows in TYPE_ROW_RE.findall(m.group(1))]
    if not order:
        raise ListError(f"{rel(d3d9_c)}: g_types[] names no interface")
    return order, {t: ifaces[t] for t in order}


def d3dx_stubs(imports_c: Path) -> list[str]:
    """Every d3dx9_42 entry point runtime/sa2_imports.c answers (its g_stubs rows)."""
    return sorted(set(STUB_RE.findall(imports_c.read_text(encoding="utf-8"))))


def rows_text(rows: list[tuple[int, int, str, str]]) -> str:
    """The rows as the device hashes them (runtime/sa2_d3d9.c, list_rows_digest): one
    line per row, in the header's order, "<type> <slot> <interface>::<method>"."""
    return "".join(f"{t} {s} {iface}::{method}\n" for t, s, iface, method in rows)


def rows_digest(rows: list[tuple[int, int, str, str]]) -> str:
    """sha256 of `rows_text`: the list's identity, the same for a receipt rewritten with
    the same names, and the value the device checks its compiled rows against."""
    return hashlib.sha256(rows_text(rows).encode()).hexdigest()


def build(receipt: Path = RECEIPT, d3d9_c: Path = D3D9_C,
          imports_c: Path = IMPORTS_C) -> tuple[str, dict]:
    """The header's text and a summary. Raises ListError on a listed name the
    device table lacks, a name of no known shape, or a d3dx9 import off the list."""
    called = json.loads(receipt.read_text(encoding="utf-8")).get("called")
    if not isinstance(called, list) or not all(isinstance(n, str) for n in called):
        raise ListError(f"{rel(receipt)}: `called` is not a list of names")
    if len(set(called)) != len(called):
        raise ListError(f"{rel(receipt)}: `called` repeats a name")
    order, table = device_table(d3d9_c)
    rows, d3dx, problems = [], [], []
    for name in sorted(called):
        if name.startswith(D3DX_PREFIX):
            d3dx.append(name[len(D3DX_PREFIX):])
            continue
        iface, sep, method = name.partition("::")
        if not sep or not iface or not method:
            problems.append(f"{name!r} is neither interface::method nor {D3DX_PREFIX}name")
            continue
        if iface not in table:
            problems.append(f"{name}: the device table has no interface {iface}")
            continue
        slots = [s for s, r in table[iface].items() if r["name"] == method]
        if len(slots) != 1:
            problems.append(f"{name}: the device table has {len(slots)} rows named {method} "
                            f"in {iface}, not one")
            continue
        rows.append((order.index(iface), slots[0], iface, method))
    answered = d3dx_stubs(imports_c)
    off_list = sorted(set(answered) - set(d3dx))
    if off_list:
        problems.append(f"runtime/sa2_imports.c answers d3dx9 entry points off the list, so a "
                        f"D3DX name outside it can reach the device: {off_list}")
    if problems:
        raise ListError("; ".join(problems))
    rows.sort()
    digest = rows_digest(rows)
    lines = [
        "/* runtime/sa2_d3d9_m7_list.h: GENERATED by tools/s4/m7_device_list.py; do not edit.",
        " *",
        " * The M7 list, which the recording device's closed world holds every method call to",
        " * (runtime/sa2_d3d9.c, the closed world; the user's ruling of 2026-10-09, item 1):",
        " * a method with no row here traps by name in a gate or an automated run, and is",
        " * logged and reported at exit in live play.",
        " *",
        " *   write: source tools/env.sh && .venv/bin/python tools/s4/m7_device_list.py",
        " *   check: source tools/env.sh && .venv/bin/python tools/s4/m7_device_list.py --check",
        " *",
        f" * Source: {rel(receipt)}, field `called`: {len(called)} names, {len(rows)} device",
        f" * methods below and {len(d3dx)} d3dx9 entry points, which the import table closes",
        " * (runtime/sa2_imports.c g_stubs answers no other d3dx9 name):",
    ]
    for n in d3dx:
        lines.append(f" *   {D3DX_PREFIX}{n}")
    lines += [
        " * Each row's type is the interface's index in g_types and its slot the vtable slot",
        " * of its row there, as tools/s4/d3d9_methods.py parse_vtables reads them; the device",
        " * checks both names against its own table before it answers a call.",
        " */",
        "#ifndef SA2_RUNTIME_D3D9_M7_LIST_H",
        "#define SA2_RUNTIME_D3D9_M7_LIST_H",
        "",
        "#include <stdint.h>",
        "",
        "/* sha256 of the rows below, each as the line \"<type> <slot> <interface>::<method>\"",
        " * with a newline, in this order. runtime/sa2_d3d9.c recomputes it from the rows it",
        " * was compiled with before the first call and stops the run when they differ. */",
        f"#define SA2_D3D9_M7_LIST_DIGEST \"{digest}\"",
        f"#define SA2_D3D9_M7_LIST_NAMES  {len(called)}u   /* device methods and d3dx9 entry points */",
        f"#define SA2_D3D9_M7_LIST_ROWS   {len(rows)}u",
        "",
        "typedef struct {",
        "    uint8_t     type;     /* index into g_types */",
        "    uint8_t     slot;     /* vtable slot */",
        "    const char *iface;",
        "    const char *method;",
        "} sa2_d3d9_m7_row;",
        "",
        "static const sa2_d3d9_m7_row SA2_D3D9_M7_LIST[] = {",
    ]
    for t, s, iface, method in rows:
        lines.append(f"    {{{t:2d}, {s:3d}, \"{iface}\", \"{method}\"}},")
    lines += ["};",
              "_Static_assert(sizeof SA2_D3D9_M7_LIST / sizeof SA2_D3D9_M7_LIST[0] == "
              "SA2_D3D9_M7_LIST_ROWS,",
              "               \"runtime/sa2_d3d9_m7_list.h: the row count is not the rows; run "
              "tools/s4/m7_device_list.py\");",
              "", "#endif", ""]
    summary = {"names": len(called), "device_rows": len(rows), "d3dx9": len(d3dx),
               "digest": digest, "d3dx9_answered": answered}
    return "\n".join(lines), summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help=f"exit 1 when {rel(HEADER)} is not what this tool writes now")
    ap.add_argument("--receipt", type=Path, default=RECEIPT)
    ap.add_argument("--d3d9-c", type=Path, default=D3D9_C)
    ap.add_argument("--imports-c", type=Path, default=IMPORTS_C)
    ap.add_argument("--header", type=Path, default=HEADER)
    a = ap.parse_args(argv)
    try:
        text, summary = build(a.receipt, a.d3d9_c, a.imports_c)
    except ListError as e:
        print(f"M7LIST refused: {e}", file=sys.stderr)
        return 1
    if a.check:
        have = a.header.read_text(encoding="utf-8") if a.header.is_file() else None
        if have != text:
            why = "missing" if have is None else "stale"
            print(f"M7LIST {rel(a.header)} is {why} against {rel(a.receipt)} and "
                  f"{rel(a.d3d9_c)}; run: source tools/env.sh && .venv/bin/python "
                  f"tools/s4/m7_device_list.py", file=sys.stderr)
            return 1
        print(f"M7LIST ok: {summary['device_rows']} device methods, {summary['d3dx9']} d3dx9 "
              f"entry points, digest {summary['digest'][:16]}")
        return 0
    if a.header.is_file() and a.header.read_text(encoding="utf-8") == text:
        print(f"M7LIST {rel(a.header)} is current: {summary['device_rows']} device methods, "
              f"{summary['d3dx9']} d3dx9 entry points, digest {summary['digest'][:16]}")
        return 0
    a.header.write_text(text, encoding="utf-8")
    print(f"M7LIST wrote {rel(a.header)}: {summary['device_rows']} device methods, "
          f"{summary['d3dx9']} d3dx9 entry points, digest {summary['digest'][:16]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
