#!/usr/bin/env python3
"""Every import the runtime refuses, classified against design 3.5.

Why this exists
---------------
The DirectSound slice found a refusal five call sites away from its damage: a
recording device that returned failure instead of succeeding and recording
disabled CRI's *file system*, and the game stopped in `adxf_GetPtStat`. Design
3.5 classifies dsound as **presentation**, and S4's work list says
"presentation stubs as no-ops that record calls" -- so the refusal was never a
policy choice, it was a nonconformance nobody had a way to see.

One refusal being wrong is a bug. No way to enumerate the refusals is the
defect, because the next one is found the same expensive way. This enumerates
them, and `Documentation/scripts/check_refused_presentation.py` turns the
enumeration into a gate.

What counts as a refusal
------------------------
Three kinds, each derived from the runtime's own source so it cannot drift
from what is compiled:

* `no-stub`   -- an IAT slot with no row in `g_stubs`. `import_native` records
                 the call and raises a trap naming DLL, symbol and call site.
* `null-impl` -- a `g_dyn_table` row whose implementation is `NULL`. The name
                 resolves through `GetProcAddress` to a thunk that traps.
* `policy`    -- a stub that exists and refuses by policy: it answers with
                 failure, or with a success whose answer carries none of
                 native's (EnumSystemLocalesA's TRUE with no callback was one
                 until 2026-09-26), or it traps on arguments a replay's native
                 side passes (code page 936's conversions were, until the same
                 day); `Documentation/refusal-reasons.json`'s
                 `about` has the definition. The runtime marks each such answer,
                 or the trap path before it traps, with a call of
                 `sa2_import_refused("DLL", "Name", code)`, which records the
                 refusal against the import and returns the code, and this
                 audit reads those calls out of `runtime/*.c`.

**Until any marker exists, policy refusals are declared, not derived.** The
marker is added to the runtime at integration; before the first one, the only
record of a policy refusal is its entry in the reasons file, so an entry of
kind `policy` is taken at its word, and deleting one makes its refusal
invisible. From the first marker on, a policy refusal is exactly a marked
import: a marker with no entry is unexcused, and a policy entry with no marker
is stale. Which regime ran is in the result (`marker_checks`) and in the gate's
summary line, so the weaker one is never mistaken for the stronger.

A marker must be a direct call with two string literals, so the audit can name
what it marks; a call through variables is refused by name, as is a marker
naming an import the runtime does not implement. Calls inside comments and
string literals are not markers, and the function's own declaration and
definition are not calls.

The join, and the two normalisations in it
------------------------------------------
`Documentation/receipts/import-classification.json` is the S1 census: 251
slots, each with a design-3.5 class. The runtime's table is joined to it on
(module, name). Module comparison strips a path and a `.dll` suffix and is
case-blind, because "KERNEL32.dll" and "KERNEL32" name the same module.

**Symbol comparison is case-sensitive, with exactly two exceptions**, both for
imports by ordinal, which have no name, so each side invents one:

* the invented forms differ in capitalisation -- `Ordinal#11` in the runtime,
  `ordinal#11` in the receipt. The first version of this audit matched exactly
  and reported DSOUND!Ordinal#11, which *is* implemented, as a refused
  presentation import;
* for OLEAUT32 (and ws2_32 and wsock32) the census's pefile filled the name in
  from its own ordinal table -- `SysAllocString` for what the executable
  imports as ordinal 2, and the runtime names `Ordinal#2`. Joined as written,
  the census's SysAllocString and SysFreeString would stay "refused" after
  their stubs landed. A name pefile's table gives an ordinal of that module is
  keyed as that ordinal, on both sides.

An audit that invents work is worse than none, so the ordinal forms are
normalised and nothing else is: lowercasing every symbol would let
`getversion` match `GetVersion` and hide a real miss behind a typo.

Usage
-----

    .venv/bin/python tools/s4/refusal_audit.py [--json] [--out PATH]

Prints summary lines prefixed `REFUSAL_AUDIT`.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "runtime" / "sa2_imports.c"
CENSUS = ROOT / "Documentation" / "receipts" / "import-classification.json"
REASONS = ROOT / "Documentation" / "refusal-reasons.json"
RUNTIME = ROOT / "runtime"

ROW = re.compile(r'^\s*\{\s*"([^"]+)",\s*"([^"]+)",\s*(\d+),\s*(SA2_IMP_\w+),\s*(\w+)?')
ORDINAL = re.compile(r"^ordinal#(\d+)$", re.IGNORECASE)
MARKER = "sa2_import_refused"
LITERAL = re.compile(r'"((?:[^"\\\n]|\\.)*)"\Z')
KINDS = ("no-stub", "null-impl", "policy")


def module_key(name: str) -> str:
    """Strip a path and a `.dll` suffix, upper-case. Mirrors `module_key` in
    sa2_imports.c, which is the function the runtime itself matches with."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    if base.lower().endswith(".dll"):
        base = base[:-4]
    return base.upper()


def _pefile_ordinal_names() -> dict[str, dict[str, int]]:
    """pefile's own ordinal-to-name tables, reversed, by module key."""
    import ordlookup      # shipped with pefile, which fills `imp.name` from it
    return {module_key(mod.decode()): {name.decode(): o for o, name in names.items()}
            for mod, names in ordlookup.ords.items()}


ORDINAL_NAMES = _pefile_ordinal_names()


def symbol_key(name: str, module: str = "") -> str:
    """Case-sensitive, except for the invented ordinal forms. See the header."""
    m = ORDINAL.match(name)
    if m:
        return f"ordinal#{m.group(1)}"
    o = ORDINAL_NAMES.get(module, {}).get(name)
    return f"ordinal#{o}" if o is not None else name


def key(dll: str, name: str) -> tuple[str, str]:
    mod = module_key(dll)
    return (mod, symbol_key(name, mod))


def table(lines: list[str], opener: str) -> list[dict]:
    """Rows of a `static const ... X[] = {` table, up to its closing `};`.

    The opener must carry its `= {`: both tables have a forward declaration
    earlier in the file, and matching that instead would run the scan from the
    declaration through every intervening line.

    **A row is accumulated until its braces balance, not read one line at a
    time.** C lets an initialiser wrap, both tables already wrap some rows, and
    the first version of this parser matched a single line -- so the four
    `SteamMetricsSDK` rows, whose decorated names are long enough to push the
    argc onto the next line, were silently absent from the table. It counted
    156 rows where there were 160 and reported four implemented imports as
    refused. A parser that drops what it cannot fit on a line reports an
    absence that is its own and not the code's.
    """
    try:
        start = next(i for i, ln in enumerate(lines) if ln.startswith(opener + " = {"))
    except StopIteration:
        raise SystemExit(f"REFUSAL_AUDIT_FAILED no table {opener!r} in {SRC}")
    out, buf, depth, first = [], "", 0, 0
    for i in range(start + 1, len(lines)):
        ln = lines[i]
        if depth == 0 and ln.strip().startswith("};"):
            if buf.strip():
                raise SystemExit(f"REFUSAL_AUDIT_FAILED table {opener!r} ends "
                                 f"mid-row at line {i + 1}")
            return out
        if depth == 0 and not ln.lstrip().startswith("{"):
            continue                      # a comment or a blank line between rows
        if depth == 0:
            first = i + 1
        buf += " " + ln.strip()
        depth += ln.count("{") - ln.count("}")
        if depth > 0:
            continue
        m = ROW.match(buf)
        if m:
            out.append({"dll": m.group(1), "name": m.group(2),
                        "argc": int(m.group(3)), "conv": m.group(4),
                        "impl": m.group(5), "line": first})
        elif buf.strip() not in ("{NULL, NULL, 0, 0, NULL},", "{ NULL, NULL, 0, 0, NULL },"):
            raise SystemExit(f"REFUSAL_AUDIT_FAILED line {first} of {opener!r} is "
                             f"a row this parser cannot read: {buf.strip()[:90]}")
        buf, depth = "", 0
    raise SystemExit(f"REFUSAL_AUDIT_FAILED table {opener!r} is unterminated")


# ---------------------------------------------------------------- markers

def _mask(src: str) -> tuple[str, str]:
    """(code, text): `text` is `src` with comments blanked; `code` also blanks the
    inside of every string and character literal. Both keep every offset and
    newline, so a position found in one is the same position in the other."""
    code, text, i, n = [], [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank = "".join("\n" if ch == "\n" else " " for ch in src[i:j])
            code.append(blank)
            text.append(blank)
            i = j
        elif src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            code.append(" " * (j - i))
            text.append(" " * (j - i))
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            j = min(j + 1, n)
            code.append(c + " " * max(j - i - 2, 0) + (src[j - 1] if j - i >= 2 else ""))
            text.append(src[i:j])
            i = j
        else:
            code.append(c)
            text.append(c)
            i += 1
    return "".join(code), "".join(text)


def _args(text: str, open_paren: int) -> tuple[list[str], int] | None:
    """The top-level arguments of the call whose `(` is at `open_paren`, and the
    offset past its `)`; None when the parentheses never close."""
    depth, i, start, out, n = 0, open_paren, open_paren + 1, [], len(text)
    while i < n:
        c = text[i]
        if c in "\"'":
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == "\\" else 1
            i = j + 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
            if depth == 0:
                out.append(text[start:i].strip())
                return out, i + 1
        elif c == "," and depth == 1:
            out.append(text[start:i].strip())
            start = i + 1
        i += 1
    return None


def markers(sources: list[Path] | None = None) -> list[dict]:
    """Every `sa2_import_refused("DLL", "Name", code)` call in `sources`
    (default: runtime/*.c), in file and line order. See the header for what is
    refused rather than read."""
    paths = sorted(RUNTIME.glob("*.c")) if sources is None else sorted(sources)
    out = []
    for path in paths:
        code, text = _mask(path.read_text(errors="replace"))
        for m in re.finditer(r"\b" + MARKER + r"\s*\(", code):
            line = code.count("\n", 0, m.start()) + 1
            before = code[:m.start()].rstrip()
            prev = re.search(r"([A-Za-z_][A-Za-z_0-9]*|\*)\Z", before)
            parsed = _args(text, m.end() - 1)
            if parsed is None:
                raise SystemExit(f"REFUSAL_AUDIT_FAILED {path}:{line}: {MARKER}( never closes")
            args, _ = parsed
            dll, name = (LITERAL.match(a) for a in (args + ["", ""])[:2])
            if len(args) == 3 and dll and name:
                out.append({"dll": dll.group(1), "name": name.group(1), "code": args[2],
                            "file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT)
                            else str(path), "line": line})
                continue
            if prev and prev.group(1) not in ("return", "else", "do"):
                continue      # a declaration or the definition: typed parameters, a type before it
            raise SystemExit(f"REFUSAL_AUDIT_FAILED {path}:{line}: {MARKER} must be called "
                             "with the DLL and the name as string literals and a code, so "
                             f"the audit can name what it marks; got ({', '.join(args)})")
    return out


# ---------------------------------------------------------------- the audit

def audit(reasons_path: Path | None = None, sources: list[Path] | None = None,
          src: Path | None = None) -> dict:
    """The refusals of the runtime whose import tables are in `src` (default
    runtime/sa2_imports.c), with markers read from `sources` (default
    runtime/*.c), against the reasons file at `reasons_path`. `src` exists so a
    test can hold the gate to a runtime whose tables are not the tree's."""
    reasons_path = reasons_path or REASONS
    lines = (src or SRC).read_text().split("\n")
    stubs = table(lines, "static const stub_row g_stubs[]")
    dyn = table(lines, "static const dyn_row g_dyn_table[]")
    if not stubs or not dyn:
        raise SystemExit("REFUSAL_AUDIT_FAILED a table parsed to zero rows")

    census = json.loads(CENSUS.read_text())["imports"]
    reasons = json.loads(reasons_path.read_text()) if reasons_path.exists() else {"reasons": []}
    entries = [r for r in reasons.get("reasons", []) if isinstance(r, dict)]
    by_key = {key(str(r.get("dll", "")), str(r.get("name", ""))): r for r in entries}

    marks = markers(sources)
    derived = bool(marks)
    marked: dict[tuple[str, str], list[dict]] = {}
    for m in marks:
        marked.setdefault(key(m["dll"], m["name"]), []).append(m)

    implemented = {key(r["dll"], r["name"]) for r in stubs}
    answered = implemented | {key(r["dll"], r["name"]) for r in dyn
                              if r["impl"] not in (None, "NULL")}
    for k, ms in sorted(marked.items()):
        if k not in answered:
            raise SystemExit(f"REFUSAL_AUDIT_FAILED {ms[0]['file']}:{ms[0]['line']} marks "
                             f"{ms[0]['dll']}!{ms[0]['name']} refused, but the runtime has no "
                             "implementation of it to refuse from")

    def entry(k: tuple[str, str], kind: str) -> dict | None:
        d = by_key.get(k)
        return d if d is not None and d.get("kind") == kind else None

    def policy(k: tuple[str, str]) -> bool:
        return k in marked if derived else entry(k, "policy") is not None

    rows, used = [], set()
    for imp in census:
        k = key(imp["dll"], imp["name"])
        if k in implemented:
            if policy(k):
                reason = entry(k, "policy")
                if reason:
                    used.add(k)
                rows.append({**imp, "surface": "iat", "refused": True, "kind": "policy",
                             "reason": reason, "markers": marked.get(k, [])})
            else:
                rows.append({**imp, "surface": "iat", "refused": False, "kind": None})
            continue
        if entry(k, "policy"):
            raise SystemExit(
                f"REFUSAL_AUDIT_FAILED {imp['dll']}!{imp['name']} is declared a "
                "policy refusal but has no stub at all; it is a no-stub refusal "
                "and the reasons file is stale")
        reason = entry(k, "no-stub")
        if reason:
            used.add(k)
        rows.append({**imp, "surface": "iat", "refused": True,
                     "kind": "no-stub", "reason": reason})

    for r in dyn:
        k = key(r["dll"], r["name"])
        declared = by_key.get(k)
        if r["impl"] in (None, "NULL"):
            kind, reason = "null-impl", entry(k, "null-impl")
        elif policy(k):
            kind, reason = "policy", entry(k, "policy")
        else:
            continue          # answered, not refused: an entry for it is stale
        cls = (declared or {}).get("class")
        if not cls:
            raise SystemExit(
                f"REFUSAL_AUDIT_FAILED dynamic {r['dll']}!{r['name']} refuses ({kind}) and "
                "has no reasons-file entry to classify it; the census covers IAT slots "
                "only, so a dynamic name carries its own design-3.5 class or it is "
                "unclassified")
        if reason:
            used.add(k)
        rows.append({"dll": r["dll"], "name": r["name"], "class": cls,
                     "subclass": declared.get("subclass"), "surface": "dynamic",
                     "refused": True, "kind": kind, "reason": reason,
                     "markers": marked.get(k, [])})

    stale = sorted(f"{k[0]}!{k[1]}" for k in set(by_key) - used)
    refused = [r for r in rows if r["refused"]]
    pres = [r for r in refused if r["class"] == "presentation"]
    unexcused = [r for r in pres if not r.get("reason")]
    no_entry = [r for r in refused if derived and r["kind"] == "policy" and not r.get("reason")]
    no_marker = sorted(f"{k[0]}!{k[1]}" for k, d in by_key.items()
                       if derived and d.get("kind") == "policy" and k not in marked)
    return {"rows": rows, "refused": refused, "presentation_refused": pres,
            "unexcused": unexcused, "stale_reasons": stale,
            "markers": marks, "marker_checks": derived,
            "markers_without_entry": no_entry, "policy_without_marker": no_marker,
            "counts": {"census": len(census), "stub_rows": len(stubs),
                       "dyn_rows": len(dyn), "refused": len(refused),
                       "presentation_refused": len(pres),
                       "unexcused": len(unexcused), "markers": len(marks)}}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    ap.add_argument("--reasons", help="an alternative reasons file; the gate's "
                                      "falsifications use it to mutate one field "
                                      "without touching the real record")
    a = ap.parse_args()
    r = audit(Path(a.reasons) if a.reasons else None)
    c = r["counts"]
    if a.json:
        print(json.dumps(r, indent=2, sort_keys=True))
    else:
        for row in sorted(r["refused"], key=lambda x: (x["class"], x["dll"], x["name"])):
            why = row["reason"]["why"][:58] if row.get("reason") else "-- NO RECORDED REASON"
            print(f"  {row['class']:<12} {row['dll']:<16} {row['name']:<40} "
                  f"{row['kind']:<9} {why}")
    regime = ("derived from runtime markers" if r["marker_checks"]
              else f"declared: no {MARKER} call in runtime/*.c yet")
    print(f"REFUSAL_AUDIT census={c['census']} stub_rows={c['stub_rows']} "
          f"refused={c['refused']} presentation_refused={c['presentation_refused']} "
          f"unexcused={c['unexcused']} stale_reasons={len(r['stale_reasons'])} "
          f"markers={c['markers']} (policy refusals {regime})")
    if a.out:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT,
                              capture_output=True, text=True).stdout.strip()
        Path(a.out).write_text(json.dumps({
            "schema": "sa2-recomp-receipt/v1",
            "id": "s4-refusal-audit-v1",
            "generated_by": "tools/s4/refusal_audit.py",
            "commit": head,
            "inputs": {"runtime": "runtime/sa2_imports.c",
                       "census": str(CENSUS.relative_to(ROOT)),
                       "reasons": str(REASONS.relative_to(ROOT))},
            "counts": c,
            "refused": r["refused"],
            "stale_reasons": r["stale_reasons"],
        }, indent=2, sort_keys=True) + "\n")
        print(f"REFUSAL_AUDIT wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
