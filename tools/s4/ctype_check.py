#!/usr/bin/env python3
"""The C runtime's character tables, held to the host's.

Subtitle: derive the runtime's character data from the oracle's Wine (emit),
hold the compiled data to Wine's files and to the native locale trace
(tables), and hold the lifted boot's own calls and case tables to native's,
with two breaks that must move them (run).

    source tools/env.sh
    .venv/bin/python tools/s4/ctype_check.py emit                 # rewrite runtime/sa2_chardata.h
    .venv/bin/python tools/s4/ctype_check.py tables [--json OUT]  # offline, seconds
    .venv/bin/python tools/s4/ctype_check.py run [--json OUT]     # three lifted boots

Why this exists
---------------
The game's statically linked C runtime builds its character tables from the
host: at start-up (_setmbcp's setSBUpLow, 0x007AD022: _mbctype and _mbcasemap
at 0x00917550 and 0x00917658, in the image) and whenever an archive loader's
setlocale(LC_ALL, "") qualifies the user's locale (__init_ctype: the ctype,
lower-case and upper-case tables on the heap). Each is the answer of three
imports to the code page's 256 characters: GetStringTypeW (CT_CTYPE1), the
upper and lower case of LCMapStringW, and WideCharToMultiByte turning the case
back into bytes. Game code reads them (isalpha, isspace, tolower, the C++ ctype
facet). The runtime answered them from rules, which differed from Wine for
twelve characters and one case (agents-journals/s4/2026-09-26-ctype.md).

Where the data comes from
-------------------------
Wine answers from three NLS files of the oracle's Proton
(private/predecessor-tools/proton-private-wrapper/files/share/wine/nls):
sortdefault.nls (CT_CTYPE1, read as Wine's get_char_type does), l_intl.nls
(the case tables, as ntdll's casemap does) and c_1252.nls (the code page's
tables, WideCharToMultiByte's best fit included). `emit` writes
runtime/sa2_chardata.h from them; their sha256 are pinned here (NLS), so a
Proton update that changes them is refused, not read. The files are validated
against the native trace, not assumed: every CT_CTYPE1 answer and every case
the trace records is the files' (`tables`, data_follows_native).

What it checks (each can fail, and a mode fails if any of its checks does)
--------------------------------------------------------------------------
tables, offline:
  * header_is_generated: runtime/sa2_chardata.h is what `emit` writes now;
  * data_follows_wine: runtime/sa2_codepage.c, compiled, answers every
    character of its domain (U+0000..U+00FF, code page 1252's 27 positions
    above it, U+0191 and U+F8F5) with Wine's CT_CTYPE1, upper and lower case,
    and every other character with no CT_CTYPE1 and its own case;
  * best_fit_follows_wine: for every UTF-16 unit, the compiled conversion to
    1252 is c_1252.nls's: the byte that round-trips, else the best-fit byte,
    else none;
  * data_follows_native: the compiled data answers every character the native
    trace's GetStringTypeW and LCMapStringW calls carried as the trace does,
    those the lifted run never asks about (U+F8F5, code page 936's) included.
run, the boot replay's lifted build on its capture's profile, three times:
with the runtime's answers, under SA2_CTYPE_BREAK=rules (the answers before
2026-09-26) and under SA2_CTYPE_BREAK=nobestfit (the new answers, without the
best fit), each recording its calls (SA2_CTYPE_ANSWERS). The rules run also
enumerates no locale (SA2_LOCALE_BREAK=noenum, BREAK_ENV), as the runtime did
before the locale take: the rules classify code page 1252's characters and trap
on any other, and since the locale take the C runtime asks GetStringTypeW about
code page 936's once it enters Chinese-simplified (U+F8F5 first; unit IN,
2026-09-26), so with the enumeration the rules run stops in its own trap before
any tick is captured:
  * covers_native: every native GetStringTypeW and LCMapStringW call, of the
    code-page-1252 path and of the code-page-936 path (its MultiByteToWideChar's
    code page, on its thread; the second is setlocale(LC_ALL,
    "Chinese-simplified")'s, which the lifted run enters since 2026-09-26), has
    a lifted call from the same site with the same input, in every run but the
    936 path's calls in a run that enumerates no locale, whose C runtime never
    enters Chinese-simplified; a call on any other code page is refused;
  * answers_follow_native: each such lifted call answers what the native one
    did, return value included;
  * conversions_follow_wine: every lifted WideCharToMultiByte call on code page
    1252 wrote what c_1252.nls gives for its input, flags and default, and every
    one on code page 936 what c_936.nls gives (tools/s4/nls_data.py's model of
    kernelbase);
  * case_tables_follow_native: _mbctype and _mbcasemap equal each registered
    native boot's on every tick both logs hold;
  * rules_moves_answers: under the rules break, answers_follow_native fails;
  * nobestfit_moves_case_tables: under the nobestfit break, the case tables
    differ from native's on a tick both logs hold (every native log compared
    through at least one tick) and a conversion writes a byte other than
    Wine's, so the case table check sees the best fit (U+0192's upper case,
    U+0191, has no byte in code page 1252, and natively turns back into 0x83).
    A log compared through no tick, or a conversion the check cannot judge, is
    not a move.

The native side is the locale trace (NATIVE_TRACE, pinned) and the boot entry
of Documentation/replays.json: its capture's word log and every repeat's, and
the profile the registry names. Nothing here writes a tracked file but `emit`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

NLS_DIR = ROOT / "private" / "predecessor-tools" / "proton-private-wrapper" / "files" / "share" \
    / "wine" / "nls"
# The oracle's Proton's files, as `emit` read them. Replace an exact known hash
# when the oracle's Proton moves, after `tables` and `run` pass on the new files.
NLS = {"sortdefault.nls": "2e58e6165d423c8764af40ebd30a9567e4ca710e230f93817e36df7bed3fd433",
       "l_intl.nls": "ad190cb85aa387d3d92a9d97ff254e20bf982a2ea9c11c2cad651ba0f8d20584",
       "c_1252.nls": "e2634ee32da7977d762d2d45be2053309ee718572a7d8bb0034180dc1b0b6b76"}
# The Proton the NLS files were read from, named in the header so a reader need
# not have the private tree. The wrapper's files/ is a link to the Steam
# installation's Proton - Experimental, which Steam updates in place, so the NLS
# pins above identify the files and a build number does not: this is that
# directory's `version` when the pins were last checked (the wrapper's own
# `version`, experimental-11.0-20260910b, is its copy's), and the files' mtime,
# which predates that build and the two before it.
PROTON_BUILD = "experimental-11.0-20260924-x86_64"
NLS_SINCE = "2026-09-07"
NATIVE_TRACE = (ROOT / "private" / "generated" / "oracle" / "captures" / "locale-trace-v1a"
                / "sa2-locale-trace.txt")
NATIVE_TRACE_SHA256 = "52cb1dd8cf09762f6f70943ed99def975931ed690178c4e11f0d1af52d50a8be"
HEADER = ROOT / "runtime" / "sa2_chardata.h"
CODEPAGE_C = ROOT / "runtime" / "sa2_codepage.c"
ZIG = Path(os.environ.get("SA2_ZIG") or
           ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig")

# _mbctype (257 bytes) and _mbcasemap (256), which _setmbcp copies from the
# thread's multibyte block at 0x007AD5B8 and 0x007AD5D1; seven bytes of
# padding lie between them, and the word range covers both whole.
MB_TABLES = (0x00917550, 0x00917758)
BREAKS = ("rules", "nobestfit")
# Each break's run environment (the module docstring's run): the rules run
# enumerates no locale, so its C runtime stays on code page 1252's path, the one
# path the rules classify.
BREAK_ENV = {"rules": {"SA2_CTYPE_BREAK": "rules", "SA2_LOCALE_BREAK": "noenum"},
             "nobestfit": {"SA2_CTYPE_BREAK": "nobestfit"}}
WC_NO_BEST_FIT_CHARS = 0x400

# Code page 1252's 0x80..0x9F, as runtime/sa2_codepage.c holds them; the check
# holds this copy to c_1252.nls (cp1252_tables).
CP1252_HIGH = (0x20AC, 0x0081, 0x201A, 0x0192, 0x201E, 0x2026, 0x2020, 0x2021,
               0x02C6, 0x2030, 0x0160, 0x2039, 0x0152, 0x008D, 0x017D, 0x008F,
               0x0090, 0x2018, 0x2019, 0x201C, 0x201D, 0x2022, 0x2013, 0x2014,
               0x02DC, 0x2122, 0x0161, 0x203A, 0x0153, 0x009D, 0x017E, 0x0178)


class Refusal(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"CTYPE_REFUSED {message}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ Wine's data

def nls(name: str) -> bytes:
    path = NLS_DIR / name
    if not path.is_file():
        raise Refusal(f"{path} is missing: the oracle's Proton is not on this machine")
    data = path.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != NLS[name]:
        raise Refusal(f"{path} hashes to {got[:12]}, not the pinned {NLS[name][:12]}: the "
                      "oracle's Proton changed its data; run `tables` and `run` on it before "
                      "re-pinning NLS")
    return data


def wine_ctype1() -> list[int]:
    """CT_CTYPE1 of every UTF-16 unit, as kernelbase's get_char_type reads
    sortdefault.nls: the header's third offset is the ctype block, whose second
    word is the offset of a three-level index into rows of three types."""
    d = nls("sortdefault.nls")
    base = struct.unpack_from("<4I", d, 0)[2]
    table = base + 4
    index = base + struct.unpack_from("<H", d, base + 2)[0] + 2
    out = []
    for ch in range(0x10000):
        o1 = struct.unpack_from("<H", d, index + 2 * (ch >> 8))[0]
        o2 = struct.unpack_from("<H", d, index + o1 + 2 * ((ch >> 4) & 0xF))[0]
        row = d[index + o2 + (ch & 0xF)]
        out.append(struct.unpack_from("<H", d, table + 2 * (row * 3))[0])
    return out


def wine_case() -> tuple[list[int], list[int]]:
    """Upper and lower case of every unit, as ntdll's casemap reads l_intl.nls:
    the upper table two words in, the lower at its second word plus two, each a
    three-level table of deltas."""
    d = nls("l_intl.nls")
    t = struct.unpack_from(f"<{len(d) // 2}H", d, 0)

    def cm(base: int, ch: int) -> int:
        return (ch + t[base + t[base + t[base + (ch >> 8)] + ((ch >> 4) & 0xF)] + (ch & 0xF)]) \
            & 0xFFFF
    upper, lower = 2, t[1] + 2
    return [cm(upper, c) for c in range(0x10000)], [cm(lower, c) for c in range(0x10000)]


def cp1252_tables() -> tuple[list[int], bytes, int]:
    """c_1252.nls: the byte-to-unit table, the unit-to-byte table (the best fit
    in it), and the default byte."""
    d = nls("c_1252.nls")
    header, cp, width, default = struct.unpack_from("<4H", d, 0)
    if (header, cp, width) != (13, 1252, 1):
        raise Refusal(f"c_1252.nls's header reads code page {cp}, width {width}")
    mb = list(struct.unpack_from("<256H", d, 28))
    wc_at = 26 + 2 * struct.unpack_from("<H", d, 26)[0] + 2
    wc = d[wc_at:wc_at + 0x10000]
    if len(wc) != 0x10000:
        raise Refusal("c_1252.nls's unit-to-byte table is short")
    for b in range(256):
        if wc[mb[b]] != b:
            raise Refusal(f"c_1252.nls does not round-trip byte {b:#04x}")
    high = tuple(mb[0x80:0xA0])
    if high != CP1252_HIGH or any(mb[b] != b for b in list(range(0x80)) + list(range(0xA0, 0x100))):
        raise Refusal("c_1252.nls's byte table is not the one runtime/sa2_codepage.c holds")
    return mb, wc, default


def best_fit_kind(w: int, mb: list[int], wc: bytes, default: int) -> tuple[int, int]:
    """What WideCharToMultiByte(1252, 0) makes of `w`: (1, byte) for a unit the
    code page holds, (2, byte) for Wine's best fit, (0, default) for none. A
    unit whose table byte is the default is valid only when it is the default's
    own unit (kernelbase is_valid_sbcs_mapping)."""
    b = wc[w]
    if mb[b] == w:
        return 1, b
    if b != default:
        return 2, b
    return 0, default


def domain() -> list[int]:
    """The characters the runtime has data for: the code page's own, U+0191
    (U+0192's upper case, which the code page lacks) and U+F8F5 (code page
    936's byte 0xFF, which the C runtime asks about in the Chinese-simplified
    locale)."""
    return list(range(0x100)) + sorted({w for w in CP1252_HIGH if w >= 0x100} | {0x0191, 0xF8F5})


def emit_text() -> str:
    ctype, (upper, lower) = wine_ctype1(), wine_case()
    mb, wc, default = cp1252_tables()
    rows = [(w, ctype[w], upper[w], lower[w]) for w in domain()]
    zero = [w for w, t, _u, _l in rows if not t]
    if zero:
        raise Refusal(f"Wine gives no CT_CTYPE1 for {', '.join(f'U+{w:04X}' for w in zero)}, "
                      "and 0 is the runtime's `no answer`")
    fits = [(w, b) for w in range(0x10000) for k, b in [best_fit_kind(w, mb, wc, default)]
            if k == 2]
    lines = [
        "/* GENERATED by tools/s4/ctype_check.py emit. Do not edit: run it again.",
        " *",
        " * Wine's answers for the characters runtime/sa2_codepage.c has data for,",
        " * read from the oracle's Proton (files/share/wine/nls):",
    ]
    for name in ("sortdefault.nls", "l_intl.nls", "c_1252.nls"):
        lines.append(f" *   {name:<16} sha256 {NLS[name]}")
    lines += [
        " * sortdefault.nls gives CT_CTYPE1, l_intl.nls the upper and lower case,",
        " * c_1252.nls the best fit WideCharToMultiByte uses without",
        " * WC_NO_BEST_FIT_CHARS. The check that holds this file to them, and them",
        " * to the native trace, is tools/s4/ctype_check.py tables.",
        " *",
        " * The oracle's Proton is Steam's Proton - Experimental, which Steam updates",
        " * in place, so the sha256 above identify the files, not a build: they read",
        f" * as above under {PROTON_BUILD}, unchanged since {NLS_SINCE}.",
        " * The values are character properties Wine compiles from Unicode Consortium",
        " * data and Microsoft's published code page 1252 best-fit table; none is the",
        " * game's. */",
        "#ifndef SA2_RUNTIME_CHARDATA_H",
        "#define SA2_RUNTIME_CHARDATA_H",
        "",
        "#include <stdint.h>",
        "",
        "typedef struct { uint16_t w, ctype1, upper, lower; } sa2_char_row;",
        "",
        f"/* {len(rows)} characters: U+0000..U+00FF at their own index, then the rest",
        " * in ascending order. */",
        f"static const sa2_char_row sa2_char_rows[{len(rows)}] = {{",
    ]
    for i in range(0, len(rows), 3):
        lines.append("    " + " ".join(f"{{0x{w:04X}u, 0x{t:04X}u, 0x{u:04X}u, 0x{lo:04X}u}},"
                                        for w, t, u, lo in rows[i:i + 3]))
    lines += [
        "};",
        "",
        "typedef struct { uint16_t w; uint8_t b; } sa2_best_fit_row;",
        "",
        f"/* {len(fits)} units code page 1252 lacks and Wine converts to a byte anyway,",
        " * in ascending order. */",
        f"static const sa2_best_fit_row sa2_best_fit_rows[{len(fits)}] = {{",
    ]
    for i in range(0, len(fits), 6):
        lines.append("    " + " ".join(f"{{0x{w:04X}u, 0x{b:02X}u}}," for w, b in fits[i:i + 6]))
    lines += ["};", "", "#endif", ""]
    return "\n".join(lines)


# ------------------------------------------------------------------ the native trace

STR = re.compile(r'(L?)"((?:[^"\\]|\\u[0-9a-f]{4}|\\x[0-9a-f]{2})*)"')
DATA_CALLS = ("GetStringTypeW", "GetStringTypeA", "LCMapStringW", "LCMapStringA",
              "MultiByteToWideChar", "WideCharToMultiByte")


def units(s: str) -> list[int]:
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\":
            n = 4 if s[i + 1] == "u" else 2
            out.append(int(s[i + 2:i + 2 + n], 16))
            i += 2 + n
        else:
            out.append(ord(s[i]))
            i += 1
    return out


def native_calls(path: Path = NATIVE_TRACE) -> list[dict]:
    """The trace's string-data calls in order: {seq, thread, caller, name, args,
    ret, strs, types}. The probe prints each call's inputs and, for
    GetStringTypeW, the types, and for LCMapStringW with a buffer, the output;
    it truncates MultiByteToWideChar's and WideCharToMultiByte's inputs at 96
    units and does not print their outputs."""
    if not path.is_file():
        raise Refusal(f"{path} is missing")
    if sha256(path) != NATIVE_TRACE_SHA256:
        raise Refusal(f"{path} is not the pinned native locale trace")
    calls = []
    for ln in path.read_text(encoding="latin-1").splitlines():
        f = ln.split(" ", 5)
        if len(f) < 6 or not f[0].isdigit() or not f[1].startswith("f") or f[4] not in DATA_CALLS:
            continue
        head, _, tail = f[5].partition(" -> ")
        m = re.match(r"(-?\d+)(.*)\Z", tail)
        if not m:
            raise Refusal(f"trace line {f[0]} has no answer")
        types = re.search(r"\[([0-9a-f ]*)\]\s*\Z", m.group(2))
        calls.append({"seq": int(f[0]), "thread": f[2], "caller": int(f[3], 16), "name": f[4],
                      "args": head.split(), "ret": int(m.group(1)),
                      "strs": [units(s) for _w, s in STR.findall(m.group(2))],
                      "types": [int(x, 16) for x in types.group(1).split()] if types else None})
    return calls


def native_answers(calls: list[dict]) -> dict:
    """Each GetStringTypeW and LCMapStringW call with an answer, by key, with its
    code page: the last MultiByteToWideChar's on the same thread, since both
    are the C runtime's __crtGetStringTypeA and __crtLCMapStringA, which convert
    first. {key: {"cps": {cp, ...}, "ret": r, "out": [...], "seq": first}}: an
    ASCII input is on both code pages' paths. A key seen twice with two
    answers is refused."""
    last_cp: dict[str, int] = {}
    out: dict = {}
    for c in calls:
        if c["name"] == "MultiByteToWideChar":
            last_cp[c["thread"]] = int(c["args"][0].split("=")[1])
            continue
        if c["name"] == "GetStringTypeW":
            info, n = int(c["args"][0]), int(c["args"][1])
            key = ("GetStringTypeW", c["caller"], info, n, tuple(c["strs"][0]))
            ans = (c["ret"], tuple(c["types"] or ()))
        elif c["name"] == "LCMapStringW" and len(c["strs"]) == 2:
            lcid, flags, n = int(c["args"][0], 16), int(c["args"][1], 16), int(c["args"][2])
            key = ("LCMapStringW", c["caller"], flags, n, tuple(c["strs"][0]))
            ans = (c["ret"], tuple(c["strs"][1]))
            if lcid not in (0x0409, 0x0804):
                raise Refusal(f"trace line {c['seq']}: LCMapStringW for LCID {lcid:#x}")
        else:
            continue
        have = out.setdefault(key, {"cps": set(), "ret": ans[0], "out": ans[1],
                                    "seq": c["seq"]})
        if (have["ret"], have["out"]) != ans:
            raise Refusal(f"trace line {c['seq']} answers {c['name']} differently from line "
                          f"{have['seq']} for the same input")
        have["cps"].add(last_cp.get(c["thread"]))
    return out


def native_characters(calls: list[dict]) -> tuple[dict[int, int], dict[tuple[int, int], int]]:
    """Every character's CT_CTYPE1 and every (character, upper) case the trace
    answers."""
    ctype: dict[int, int] = {}
    case: dict[tuple[int, int], int] = {}
    for c in calls:
        if c["name"] == "GetStringTypeW" and c["types"]:
            for w, t in zip(c["strs"][0], c["types"]):
                if ctype.setdefault(w, t) != t:
                    raise Refusal(f"trace line {c['seq']}: two CT_CTYPE1 answers for U+{w:04X}")
        if c["name"] == "LCMapStringW" and len(c["strs"]) == 2:
            up = int(bool(int(c["args"][1], 16) & 0x200))
            for w, m in zip(c["strs"][0], c["strs"][1]):
                if case.setdefault((w, up), m) != m:
                    raise Refusal(f"trace line {c['seq']}: two cases for U+{w:04X}")
    return ctype, case


# ------------------------------------------------------------------ the lifted answers

WIDTH = {  # (input unit width, output unit width) in hex digits
    "GetStringTypeW": (4, 4), "GetStringTypeA": (2, 4), "LCMapStringW": (4, 4),
    "LCMapStringA": (2, 2), "MultiByteToWideChar": (2, 4), "WideCharToMultiByte": (4, 2)}


def hexunits(s: str, width: int) -> list[int]:
    if len(s) % width:
        raise Refusal(f"a recorded buffer of {len(s)} hex digits is not whole units")
    return [int(s[i:i + width], 16) for i in range(0, len(s), width)]


def lifted_calls(path: Path) -> list[dict]:
    """runtime/sa2_imports.c's SA2_CTYPE_ANSWERS lines:
    `seq tN caller name arg... -> ret in=HEX out=HEX`."""
    if not path.is_file():
        raise Refusal(f"the lifted run wrote no answers at {path}: SA2_CTYPE_ANSWERS did not run")
    out = []
    for ln in path.read_text().splitlines():
        left, _, right = ln.partition(" -> ")
        f, r = left.split(), right.split()
        if len(f) < 4 or len(r) != 3 or not r[1].startswith("in=") or not r[2].startswith("out="):
            raise Refusal(f"{path}: a line this reader cannot read: {ln[:80]}")
        wi, wo = WIDTH[f[3]]
        out.append({"seq": int(f[0]), "thread": f[1], "caller": int(f[2], 16), "name": f[3],
                    "args": [int(a, 16) for a in f[4:]], "ret": int(r[0], 16),
                    "in": hexunits(r[1][3:], wi), "out": hexunits(r[2][4:], wo)})
    return out


def lifted_key(c: dict):
    a = c["args"]
    if c["name"] == "GetStringTypeW":
        return ("GetStringTypeW", c["caller"], a[0], a[2] - (1 << 32) if a[2] >> 31 else a[2],
                tuple(c["in"]))
    if c["name"] == "LCMapStringW" and a[5]:
        return ("LCMapStringW", c["caller"], a[1], a[3] - (1 << 32) if a[3] >> 31 else a[3],
                tuple(c["in"]))
    return None


def compare_answers(native: dict, lifted: list[dict]) -> dict:
    """Every native key of the 1252 and 936 paths against the lifted calls with it.
    `other_code_page` stays for the record's shape: since the lifted run enters
    the 936 path (2026-09-26) nothing is only listed."""
    got: dict = {}
    for c in lifted:
        k = lifted_key(c)
        if k is not None:
            got.setdefault(k, []).append((c["ret"], tuple(c["out"][:c["ret"]]
                                                          if c["name"] == "LCMapStringW"
                                                          else c["out"])))
    rows, uncovered, other_path, alone936 = [], [], [], []
    for k, n in sorted(native.items(), key=lambda kv: kv[1]["seq"]):
        label = f"{k[0]} from {k[1]:#010x}, flags {k[2]:#x}, {k[3]} units (trace line {n['seq']})"
        if not n["cps"] <= {936, 1252}:
            raise Refusal(f"{label} rides code page(s) {sorted(n['cps'], key=str)}: only 1252's "
                          "calls are compared and 936's")
        answers = got.get(k)
        if not answers:
            uncovered.append(label)
            if n["cps"] == {936}:
                alone936.append(label)
            continue
        want = (n["ret"], n["out"])
        bad = [a for a in answers if a != want]
        diff = []
        if bad:
            ret, out = bad[0]
            chars = k[4]
            diff = [{"unit": f"U+{chars[i]:04X}", "native": f"{want[1][i]:#06x}",
                     "lifted": f"{out[i]:#06x}" if i < len(out) else None}
                    for i in range(len(want[1])) if i >= len(out) or out[i] != want[1][i]]
            if ret != want[0]:
                diff.insert(0, {"return": {"native": want[0], "lifted": ret}})
        rows.append({"call": label, "lifted_calls": len(answers), "differing_calls": len(bad),
                     "differences": diff})
    return {"compared": rows, "uncovered": uncovered, "other_code_page": other_path,
            "uncovered_936_alone": alone936}


def conversion_problems(lifted: list[dict], mb, wc, default, cp936=None) -> list[str]:
    """Each lifted WideCharToMultiByte whose bytes are not c_1252.nls's, or on
    code page 936 c_936.nls's (`cp936`, tools/s4/nls_data.py CodePage; without
    it a 936 call is not judged), for its input, flags and default character
    (the recording holds the default's address, not its byte; the C runtime
    passes none)."""
    out = []
    for c in lifted:
        if c["name"] != "WideCharToMultiByte":
            continue
        cp, flags, deflt = c["args"][0], c["args"][1], c["args"][6]
        if cp == 936 and cp936 is not None and not deflt:
            import nls_data as N
            r936, want936, _u, _e = N.wctomb(cp936, flags, list(c["in"]), c["args"][5])
            if list(c["out"]) == list(want936[:len(c["out"])]) and (
                    c["ret"] != r936 or (c["args"][5] and r936 and len(c["out"]) != r936)):
                out.append(f"line {c['seq']} from {c['caller']:#010x}: code page 936 answered "
                           f"{c['ret']} with {len(c['out'])} byte(s), Wine answers {r936}")
            elif list(c["out"]) != list(want936[:len(c["out"])]):
                i = next((i for i, (x, y) in enumerate(zip(c["out"], want936)) if x != y),
                         min(len(c["out"]), len(want936)))
                out.append(f"line {c['seq']} from {c['caller']:#010x}: code page 936 wrote "
                           f"{bytes(c['out'][i:i + 2]).hex()} at byte {i}, Wine writes "
                           f"{bytes(want936[i:i + 2]).hex()}")
            continue
        if cp not in (0, 3, 1252) or deflt:
            out.append(f"line {c['seq']}: code page {cp}, default {deflt:#x}: not a call this "
                       "check can judge")
            continue
        want = []
        for w in c["in"]:
            k, b = best_fit_kind(w, mb, wc, default)
            if k == 0 or (k == 2 and flags & WC_NO_BEST_FIT_CHARS):
                b = default
            want.append(b)
        if c["out"] and c["out"] != want[:len(c["out"])]:
            i = next(i for i, (x, y) in enumerate(zip(c["out"], want)) if x != y)
            out.append(f"line {c['seq']} from {c['caller']:#010x}: U+{c['in'][i]:04X} became "
                       f"{c['out'][i]:#04x}, Wine writes {want[i]:#04x}")
    return out


# ------------------------------------------------------------------ tables mode

DRIVER = r"""
#include <stdio.h>
#include "sa2_codepage.h"
int main(void) {
    for (unsigned w = 0; w < 0x10000u; w++) {
        uint16_t up, lo; uint8_t b = 0;
        int in = sa2_char_case((uint16_t)w, 1, &up);
        int in2 = sa2_char_case((uint16_t)w, 0, &lo);
        int k = sa2_cp1252_best_fit((uint16_t)w, &b);
        printf("%04x %04x %d %04x %d %04x %d %02x\n", w, sa2_char_ctype1((uint16_t)w),
               in, up, in2, lo, k, b);
    }
    return 0;
}
"""


def compiled() -> list[tuple[int, ...]]:
    """runtime/sa2_codepage.c's answers for every unit, from a build of it."""
    if not ZIG.is_file():
        raise Refusal(f"the pinned zig is missing: {ZIG}")
    with tempfile.TemporaryDirectory() as td:
        drv, exe = Path(td) / "driver.c", Path(td) / "chardata"
        drv.write_text(DRIVER)
        env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(Path(td) / "zc"),
                   ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                       "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))
        p = subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O1", "-Wall",
                            "-Wextra", "-Werror", "-I", str(ROOT / "runtime"), str(drv),
                            str(CODEPAGE_C), "-o", str(exe)], capture_output=True, text=True,
                           env=env)
        if p.returncode:
            raise Refusal(f"runtime/sa2_codepage.c does not build:\n{p.stderr[-2000:]}")
        run = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
    return [tuple(int(x, 16) for x in ln.split()) for ln in run.stdout.splitlines()]


def tables() -> dict:
    ctype, (upper, lower) = wine_ctype1(), wine_case()
    mb, wc, default = cp1252_tables()
    dom = set(domain())
    rows = compiled()
    if len(rows) != 0x10000:
        raise Refusal("the compiled driver did not answer every unit")
    wine_bad, fit_bad = [], []
    for w, t, in_up, up, in_lo, lo, k, b in rows:
        if w in dom:
            want = (ctype[w], 1, upper[w], 1, lower[w])
        else:
            want = (0, 0, w, 0, w)
        if (t, in_up, up, in_lo, lo) != want:
            wine_bad.append(f"U+{w:04X}: ctype1/upper/lower {t:#06x}/{up:#06x}/{lo:#06x}, "
                            f"Wine {want[0]:#06x}/{want[2]:#06x}/{want[4]:#06x}")
        wk, wb = best_fit_kind(w, mb, wc, default)
        if (k, b) != (wk, wb):
            fit_bad.append(f"U+{w:04X}: kind {k} byte {b:#04x}, Wine kind {wk} byte {wb:#04x}")
    calls = native_calls()
    n_ctype, n_case = native_characters(calls)
    by_w = {r[0]: r for r in rows}
    native_bad = [f"U+{w:04X}: CT_CTYPE1 {by_w[w][1]:#06x}, native {t:#06x}"
                  for w, t in sorted(n_ctype.items()) if by_w[w][1] != t]
    native_bad += [f"U+{w:04X} {'upper' if up else 'lower'}: "
                   f"{by_w[w][3 if up else 5]:#06x}, native {m:#06x}"
                   for (w, up), m in sorted(n_case.items()) if by_w[w][3 if up else 5] != m]
    header_ok = HEADER.is_file() and HEADER.read_text() == emit_text()
    checks = {"header_is_generated": header_ok,
              "data_follows_wine": not wine_bad,
              "best_fit_follows_wine": not fit_bad,
              "data_follows_native": bool(n_ctype) and bool(n_case) and not native_bad}
    return {"checks": checks, "domain": len(dom),
            "best_fit_units": sum(1 for r in rows if r[6] == 2),
            "native_characters": {"ctype1": len(n_ctype), "case": len(n_case)},
            "differences": {"wine": wine_bad[:20], "best_fit": fit_bad[:20],
                            "native": native_bad[:20]},
            "nls": {k: v for k, v in NLS.items()}, "native_trace": str(NATIVE_TRACE.relative_to(ROOT)),
            "ok": all(checks.values())}


# ------------------------------------------------------------------ run mode

def mb_table_diff(lifted_log: Path, natives: list[Path]) -> list[dict]:
    import wordlog as WL
    out = []
    for n in natives:
        rows, meta = WL.diff(lifted_log, n, *MB_TABLES)
        out.append({"native": str(n.relative_to(ROOT)), "compared_through": meta["compared_through"],
                    "words": [{"va": f"{r['va']:#010x}", "first_tick": r["first_tick"],
                               "lifted": f"{r['a']:#010x}", "native": f"{r['b']:#010x}"}
                              for r in rows]})
    return out


def one_run(TR, name: str, env: dict, profile: dict, native: dict, natives: list[Path],
            cp1252) -> dict:
    answers = TR.OUT / name / "ctype-answers.txt"
    answers.parent.mkdir(parents=True, exist_ok=True)
    answers.unlink(missing_ok=True)
    run = TR.run_lifted(name, {**env, "SA2_CTYPE_ANSWERS": str(answers)},
                        device_profile=profile, words=True)
    lifted = lifted_calls(answers)
    cmp = compare_answers(native, lifted)
    import nls_data as N
    return {"env": env, "capture_sha256": run["sha256"], "answers": str(answers.relative_to(ROOT)),
            "answers_sha256": sha256(answers), "calls": len(lifted), **cmp,
            "conversions": conversion_problems(lifted, *cp1252, cp936=N.cp936()),
            "mb_tables": mb_table_diff(TR.ROOT / run["words"]["path"], natives)}


def follows(r: dict) -> bool:
    return bool(r["compared"]) and not any(x["differing_calls"] for x in r["compared"])


def tables_follow(r: dict) -> bool:
    return all(not x["words"] and x["compared_through"] > 0 for x in r["mb_tables"])


def tables_moved(r: dict) -> bool:
    # A break moves the tables only on ticks both logs hold: a log compared
    # through no tick shows nothing, and is not a difference.
    return (all(x["compared_through"] > 0 for x in r["mb_tables"])
            and any(x["words"] for x in r["mb_tables"]))


def fit_dropped(r: dict) -> bool:
    # A conversion off Wine's by its byte, not one this check cannot judge.
    return any("Wine writes" in p for p in r["conversions"])


def covered(r: dict) -> bool:
    """Every native call has a lifted one, but in a run that enumerated no locale
    (SA2_LOCALE_BREAK=noenum, BREAK_ENV's rules run) the calls on code page 936
    alone, which its C runtime never makes: it never enters Chinese-simplified."""
    missing = set(r["uncovered"])
    if (r.get("env") or {}).get("SA2_LOCALE_BREAK") == "noenum":
        missing -= set(r.get("uncovered_936_alone") or [])
    return not missing


def run_checks(runs: dict) -> dict:
    """run's six checks over the three runs one_run recorded."""
    c = runs["control"]
    return {
        "covers_native": bool(c["compared"]) and not c["uncovered"]
        and all(covered(r) for r in runs.values()),
        "answers_follow_native": follows(c),
        "conversions_follow_wine": not c["conversions"],
        "case_tables_follow_native": tables_follow(c),
        "rules_moves_answers": bool(runs["rules"]["compared"]) and not follows(runs["rules"]),
        "nobestfit_moves_case_tables": (tables_moved(runs["nobestfit"])
                                        and fit_dropped(runs["nobestfit"])),
    }


def run_mode() -> dict:
    import tick_replay as TR
    entry = next(e for e in json.loads(TR.REGISTRY.read_text())["replays"] if e["name"] == "boot")
    sources = [entry["native"]] + list(entry["native"].get("repeats", []))
    if any(not s.get("word_log") for s in sources):
        raise Refusal("a registered boot has no word log")
    natives = [TR.ROOT / s["word_log"]["file"] for s in sources]
    stale = [str(p) for s, p in zip(sources, natives) if TR.sha256(p) != s["word_log"]["sha256"]]
    if stale:
        raise Refusal(f"word log(s) not the registered bytes: {stale}")
    native = native_answers(native_calls())
    cp1252 = cp1252_tables()
    profile = TR.native_device_profile(entry)
    runs = {"control": one_run(TR, "ctype-control", {}, profile, native, natives, cp1252)}
    for b in BREAKS:
        runs[b] = one_run(TR, f"ctype-{b}", BREAK_ENV[b], profile, native, natives, cp1252)
    checks = run_checks(runs)
    return {"checks": checks, "natives": [str(p.relative_to(ROOT)) for p in natives],
            "native_trace": str(NATIVE_TRACE.relative_to(ROOT)), "runs": runs,
            "ok": all(checks.values())}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("emit", "tables", "run"))
    ap.add_argument("--json", type=Path, default=None, help="also write the result here")
    a = ap.parse_args(argv)
    if a.mode == "emit":
        HEADER.write_text(emit_text())
        print(f"CTYPE wrote {HEADER.relative_to(ROOT)}")
        return 0
    result = tables() if a.mode == "tables" else run_mode()
    for k, v in result["checks"].items():
        print(f"CTYPE {k}: {'ok' if v else 'FAILED'}")
    if a.mode == "run":
        for name, r in result["runs"].items():
            differing = sorted({d["unit"] for x in r["compared"] for d in x["differences"]
                                if "unit" in d})
            print(f"CTYPE {name}: {len(r['compared'])} native calls compared, "
                  f"{len(r['other_code_page'])} only listed, "
                  f"{len(r['uncovered'])} uncovered"
                  + (f" ({len(r.get('uncovered_936_alone') or [])} on code page 936 alone, "
                     "excused: no locale enumerated)"
                     if (r.get('env') or {}).get('SA2_LOCALE_BREAK') == 'noenum' else "")
                  + ", "
                  f"differing units {differing or 'none'}, "
                  f"{len(r['conversions'])} conversion(s) off Wine's, "
                  f"_mbctype/_mbcasemap words differing "
                  f"{sorted({w['va'] for x in r['mb_tables'] for w in x['words']}) or 'none'}")
    if a.json:
        a.json.write_text(json.dumps(result, indent=1) + "\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
