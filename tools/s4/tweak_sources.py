"""The play tweaks as the build tools read them: the list, each tweak's runtime file, its wraps.

Subtitle: runtime/sa2_tweaks.h's SA2_TWEAK_LIST, runtime/tweaks/*.c by glob, and the one
`TWEAK_WRAPS:` line in each file's header comment that names the lifted functions it wraps
(runtime/sa2_tweaks.h, "Add a tweak"). A tweak adds its own file and no line to any tool.
`patches` reads SA2_TWEAK_PATCH_LIST, the code-patch tweaks among the list's: they have no runtime
file (tools/s8/variants/variants.py generates their wraps from tools/s8/variants/tweaks/<name>.json,
SPECS here), and a link takes them with --variants.

tools/s4/link_s4.py adds `sources` to its RUNTIME_C and `wraps` to its PRODUCTION_WRAPS when it
is imported, after its literals, so the Windows and Android links (which take link_s4's lists)
compile the tweaks' files and pass their -wrap too. tools/s7/arm64_link.py and
tools/s4/build_cost.py read link_s4.py's literals without importing it (ast.literal_eval, a
regular expression) and add the same from here, for the runtime directory they compile.

A tweak that acts on a function another tweak's file wraps cannot wrap it again; it acts in that
wrap (runtime/sa2_tweaks.h, "Add a tweak", step 2), and its line says so in place of a list:
`TWEAK_WRAPS: none (it acts in no-event-bars.c's wrap of sub_5f8ec0)`. Its file then wraps nothing
(`shares` names the wrap it acts in) and is compiled like every other.

Refused by name, never skipped: a tweak file without its TWEAK_WRAPS line, or with a word in it
that is no lifted function's name; a function two tweak files wrap, or one the runtime's own
literal list wraps already (a second `__wrap_` would be a duplicate symbol, or a wrap left out);
a file that acts in a wrap no tweak file defines; a tweak file whose object would have a runtime
source's name (every link names an object by the source's stem).
"""
from __future__ import annotations

import re
from pathlib import Path

HEADER = "sa2_tweaks.h"
FOLDER = "tweaks"
WRAPS_LINE = re.compile(r"^[ \t/*]*TWEAK_WRAPS:(.*?)(?:\*/.*)?$", re.M)
LIFTED = re.compile(r"sub_[0-9a-f]+")
# A TWEAK_WRAPS line that names the wrap of another tweak's file this file acts in.
SHARED = re.compile(r"\s*none\s*\(it acts in ([a-z0-9-]+\.c)'s wrap of (sub_[0-9a-f]+)\)\s*")
# X(bit, name, report, summary) in SA2_TWEAK_LIST; X(bit) in SA2_TWEAK_PATCH_LIST.
ROW = re.compile(r'X\(\s*(SA2_TWEAK_\w+)\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*,\s*"([^"]*)"\s*\)')
PATCH_ROW = re.compile(r'X\(\s*(SA2_TWEAK_\w+)\s*\)')
# The code-patch tweaks' specs, relative to the checkout (runtime/sa2_tweaks.h, "Add a code-patch
# tweak"), and the variant tool's test fixtures, which are no tweak.
SPECS = "tools/s8/variants/tweaks"
FIXTURES = "tools/s8/variants/fixtures"


def rows(runtime: Path) -> list[dict]:
    """SA2_TWEAK_LIST's lines in order: {bit, name, report, summary}."""
    text = (runtime / HEADER).read_text()
    m = re.search(r"#define SA2_TWEAK_LIST\(X\)(.*?)\n\s*\n", text, re.S)
    if not m:
        raise SystemExit(f"{runtime / HEADER} has no SA2_TWEAK_LIST to read")
    return [dict(zip(("bit", "name", "report", "summary"), r)) for r in ROW.findall(m.group(1))]


def patches(runtime: Path) -> list[dict]:
    """The rows of SA2_TWEAK_LIST that SA2_TWEAK_PATCH_LIST names, the code-patch tweaks, in its
    order; refused by name when it names a bit the list has not."""
    text = (runtime / HEADER).read_text()
    m = re.search(r"#define SA2_TWEAK_PATCH_LIST\(X\)(.*?)\n\s*\n", text, re.S)
    if not m:
        raise SystemExit(f"{runtime / HEADER} has no SA2_TWEAK_PATCH_LIST to read")
    by_bit = {r["bit"]: r for r in rows(runtime)}
    bits = PATCH_ROW.findall(m.group(1))
    stray = [b for b in bits if b not in by_bit]
    if stray:
        raise SystemExit(f"{runtime / HEADER}: SA2_TWEAK_PATCH_LIST names {stray}, which SA2_TWEAK_LIST "
                         "has no line for")
    return [by_bit[b] for b in bits]


def _read(runtime: Path) -> list[tuple[str, list[str], tuple[str, str] | None]]:
    """(source relative to runtime/, the functions it wraps, the (file, function) of the wrap it
    acts in or None) for every runtime/tweaks/*.c, in name order, each line checked."""
    taken = {p.stem: p.name for p in runtime.glob("*.c")} | {p.stem: p.name for p in runtime.glob("*.cpp")}
    out, seen = [], {}
    for path in sorted((runtime / FOLDER).glob("*.c")):
        rel = f"{FOLDER}/{path.name}"
        if path.stem in taken:
            raise SystemExit(f"{rel}: its object would be {path.stem}.o, the name of runtime/"
                             f"{taken[path.stem]}'s; rename the tweak's file")
        lines = WRAPS_LINE.findall(path.read_text())
        if len(lines) != 1:
            raise SystemExit(f"{rel}: {len(lines)} 'TWEAK_WRAPS:' lines in the file; a tweak's "
                             f"header comment names the lifted functions it wraps in one")
        shared = SHARED.fullmatch(lines[0])
        if shared:
            out.append((rel, [], (f"{FOLDER}/{shared.group(1)}", shared.group(2))))
            continue
        words = lines[0].replace(",", " ").split()
        bad = [w for w in words if not LIFTED.fullmatch(w)]
        if bad or not words:
            raise SystemExit(f"{rel}: TWEAK_WRAPS names {bad or 'nothing'}; it lists lifted "
                             f"functions as sub_<address>, e.g. 'TWEAK_WRAPS: sub_5f8ec0'")
        for w in words:
            if w in seen:
                raise SystemExit(f"{rel} and {seen[w]} both wrap {w}; one wrap per function")
            seen[w] = rel
        out.append((rel, words, None))
    for rel, _, shared in out:
        if shared and seen.get(shared[1]) != shared[0]:
            raise SystemExit(f"{rel}: TWEAK_WRAPS says it acts in {shared[0]}'s wrap of {shared[1]}, "
                             f"but {f'{seen[shared[1]]} wraps it' if shared[1] in seen else 'no tweak file wraps it'}")
    return out


def files(runtime: Path) -> list[tuple[str, list[str]]]:
    """(source as RUNTIME_C names it, relative to runtime/, the functions it wraps) for every
    runtime/tweaks/*.c, in name order; a file that acts in another's wrap wraps none."""
    return [(rel, words) for rel, words, _ in _read(runtime)]


def shares(runtime: Path) -> list[tuple[str, str, str]]:
    """(file, the file whose wrap it acts in, the function that wrap wraps) for each tweak file
    whose TWEAK_WRAPS line names another file's wrap, in name order."""
    return [(rel, *shared) for rel, _, shared in _read(runtime) if shared]


def sources(runtime: Path) -> list[str]:
    """The tweaks' runtime sources, relative to runtime/, in name order."""
    return [rel for rel, _ in files(runtime)]


def wraps(runtime: Path, production: list[str]) -> list[str]:
    """The lifted functions the tweaks' files wrap, file by file in name order; `production` is
    the runtime's own literal list, which none of them may repeat."""
    out = []
    for rel, words in files(runtime):
        twice = [w for w in words if w in production]
        if twice:
            raise SystemExit(f"{rel} wraps {twice}, which the runtime wraps already "
                             f"(link_s4.py PRODUCTION_WRAPS); a tweak acts in that wrap instead")
        out += words
    return out
