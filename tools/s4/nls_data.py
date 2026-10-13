#!/usr/bin/env python3
"""The oracle's Wine locale and code page 936 data, read as Wine reads it.

Subtitle: parse the oracle's Proton locale.nls and c_936.nls (pinned by
sha256), answer what kernelbase answers from them, and write
runtime/sa2_localedata.h, the generated data runtime/sa2_locale.c answers from.

    source tools/env.sh
    .venv/bin/python tools/s4/nls_data.py emit       # rewrite runtime/sa2_localedata.h

tools/s4/locale_check.py holds the header, the runtime's compiled answers and
the native traces to this module (its `tables` mode); this module only reads
and writes.

Why fixed data and not a recorded host input
--------------------------------------------
The game's C runtime calls setlocale(LC_ALL, "Chinese-simplified") on each
archive loader's thread (agents-journals/s4/2026-09-26-locale.md), and
natively Wine answers it from two of its NLS files: the enumeration of
installed locales, 0x0804's locale data and code page 936's tables. Those are
the platform's data, compiled by Wine from Unicode CLDR and Microsoft's
published code page tables, not a property of the user's machine; the ctype
fold treated sortdefault.nls, l_intl.nls and c_1252.nls the same way
(tools/s4/ctype_check.py NLS). What is the user's -- the user's LCID, its data
with any registry override, the ANSI and OEM code pages -- stays in the
capture's device profile (runtime/sa2_devprofile.h, "The locale"). The files
are pinned here, so a Proton update that changes them is refused, not read.

What is read, and from where in Wine
------------------------------------
Every rule below is kernelbase's (dlls/kernelbase/locale.c), and the native
traces locale_check.py pins are the evidence that the oracle's Proton answers
this way:
  * EnumSystemLocalesA(LCID_INSTALLED): the locale-name index in its order,
    without the invariant locale, locales with no LCID, aliases, neutral
    locales and alternate sorts (`installed`);
  * IsValidLocale: the LCID index (`lcids`), with LOCALE_NEUTRAL,
    LOCALE_USER_DEFAULT and LOCALE_SYSTEM_DEFAULT FALSE, as kernelbase answers;
  * get_locale_info for the LCTYPEs the device profile carries
    (tools/s4/device_profile.py LOCALE_LCTYPES), with no user override, since
    the locales answered from here are never the user's (`answer`);
  * code page 936: RtlInitCodePageTable's layout of c_936.nls (`CodePage`),
    mbstowcs_dbcs, wcstombs_dbcs and is_valid_dbcs_mapping.
"""
from __future__ import annotations

import hashlib
import struct
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import ctype_check as CT  # noqa: E402  the NLS directory and the build the pins were checked under
import device_profile as DP  # noqa: E402  LOCALE_LCTYPES

NLS_DIR = CT.NLS_DIR
# Replace an exact known hash when the oracle's Proton moves, after
# tools/s4/locale_check.py tables and run pass on the new files.
NLS = {"locale.nls": "868e5d3a3f115c2649706f566f9741aa3e2c6927c10b3f07d5e4c76b0e403115",
       "c_936.nls": "29f6633ee0df301fb6cc659f3941be9fcc1d9f6e633c7df24864c6d5b462f5a0"}
HEADER = ROOT / "runtime" / "sa2_localedata.h"

# The locales the runtime has every LCTYPE of the profile's list for: 0x0409,
# the user's locale when no capture's profile records one (runtime/sa2_locale.h,
# "The default"), and 0x0804, the one the C runtime's "Chinese-simplified"
# selects natively (the locale traces, EnumSystemLocalesA's second pass).
DATA_LCIDS = (0x0409, 0x0804)
# What the enumeration callbacks ask of every installed locale:
# LOCALE_SABBREVLANGNAME and LOCALE_SENGLANGUAGE (0x007B7D55's two passes).
CATALOGUE_LCTYPES = (0x0003, 0x1001)
CP_UTF8 = 65001

LOCALE_NEUTRAL, LOCALE_USER_DEFAULT, LOCALE_SYSTEM_DEFAULT = 0x0000, 0x0400, 0x0800
LOCALE_CUSTOM_UNSPECIFIED = 0x1000


class Refusal(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"NLS_REFUSED {message}")


def nls(name: str) -> bytes:
    path = NLS_DIR / name
    if not path.is_file():
        raise Refusal(f"{path} is missing: the oracle's Proton is not on this machine")
    data = path.read_bytes()
    got = hashlib.sha256(data).hexdigest()
    if got != NLS[name]:
        raise Refusal(f"{path} hashes to {got[:12]}, not the pinned {NLS[name][:12]}: the "
                      "oracle's Proton changed its data; run tools/s4/locale_check.py tables "
                      "and run on it before re-pinning NLS")
    return data


# ------------------------------------------------------------------ locale.nls

# NLS_LOCALE_DATA's fields this module reads (include/winternl.h): offset, width.
FIELDS = {
    "ilanguage": (0x008, 2), "icurrdigits": (0x010, 2), "icurrency": (0x012, 2),
    "inegcurr": (0x014, 2), "inotneutral": (0x018, 2), "icountry": (0x01e, 2),
    "sgrouping": (0x024, 4), "smongrouping": (0x028, 4), "sdecimal": (0x030, 4),
    "sthousand": (0x034, 4), "scurrency": (0x038, 4), "smondecimalsep": (0x03c, 4),
    "smonthousandsep": (0x040, 4), "spositivesign": (0x044, 4), "snegativesign": (0x048, 4),
    "s1159": (0x04c, 4), "s2359": (0x050, 4), "stimeformat": (0x058, 4),
    "sshortdate": (0x05c, 4), "slongdate": (0x060, 4), "idefaultlanguage": (0x06c, 2),
    "idefaultansicodepage": (0x06e, 2), "idefaultcodepage": (0x070, 2),
    "scalendartype": (0x07c, 4), "sabbrevlangname": (0x080, 4), "senglanguage": (0x088, 4),
    "snativelangname": (0x08c, 4), "sengcountry": (0x090, 4), "snativectryname": (0x094, 4),
    "sabbrevctryname": (0x098, 4), "sintlsymbol": (0x0a0, 4), "sdayname": (0x0bc, 4),
    "sabbrevdayname": (0x0c0, 4), "smonthname": (0x0c4, 4), "sabbrevmonthname": (0x0c8, 4),
}
# get_locale_info's tables for the sign positions (kernelbase).
IPOSSIGNPOSN = (3, 3, 4, 2, 1, 1, 3, 4, 1, 3, 4, 2, 4, 3, 3, 1)
INEGSIGNPOSN = (0, 3, 4, 2, 0, 1, 3, 4, 1, 3, 4, 2, 4, 3, 0, 0)
INEGSYMPRECEDES = (1, 1, 1, 1, 0, 0, 0, 0, 0, 1, 0, 1, 1, 0, 1, 0)


class LocaleNls:
    """locale.nls as kernelbase's load_locale_nls maps it."""

    def __init__(self, data: bytes) -> None:
        self.d = data
        self.base = struct.unpack_from("<8I", data, 0)[4]          # header->locales
        b = self.base
        (self.nb_lcids, self.nb_locales, self.locale_size, self.locales_offset,
         self.nb_lcnames) = struct.unpack_from("<HHHIH", data, b + 0x1e)
        self.lcids_offset, self.lcnames_offset = struct.unpack_from("<II", data, b + 0x2c)
        self.strings_offset = struct.unpack_from("<I", data, b + 0x40)[0]
        magic = struct.unpack_from("<I", data, b + 0x0c)[0]
        if magic != 0x5344534E:                                    # 'NSDS'
            raise Refusal(f"locale.nls: magic {magic:#010x} is not NSDS")
        if self.locale_size < 0x128:
            raise Refusal(f"locale.nls: a locale record of {self.locale_size} bytes is shorter "
                          "than the fields this reader uses")

    def u16(self, off: int) -> int:
        return struct.unpack_from("<H", self.d, off)[0]

    def u32(self, off: int) -> int:
        return struct.unpack_from("<I", self.d, off)[0]

    def unit(self, pos: int) -> int:
        return self.u16(self.base + self.strings_offset + 2 * pos)

    def string(self, pos: int) -> list[int]:
        """locale_return_string's data: the length-prefixed units at `pos`,
        which the file stores with a NUL after them."""
        n = self.unit(pos)
        out = [self.unit(pos + 1 + i) for i in range(n)]
        if self.unit(pos + 1 + n) != 0:
            raise Refusal(f"locale.nls: the string at {pos} is not NUL-terminated")
        return out

    def record(self, idx: int) -> int:
        if not 0 <= idx < self.nb_locales:
            raise Refusal(f"locale.nls: locale index {idx} is outside the array")
        return self.base + self.locales_offset + idx * self.locale_size

    def field(self, idx: int, name: str) -> int:
        off, width = FIELDS[name]
        at = self.record(idx) + off
        return self.u16(at) if width == 2 else self.u32(at)

    def lcnames(self) -> list[tuple[int, int, int]]:
        """(name, idx, id) of the name index, in its (name) order."""
        at = self.base + self.lcnames_offset
        return [struct.unpack_from("<HHI", self.d, at + 8 * i) for i in range(self.nb_lcnames)]

    def lcid_index(self) -> list[tuple[int, int]]:
        """(id, idx) of the LCID index, in its (ascending id) order."""
        at = self.base + self.lcids_offset
        rows = [struct.unpack_from("<IHH", self.d, at + 8 * i)[:2] for i in range(self.nb_lcids)]
        if [r[0] for r in rows] != sorted(r[0] for r in rows):
            raise Refusal("locale.nls: the LCID index is not ascending, and find_lcid_entry "
                          "searches it as if it were")
        return rows

    def installed(self) -> list[tuple[int, int]]:
        """EnumSystemLocalesA(LCID_INSTALLED)'s LCIDs in order, with each one's
        locale index: kernelbase's loop over the name index."""
        out = []
        for name, idx, lcid in self.lcnames():
            if not name:
                continue                              # the invariant locale
            if lcid == LOCALE_CUSTOM_UNSPECIFIED:
                continue                              # a locale with no LCID
            if lcid & 0x80000000:
                continue                              # an alias
            if not self.field(idx, "inotneutral"):
                continue                              # a neutral locale
            if (lcid >> 16) & 0xF:
                continue                              # an alternate sort: not asked for
            out.append((lcid, idx))
        return out

    def locale_of(self, lcid: int) -> int:
        """NlsValidateLocale's locale index for an LCID of the index."""
        for i, idx in self.lcid_index():
            if i == lcid:
                if not self.field(idx, "inotneutral"):
                    raise Refusal(f"LCID {lcid:#06x} is neutral; the runtime answers no neutral "
                                  "locale")
                return idx
        raise Refusal(f"LCID {lcid:#06x} is not in locale.nls's LCID index")

    # get_locale_info, for the LCTYPEs of the profile's list, with no override

    def strarray(self, pos: int, i: int) -> list[int]:
        count = self.unit(pos)
        if i >= count:
            return self.string(0)
        at = self.base + self.strings_offset + 2 * (pos + 1) + 4 * i
        return self.string(self.u32(at))

    def grouping(self, pos: int) -> list[int]:
        count = self.unit(pos)
        if not count:
            raise Refusal(f"locale.nls: an empty grouping at {pos}")
        units = [self.unit(pos + 1 + i) for i in range(count)]
        out: list[int] = []
        for u in units:
            if not u:                         # an explicit end: the last ';' becomes the end
                return out[:-1]
            out += [ord("0") + u, ord(";")]
        return out + [ord("0")]

    def answer(self, idx: int, lctype: int, acp_process: int = 1252) -> tuple[str, list[int], int]:
        """get_locale_info(locale, lcid, lctype) as kernelbase answers it for a
        locale that is not the user's: ("string" or "number", the UTF-16 units
        without the NUL, the number for LOCALE_RETURN_NUMBER or 0)."""
        f = lambda n: self.field(idx, n)  # noqa: E731
        s = lambda n: ("string", self.string(f(n)), 0)  # noqa: E731

        def num(val: int, fmt: str = "%u") -> tuple[str, list[int], int]:
            return ("number", [ord(c) for c in fmt % val], val)

        t = lctype
        if t == 0x0001:
            return num(f("ilanguage") if f("inotneutral") else f("idefaultlanguage"), "%04x")
        if t == 0x0003:
            return s("sabbrevlangname")
        if t == 0x0004:
            return s("snativelangname")
        if t == 0x0005:
            return num(f("icountry"))
        if t == 0x0007:
            return s("sabbrevctryname")
        if t == 0x0008:
            return s("snativectryname")
        if t == 0x0009:
            return num(f("idefaultlanguage"), "%04x")
        if t == 0x000A:
            return num(f("icountry"))
        if t == 0x000B:
            cp = f("idefaultcodepage")
            return num(1 if cp == CP_UTF8 else cp)            # CP_OEMCP for UTF-8
        if t == 0x000E:
            return s("sdecimal")
        if t == 0x000F:
            return s("sthousand")
        if t == 0x0010:
            return ("grouping", self.grouping(f("sgrouping")), 0)
        if t == 0x0014:
            return s("scurrency")
        if t == 0x0015:
            return s("sintlsymbol")
        if t == 0x0016:
            return s("smondecimalsep")
        if t == 0x0017:
            return s("smonthousandsep")
        if t == 0x0018:
            return ("grouping", self.grouping(f("smongrouping")), 0)
        if t in (0x0019, 0x001A):
            return num(f("icurrdigits"))
        if t == 0x001F:
            return ("string", self.strarray(f("sshortdate"), 0), 0)
        if t == 0x0020:
            return ("string", self.strarray(f("slongdate"), 0), 0)
        if t == 0x0028:
            return s("s1159")
        if t == 0x0029:
            return s("s2359")
        if 0x002A <= t <= 0x0030:
            return ("string", self.strarray(f("sdayname"), (t - 0x002A + 1) % 7), 0)
        if 0x0031 <= t <= 0x0037:
            return ("string", self.strarray(f("sabbrevdayname"), (t - 0x0031 + 1) % 7), 0)
        if 0x0038 <= t <= 0x0043:
            return ("string", self.strarray(f("smonthname"), t - 0x0038), 0)
        if 0x0044 <= t <= 0x004F:
            return ("string", self.strarray(f("sabbrevmonthname"), t - 0x0044), 0)
        if t == 0x0050:
            return s("spositivesign")
        if t == 0x0051:
            return s("snegativesign")
        neg, cur = f("inegcurr"), f("icurrency")
        if t in (0x0052, 0x0053, 0x0056) and neg >= 16:
            raise Refusal(f"locale index {idx}: LOCALE_INEGCURR {neg} is past kernelbase's tables")
        if t == 0x0052:
            return num(IPOSSIGNPOSN[neg])
        if t == 0x0053:
            return num(INEGSIGNPOSN[neg])
        if t == 0x0054:
            return num(int(not (cur & 1)))
        if t == 0x0055:
            return num(int(bool(cur & 2)))
        if t == 0x0056:
            return num(INEGSYMPRECEDES[neg])
        if t == 0x0057:
            return num(int(neg >= 8))
        if t == 0x1001:
            return s("senglanguage")
        if t == 0x1002:
            return s("sengcountry")
        if t == 0x1003:
            return ("string", self.strarray(f("stimeformat"), 0), 0)
        if t == 0x1004:
            cp = f("idefaultansicodepage")
            return num(0 if cp == CP_UTF8 else cp)            # CP_ACP for UTF-8
        if t == 0x1009:
            return num(self.unit(f("scalendartype") + 1))
        raise Refusal(f"LCTYPE {t:#06x}: this reader has no rule for it")


# ------------------------------------------------------------------ c_936.nls

class CodePage:
    """A double-byte code page as RtlInitCodePageTable lays out its NLS file."""

    def __init__(self, data: bytes, want_cp: int) -> None:
        w = struct.unpack_from(f"<{len(data) // 2}H", data, 0)
        if w[0] != 13 or w[1] != want_cp or w[2] != 2:
            raise Refusal(f"c_{want_cp}.nls's header reads size {w[0]}, code page {w[1]}, "
                          f"width {w[2]}")
        self.cp = w[1]
        self.default, self.unidefault, self.trans, self.transuni = w[3], w[4], w[5], w[6]
        self.leadbyte = bytes(struct.unpack_from("<12B", data, 14))
        p = w[0]
        self.wc_at = p + w[p] + 1
        mb_at = p + 1
        self.single = list(w[mb_at:mb_at + 256])
        q = mb_at + 256
        if w[q]:
            raise Refusal(f"c_{want_cp}.nls has a glyph table, which this reader does not read")
        q += 1
        if not w[q]:
            raise Refusal(f"c_{want_cp}.nls has no DBCS ranges")
        self.off_at = q + 1
        self.offsets = list(w[self.off_at:self.off_at + 256])
        self.w = w
        self.leads = [b for b in range(256) if self.offsets[b]]
        self.wc = list(w[self.wc_at:self.wc_at + 0x10000])
        if len(self.wc) != 0x10000:
            raise Refusal(f"c_{want_cp}.nls's unit-to-bytes table is short")

    def pair(self, lead: int, trail: int) -> int:
        return self.w[self.off_at + self.offsets[lead] + trail]

    def valid_mapping(self, wch: int, no_best_fit: bool) -> bool:
        """kernelbase's is_valid_dbcs_mapping."""
        ch = self.wc[wch]
        if no_best_fit or ch == self.default:
            if ch >> 8:
                return self.pair(ch >> 8, ch & 0xFF) == wch
            return self.single[ch] == wch
        return True


def locale_nls() -> LocaleNls:
    return LocaleNls(nls("locale.nls"))


def cp936() -> CodePage:
    return CodePage(nls("c_936.nls"), 936)


# ------------------------------------------------------------------ conversions

def mbtowc(cp: CodePage, flags: int, src: bytes, dstlen: int) -> tuple[int, list[int], int]:
    """kernelbase's MultiByteToWideChar for a DBCS code page, as far as the
    runtime implements it: (return, the units written, the last error)."""
    if flags & ~0xF:
        return 0, [], 1004
    if flags & 8:
        i = 0
        while i < len(src):
            b = src[i]
            if cp.offsets[b]:
                if i + 1 == len(src):
                    return 0, [], 1113
                if cp.pair(b, src[i + 1]) == cp.unidefault and (b << 8 | src[i + 1]) != cp.transuni:
                    return 0, [], 1113
                i += 2
                continue
            w = cp.single[b]
            if (w == cp.unidefault and b != cp.transuni) or 0xE000 <= w <= 0xF8FF:
                return 0, [], 1113
            i += 1
    out, i = [], 0
    while i < len(src):
        b = src[i]
        if cp.offsets[b]:
            if i + 1 < len(src) and src[i + 1]:
                out.append(cp.pair(b, src[i + 1]))
                i += 2
                continue
            out.append(cp.unidefault)
        else:
            out.append(cp.single[b])
        i += 1
    if not dstlen:
        return len(out), [], 0
    if len(out) > dstlen:
        return 0, out[:dstlen], 122
    return len(out), out, 0


def wctomb(cp: CodePage, flags: int, src: list[int], dstlen: int,
           defchar: bytes | None = None) -> tuple[int, bytes, int, int]:
    """kernelbase's WideCharToMultiByte for a DBCS code page (wcstombs_dbcs_slow;
    the fast path's bytes are the same): (return, bytes written, used, error)."""
    if flags & ~0x6F0:
        return 0, b"", 0, 1004
    d = defchar if defchar is not None else bytes([cp.default & 0xFF, cp.default >> 8])
    dv = d[0] if not d[1] else (d[0] << 8 | d[1])
    out, used = bytearray(), 0
    for u in src:
        res = cp.wc[u]
        if not cp.valid_mapping(u, bool(flags & 0x400)):
            res, used = dv, 1
        if res >> 8:
            if dstlen and len(out) + 1 == dstlen:
                return 0, bytes(out), used, 122
            out.append(res >> 8)
        out.append(res & 0xFF)
        if dstlen and len(out) > dstlen:
            return 0, bytes(out[:dstlen]), used, 122
    if dstlen and len(out) > dstlen:
        return 0, bytes(out[:dstlen]), used, 122
    return len(out), bytes(out) if dstlen else b"", used, 0


# ------------------------------------------------------------------ the model

def model() -> dict:
    """Everything the header holds, as plain data."""
    loc, cp = locale_nls(), cp936()
    lcids = [lcid for lcid, _ in loc.lcid_index()]
    installed = loc.installed()
    answers: dict[tuple[int, int], tuple[str, list[int], int]] = {}
    codepages: dict[int, tuple[int, int]] = {}
    for lcid, idx in installed:
        codepages[lcid] = (loc.field(idx, "idefaultansicodepage"), loc.field(idx, "idefaultcodepage"))
        for t in CATALOGUE_LCTYPES:
            answers[(lcid, t)] = loc.answer(idx, t)
    for lcid in DATA_LCIDS:
        idx = loc.locale_of(lcid)
        codepages[lcid] = (loc.field(idx, "idefaultansicodepage"), loc.field(idx, "idefaultcodepage"))
        for t in DP.LOCALE_LCTYPES:
            answers[(lcid, t)] = loc.answer(idx, t)
    for key, (_k, units, _v) in answers.items():
        if any(0xD800 <= u < 0xE000 for u in units):
            raise Refusal(f"LCID {key[0]:#06x} LCTYPE {key[1]:#06x}: a surrogate, which the "
                          "runtime's code pages do not convert")
    leads = cp.leads
    if leads != list(range(leads[0], leads[-1] + 1)):
        raise Refusal("code page 936's lead bytes are not one range")
    if any(cp.single[b] for b in leads):
        raise Refusal("code page 936 maps a lead byte alone to a character")
    lead_ranges = list(cp.leadbyte)
    if lead_ranges[:2] != [leads[0], leads[-1]] or any(lead_ranges[2:]):
        raise Refusal("code page 936's header lead-byte ranges are not its table's")
    fits = fits_of(cp)
    return {"lcids": lcids, "installed": installed, "answers": answers,
            "codepages": codepages, "cp936": cp, "fits": fits}


def inverse(cp: CodePage) -> dict[int, int]:
    """The unit-to-bytes answer runtime/sa2_locale.c derives from the bytes-to-unit
    tables: the first single byte, then the first pair in (lead, trail) order,
    that maps to the unit; pairs that map to the Unicode default character are
    Wine's invalid trail bytes and are skipped."""
    inv: dict[int, int] = {}
    for b in range(256):
        if not cp.offsets[b]:
            inv.setdefault(cp.single[b], b)
    for lead in cp.leads:
        for trail in range(256):
            u = cp.pair(lead, trail)
            if u != cp.unidefault:
                inv.setdefault(u, (lead << 8) | trail)
    return inv


def fits_of(cp: CodePage) -> list[tuple[int, int]]:
    """Every unit whose c_936.nls answer is not `inverse`'s (or the default
    character where it has none): Wine's best fit, which the header lists."""
    inv = inverse(cp)
    return [(u, cp.wc[u]) for u in range(0x10000) if inv.get(u, cp.default) != cp.wc[u]]


def trail_range(cp: CodePage) -> tuple[int, int]:
    """The first and last trail byte any lead byte pairs with; every pair outside
    them maps to the Unicode default character, which the header leaves out."""
    valid = [t for lead in cp.leads for t in range(256) if cp.pair(lead, t) != cp.unidefault]
    lo, hi = min(valid), max(valid)
    if any(cp.pair(lead, t) != cp.unidefault for lead in cp.leads for t in range(256)
           if not lo <= t <= hi):
        raise Refusal("code page 936 has a valid pair outside its trail range")
    return lo, hi


# ------------------------------------------------------------------ emit

def _rows(values: list[str], per: int) -> list[str]:
    return ["    " + " ".join(values[i:i + per]) for i in range(0, len(values), per)]


def emit_text() -> str:
    m = model()
    cp: CodePage = m["cp936"]
    pool: list[int] = []
    at: dict[tuple[int, ...], int] = {}

    def intern(units: list[int]) -> int:
        k = tuple(units)
        if k not in at:
            at[k] = len(pool)
            pool.extend(units)
        return at[k]

    trail_lo, trail_hi = trail_range(cp)
    kinds = {"string": 0, "number": 1, "grouping": 2}
    rows = []
    for (lcid, t), (kind, units, value) in sorted(m["answers"].items()):
        rows.append((lcid, t, kinds[kind], value, intern(units), len(units)))
    if len(pool) >= 0x10000 or max(r[5] for r in rows) >= 0x100:
        raise Refusal("the answers do not fit the header's 16-bit offsets and 8-bit lengths")
    lines = [
        "/* GENERATED by tools/s4/nls_data.py emit. Do not edit: run it again.",
        " *",
        " * Wine's locale and code page 936 data, as the oracle's Proton answers from",
        " * them (files/share/wine/nls):",
    ]
    for name in ("locale.nls", "c_936.nls"):
        lines.append(f" *   {name:<11} sha256 {NLS[name]}")
    lines += [
        " * read as kernelbase reads them (tools/s4/nls_data.py says where). The check",
        " * that holds this file to them, the runtime's compiled answers to this file,",
        " * and both to the native traces, is tools/s4/locale_check.py tables.",
        " *",
        " * The oracle's Proton is Steam's Proton - Experimental, updated in place, so",
        " * the sha256 above identify the files, not a build: they read as above under",
        f" * {CT.PROTON_BUILD}. The values are Wine's compilation of Unicode CLDR",
        " * and Microsoft's published code page 936 tables; none is the game's. */",
        "#ifndef SA2_RUNTIME_LOCALEDATA_H",
        "#define SA2_RUNTIME_LOCALEDATA_H",
        "",
        "#include <stdint.h>",
        "",
        "/* locale.nls's LCID index, ascending: IsValidLocale's TRUE. */",
        f"#define SA2_NLS_LCIDS {len(m['lcids'])}u",
        f"static const uint32_t sa2_nls_lcids[{len(m['lcids'])}] = {{",
    ]
    lines += _rows([f"0x{x:05X}u," for x in m["lcids"]], 10)
    lines += [
        "};",
        "",
        "/* EnumSystemLocalesA(LCID_INSTALLED)'s LCIDs, in its order. */",
        f"#define SA2_NLS_INSTALLED {len(m['installed'])}u",
        f"static const uint32_t sa2_nls_installed[{len(m['installed'])}] = {{",
    ]
    lines += _rows([f"0x{x:05X}u," for x, _ in m["installed"]], 10)
    lines += [
        "};",
        "",
        "/* Each answered locale's LOCALE_IDEFAULTANSICODEPAGE and LOCALE_IDEFAULTCODEPAGE",
        " * as the file holds them (65001 is UTF-8, which kernelbase replaces), ascending. */",
        "typedef struct { uint32_t lcid; uint16_t acp, oemcp; } sa2_nls_codepages;",
        f"#define SA2_NLS_CODEPAGES {len(m['codepages'])}u",
        f"static const sa2_nls_codepages sa2_nls_cps[{len(m['codepages'])}] = {{",
    ]
    lines += _rows([f"{{0x{k:05X}u, {a}u, {o}u}}," for k, (a, o) in sorted(m["codepages"].items())],
                   4)
    lines += [
        "};",
        "",
        "/* get_locale_info's answers, ascending by (lcid, lctype): the installed",
        " * locales' LOCALE_SABBREVLANGNAME and LOCALE_SENGLANGUAGE, and every LCTYPE of",
        " * SA2_DP_LOCALE_LCTYPES for 0x0409 and 0x0804. kind 0 is a string, 1 a number",
        " * (LOCALE_RETURN_NUMBER answers `value`), 2 a grouping; the units, without",
        " * the NUL, are sa2_nls_units[at .. at + n). */",
        "typedef struct { uint32_t lcid; uint16_t lctype; uint8_t kind, n; uint32_t value;",
        "                 uint16_t at; } sa2_nls_answer;",
        f"#define SA2_NLS_ANSWERS {len(rows)}u",
        f"static const sa2_nls_answer sa2_nls_answers[{len(rows)}] = {{",
    ]
    lines += _rows([f"{{0x{l:05X}u, 0x{t:04X}u, {k}u, {n}u, {v}u, {a}u}},"
                    for l, t, k, v, a, n in rows], 3)
    lines += [
        "};",
        f"static const uint16_t sa2_nls_units[{max(len(pool), 1)}] = {{",
    ]
    lines += _rows([f"0x{u:04X}u," for u in pool] or ["0u,"], 12)
    lines += [
        "};",
        "",
        "/* Code page 936. The header's characters: */",
        f"#define SA2_CP936_DEFAULT      0x{cp.default:04X}u   /* DefaultChar */",
        f"#define SA2_CP936_UNIDEFAULT   0x{cp.unidefault:04X}u   /* UniDefaultChar */",
        f"#define SA2_CP936_TRANSUNI     0x{cp.transuni:04X}u   /* TransUniDefaultChar */",
        f"#define SA2_CP936_LEAD_FIRST   0x{cp.leads[0]:02X}u",
        f"#define SA2_CP936_LEAD_LAST    0x{cp.leads[-1]:02X}u",
        "/* MultiByteTable: each byte alone (a lead byte alone is 0). */",
        "static const uint16_t sa2_nls_936_single[256] = {",
    ]
    lines += _rows([f"0x{u:04X}u," for u in cp.single], 16)
    lines += [
        "};",
        "/* DBCSOffsets' rows between the first and last trail byte any lead byte",
        " * pairs with: the unit of lead byte L and trail byte T is",
        " * sa2_nls_936_pairs[(L - SA2_CP936_LEAD_FIRST) * SA2_CP936_TRAILS",
        " *                 + T - SA2_CP936_TRAIL_FIRST],",
        " * and every pair outside them is SA2_CP936_UNIDEFAULT. */",
        f"#define SA2_CP936_TRAIL_FIRST  0x{trail_lo:02X}u",
        f"#define SA2_CP936_TRAIL_LAST   0x{trail_hi:02X}u",
        f"#define SA2_CP936_TRAILS       {trail_hi - trail_lo + 1}u",
        f"static const uint16_t sa2_nls_936_pairs[{len(cp.leads) * (trail_hi - trail_lo + 1)}] = {{",
    ]
    lines += _rows([f"0x{cp.pair(lead, t):04X}," for lead in cp.leads
                    for t in range(trail_lo, trail_hi + 1)], 16)
    lines += [
        "};",
        "/* WideCharTable where it is not the first byte sequence the tables above map",
        " * to the unit (nor the default where none does): Wine's best fit, ascending. */",
        "typedef struct { uint16_t w, mb; } sa2_nls_936_fit;",
        f"#define SA2_CP936_FITS {len(m['fits'])}u",
        f"static const sa2_nls_936_fit sa2_nls_936_fits[{len(m['fits'])}] = {{",
    ]
    lines += _rows([f"{{0x{u:04X}u, 0x{b:04X}u}}," for u, b in m["fits"]], 6)
    lines += ["};", "", "#endif", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args != ["emit"]:
        print(__doc__.split("\n\n")[1])
        return 2
    HEADER.write_text(emit_text())
    print(f"NLS wrote {HEADER.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
