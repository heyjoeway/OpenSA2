"""The profile probe's locale, environment and standard-handle records, read back.

harness/replay/native_device_profile.c runs only under the oracle's Wine, and a
capture taken with it is the only way to see what it writes; a mistake in a
record the readers refuse would cost the whole re-take that takes the captures
(the probe is part of every capture's build, so any change to it makes every
capture stale). So these tests compile the probe's own text for this host --
its writer and its last three sections, cut from the file between their
section rules, over a stand-in for the few Windows calls they make -- run it on
a host whose answers are known, and hold what it writes to what both readers
(runtime/sa2_devprofile.c through tools/s4/device_profile.py, which
test_device_profile.py holds together) read back.

The stand-in answers from the profile the test writes the expected file from
(test_device_profile.GOOD): the locale's answers, one of them a failure; an
environment block of four strings of which the C runtime's _environ holds the
first three, the fourth added after start-up; and stdin a character device, no
stdout, stderr a pipe. _environ is the stand-in's own array, which the probe
reads at the image address it names (the test substitutes its array's address
for that one literal).
"""
from __future__ import annotations

import dataclasses
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "s4"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import device_profile as DP  # noqa: E402
from test_device_profile import GOOD, ZIG  # noqa: E402

PROBE = ROOT / "harness" / "replay" / "native_device_profile.c"
ENVIRON_LITERAL = "0x01751B44u"

SHIM = r"""
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
typedef uint32_t DWORD, LCID, LCTYPE, UINT;
typedef int BOOL;
typedef uint16_t WCHAR;
typedef size_t SIZE_T;
typedef void *HANDLE;
typedef const char *LPCSTR;
typedef BOOL *LPBOOL;
typedef struct { uint32_t Data1; uint16_t Data2, Data3; uint8_t Data4[8]; } GUID;
typedef struct { void *BaseAddress; void *AllocationBase; DWORD AllocationProtect;
                 SIZE_T RegionSize; DWORD State, Protect, Type; } MEMORY_BASIC_INFORMATION;
#define LCID_INSTALLED 1u
#define ERROR_INSUFFICIENT_BUFFER 122u
#define CP_ACP 0u
#define CP_UTF8 65001u
#define WC_ERR_INVALID_CHARS 0x80u
#define MEM_COMMIT 0x1000u
#define PAGE_NOACCESS 0x01u
#define PAGE_GUARD 0x100u
#define STD_INPUT_HANDLE ((DWORD)-10)
#define STD_OUTPUT_HANDLE ((DWORD)-11)
#define STD_ERROR_HANDLE ((DWORD)-12)
#define INVALID_HANDLE_VALUE ((HANDLE)(intptr_t)-1)
#define FILE_TYPE_UNKNOWN 0u

static DWORD last_error;
static void SetLastError(DWORD e) { last_error = e; }
static DWORD GetLastError(void) { return last_error; }
static LCID GetUserDefaultLCID(void) { return @USER_LCID@; }
static LCID GetSystemDefaultLCID(void) { return @SYSTEM_LCID@; }
static UINT GetACP(void) { return 1252u; }
static UINT GetOEMCP(void) { return @OEMCP@; }
static BOOL IsValidLocale(LCID l, DWORD f) { return l == @USER_LCID@ && f == LCID_INSTALLED; }

/* The locale's answers: LCTYPE, then the error or 0, then the UTF-16 units. */
typedef struct { uint32_t lctype, error, n; uint16_t w[128]; } answer;
static const answer ANSWERS[] = { @ANSWER_ROWS@ };
static int GetLocaleInfoW(LCID l, LCTYPE t, WCHAR *buf, int len) {
    if (l != @USER_LCID@) { last_error = 87; return 0; }
    for (unsigned i = 0; i < sizeof ANSWERS / sizeof ANSWERS[0]; i++) {
        if (ANSWERS[i].lctype != t) continue;
        if (ANSWERS[i].error) { last_error = ANSWERS[i].error; return 0; }
        if ((int)ANSWERS[i].n + 1 > len) { last_error = ERROR_INSUFFICIENT_BUFFER; return 0; }
        memcpy(buf, ANSWERS[i].w, 2u * ANSWERS[i].n);
        buf[ANSWERS[i].n] = 0;
        return (int)ANSWERS[i].n + 1;
    }
    last_error = 1004;
    return 0;
}

static int lstrlenW(const WCHAR *s) { int n = 0; while (s[n]) n++; return n; }

/* UTF-8 with WC_ERR_INVALID_CHARS, or the ANSI code page for the characters the
 * test uses (Latin-1's); `n` -1 counts the NUL. */
static int WideCharToMultiByte(UINT cp, DWORD flags, const WCHAR *w, int n, char *out, int size,
                               LPCSTR d, LPBOOL used) {
    (void)flags; (void)d; (void)used;
    if (n < 0) n = lstrlenW(w) + 1;
    int k = 0;
    for (int i = 0; i < n; i++) {
        unsigned char b[3];
        int m;
        const unsigned c = w[i];
        if (c >= 0xD800u && c < 0xE000u) return 0;
        if (cp != CP_UTF8) { if (c > 0xFFu) return 0; b[0] = (unsigned char)c; m = 1; }
        else if (c < 0x80u) { b[0] = (unsigned char)c; m = 1; }
        else if (c < 0x800u) { b[0] = (unsigned char)(0xC0u | c >> 6); b[1] = (unsigned char)(0x80u | (c & 0x3Fu)); m = 2; }
        else { b[0] = (unsigned char)(0xE0u | c >> 12); b[1] = (unsigned char)(0x80u | (c >> 6 & 0x3Fu));
               b[2] = (unsigned char)(0x80u | (c & 0x3Fu)); m = 3; }
        if (size) { if (k + m > size) return 0; memcpy(out + k, b, (size_t)m); }
        k += m;
    }
    return k;
}

/* The block and the C runtime's copy of its first CRT strings. */
static WCHAR BLOCK[] = { @BLOCK_UNITS@ };
static char *test_environ_array[] = { @ENVIRON_STRINGS@ NULL };
static char **test_environ_slot = test_environ_array;
static const void *unreadable = @UNREADABLE@;
static WCHAR *GetEnvironmentStringsW(void) { return BLOCK; }
static BOOL FreeEnvironmentStringsW(WCHAR *b) { return b == BLOCK; }
static SIZE_T VirtualQuery(const void *p, MEMORY_BASIC_INFORMATION *mi, SIZE_T n) {
    (void)n;
    memset(mi, 0, sizeof *mi);
    mi->BaseAddress = (void *)((uintptr_t)p & ~(uintptr_t)0xFFF);
    mi->RegionSize = 0x10000;
    mi->State = MEM_COMMIT;
    mi->Protect = p == unreadable ? PAGE_NOACCESS : 0x04u;
    return sizeof *mi;
}
static HANDLE GetProcessHeap(void) { return (HANDLE)1; }
static void *HeapAlloc(HANDLE h, DWORD f, SIZE_T n) { (void)h; (void)f; return malloc(n); }
static BOOL HeapFree(HANDLE h, DWORD f, void *p) { (void)h; (void)f; free(p); return 1; }

static HANDLE GetStdHandle(DWORD which) {
    const uintptr_t H[3] = {@STD_HANDLES@};
    return (HANDLE)H[(DWORD)-10 - which];
}
static DWORD GetFileType(HANDLE h) {
    const uintptr_t H[3] = {@STD_HANDLES@};
    const DWORD T[3] = {@STD_TYPES@};
    for (unsigned i = 0; i < 3u; i++) if ((uintptr_t)h == H[i]) return T[i];
    return 0;
}

static FILE *out;
"""


def section(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i)]


def probe_parts() -> str:
    text = PROBE.read_text()
    writer = section(text, "/* ------------------------------------------------------------- the writer */",
                     "static FARPROC proc(")
    writer = writer.replace("static FILE *out;\n", "")
    tail = section(text, "/* ----------------------------------------------------------------- locale */",
                   "/* ------------------------------------------------------------------ Init */")
    assert tail.count(ENVIRON_LITERAL) == 1, "the probe names _environ once"
    return writer + tail.replace(ENVIRON_LITERAL, "((uintptr_t)&test_environ_slot)")


def c_str(b: bytes) -> str:
    return '"' + "".join(f"\\x{c:02x}" for c in b) + '"'


def build_and_run(tmp_path: Path, p: DP.Profile, crt_strings: list[bytes],
                  unreadable: str = "NULL") -> str:
    if not ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {ZIG}")
    rows = []
    for t, (err, value) in zip(DP.LOCALE_LCTYPES, p.locale.info):
        w = [] if value is None else list(value.decode("utf-8").encode("utf-16-le"))
        units = [w[i] | w[i + 1] << 8 for i in range(0, len(w), 2)]
        rows.append(f"{{0x{t:x}u, {err}u, {len(units)}u, {{{','.join(map(str, units)) or '0'}}}}}")
    whole = b"".join(s.decode("utf-8").encode("utf-16-le") + b"\0\0"
                     for s in p.environment.strings) + b"\0\0"     # every string, at Init
    units = [whole[i] | whole[i + 1] << 8 for i in range(0, len(whole), 2)]
    src = SHIM
    for key, value in {"USER_LCID": f"0x{p.locale.lcid:x}u",
                       "SYSTEM_LCID": f"0x{p.locale.system:x}u",
                       "OEMCP": f"{p.locale.oemcp}u", "ANSWER_ROWS": ", ".join(rows),
                       "BLOCK_UNITS": ", ".join(map(str, units)),
                       "ENVIRON_STRINGS": "".join(c_str(s) + ", " for s in crt_strings),
                       "UNREADABLE": unreadable,
                       "STD_HANDLES": ", ".join(str(h if h != 0xFFFFFFFF else -1)
                                                for h, _t in p.std_handles),
                       "STD_TYPES": ", ".join(str(t) for _h, t in p.std_handles)}.items():
        assert f"@{key}@" in src
        src = src.replace(f"@{key}@", value)
    src += probe_parts() + r"""
int main(void) {
    out = stdout;
    locale();
    environment();
    std_handles();
    return 0;
}
"""
    d = tmp_path / "probe"
    d.mkdir(exist_ok=True)
    (d / "probe.c").write_text(src)
    env = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
               ZIG_GLOBAL_CACHE_DIR=str(ROOT / "private" / "generated" / "zig-cache-tests"))
    done = subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O1", "-w",
                           str(d / "probe.c"), "-o", str(d / "probe")],
                          capture_output=True, text=True, env=env)
    assert done.returncode == 0, done.stderr[-3000:]
    return subprocess.run([str(d / "probe")], capture_output=True, text=True,
                          check=True).stdout


def file_with(sections: str) -> bytes:
    """GOOD's records up to its mastering voice, the probe's three sections, `end`."""
    head = DP.dump(dataclasses.replace(GOOD, locale=None, environment=None,
                                       std_handles=None)).decode("ascii")
    assert head.endswith("end\n")
    return (head[:-len("end\n")] + sections + "end\n").encode("ascii")


def acp(s: bytes) -> bytes:
    return s.decode("utf-8").encode("latin-1")


def test_the_probe_writes_what_both_readers_read_as_the_host_s_answers(tmp_path):
    kept = [acp(s) for s in GOOD.environment.strings[:GOOD.environment.crt]]
    written = build_and_run(tmp_path, GOOD, kept)
    path = tmp_path / DP.FILE
    path.write_bytes(file_with(written))
    p, _ = DP.load(path)
    assert p == GOOD
    assert "environment strings=4 crt=3\n" in written
    assert "std_handle which=1 handle=0x00000000 type=0\n" in written


def test_an_environ_that_is_not_the_block_s_prefix_is_an_error_record(tmp_path):
    """A string changed after start-up makes the block at start-up unknown: the
    probe writes an error in the section's place, and the readers refuse it."""
    kept = [acp(s) for s in GOOD.environment.strings[:GOOD.environment.crt]]
    kept[1] = b"PATH=changed"
    written = build_and_run(tmp_path, GOOD, kept)
    assert ('error step="the C runtime\'s _environ is not a prefix of the environment block" '
            'code=0x00000001\n') in written
    path = tmp_path / DP.FILE
    path.write_bytes(file_with(written))
    with pytest.raises(DP.ProfileError) as e:
        DP.load(path)
    assert e.value.kind == DP.UNMEASURED


def test_more_kept_strings_than_the_block_holds_is_an_error_record(tmp_path):
    kept = [acp(s) for s in GOOD.environment.strings] + [b"EXTRA=1"]
    written = build_and_run(tmp_path, GOOD, kept)
    assert ('error step="the C runtime\'s _environ is not a prefix of the environment block" '
            'code=0x00000005\n') in written


def test_an_unreadable_environ_slot_is_an_error_record_not_a_shorter_count(tmp_path):
    kept = [acp(s) for s in GOOD.environment.strings[:GOOD.environment.crt]]
    written = build_and_run(tmp_path, GOOD, kept, unreadable="&test_environ_array[1]")
    assert ('error step="the C runtime\'s _environ is not a prefix of the environment block" '
            'code=0x00000001\n') in written


def test_the_probe_names_environ_at_the_c_runtime_s_address():
    """_environ, which __setenvp fills (0x007B4CF6), is 0x01751B44: the address
    the N0 trace read the C runtime's copy at, 78 strings, the block's first."""
    text = PROBE.read_text()
    assert re.search(r"#define GAME_CRT_ENVIRON\s+0x01751B44u", text)


def test_init_writes_the_three_sections_in_the_readers_order_before_end():
    """The tests above run the sections from their own main(); what a capture
    holds is what Init calls. Both readers take the sections only in this order
    and only before `end`, so an Init that dropped, repeated or reordered one
    would have every capture of the re-take refused (or, dropped, read as the
    default)."""
    text = PROBE.read_text()
    body = section(text, "EXPORT void __cdecl Init(", "\n}\n")
    calls = re.findall(r"^\s*(\w+)\(\);", body, re.M)
    assert calls[-3:] == ["locale", "environment", "std_handles"], calls
    assert body.index("std_handles();") < body.index('fputs("end\\n", out);')


def test_the_probe_builds_as_the_oracle_s_runner_builds_it(tmp_path, monkeypatch):
    """The runner's own compile (tools/oracle/run_oracle_probe.py compile_probe:
    x86-windows-gnu, -O2 -Wall -Wextra -Werror) and export check, which the
    host build above (-w, x86_64) does not stand for."""
    if not ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {ZIG}")
    sys.path.insert(0, str(ROOT / "tools" / "oracle"))
    import run_oracle_probe as R
    monkeypatch.setattr(R, "OUT", tmp_path)
    dll, _sha = R.compile_probe(PROBE, "device_profile", [])
    assert dll.is_file()
