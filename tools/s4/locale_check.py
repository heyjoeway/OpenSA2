#!/usr/bin/env python3
"""The locale, the environment block, the standard handles and the performance
frequency, held to Wine and to native.

Subtitle: hold runtime/sa2_locale.c's compiled answers and the generated
runtime/sa2_localedata.h to the oracle's NLS files and to the native traces
(tables, offline), and the lifted boot's own calls and words to the native
boot's, with five breaks that must move them (run).

    source tools/env.sh
    .venv/bin/python tools/s4/locale_check.py tables [--json OUT]           # seconds
    .venv/bin/python tools/s4/locale_check.py run [--capture DIR] [--json OUT]   # six lifted boots
    .venv/bin/python tools/s4/locale_check.py run --write [--capture DIR]   # both, and the receipt

Why this exists
---------------
Nine imports did not reproduce native until 2026-09-26, and M4 did not count
them (Documentation/refusal-reasons.json, as it was): EnumSystemLocalesA,
GetLocaleInfoA and W, IsValidLocale, IsValidCodePage, code page 936's
GetCPInfo, MultiByteToWideChar and WideCharToMultiByte, and
GetEnvironmentStringsW. Each archive loader of the game calls
setlocale(LC_ALL, "Chinese-simplified") and then setlocale(LC_ALL, ""), and
natively the first succeeds and spends the load in 0x0804 and code page 936
(agents-journals/s4/2026-09-26-locale.md). The runtime now answers all of it:
the user's locale, the environment block and the standard handles from the
capture's device profile (runtime/sa2_devprofile.h), every other locale and
code page 936 from Wine's data (runtime/sa2_locale.h). A reproduction nobody
checks is a claim, so this checks it, and a break nobody runs is prose, so
this runs them.

The native side
---------------
Three traced native boots, pinned (TRACES): locale-trace-v1a, which hooked the
locale imports at Init (the locale unit's probe), and n0-trace-v1a and v1b,
which also logged every code page conversion whole and the environment block
(the N0 unit's probe). Both probes are scratch, never registered; they hooked
the imports' slots at the Mod Loader's Init, so they see every call after it,
which is every call this checks but GetEnvironmentStringsW, GetStdHandle and
GetFileType's first, which the C runtime makes at start-up; those the capture's
profile records instead (its probe's header says how). The word logs are the
registered boot's (Documentation/replays.json).

What it checks (each can fail, and a mode fails if any of its checks does)
--------------------------------------------------------------------------
tables, offline:
  * header_is_generated: runtime/sa2_localedata.h is what tools/s4/nls_data.py
    emit writes now;
  * data_follows_wine: runtime/sa2_locale.c, compiled, answers as the model of
    kernelbase over the pinned files (tools/s4/nls_data.py) does: every LCID's
    IsValidLocale, the installed-locale order, every answer the data holds,
    which of the profile's LCTYPEs are numbers (sa2_locale_numeric), GetCPInfo's
    lead-byte ranges (sa2_cp936_lead_bytes), and for code page 936 every byte
    alone, every lead and trail pair, every unit's bytes with and without
    WC_NO_BEST_FIT_CHARS, and the conversions of CONVERSION_CASES;
  * data_follows_native: the same model answers every call the traces hold as
    the traces show it answered: each enumeration's order and each callback's
    GetLocaleInfoA, every GetLocaleInfoA and W of 0x0409 and 0x0804,
    IsValidLocale, IsValidCodePage, GetCPInfo(936) and every code page 936
    conversion, whole; and the user's locale as the locale unit's native
    capture recorded it (locale-boot-v2a's profile) is the model's 0x0409.
run, the boot replay's lifted build on the capture's profile, six times: with
the runtime's answers and under SA2_LOCALE_BREAK=refuse and noenum,
SA2_ENV_BREAK=empty, SA2_STDIO_BREAK=pipe and SA2_TIME_BREAK=qpf, each
recording its answers (SA2_LOCALE_ANSWERS, SA2_CTYPE_ANSWERS):
  * enumeration_follows_native: every lifted EnumSystemLocalesA hands its
    callback the LCIDs one native enumeration did, in order, and the callback
    answers as natively; and both native passes (the whole catalogue, and the
    one that stops at 0x0804) occur lifted;
  * locale_info_follows_native: every lifted GetLocaleInfoA and W, keyed by
    call site, LCID, LCTYPE and buffer length, has native calls with that key
    and answers as they did (return, bytes written, error), and every native key
    has a lifted call;
  * validity_follows_native: likewise IsValidLocale and IsValidCodePage;
  * cpinfo_follows_native: likewise GetCPInfo, its 18 bytes;
  * conversions_follow_native: likewise every code page 936
    MultiByteToWideChar and WideCharToMultiByte, keyed by call site, flags,
    lengths and whole input;
  * environment_follows_profile: GetEnvironmentStringsW answered the block
    the profile records the C runtime saw, to its final NUL;
  * std_handles_follow_profile: GetStdHandle answered a handle exactly where
    the profile records one, and GetFileType of it the recorded type;
  * locale_words_follow_native: the C runtime's locale words (WORDS) equal
    each native boot's on every tick both logs hold;
  * refuse_moves_them: under SA2_LOCALE_BREAK=refuse the enumeration, the
    locale answers and the locale words differ from native's;
  * noenum_moves_them: under SA2_LOCALE_BREAK=noenum no 0x0804 answer and no
    code page 936 conversion is made, so native's go uncovered;
  * env_break_moves_it, stdio_break_moves_it: under the two breaks the
    environment and standard-handle checks fail;
  * frequency_words_follow_native: the two words QueryPerformanceFrequency's
    answer reaches (TIME_WORDS: Sofdec's copy of it, set during Init, and its
    timer unit, set at tick 380) equal each native boot's on every tick both
    logs hold; the G4 interrogation found the runtime answering 3,579,545
    where every native boot holds Wine's 10,000,000 (Reviewer B's finding 1);
  * frequency_answer_is_native: the answer the lifted guest kept at Init
    (Sofdec's copy on tick 1) equals each native boot's recorded answer, the
    word log's start record (ref_hz: harness/replay/native_tick_probe.c calls
    QueryPerformanceFrequency in the game's process at Init, wlog_init). The
    answer is recorded by every native capture; until the review of the G4
    round-one fold (its runtime reviewer's finding 1) the records said no
    capture recorded it;
  * qpf_break_moves_it: under SA2_TIME_BREAK=qpf (the answer before
    2026-09-27) both words and the answer differ from every native boot's.
A lifted log compared through no tick, or a native key with no lifted call, is
not a pass. The native keys the lifted run has no call for are listed.

The receipt
-----------
The nine imports were retired from Documentation/refusal-reasons.json on the
strength of these checks, so their result is durable evidence, not scratch:
`run --write` runs tables, then the five boots, and writes
Documentation/receipts/s4-locale-check-v1.json (RECEIPT) -- its inputs (the
runtime's digest, the tracked tools and files that produced the numbers in
inputs.pipeline, the pinned NLS files and native traces, the capture, its
device profile and the native word logs, each with its sha256), the command,
the commit and whether the tree differed from it, and every check of both
modes with the counts behind it. It writes nothing unless every check of both
modes holds and no input moved while the boots ran, and it refuses --profile:
a profile no capture carries is a stand-in, and the receipt is the record that
a capture's own profile, written by the probe in the tree, was answered as
native. Without --write nothing here writes a tracked file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))

import device_profile as DP  # noqa: E402
import nls_data as N  # noqa: E402

CAPTURES = ROOT / "private" / "generated" / "oracle" / "captures"
TRACES = {
    "locale-trace-v1a": (CAPTURES / "locale-trace-v1a" / "sa2-locale-trace.txt",
                         "52cb1dd8cf09762f6f70943ed99def975931ed690178c4e11f0d1af52d50a8be"),
    "n0-trace-v1a": (CAPTURES / "n0-trace-v1a" / "sa2-n0-trace.txt",
                     "04d6bab54509fa2b8bdd325ef586e0e432133b128e2c1a4127aeff213ee8465f"),
    "n0-trace-v1b": (CAPTURES / "n0-trace-v1b" / "sa2-n0-trace.txt",
                     "c99ee1d71545d679960bef4faf67b6f0ca66a72f510bce2b8f512ac3ca3fdfb6"),
}
# The traces that log every conversion whole (the N0 probe); the locale unit's
# cut inputs at 96 units and logged no output.
WHOLE = ("n0-trace-v1a", "n0-trace-v1b")
# The user's locale as the locale unit's probe recorded it natively.
USER_LOCALE_PROFILE = (CAPTURES / "locale-boot-v2a" / "sa2-device-profile.txt",
                       "a0f9f7d8165623b680ab4c4fb5a132280984a2a277d9988c5ed359233bf227bf")
LOCALE_C = ROOT / "runtime" / "sa2_locale.c"
ZIG = Path(os.environ.get("SA2_ZIG") or
           ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig")

# The C runtime's locale words (the locale unit's): __lc_handle's categories,
# its code pages, ___getlocaleinfo's LC_INT_TYPE buffer and __lc_time_c's count.
LC_HANDLE = 0x01751B90
CATEGORIES = {"LC_COLLATE": LC_HANDLE + 4, "LC_CTYPE": LC_HANDLE + 8,
              "LC_MONETARY": LC_HANDLE + 12, "LC_NUMERIC": LC_HANDLE + 16,
              "LC_TIME": LC_HANDLE + 20}
WORDS = {**CATEGORIES,
         "__lc_codepage": LC_HANDLE + 24, "__lc_collate_cp": LC_HANDLE + 28,
         "getlocaleinfo_int_buffer": 0x01751ED4, "getlocaleinfo_int_buffer_hi": 0x01751ED8,
         "__lc_time_c_refcount": 0x00917FC4}
# Calls the C runtime makes before any probe's Init, which no trace can hold,
# by call site: each is answered from the capture's profile, which recorded the
# answer natively, and is held to that. __crtGetLocaleInfoA_stat asks once, at
# start-up, whether the W function works: GetLocaleInfoW(LOCALE_NEUTRAL,
# LOCALE_ILANGUAGE, NULL, 0).
PRE_INIT_SITES = {0x007BC578: "__crtGetLocaleInfoA_stat's probe for GetLocaleInfoW"}
# The words QueryPerformanceFrequency's answer reaches (runtime/sa2_imports.c
# SA2_QPC_HZ): Sofdec's copy of the frequency, which Init writes (the keyframe of
# every boot and Metal Harbor word log), and its timer unit, written at 0x007f7c51
# on tick 380 of every v15 boot; 0x00989680 in every native log.
TIME_WORDS = {"sofdec_frequency": 0x01C05CD0, "sofdec_timer_unit": 0x0176A608}
BREAKS = {"refuse": ("SA2_LOCALE_BREAK", "refuse"), "noenum": ("SA2_LOCALE_BREAK", "noenum"),
          "env": ("SA2_ENV_BREAK", "empty"), "stdio": ("SA2_STDIO_BREAK", "pipe"),
          "qpf": ("SA2_TIME_BREAK", "qpf")}
# Conversions data_follows_wine runs through the compiled code and the model:
# bytes (MultiByteToWideChar) and units (WideCharToMultiByte), with the
# flags the model covers. The C runtime's own inputs are the native traces'.
CONVERSION_CASES = {
    "mb": [bytes(range(1, 128)), bytes(range(256)), b"\x81", b"\x81\x00", b"\x81\x40\x41",
           b"\xa1\xa1\xb0\xa1\xff\x80", b"\xfe\xfe\x7f", b"\x81\x7f\x81\xff"],
    "wc": [list(range(1, 256)), [0x00A5, 0x20AC, 0xF8F5, 0x4E00, 0x6708, 0x3000],
           [0x0100, 0x0192, 0x2122, 0xD800, 0xFFFF, 0x0000]],
}


class Refusal(SystemExit):
    def __init__(self, message: str) -> None:
        super().__init__(f"LOCALE_REFUSED {message}")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ------------------------------------------------------------------ the model

def model_answer(m: dict, lcid: int, lctype: int, wide: bool, cch: int,
                 user_acp: int = 1252) -> tuple[int, bytes, int]:
    """kernelbase's GetLocaleInfoA/W of a locale the model holds, with no flag:
    (return, the bytes written, the last error when it returned 0)."""
    kind, units, _value = m["answers"][(lcid, lctype)]
    if wide:
        need = len(units) + 1
        if not cch:
            return need, b"", 0
        if cch < need:
            return 0, b"", 122
        return need, b"".join(u.to_bytes(2, "little") for u in units + [0]), 0
    acp = m["codepages"][lcid][0]
    acp = user_acp if acp == N.CP_UTF8 else acp
    data = b"".join(wide_to_cp(m, acp, u) for u in units + [0])
    if not cch:
        return len(data), b"", 0
    if cch < len(data):
        return 0, b"", 122
    return len(data), data, 0


def wide_to_cp(m: dict, cp: int, u: int) -> bytes:
    """WideCharToMultiByte(cp, 0) of one unit, as kernelbase's fast path does."""
    if cp == 1252:
        import ctype_check as CT
        mb, wc, _default = CT.cp1252_tables()
        return bytes([wc[u]])
    if cp == 936:
        b = m["cp936"].wc[u]
        return bytes([b >> 8, b & 0xFF]) if b >> 8 else bytes([b])
    if u < 0x80:
        return bytes([u])
    raise Refusal(f"U+{u:04X} through code page {cp}: the model has no table for it")


def model_valid(m: dict, lcid: int) -> int:
    if lcid in (0x0000, 0x0400, 0x0800):
        return 0
    if lcid in (0x0C00, 0x1000, 0x1400):
        return 1
    return int(lcid in set(m["lcids"]))


# ------------------------------------------------------------------ the traces

LINE = re.compile(r"^(\d+) f(\d+) t(\d+) ([0-9a-f]{8}) +(\S+)(.*)$")
STR = re.compile(r'(L?)"((?:[^"\\]|\\u[0-9a-f]{4}|\\x[0-9a-f]{2})*)"')


def unescape(s: str, wide: bool) -> bytes:
    """A trace string's bytes: UTF-16LE units for an L"" string."""
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\":
            n = 4 if s[i + 1] == "u" else 2
            out.append(int(s[i + 2:i + 2 + n], 16))
            i += 2 + n
        else:
            out.append(ord(s[i]))
            i += 1
    if wide:
        return b"".join(u.to_bytes(2, "little") for u in out)
    return bytes(out)


def trace_lines(name: str) -> list[str]:
    path, pin = TRACES[name]
    if not path.is_file():
        raise Refusal(f"{path} is missing")
    if sha256(path) != pin:
        raise Refusal(f"{path} is not the pinned native trace")
    return path.read_text(encoding="latin-1").splitlines()


def native_calls(name: str) -> dict:
    """What a trace holds, keyed as the lifted recorders key it:
    info {(fn, caller, lcid, lctype, cch): {(ret, bytes, err)}}, valid
    {(fn, arg): {ret}}, cpinfo {cp: {18 bytes}}, conv {(fn, caller, flags, n,
    dn, input): {(ret, output)}} (the whole traces only), enums [[(lcid,
    answer), ...] per call]."""
    out = {"info": {}, "valid": {}, "cpinfo": {}, "conv": {}, "enums": []}
    cur: dict[str, list] = {}
    for ln in trace_lines(name):
        m = LINE.match(ln)
        if not m:
            continue
        _seq, _frame, tid, caller, fn, rest = m.groups()
        caller = int(caller, 16)
        if fn == "EnumSystemLocalesA":
            if rest.endswith(" begin"):
                cur[tid] = []
            else:
                out["enums"].append(tuple(cur.pop(tid, [])))
            continue
        if fn == "enum-callback":
            e = re.match(r' "([0-9a-f]{8})" -> (\d+)$', rest)
            cur.setdefault(tid, []).append((int(e.group(1), 16), int(e.group(2))))
            continue
        if fn in ("GetLocaleInfoA", "GetLocaleInfoW"):
            a = re.match(r" ([0-9a-f]{8}) ([0-9a-f]{8}) (-?\d+) -> (-?\d+)(.*)$", rest)
            lcid, lctype, cch, ret, tail = (int(a.group(1), 16), int(a.group(2), 16),
                                            int(a.group(3)), int(a.group(4)), a.group(5))
            if lctype & 0x20000000:
                raise Refusal(f"{name}: a LOCALE_RETURN_NUMBER call, which this reader does "
                              "not read")
            err = re.search(r" err=(\d+)", tail)
            data = b""
            if ret > 0 and cch > 0:
                s = STR.search(tail)
                data = unescape(s.group(2), fn.endswith("W")) + (b"\0\0" if fn.endswith("W")
                                                                  else b"\0")
            out["info"].setdefault((fn, caller, lcid, lctype, cch), set()).add(
                (ret, data, int(err.group(1)) if err else 0))
            continue
        if fn == "IsValidLocale":
            a = re.match(r" ([0-9a-f]{8}) ([0-9a-f]{8}) -> (\d+)$", rest)
            out["valid"].setdefault((fn, int(a.group(1), 16)), set()).add(int(a.group(3)))
            continue
        if fn == "IsValidCodePage":
            a = re.match(r" (\d+) -> (\d+)$", rest)
            out["valid"].setdefault((fn, int(a.group(1))), set()).add(int(a.group(2)))
            continue
        if fn == "GetCPInfo":
            # CPINFO's 18 bytes from the three fields both probes log (the N0
            # probe's raw= adds the two bytes of padding past them).
            a = re.match(r" (\d+) -> (\d+) max=(\d+) default=([0-9a-f]{4}) lead=([0-9a-f]{24})",
                         rest)
            info = int(a.group(3)).to_bytes(4, "little") + bytes.fromhex(a.group(4)) + \
                bytes.fromhex(a.group(5))
            raw = re.search(r" raw=([0-9a-f]+)", rest)
            if raw and bytes.fromhex(raw.group(1))[:18] != info:
                raise Refusal(f"{name}: GetCPInfo's raw bytes are not its fields")
            out["cpinfo"].setdefault(int(a.group(1)), set()).add(info)
            continue
        if fn in ("MultiByteToWideChar", "WideCharToMultiByte") and name in WHOLE:
            a = re.match(r" cp=(\d+) ([0-9a-f]{8}) (-?\d+) (-?\d+) -> (-?\d+)", rest)
            if int(a.group(1)) != 936:
                continue
            kv = dict(re.findall(r" (\w+)=(\S+)", rest))
            if kv.get("defchar", "null") != "null" or kv.get("used", "null") != "null":
                raise Refusal(f"{name}: a code page 936 conversion with a default character, "
                              "which this reader does not read")
            wide_in = fn.startswith("Wide")
            raw = bytes.fromhex(kv["in"])
            inp = tuple(int(kv["in"][i:i + 4], 16) for i in range(0, len(kv["in"]), 4)) \
                if wide_in else tuple(raw)
            outp = kv.get("out", "")
            outv = tuple(int(outp[i:i + 4], 16) for i in range(0, len(outp), 4)) \
                if not wide_in else tuple(bytes.fromhex(outp))
            out["conv"].setdefault((fn, caller, int(a.group(2), 16), int(a.group(3)),
                                    int(a.group(4)), inp), set()).add((int(a.group(5)), outv))
    return out


def native_all() -> dict:
    merged = {"info": {}, "valid": {}, "cpinfo": {}, "conv": {}, "enums": []}
    for name in TRACES:
        n = native_calls(name)
        for k in ("info", "valid", "cpinfo", "conv"):
            for key, answers in n[k].items():
                merged[k].setdefault(key, set()).update(answers)
        merged["enums"] += n["enums"]
    return merged


# ------------------------------------------------------------------ tables mode

DRIVER = r"""
#include <stdio.h>
#include "sa2_locale.h"
static void mb(unsigned flags, const unsigned char *s, unsigned n, unsigned dstlen) {
    uint16_t out[1024]; int of = 0;
    int bad = (flags & 8u) && sa2_cp936_invalid_chars(s, n);
    unsigned r = bad ? 0u : sa2_cp936_mbtowc(s, n, out, dstlen, &of);
    printf("mb %u %u %u %d %d", flags, n, dstlen, bad, of);
    for (unsigned i = 0; i < (dstlen ? r : 0u); i++) printf(" %04x", out[i]);
    printf(" = %u\n", r);
}
static void wc(unsigned flags, const uint16_t *s, unsigned n, unsigned dstlen) {
    uint8_t out[2048]; int used = 0, of = 0;
    const uint16_t d = sa2_cp936_default();
    const uint8_t dc[2] = {(uint8_t)(d & 0xff), (uint8_t)(d >> 8)};
    unsigned r = sa2_cp936_wctomb(s, n, out, dstlen, dc, (flags & 0x400u) != 0, &used, &of);
    printf("wc %u %u %u %d %d", flags, n, dstlen, used, of);
    for (unsigned i = 0; i < (dstlen ? r : 0u); i++) printf(" %02x", out[i]);
    printf(" = %u\n", r);
}
int main(void) {
    for (unsigned l = 0; l < 0x10000u; l++) if (sa2_locale_valid(l)) printf("valid %x\n", l);
    for (unsigned i = 0; i < sa2_locale_installed_count(); i++)
        printf("installed %u %x\n", i, sa2_locale_installed_at(i));
    CASES
    { uint8_t lb[12]; sa2_cp936_lead_bytes(lb); printf("leadbytes");
      for (unsigned i = 0; i < 12u; i++) printf(" %02x", lb[i]); printf("\n"); }
    NUMERIC
    for (unsigned b = 0; b < 256u; b++)
        printf("single %02x %04x %d\n", b, sa2_cp936_single((uint8_t)b), sa2_cp936_is_lead((uint8_t)b));
    for (unsigned l = 0x80u; l < 0x100u; l++)
        for (unsigned t = 0; t < 256u; t++)
            printf("pair %02x%02x %04x\n", l, t, sa2_cp936_pair((uint8_t)l, (uint8_t)t));
    for (unsigned w = 0; w < 0x10000u; w++)
        printf("table %04x %04x %d %d\n", w, sa2_cp936_table((uint16_t)w),
               sa2_cp936_valid_mapping((uint16_t)w, 0), sa2_cp936_valid_mapping((uint16_t)w, 1));
    return 0;
}
"""


def driver_cases(m: dict) -> str:
    lines = []
    for (lcid, lctype) in sorted(m["answers"]):
        lines.append(f"{{ sa2_locale_answer a; uint32_t acp = 0, oem = 0;"
                     f" int k = sa2_locale_data(0x{lcid:x}u, 0x{lctype:x}u, &a);"
                     f" sa2_locale_codepages(0x{lcid:x}u, &acp, &oem);"
                     f" printf(\"answer %x %x %d %d %u %u %u\", 0x{lcid:x}u, 0x{lctype:x}u, k,"
                     f" a.kind, a.value, acp, oem);"
                     f" for (uint32_t i = 0; k && i < a.n; i++) printf(\" %04x\", a.w[i]);"
                     f" printf(\"\\n\"); }}")
    for flags in (0, 1, 9):
        for s in CONVERSION_CASES["mb"]:
            arr = ",".join(str(b) for b in s)
            for dstlen in (0, len(s) + 2, max(1, len(s) // 2)):
                lines.append(f"{{ static const unsigned char s[] = {{{arr}}};"
                             f" mb({flags}u, s, {len(s)}u, {dstlen}u); }}")
    for flags in (0, 0x400):
        for s in CONVERSION_CASES["wc"]:
            arr = ",".join(str(u) for u in s)
            for dstlen in (0, 2 * len(s) + 2, len(s)):
                lines.append(f"{{ static const uint16_t s[] = {{{arr}}};"
                             f" wc({flags}u, s, {len(s)}u, {dstlen}u); }}")
    return "\n    ".join(lines)


def compiled(m: dict) -> list[str]:
    if not ZIG.is_file():
        raise Refusal(f"the pinned zig is missing: {ZIG}")
    with tempfile.TemporaryDirectory() as td:
        drv, exe = Path(td) / "driver.c", Path(td) / "localedata"
        numeric = "\n    ".join(f'printf("numeric %x %d\\n", 0x{t:x}u, sa2_locale_numeric(0x{t:x}u));'
                                 for t in DP.LOCALE_LCTYPES)
        drv.write_text(DRIVER.replace("CASES", driver_cases(m)).replace("NUMERIC", numeric))
        env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(Path(td) / "zc"),
                   ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                       "ZIG_GLOBAL_CACHE_DIR",
                       str(ROOT / "private" / "generated" / "zig-cache-tests")))
        p = subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O1", "-Wall",
                            "-Wextra", "-Werror", "-I", str(ROOT / "runtime"), str(drv),
                            str(LOCALE_C), "-o", str(exe)], capture_output=True, text=True,
                           env=env)
        if p.returncode:
            raise Refusal(f"runtime/sa2_locale.c does not build:\n{p.stderr[-2000:]}")
        run = subprocess.run([str(exe)], capture_output=True, text=True, check=True)
    return run.stdout.splitlines()


def wine_problems(m: dict, lines: list[str]) -> list[str]:
    """Every line of the compiled driver against the model."""
    cp: N.CodePage = m["cp936"]
    bad: list[str] = []
    want_counts = {"answer ": len(m["answers"]), "single ": 256, "pair ": 128 * 256,
                   "table ": 0x10000, "installed ": len(m["installed"]),
                   "leadbytes": 1, "numeric ": len(DP.LOCALE_LCTYPES),
                   "mb ": 3 * 3 * len(CONVERSION_CASES["mb"]),
                   "wc ": 2 * 3 * len(CONVERSION_CASES["wc"])}
    for tag, n in want_counts.items():
        got = sum(1 for ln in lines if ln.startswith(tag))
        if got != n:
            bad.append(f"the driver printed {got} `{tag.strip()}` lines, not {n}")
    valid = sorted(int(ln.split()[1], 16) for ln in lines if ln.startswith("valid "))
    want_valid = sorted(x for x in range(0x10000) if model_valid(m, x))
    if valid != want_valid:
        bad.append(f"IsValidLocale differs for {sorted(set(valid) ^ set(want_valid))[:8]}")
    inst = [int(ln.split()[2], 16) for ln in lines if ln.startswith("installed ")]
    if inst != [lcid for lcid, _ in m["installed"]]:
        bad.append("the installed-locale order differs from locale.nls's enumeration")
    lead = [int(x, 16) for ln in lines if ln.startswith("leadbytes") for x in ln.split()[1:]]
    if lead != list(cp.leadbyte):
        bad.append(f"GetCPInfo(936)'s lead-byte ranges {lead}, c_936.nls {list(cp.leadbyte)}")
    for ln in (x for x in lines if x.startswith("numeric ")):
        t, num = int(ln.split()[1], 16), int(ln.split()[2])
        if num != int(m["answers"][(0x0409, t)][0] == "number"):
            bad.append(f"LCTYPE {t:#06x}: sa2_locale_numeric says {num}, the model's kind is "
                       f"{m['answers'][(0x0409, t)][0]}")
    kinds = {"string": 0, "number": 1, "grouping": 2}
    for ln in (x for x in lines if x.startswith("answer ")):
        f = ln.split()
        lcid, lctype, found, kind, value, acp, oem = (int(f[1], 16), int(f[2], 16), int(f[3]),
                                                      int(f[4]), int(f[5]), int(f[6]), int(f[7]))
        units = [int(x, 16) for x in f[8:]]
        wk, wu, wv = m["answers"][(lcid, lctype)]
        if (found, kind, units, value if wk == "number" else 0) != (1, kinds[wk], wu, wv) or \
                (acp, oem) != m["codepages"][lcid]:
            bad.append(f"LCID {lcid:#06x} LCTYPE {lctype:#06x}: the data answers {units} "
                       f"({kind}, {value}), the model {wu} ({wk}, {wv})")
    for ln in (x for x in lines if x.startswith("single ")):
        _t, b, u, lead = ln.split()
        b, u, lead = int(b, 16), int(u, 16), int(lead)
        if u != cp.single[b] or lead != int(bool(cp.offsets[b])):
            bad.append(f"byte {b:#04x}: {u:#06x} lead {lead}, Wine {cp.single[b]:#06x}")
    for ln in (x for x in lines if x.startswith("pair ")):
        _t, lt, u = ln.split()
        lead, trail, u = int(lt[:2], 16), int(lt[2:], 16), int(u, 16)
        want = cp.pair(lead, trail) if cp.offsets[lead] else cp.unidefault
        if u != want:
            bad.append(f"pair {lead:02x}{trail:02x}: {u:#06x}, Wine {want:#06x}")
    for ln in (x for x in lines if x.startswith("table ")):
        _t, w, mb, v0, v1 = ln.split()
        w, mb, v0, v1 = int(w, 16), int(mb, 16), int(v0), int(v1)
        want = (cp.wc[w], int(cp.valid_mapping(w, False)), int(cp.valid_mapping(w, True)))
        if (mb, v0, v1) != want:
            bad.append(f"U+{w:04X}: bytes {mb:#06x} valid {v0}/{v1}, Wine {want}")
    cases = iter(x for x in lines if x.startswith(("mb ", "wc ")))
    for flags in (0, 1, 9):
        for s in CONVERSION_CASES["mb"]:
            for dstlen in (0, len(s) + 2, max(1, len(s) // 2)):
                f = next(cases).split()
                bad_, of, r = int(f[4]), int(f[5]), int(f[-1])
                units = [int(x, 16) for x in f[6:-2]]
                got = (0, [], 1113) if bad_ else ((0, units, 122) if of else
                                                   (r, units, 0))
                want = N.mbtowc(cp, flags, s, dstlen)
                if got != want:
                    bad.append(f"MultiByteToWideChar(936, {flags}, {s[:8].hex()}.., "
                               f"{dstlen}): {got[:3]}, Wine {want}")
    for flags in (0, 0x400):
        for s in CONVERSION_CASES["wc"]:
            for dstlen in (0, 2 * len(s) + 2, len(s)):
                f = next(cases).split()
                used, of, r = int(f[4]), int(f[5]), int(f[-1])
                data = bytes(int(x, 16) for x in f[6:-2])
                got = (0, data, used, 122) if of else (r, data, used, 0)
                want = N.wctomb(cp, flags, s, dstlen)
                if got != want:
                    bad.append(f"WideCharToMultiByte(936, {flags:#x}, {s[:4]}.., {dstlen}): "
                               f"{got}, Wine {want}")
    return bad


def native_problems(m: dict, nat: dict) -> tuple[list[str], dict]:
    """The model against every traced native answer."""
    bad: list[str] = []
    counts = {"enumerations": len(nat["enums"]), "info_keys": 0, "conversion_keys": 0}
    order = [lcid for lcid, _ in m["installed"]]
    for e in nat["enums"]:
        lcids = [x for x, _ in e]
        if lcids != order[:len(lcids)] or any(r != 1 for _x, r in e[:-1]):
            bad.append(f"an enumeration of {len(lcids)} locales is not locale.nls's order")
    if not any(len(e) == len(order) for e in nat["enums"]) or \
            not any(e and e[-1] == (0x0804, 0) for e in nat["enums"]):
        bad.append("the traces hold no whole enumeration, or none that stops at 0x0804")
    for (fn, _caller, lcid, lctype, cch), answers in nat["info"].items():
        if (lcid, lctype) not in m["answers"]:
            bad.append(f"{fn}({lcid:#06x}, {lctype:#06x}) natively, which the data does not hold")
            continue
        counts["info_keys"] += 1
        want = model_answer(m, lcid, lctype, fn.endswith("W"), cch)
        for a in answers:
            if a != want:
                bad.append(f"{fn}({lcid:#06x}, {lctype:#06x}, {cch}): native {a}, model {want}")
    for (fn, arg), answers in nat["valid"].items():
        want = model_valid(m, arg) if fn == "IsValidLocale" else int(arg in (1252, 437, 936))
        if answers != {want}:
            bad.append(f"{fn}({arg:#x}): native {answers}, model {want}")
    for cp_, answers in nat["cpinfo"].items():
        if cp_ != 936:
            continue
        want = (2).to_bytes(4, "little") + bytes([m["cp936"].default & 0xFF,
                                                  m["cp936"].default >> 8]) + \
            bytes(m["cp936"].leadbyte)
        if answers != {want}:
            bad.append(f"GetCPInfo(936): native {sorted(a.hex() for a in answers)}, model "
                       f"{want.hex()}")
    for (fn, _caller, flags, n, dn, inp), answers in nat["conv"].items():
        counts["conversion_keys"] += 1
        if fn == "MultiByteToWideChar":
            r, units, _e = N.mbtowc(m["cp936"], flags, bytes(inp), dn)
            want = (r, tuple(units) if dn else ())
        else:
            r, data, _u, _e = N.wctomb(m["cp936"], flags, list(inp), dn)
            want = (r, tuple(data) if dn else ())
        for a in answers:
            if a != want:
                bad.append(f"{fn}(936, {flags:#x}, {n}, {dn}) from {_caller:#010x}: native {a}, "
                           f"model {want}")
    path, pin = USER_LOCALE_PROFILE
    if sha256(path) != pin:
        raise Refusal(f"{path} is not the pinned native profile")
    p, _ = DP.load(path)
    for t, (err, value) in zip(DP.LOCALE_LCTYPES, p.locale.info):
        _k, units, _v = m["answers"][(0x0409, t)]
        if err or value.decode("utf-8") != "".join(map(chr, units)):
            bad.append(f"0x0409 LCTYPE {t:#06x}: the recording host's {value!r}, the model "
                       f"{''.join(map(chr, units))!r}")
    return bad, counts


def tables() -> dict:
    m = N.model()
    lines = compiled(m)
    wine_bad = wine_problems(m, lines)
    nat = native_all()
    native_bad, counts = native_problems(m, nat)
    header_ok = N.HEADER.is_file() and N.HEADER.read_text() == N.emit_text()
    checks = {"header_is_generated": header_ok,
              "data_follows_wine": not wine_bad,
              "data_follows_native": counts["enumerations"] > 0 and counts["info_keys"] > 0
              and counts["conversion_keys"] > 0 and not native_bad}
    return {"checks": checks, "counts": counts, "installed": len(m["installed"]),
            "lcids": len(m["lcids"]), "answers": len(m["answers"]), "fits": len(m["fits"]),
            "differences": {"wine": wine_bad[:20], "native": native_bad[:20]},
            "nls": dict(N.NLS), "traces": {k: str(v[0].relative_to(ROOT)) for k, v in
                                           TRACES.items()},
            "ok": all(checks.values())}


# ------------------------------------------------------------------ run mode

def lifted_answers(path: Path) -> dict:
    """runtime/sa2_imports.c's SA2_LOCALE_ANSWERS lines, keyed as native_calls."""
    out = {"info": {}, "valid": {}, "cpinfo": {}, "enums": [], "env": [], "std": []}
    if not path.is_file():
        raise Refusal(f"the lifted run wrote no answers at {path}")
    for ln in path.read_text().splitlines():
        left, _, right = ln.partition(" -> ")
        f, r = left.split(), right.split()
        name, args, ret = f[3], [int(x, 16) for x in f[4:]], int(r[0], 16)
        kv = dict(x.split("=", 1) for x in r[1:])
        err = int(kv.get("err", "0"))
        data = bytes.fromhex(kv["out"]) if "out" in kv and ".." not in kv["out"] else b""
        caller = int(f[2], 16)
        if name in ("GetLocaleInfoA", "GetLocaleInfoW"):
            if name.endswith("W"):
                data = b"".join(int(kv["out"][i:i + 4], 16).to_bytes(2, "little")
                                for i in range(0, len(kv["out"]), 4))
            out["info"].setdefault((name, caller, args[0], args[1], args[3]), set()).add(
                (ret, data, err if not ret else 0))
        elif name in ("IsValidLocale", "IsValidCodePage"):
            out["valid"].setdefault((name, args[0]), set()).add(ret)
        elif name == "GetCPInfo":
            out["cpinfo"].setdefault(args[0], set()).add(data)
        elif name == "EnumSystemLocalesA":
            cbs = kv.get("cb", "")
            out["enums"].append(tuple((int(x.split(":")[0], 16), int(x.split(":")[1]))
                                      for x in cbs.split(",") if x))
        elif name == "GetEnvironmentStringsW":
            out["env"].append(b"".join(int(kv["out"][i:i + 4], 16).to_bytes(2, "little")
                                       for i in range(0, len(kv["out"]), 4)))
        elif name in ("GetStdHandle", "GetFileType"):
            out["std"].append((name, args[0], ret))
    return out


def lifted_conversions(path: Path) -> dict:
    """runtime/sa2_imports.c's SA2_CTYPE_ANSWERS code page 936 conversions,
    keyed as native_calls keys the whole traces'."""
    out: dict = {}
    if not path.is_file():
        raise Refusal(f"the lifted run wrote no conversions at {path}")
    for ln in path.read_text().splitlines():
        left, _, right = ln.partition(" -> ")
        f, r = left.split(), right.split()
        name = f[3]
        if name not in ("MultiByteToWideChar", "WideCharToMultiByte"):
            continue
        a = [int(x, 16) for x in f[4:]]
        if a[0] != 936:
            continue
        ret = int(r[0], 16)
        inp, outp = r[1][3:], r[2][4:]
        wide_in = name.startswith("Wide")
        iv = tuple(int(inp[i:i + 4], 16) for i in range(0, len(inp), 4)) if wide_in else \
            tuple(bytes.fromhex(inp))
        ov = tuple(bytes.fromhex(outp)) if wide_in else \
            tuple(int(outp[i:i + 4], 16) for i in range(0, len(outp), 4))
        n = a[3] - (1 << 32) if a[3] >> 31 else a[3]
        out.setdefault((name, int(f[2], 16), a[1], n, a[5], iv), set()).add((ret, ov))
    return out


def pre_init(lifted: dict, p) -> dict:
    """The recorded native answers of the PRE_INIT_SITES calls the lifted run
    made: GetLocaleInfoW of the user's locale with no buffer answers the
    recorded value's length and its NUL."""
    out = {}
    user = p.locale.lcid if p.locale else None
    for key in lifted:
        fn, caller, lcid, lctype, cch = key
        if caller not in PRE_INIT_SITES or fn != "GetLocaleInfoW" or cch or p.locale is None \
                or lcid not in (0, 0x0400, user) or lctype not in DP.LOCALE_LCTYPES:
            continue
        err, value = p.locale.info[DP.LOCALE_LCTYPES.index(lctype)]
        out[key] = {(0, b"", err)} if err else {(len(value.decode("utf-8")) + 1, b"", 0)}
    return out


def keyed(native: dict, lifted: dict) -> dict:
    """Each lifted key against native's: the keys native lacks, the keys whose
    answers differ, and the native keys the lifted run never made."""
    extra = sorted(str(k)[:160] for k in lifted if k not in native)
    differ = sorted(str(k)[:160] for k in lifted if k in native and not lifted[k] <= native[k])
    uncovered = sorted(str(k)[:160] for k in native if k not in lifted)
    return {"compared": sum(1 for k in lifted if k in native), "not_native": extra[:20],
            "not_native_count": len(extra), "differ": differ[:20], "differ_count": len(differ),
            "uncovered": uncovered[:20], "uncovered_count": len(uncovered)}


def follows(r: dict) -> bool:
    return r["compared"] > 0 and not r["not_native_count"] and not r["differ_count"] \
        and not r["uncovered_count"]


def series(log: Path, words: dict[str, int] | None = None) -> tuple[dict[int, dict[int, int]],
                                                                    int]:
    import wordlog as WL
    words = WORDS if words is None else words
    out: dict[int, dict[int, int]] = {va: {} for va in words.values()}
    lo, hi = min(words.values()), max(words.values()) + 4
    for (t, va, _old, new) in WL.changes(log, lo, hi):
        if va in out:
            out[va][t] = new
    info = WL.info(log)
    return out, int(info.get("last_tick") or info.get("ticks"))


def values(changes: dict[int, int], upto: int) -> list[int]:
    out, cur = [], changes.get(0, 0)
    for t in range(0, upto + 1):
        cur = changes.get(t, cur)
        out.append(cur)
    return out


def words_differ(a, b, words: dict[str, int] | None = None) -> dict[str, list[int]]:
    upto = min(a[1], b[1])
    out = {}
    for k, va in (WORDS if words is None else words).items():
        xa, xb = values(a[0][va], upto), values(b[0][va], upto)
        ticks = [t for t in range(1, upto + 1) if xa[t] != xb[t]]
        if ticks:
            out[k] = ticks[:5]
    return out


def profile_of(capture: Path | None, profile_file: Path | None) -> tuple[dict, list[Path]]:
    """The profile the lifted runs read, and the native word logs they are held
    to: the registered boot's (Documentation/replays.json), an unregistered
    capture's (`--capture`), or, before any capture carries the tree's
    profile, a profile file with the registered boot capture's guest paths
    (`--profile`, which the result names)."""
    import tick_replay as TR
    entry = next(e for e in json.loads(TR.REGISTRY.read_text())["replays"] if e["name"] == "boot")
    sources = [entry["native"]] + list(entry["native"].get("repeats", []))
    if any(not s.get("word_log") for s in sources):
        raise Refusal("a registered boot has no word log")
    logs = [ROOT / s["word_log"]["file"] for s in sources]
    stale = [str(p) for s, p in zip(sources, logs) if sha256(p) != s["word_log"]["sha256"]]
    if stale:
        raise Refusal(f"word log(s) not the registered bytes: {stale}")
    if capture is not None:
        try:
            prof = DP.capture_profile(capture, ROOT)
        except DP.Refusal as e:
            raise Refusal(str(e)) from None
        log = capture / "sa2-word-log.bin.zst"
        if not log.is_file():
            raise Refusal(f"{DP.rel(capture, ROOT)} has no word log")
        return prof, logs + [log]
    if profile_file is not None:
        receipt = json.loads((ROOT / entry["native"]["receipt"]).read_text())
        p, sha = DP.load(profile_file)
        return {"path": DP.rel(profile_file, ROOT), "sha256": sha, "counts": DP.counts(p),
                "guest": DP.guest_paths(receipt, entry["native"]["capture"])}, logs
    return TR.native_device_profile(entry), logs


def one_run(TR, name: str, env: dict, prof: dict, natives: list, nat: dict,
            installed: int) -> dict:
    work = TR.OUT / name
    work.mkdir(parents=True, exist_ok=True)
    la, ca = work / "locale-answers.txt", work / "ctype-answers.txt"
    for f in (la, ca):
        f.unlink(missing_ok=True)
    run = TR.run_lifted(name, {**env, "SA2_LOCALE_ANSWERS": str(la.relative_to(ROOT)),
                               "SA2_CTYPE_ANSWERS": str(ca.relative_to(ROOT))},
                        device_profile=prof, words=True)
    lifted = lifted_answers(la)
    conv = lifted_conversions(ca)
    p, _ = DP.load(ROOT / prof["path"])
    env_block = p.environment.block() if p.environment else None
    std_want = []
    if p.std_handles is not None:
        for i, (h, t) in enumerate(p.std_handles):
            std_want.append(None if h in (0, 0xFFFFFFFF) else t)
    std_got: dict[int, int | None] = {}
    handle_of: dict[int, int] = {}
    for fn, arg, ret in lifted["std"]:
        if fn == "GetStdHandle":
            i = {0xFFFFFFF6: 0, 0xFFFFFFF5: 1, 0xFFFFFFF4: 2}[arg]
            std_got[i] = None if ret in (0, 0xFFFFFFFF) else -1
            if ret not in (0, 0xFFFFFFFF):
                handle_of[ret] = i
        else:
            if arg in handle_of:
                std_got[handle_of[arg]] = ret
    import wordlog as WL
    l_ser = series(ROOT / run["words"]["path"])
    l_time = series(ROOT / run["words"]["path"], TIME_WORDS)
    # the answer the lifted guest kept: Sofdec's copy, written at Init, on tick 1
    kept = values(l_time[0][TIME_WORDS["sofdec_frequency"]], 1)[1]
    word_rows, time_rows = [], []
    for n in natives:
        n_ser = series(n)
        word_rows.append({"native": DP.rel(n, ROOT), "compared_through": min(l_ser[1], n_ser[1]),
                          "differ": words_differ(l_ser, n_ser)})
        n_time = series(n, TIME_WORDS)
        # QueryPerformanceFrequency's own answer in the native game's process: the
        # tick probe's start record (harness/replay/native_tick_probe.c wlog_init,
        # WlStart.ref_hz)
        time_rows.append({"native": DP.rel(n, ROOT),
                          "compared_through": min(l_time[1], n_time[1]),
                          "differ": words_differ(l_time, n_time, TIME_WORDS),
                          "native_answer": int(WL.info(n)["ref_hz"]),
                          "lifted_answer": kept})
    native_enums = {e for e in nat["enums"]}
    return {
        "env": env, "capture_sha256": run["sha256"],
        "answers": {"locale": DP.rel(la, ROOT), "locale_sha256": sha256(la),
                    "ctype": DP.rel(ca, ROOT), "ctype_sha256": sha256(ca)},
        "enumerations": {"lifted": len(lifted["enums"]),
                         "not_native": sum(1 for e in lifted["enums"] if e not in native_enums),
                         "whole": any(len(e) == installed and all(r for _x, r in e)
                                      for e in lifted["enums"]),
                         "stops_at_0804": any(e and e[-1] == (0x0804, 0)
                                              for e in lifted["enums"])},
        "info": keyed({**nat["info"], **pre_init(lifted["info"], p)}, lifted["info"]),
        "pre_init": sorted(str(k) for k in pre_init(lifted["info"], p)),
        "valid": keyed(nat["valid"], lifted["valid"]),
        "cpinfo": keyed(nat["cpinfo"], lifted["cpinfo"]),
        "conversions": keyed(nat["conv"], conv),
        "environment": {"calls": len(lifted["env"]),
                        "follows": bool(lifted["env"]) and env_block is not None
                        and all(b == env_block for b in lifted["env"])},
        "std_handles": {"want": std_want, "got": [std_got.get(i, "no call") for i in range(3)],
                        "follows": bool(std_want) and all(std_got.get(i, "no call") == w
                                                          for i, w in enumerate(std_want))},
        "words": word_rows,
        "time_words": time_rows,
    }


def enum_follows(r: dict) -> bool:
    e = r["enumerations"]
    return e["lifted"] > 0 and not e["not_native"] and e["whole"] and e["stops_at_0804"]


def words_follow(r: dict, key: str = "words") -> bool:
    return bool(r[key]) and all(w["compared_through"] > 0 and not w["differ"]
                                for w in r[key])


def run_checks(runs: dict) -> dict:
    """run's checks over the six runs one_run recorded."""
    c = runs["control"]
    ref, noe = runs["refuse"], runs["noenum"]
    return {
        "enumeration_follows_native": enum_follows(c),
        "locale_info_follows_native": follows(c["info"]),
        "validity_follows_native": follows(c["valid"]),
        "cpinfo_follows_native": follows(c["cpinfo"]),
        "conversions_follow_native": follows(c["conversions"]),
        "environment_follows_profile": c["environment"]["follows"],
        "std_handles_follow_profile": c["std_handles"]["follows"],
        "locale_words_follow_native": words_follow(c),
        "refuse_moves_them": (not enum_follows(ref) and not follows(ref["info"])
                              and bool(ref["words"])
                              and all(w["compared_through"] > 0 and
                                      any(k in w["differ"] for k in CATEGORIES)
                                      for w in ref["words"])),
        "noenum_moves_them": (not noe["enumerations"]["stops_at_0804"]
                              and noe["conversions"]["compared"] == 0
                              and noe["conversions"]["uncovered_count"] > 0
                              and not follows(noe["info"])),
        "env_break_moves_it": not runs["env"]["environment"]["follows"],
        "stdio_break_moves_it": not runs["stdio"]["std_handles"]["follows"],
        "frequency_words_follow_native": words_follow(c, "time_words"),
        "frequency_answer_is_native": (bool(c["time_words"])
                                       and all(w["native_answer"] == w["lifted_answer"]
                                               for w in c["time_words"])),
        "qpf_break_moves_it": (bool(runs["qpf"]["time_words"])
                               and all(w["compared_through"] > 0 and
                                       set(w["differ"]) == set(TIME_WORDS)
                                       and w["native_answer"] != w["lifted_answer"]
                                       for w in runs["qpf"]["time_words"])),
    }


def run_mode(capture: Path | None, profile_file: Path | None) -> dict:
    import tick_replay as TR
    prof, natives = profile_of(capture, profile_file)
    nat = native_all()
    installed = len(N.locale_nls().installed())
    runs = {"control": one_run(TR, "locale-control", {}, prof, natives, nat, installed)}
    for b, (var, value) in BREAKS.items():
        runs[b] = one_run(TR, f"locale-{b}", {var: value}, prof, natives, nat, installed)
    checks = run_checks(runs)
    return {"checks": checks, "profile": prof["path"], "profile_sha256": prof["sha256"],
            "natives": [DP.rel(n, ROOT) for n in natives],
            "traces": {k: str(v[0].relative_to(ROOT)) for k, v in TRACES.items()},
            "runs": runs, "ok": all(checks.values())}


# ------------------------------------------------------------------ the receipt

RECEIPT = ROOT / "Documentation" / "receipts" / "s4-locale-check-v1.json"
# The tracked files whose bytes produced the receipt's numbers besides the
# runtime (inputs.runtime_sha256), pinned in inputs.pipeline, which
# Documentation/scripts/check_receipt_pins.py holds to the receipt's own commit.
# The probe is here because the capture's profile is evidence only as that
# probe wrote it (DP.capture_profile refuses a capture another probe wrote).
PIPELINE = ("tools/s4/locale_check.py", "tools/s4/nls_data.py", "tools/s4/ctype_check.py",
            "tools/s4/device_profile.py", "tools/s4/tick_replay.py", "tools/s4/wordlog.py",
            "tools/s4/reach_receipt.py", "harness/replay/native_device_profile.c",
            "Documentation/replays.json")
QUESTION = ("Does the runtime answer the nine imports retired from "
            "Documentation/refusal-reasons.json on 2026-09-26 (EnumSystemLocalesA, "
            "GetLocaleInfoA and W, IsValidLocale, IsValidCodePage, code page 936's GetCPInfo, "
            "MultiByteToWideChar and WideCharToMultiByte, and GetEnvironmentStringsW), the "
            "standard handles, and QueryPerformanceFrequency (since 2026-09-27) as native "
            "does: its compiled data as Wine's NLS files and the native traces say (tables), "
            "and the boot replay's lifted build on a native "
            "capture's own device profile as the native boots answered and recorded, with "
            "every break moving what its check reads (run)?")
LIMITS = [
    "The native side is three traced boots (TRACES), taken with scratch probes that hooked "
    "the imports at the Mod Loader's Init; the calls the C runtime makes before Init "
    "(GetEnvironmentStringsW, GetStdHandle, GetFileType's first, and PRE_INIT_SITES) are held "
    "to the capture's device profile, which recorded their answers natively, not to a trace.",
    "Code page 936's conversions are compared whole against the N0 traces only; the locale "
    "trace cut inputs at 96 units.",
    "The words compared are the C runtime's locale words (WORDS) and the two words "
    "QueryPerformanceFrequency's answer reaches (TIME_WORDS) on every tick both logs hold; "
    "every other word is M5's comparison, not this one's. QueryPerformanceFrequency's "
    "answer itself is each native word log's start record (ref_hz), which the tick probe "
    "reads in the game's process at Init; the lifted answer is the one the guest kept "
    "(Sofdec's copy on tick 1), not a record of the runtime's call.",
    "One lifted run per configuration.",
    "The NLS files are pinned by sha256 (tools/s4/nls_data.py NLS) under the Proton build "
    "runtime/sa2_localedata.h names; a Proton update that changes them makes tables refuse.",
]


def must_sha256(path: Path) -> str:
    if not path.is_file():
        raise Refusal(f"{path} does not exist; the receipt would pin nothing")
    return sha256(path)


def receipt_inputs(prof: dict, natives: list[Path], capture: Path | None) -> dict:
    """What the receipt's numbers came from, each with its sha256."""
    import tick_replay as TR
    if capture is None:
        entry = next(e for e in json.loads(TR.REGISTRY.read_text())["replays"]
                     if e["name"] == "boot")
        where = {"registered": "boot", "path": entry["native"]["capture"]}
    else:
        where = {"registered": None, "path": DP.rel(capture, ROOT)}
    return {"runtime_sha256": TR.runtime_digest(ROOT),
            "pipeline": {q: must_sha256(ROOT / q) for q in PIPELINE},
            "nls": {name: {"path": DP.rel(N.NLS_DIR / name, ROOT), "sha256": pin}
                    for name, pin in sorted(N.NLS.items())},
            "traces": {k: {"path": DP.rel(path, ROOT), "sha256": pin}
                       for k, (path, pin) in TRACES.items()},
            "user_locale_profile": {"path": DP.rel(USER_LOCALE_PROFILE[0], ROOT),
                                    "sha256": USER_LOCALE_PROFILE[1]},
            "capture": where,
            "device_profile": {"path": prof["path"], "sha256": prof["sha256"]},
            "native_word_logs": [{"path": DP.rel(n, ROOT), "sha256": must_sha256(n)}
                                 for n in natives]}


def provenance() -> dict:
    """The commit the runs start from and whether the tree differs from it; a
    git that fails is refused rather than read as commit "" and a clean tree.
    Neither may take the index lock (GIT_OPTIONAL_LOCKS=0)."""
    def git(*args: str) -> str:
        p = subprocess.run(["git", *args], cwd=str(ROOT), capture_output=True, text=True,
                           env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
        if p.returncode:
            raise Refusal(f"git {' '.join(args)} exited {p.returncode}: "
                          f"{p.stderr.strip()[-300:]}")
        return p.stdout.strip()
    return {"commit": git("rev-parse", "HEAD"),
            "uncommitted_changes": bool(git("status", "--porcelain"))}


def run_summary(r: dict) -> dict:
    """What the receipt keeps of one run: its configuration, the digests of what
    it wrote, and the counts every check reads."""
    counts = ("compared", "not_native_count", "differ_count", "uncovered_count")
    return {"env": r["env"], "tick_capture_sha256": r["capture_sha256"],
            "answers": r["answers"], "enumerations": r["enumerations"],
            **{k: {c: r[k][c] for c in counts}
               for k in ("info", "valid", "cpinfo", "conversions")},
            "environment": r["environment"], "std_handles": r["std_handles"],
            "words": [{"native": w["native"], "compared_through": w["compared_through"],
                       "differ": sorted(w["differ"])} for w in r["words"]],
            "time_words": [{"native": w["native"], "compared_through": w["compared_through"],
                            "differ": sorted(w["differ"]), "native_answer": w["native_answer"],
                            "lifted_answer": w["lifted_answer"]} for w in r["time_words"]]}


def receipt(tab: dict, run: dict, inputs: dict, origin: dict, command: str) -> dict:
    """The receipt, refused unless every check of both modes holds."""
    failed = [f"tables {k}" for k, v in tab["checks"].items() if not v] + \
        [f"run {k}" for k, v in run["checks"].items() if not v]
    if failed:
        raise Refusal(f"no receipt: {', '.join(failed)} failed")
    if run["profile_sha256"] != inputs["device_profile"]["sha256"]:
        raise Refusal("the runs read another profile than the receipt's inputs name")
    return {"schema": "sa2-recomp-receipt/v1", "id": "s4-locale-check-v1",
            "question": QUESTION, "generated_by": "tools/s4/locale_check.py",
            "command": command, **origin, "inputs": inputs,
            "tables": {"checks": tab["checks"], "counts": tab["counts"],
                       **{k: tab[k] for k in ("installed", "lcids", "answers", "fits")}},
            "run": {"checks": run["checks"],
                    "runs": {name: run_summary(r) for name, r in run["runs"].items()}},
            "answer": (f"Yes: every check holds, {len(tab['checks'])} of tables "
                       f"({', '.join(tab['checks'])}) and {len(run['checks'])} of run "
                       f"({', '.join(run['checks'])}), on {inputs['capture']['path']}'s "
                       f"device profile."),
            "limits": LIMITS}


def write_mode(capture: Path | None, json_out: Path | None) -> dict:
    """tables, then run, then the receipt: refused before any boot when git or
    tables fails, and nothing written when a run check fails (the result says
    which) or an input moved while the boots ran."""
    origin = provenance()
    tab = tables()
    if not tab["ok"]:
        for k, v in tab["checks"].items():
            print(f"LOCALE {k}: {'ok' if v else 'FAILED'}")
        raise Refusal("tables fails, so no boot was run and no receipt written")
    prof, natives = profile_of(capture, None)
    before = receipt_inputs(prof, natives, capture)
    run = run_mode(capture, None)
    if json_out:
        json_out.write_text(json.dumps({"tables": tab, "run": run}, indent=1, default=str) + "\n")
    if not run["ok"]:
        return dict(run, written=None)
    if receipt_inputs(prof, natives, capture) != before or provenance() != origin:
        raise Refusal("an input or the commit moved while the boots ran; no receipt written")
    command = ".venv/bin/python tools/s4/locale_check.py run --write" + \
        (f" --capture {DP.rel(capture, ROOT)}" if capture is not None else "")
    doc = receipt(tab, run, before, origin, command)
    RECEIPT.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return dict(run, written=DP.rel(RECEIPT, ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("tables", "run"))
    ap.add_argument("--capture", type=Path, default=None,
                    help="run: an unregistered boot capture directory, its profile and word log")
    ap.add_argument("--profile", type=Path, default=None,
                    help="run: a profile no capture carries yet, with the registered boot "
                         "capture's guest paths")
    ap.add_argument("--json", type=Path, default=None, help="also write the result here")
    ap.add_argument("--write", action="store_true",
                    help="run: run tables too, and write the receipt (RECEIPT) when every check "
                         "of both holds; refuses --profile")
    a = ap.parse_args(argv)
    if a.write and (a.mode != "run" or a.profile is not None):
        raise Refusal("--write is run's, on a capture's own profile (--capture, or the "
                      "registered boot's): a --profile file no capture carries is a stand-in, "
                      "not evidence a receipt can hold")
    if a.write:
        result = write_mode(a.capture, a.json)
    else:
        result = tables() if a.mode == "tables" else run_mode(a.capture, a.profile)
    for k, v in result["checks"].items():
        print(f"LOCALE {k}: {'ok' if v else 'FAILED'}")
    if a.mode == "tables":
        print(f"LOCALE {result['installed']} installed locales, {result['lcids']} LCIDs, "
              f"{result['answers']} answers, {result['fits']} best fits; native: "
              f"{result['counts']}")
        for k, v in result["differences"].items():
            for line in v[:5]:
                print(f"LOCALE {k}: {line}")
    else:
        for name, r in result["runs"].items():
            print(f"LOCALE {name}: enumerations {r['enumerations']}, info "
                  f"{r['info']['compared']}/{r['info']['differ_count']}/"
                  f"{r['info']['uncovered_count']}, conversions "
                  f"{r['conversions']['compared']}/{r['conversions']['differ_count']}/"
                  f"{r['conversions']['uncovered_count']}, environment "
                  f"{r['environment']['follows']}, std {r['std_handles']['got']}")
    if a.json and not a.write:
        a.json.write_text(json.dumps(result, indent=1, default=str) + "\n")
    if a.write:
        print(f"LOCALE receipt written: {result['written']}" if result["written"] else
              "LOCALE no receipt written: a run check failed")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
