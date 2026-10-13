#!/usr/bin/env python3
"""Gate: no two synthetic value regions overlap.

What this gate is for
---------------------
The runtime hands the guest values it invented: handles above the guest
address space, synthetic COM objects and vtables inside it, and the thunk
addresses imports and interface methods are dispatched on. Each region exists
so that **a value of the wrong kind is a recognisable value rather than a
plausible one** -- every one of the headers says so in those words or close to
them.

Two regions sharing a range destroys exactly that property, silently, and
nothing else in the project looks for it.

What it cost to learn that
--------------------------
On 2026-09-21 `SA2_VFS_HANDLE_BASE` and `SA2_WND_HWND_BASE` were both
0x7e000000. With a file-handle stride of 0x1000 against a window-token stride
of 0x10, both allocators issue the same first value: **the first open file
handle and the first window handle were bit-identical.** `IsWindow` answered
true for a file handle, `sa2_window_of` found window zero for one, and the VFS
row lookup accepted the main HWND as file handle zero.

It had been that way for slices. It was found by a unit adding a *fourth* set
of tokens and listing what was already taken -- and in the same hour a second
unit put `SA2_WND_HDC_BASE` on top of `SA2_D3D9_HMONITOR_BASE`, which is the
same mistake happening again while the first one was being written up. Three
regions had been added by three authors who each checked the headers they
happened to open.

How it checks
-------------
`Documentation/handle-regions.json` declares each region by **the names of the
constants**, not by their values, and this script reads each value out of the
header it is declared in. So moving a constant is caught, renaming one is
caught, and the declaration cannot quietly disagree with the code the way a
copied number would.

Then two things must hold:

* **No two regions overlap.** Half-open ranges [base, base + stride * count).
* **Every `*_BASE` in the handle space is declared.** A new region added to a
  header and not to the declaration fails the gate, which is the clause that
  makes this a check rather than a snapshot: without it, the next author adds
  a fourth base and the gate says nothing.

Falsification runs on every pass, against a temporary copy of the declaration.

Usage: .venv/bin/python Documentation/scripts/check_handle_regions.py
"""

from __future__ import annotations

import copy
import json
import re
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECL = ROOT / "Documentation" / "handle-regions.json"

# The address space above the guest, where handles live. `SA2_GUEST_END` is
# 0x20000000, so anything at or above this is a handle by construction.
#
# **It was 0x7d000000, which was the lowest base that existed at the time --
# and that is exactly the wrong way to choose it.** Adding a token at
# 0x7c000000 put it *below* the threshold, so the undeclared-base clause
# stopped seeing it and the "a region dropped" falsification started
# surviving: the gate could no longer tell that a declared region had gone
# missing. A bound set to the lowest thing currently inside it excludes the
# next thing added below it, silently. 0x70000000 is well clear of
# SA2_GUEST_END and well below every handle region, so it is a property of
# the address-space layout rather than a high-water mark of the declaration.
HANDLE_SPACE = 0x70000000

DEFINE = re.compile(r"^#define\s+(SA2_[A-Z0-9_]+)\s+(0x[0-9a-fA-F]+|\d+)u?\s*(?:/\*|$)",
                    re.M)


def constants() -> dict[str, dict[str, int]]:
    """Every simple integer #define in the runtime, by file then by name.

    Only literal defines are read. A constant defined as an expression over
    other constants is deliberately not resolved: the declaration names the
    operands instead, so the arithmetic is visible in the record rather than
    reimplemented here from a header this script would then have to parse as C.
    """
    out: dict[str, dict[str, int]] = {}
    # Headers *and* sources. Two bases are defined in a .c file rather than a
    # header -- SA2_HANDLE_BASE in sa2_imports.c and SA2_METRICS_AGENT_TOKEN in
    # sa2_iface.c -- because nothing outside those files needs them. Scanning
    # headers alone would have let either move, or a third appear, unseen.
    for p in sorted((ROOT / "runtime").glob("*.h")) + sorted((ROOT / "runtime").glob("*.c")):
        rel = str(p.relative_to(ROOT))
        out[rel] = {m.group(1): int(m.group(2), 0) for m in DEFINE.finditer(p.read_text())}
    return out


def resolve(spec, consts: dict[str, int], where: str,
            allc: dict[str, dict[str, int]] | None = None) -> int:
    """A literal, a constant name, or a list of those to multiply.

    A name may be qualified as `runtime/other.h:SA2_THING` when the arithmetic
    genuinely crosses headers -- the interface thunk block is sized by d3d9's
    slot count, because d3d9 has the widest vtable. Unqualified names must be
    in the region's own header, so a constant cannot be picked up by accident
    from wherever it happens to also be defined.
    """
    if isinstance(spec, int):
        return spec
    if isinstance(spec, list):
        n = 1
        for s in spec:
            n *= resolve(s, consts, where, allc)
        return n
    if ":" in spec:
        hdr, _, name = spec.partition(":")
        if allc is None or hdr not in allc:
            raise KeyError(f"{where}: {hdr!r} is not a runtime file this script reads")
        if name not in allc[hdr]:
            raise KeyError(f"{where}: {name} is not a literal #define in {hdr}")
        return allc[hdr][name]
    if spec not in consts:
        raise KeyError(f"{where}: {spec} is not a literal #define in that file")
    return consts[spec]


def problems(decl_path: Path | None = None) -> list[str]:
    out: list[str] = []
    path = decl_path or DECL
    if not path.exists():
        return [f"the declaration {path} does not exist"]
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        return [f"the declaration is not JSON: {e}"]
    if doc.get("schema") != "sa2-recomp-handle-regions/v1":
        out.append(f"schema is {doc.get('schema')!r}, "
                   "expected 'sa2-recomp-handle-regions/v1'")

    allc = constants()
    spans, declared_bases = [], set()
    for r in doc.get("regions", []):
        name, hdr = r.get("name", "?"), r.get("header", "")
        if hdr not in allc:
            out.append(f"{name}: {hdr!r} is not a runtime header this script reads")
            continue
        consts = allc[hdr]
        try:
            base = resolve(r["base"], consts, name, allc)
            stride = resolve(r["stride"], consts, name, allc)
            count = resolve(r["count"], consts, name, allc)
        except KeyError as e:
            out.append(str(e))
            continue
        if stride <= 0 or count <= 0:
            out.append(f"{name}: stride {stride} count {count} spans nothing, so "
                       "it can never overlap and the declaration is inert")
            continue
        declared_bases.add((hdr, r["base"]) if isinstance(r["base"], str) else (hdr, ""))
        spans.append((base, base + stride * count, name, hdr, r["base"]))

    spans.sort()
    for i in range(len(spans) - 1):
        a, b = spans[i], spans[i + 1]
        if b[0] < a[1]:
            out.append(
                f"{a[2]} ({a[4]} = {a[0]:#010x}, ends {a[1]:#010x}) overlaps "
                f"{b[2]} ({b[4]} = {b[0]:#010x}). A value in the overlap is a "
                "plausible value of two different kinds, which is the one thing "
                "these regions exist to prevent.")

    # Undeclared bases. Without this clause the gate is a snapshot: a fourth
    # region added to a header would sit outside it and never be compared.
    #
    # **Matched on the value, not on the name.** This clause first required the
    # name to end in `_BASE`, and `SA2_METRICS_AGENT_TOKEN` -- a single handle
    # value, in the handle space, exactly the thing the clause exists to catch
    # -- slipped straight past it. A check keyed on a naming convention tests
    # the convention. Anything in the handle space is a region unless it is an
    # end marker or a size, and those three suffixes are excluded by name
    # because they are the only kinds of constant that legitimately carry an
    # address-shaped value without being one.
    for hdr, consts in allc.items():
        for cname, val in consts.items():
            if val < HANDLE_SPACE or val >= 0x80000000:
                continue
            if cname.endswith(("_END", "_STRIDE", "_MAX")):
                continue
            if (hdr, cname) not in declared_bases:
                out.append(f"{hdr} defines {cname} = {val:#010x} in the handle "
                           "space and Documentation/handle-regions.json does not "
                           "declare it, so nothing compares it to anything")
    return out


# How many falsifications ran, set by falsifications() from the cases it
# actually built. It was a literal in the summary line, which is the same
# defect this project catalogues as "a counter incremented before the thing it
# counts": adding a case without touching the literal would have printed a
# number that was simply wrong, and a gate that misreports its own rigour is
# worse than one that does not report it.
RAN = 0


def falsifications() -> list[str]:
    doc = json.loads(DECL.read_text())
    if len(doc.get("regions", [])) < 2:
        return ["fewer than two regions are declared, so no overlap can be "
                "constructed and the falsifications cannot run"]

    def mutate(name, f):
        d = copy.deepcopy(doc)
        f(d)
        return name, d

    def collide(d):
        """Point the second region's base at the first one's constant."""
        d["regions"][1]["header"] = d["regions"][0]["header"]
        d["regions"][1]["base"] = d["regions"][0]["base"]

    cases = [
        mutate("two regions given the same base constant", collide),
        mutate("a region dropped from the declaration",
               lambda d: d["regions"].pop(0)),
        mutate("a base renamed to a constant that does not exist",
               lambda d: d["regions"][0].__setitem__("base", "SA2_NO_SUCH_BASE")),
        mutate("a region's count zeroed",
               lambda d: d["regions"][0].__setitem__("count", 0)),
        mutate("a header changed to one this script does not read",
               lambda d: d["regions"][0].__setitem__("header", "runtime/nope.h")),
        mutate("the schema changed",
               lambda d: d.__setitem__("schema", "something-else/v1")),
    ]
    global RAN
    RAN = len(cases)

    survived = []
    with tempfile.TemporaryDirectory() as td:
        for name, d in cases:
            p = Path(td) / "regions.json"
            p.write_text(json.dumps(d))
            if not problems(p):
                survived.append(name)
    return [f"falsification survived: {s} -- the gate passed a declaration it "
            "should have rejected" for s in survived]


def main() -> int:
    failed = falsifications()
    if failed:
        for f in failed:
            print(f"  {f}")
        print(f"HANDLE_REGIONS_FAILED falsifications={len(failed)}")
        return 1
    probs = problems()
    if probs:
        for p in probs:
            print(f"  {p}")
        print(f"HANDLE_REGIONS_FAILED problems={len(probs)}")
        return 1
    n = len(json.loads(DECL.read_text())["regions"])
    print(f"HANDLE_REGIONS_OK regions={n} overlaps=0 undeclared=0 falsifications={RAN}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
