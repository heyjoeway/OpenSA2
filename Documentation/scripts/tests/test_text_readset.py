"""tools/s7/text_readset.py, the runtime's shipped-executable loader and tools/s4/link_s4.py's
overlay object, on synthetic inputs.

The user's DRM ruling of 2026-10-11 (Documentation/decisions/2026-10-11-S7-drm-unwrap-ruling.md):
the release build carries the code-section bytes the runtime reads, and the runtime loads the
shipped executable as-is. Nothing here reads the game's executables. Two small PE32 images stand
in for them: an "unpacked" one with `.text`, `.rdata` and `.dataa`, and a "shipped" one whose
`.text` is scrambled, whose data section is named `.data` (the two files name the same bytes
differently, as the game's do) and which has a `.bind` section past the image, as SteamStub's
has. The read-set is a handful of jump tables and emulated sites in the shape
tools/s7/drm_unwrap.py's `jump_tables()` and `emulated_sites()` return.

The generator's tests call `build` and read what it returns. The loader's compile the generated
table with the pinned zig, link it with runtime/guest_memory.c and runtime/sa2_exe_overlay.c and
a driver of the test's own, and read what guest memory holds after `sa2_guest_init`. The link
tests run link_s4's compile and link phases on stand-in sources. Without the pinned zig they
fail, never skip.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import struct
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RUNTIME = ROOT / "runtime"
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
CC = ["-target", "x86_64-linux-musl", "-O2", "-Wall", "-Wextra", "-Werror", "-I", str(RUNTIME)]

spec = importlib.util.spec_from_file_location("text_readset", ROOT / "tools" / "s7" / "text_readset.py")
T = importlib.util.module_from_spec(spec)
spec.loader.exec_module(T)

BASE = 0x00400000
EXEC_CODE, READ_DATA, RW_DATA, BIND = 0x60000020, 0x40000040, 0xC0000040, 0x60000040
LFANEW, OPT_SIZE = 0x40, 0xE0
SEC_OFF = LFANEW + 24 + OPT_SIZE


def zig_env(d: Path) -> dict:
    return dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(d / "zc"),
                ZIG_GLOBAL_CACHE_DIR=os.environ.get(
                    "ZIG_GLOBAL_CACHE_DIR", str(ROOT / "private" / "generated" / "zig-cache-tests")))


# ------------------------------------------------------------------ the synthetic images


def pe(sections: list[tuple[str, int, int, bytes, int]], size_of_image: int) -> bytes:
    """A PE32 with these sections (name, RVA, virtual size, file bytes, characteristics), file
    bytes at 0x200-aligned raw pointers after a 0x200-byte header, the import directory at RVA
    0x3000 and the resource directory at RVA 0x3080."""
    hdr = 0x200
    b = bytearray(hdr)
    b[0:2] = b"MZ"
    struct.pack_into("<I", b, 0x3C, LFANEW)
    b[LFANEW:LFANEW + 4] = b"PE\0\0"
    coff = LFANEW + 4
    struct.pack_into("<HHI", b, coff, 0x14C, len(sections), 0x4E5A0000)  # timestamp
    struct.pack_into("<HH", b, coff + 16, OPT_SIZE, 0x0102)
    opt = coff + 20
    struct.pack_into("<H", b, opt, 0x10B)
    struct.pack_into("<I", b, opt + 16, 0x1000)                 # entry point
    struct.pack_into("<III", b, opt + 28, BASE, 0x1000, 0x200)  # image base, alignments
    struct.pack_into("<II", b, opt + 56, size_of_image, hdr)
    struct.pack_into("<I", b, opt + 92, 16)
    struct.pack_into("<II", b, opt + 104, 0x3000, 0x28)         # imports
    struct.pack_into("<II", b, opt + 112, 0x3080, 0x40)         # resources
    raw = hdr
    body = bytearray()
    for i, (name, rva, vsize, data, chars) in enumerate(sections):
        s = SEC_OFF + 40 * i
        b[s:s + 8] = name.encode().ljust(8, b"\0")
        struct.pack_into("<IIII", b, s + 8, vsize, rva, len(data), raw + len(body))
        struct.pack_into("<I", b, s + 36, chars)
        body += data
        body += b"\0" * (-len(body) % 0x200)
    return bytes(b + body)


def text_bytes() -> bytearray:
    """The unpacked `.text`, 0x1000 file bytes of a 0x2000 virtual size: a counting pattern, with
    two jump tables' arm dwords and an index table laid where the tables below say."""
    t = bytearray((i * 7 + 3) & 0xFF for i in range(0x1000))
    struct.pack_into("<IIII", t, 0x100, 0x401010, 0x401020, 0x401030, 0x401040)
    struct.pack_into("<II", t, 0x200, 0x401050, 0x401060)
    t[0x208:0x20D] = bytes([0, 1, 1, 0, 1])
    return t


def rdata_bytes() -> bytes:
    r = bytearray((i * 13 + 1) & 0xFF for i in range(0x200))
    struct.pack_into("<II", r, 0x10, 0x401070, 0x401080)        # a jump table in .rdata
    return bytes(r)


RDATA = rdata_bytes()
DATA = bytes((i * 5 + 9) & 0xFF for i in range(0x200))


def unpacked() -> bytes:
    return pe([(".text", 0x1000, 0x2000, bytes(text_bytes()), EXEC_CODE),
               (".rdata", 0x3000, 0x100, RDATA, READ_DATA),
               (".dataa", 0x4000, 0x800, DATA, RW_DATA)], 0x5000)


SHIPPED_SECTIONS = {".rdata": (".rdata", 0x3000, 0x100, RDATA, READ_DATA),
                    ".data": (".data", 0x4000, 0x800, DATA, RW_DATA),
                    ".bind": (".bind", 0x5000, 0x200, bytes(0xB0 for _ in range(0x200)), BIND)}


def shipped(rdata: bytes = RDATA, rdata_vsize: int = 0x100, drop: str = "") -> bytes:
    scrambled = bytes(x ^ 0x5A for x in text_bytes())
    rows = [(".text", 0x1000, 0x2000, scrambled, EXEC_CODE)]
    rows += [r for n, r in SHIPPED_SECTIONS.items() if n != drop]
    rows = [(".rdata", 0x3000, rdata_vsize, rdata, READ_DATA) if r[0] == ".rdata" else r for r in rows]
    return pe(rows, 0x6000)


def table(va: int, entries: int, arms: list[int], index: int | None = None, cases: int = 0) -> dict:
    return {"dispatch": va - 0x20, "table": va, "index_table": index, "entries": entries,
            "cases": cases, "arms": arms, "bound_from": "check", "problem": None}


TABLES = [table(0x401100, 4, [0x401010, 0x401020, 0x401030, 0x401040]),
          table(0x401200, 2, [0x401050, 0x401060], index=0x401208, cases=5),
          table(0x403010, 2, [0x401070, 0x401080])]            # in .rdata: no run
RELOCATED = {0x401100, 0x401104, 0x401108, 0x40110C, 0x401200, 0x401204, 0x403010, 0x403014}
SITES = [{"ip": 0x40120D, "next_ip": 0x401210, "kind": "cvtps2pi"},   # touches the index table
         {"ip": 0x401280, "next_ip": 0x401284, "kind": "paddusb"},
         {"ip": 0x401FFC, "next_ip": 0x402000, "kind": "paddusw"}]    # ends where .text's bytes do
# The same read-set as drm_unwrap.measure's companion would hold it.
COMPANION = {
    "jump_tables": [
        {"dispatch": "0x004010e0", "arms_in_no_near_dword": 0,
         "ranges": [["0x00401100", "0x00401110", "executable"]]},
        {"dispatch": "0x004011e0", "arms_in_no_near_dword": 0,
         "ranges": [["0x00401200", "0x00401208", "executable"],
                    ["0x00401208", "0x0040120d", "executable"]]},
        {"dispatch": "0x00402ff0", "arms_in_no_near_dword": 0,
         "ranges": [["0x00403010", "0x00403018", "data"]]}],
    "emulated_sites": [{"ip": "0x0040120d", "length": 3}, {"ip": "0x00401280", "length": 4},
                       {"ip": "0x00401ffc", "length": 4}],
}


def built(omit=(), shipped_bytes=None, tables=TABLES, sites=SITES, companion=None):
    return T.build(shipped() if shipped_bytes is None else shipped_bytes, unpacked(), tables,
                   RELOCATED, sites, tuple(omit), companion=companion)


# ------------------------------------------------------------------ the generator


def test_the_runs_are_the_readset_in_text_merged_with_the_unpacked_bytes():
    _, m = built()
    assert m["text"] == {"va": "0x00401000", "end": "0x00402000"}
    assert m["readset"] == {"jump_tables": 3, "emulated_sites": 3, "ranges": 7,
                            "ranges_outside_text": {"jump_table": 1}, "runs": 4,
                            "bytes": 16 + 16 + 4 + 4, "companion_checked": False}
    t = text_bytes()
    want = t[0x100:0x110] + t[0x200:0x210] + t[0x280:0x284] + t[0xFFC:0x1000]
    assert m["bytes_sha256"] == hashlib.sha256(bytes(want)).hexdigest()
    canonical = (b'{"runs":[[4198656,16],[4198912,16],[4199040,4],[4202492,4]],'
                 b'"text":[4198400,4202496]}')
    assert m["readset_sha256"] == hashlib.sha256(canonical).hexdigest()
    assert m["unpacked_sha256"] == hashlib.sha256(unpacked()).hexdigest()


def test_the_readset_must_be_the_companions_range_for_range():
    _, m = built(companion=COMPANION)
    assert m["readset"]["companion_checked"] is True
    other = json.loads(json.dumps(COMPANION))
    other["emulated_sites"][1]["length"] = 5
    with pytest.raises(T.Refusal, match=r"is not the one the receipt's companion holds \(7\): "
                                        r"1 only here, 1 only there, first \(4199040, 4199044"):
        built(companion=other)
    unheld = json.loads(json.dumps(COMPANION))
    unheld["jump_tables"][0]["arms_in_no_near_dword"] = 1
    with pytest.raises(T.Refusal, match="the companion's jump table 0x004010e0 has an arm"):
        built(companion=unheld)


def test_a_table_with_an_arm_no_near_dword_holds_is_refused():
    tables = TABLES[:2] + [table(0x403010, 2, [0x401070, 0x401999])]
    with pytest.raises(T.Refusal, match=r"an arm no dword near the table holds .*: 0x00402ff0$"):
        built(tables=tables)


def test_a_range_outside_text_and_every_data_section_is_refused():
    sites = SITES[:2] + [{"ip": 0x401FFE, "next_ip": 0x402002, "kind": "paddusw"}]
    with pytest.raises(T.Refusal, match=r"the emulated_site range 0x00401ffe\.\.0x00402002 reads "
                                        r"0x00402000\.\.0x00402002, outside \.text and outside"):
        built(sites=sites)


def test_the_sections_are_kinded_by_va_named_for_the_guest_and_the_data_ones_hashed():
    c_text, m = built()
    rows = [(s["name"], s["guest_name"], s["va"], s["kind"], s["mapped"], s["sha256"])
            for s in m["sections"]]
    assert rows == [
        (".text", ".text", "0x00401000", "TEXT", 0x1000, ""),
        (".rdata", ".rdata", "0x00403000", "DATA", 0x100, hashlib.sha256(RDATA[:0x100]).hexdigest()),
        (".data", ".dataa", "0x00404000", "DATA", 0x200, hashlib.sha256(DATA).hexdigest()),
        (".bind", "", "0x00405000", "UNMAPPED", 0x200, ""),
    ]
    assert m["header"]["shipped"] == {
        "e_lfanew": 0x40, "sections_count": 4, "optional_header_size": 0xE0, "magic": 0x10B,
        "image_base": BASE, "size_of_image": 0x6000, "size_of_headers": 0x200, "rva_count": 16,
        "import_directory": [0x3000, 0x28], "resource_directory": [0x3080, 0x40],
        "clr_directory": [0, 0]}
    assert m["header"]["unpacked"]["sections_count"] == 3
    assert m["header"]["unpacked"]["size_of_image"] == 0x5000
    assert '.name = ".data", .guest_name = ".dataa", .va = 0x00404000,' in c_text


def test_the_header_bytes_verified_are_listed_by_offset():
    _, m = built()
    v = m["header_verified"]
    assert v[:4] == [["0x000", 2, "MZ"], ["0x03c", 4, "e_lfanew"], ["0x040", 4, "PE signature"],
                     ["0x046", 2, "sections_count"]]
    assert ["0x0b4", 4, "rva_count"] in v and ["0x128", 4, "clr_directory.rva"] in v
    assert v[-2:] == [["0x1b8", 12, "section 3 virtual_size,va,raw_size"],
                      ["0x1d4", 4, "section 3 characteristics"]]
    assert len(v) == 2 + len(T.HEADER_AT) + 2 * 4


def test_a_data_section_that_differs_between_the_files_is_refused():
    changed = bytearray(RDATA)
    changed[0x20] ^= 1
    with pytest.raises(T.Refusal, match=r"section \.rdata at 0x00403000 differs"):
        built(shipped_bytes=shipped(bytes(changed)))


def test_a_shipped_section_the_plan_cannot_place_is_refused():
    with pytest.raises(T.Refusal, match=r"the shipped section \.rdata at 0x00403000 is neither the "
                                        "unpacked file's section at that VA nor past"):
        built(shipped_bytes=shipped(rdata_vsize=0x180))
    with pytest.raises(T.Refusal, match=r"^unpacked sections the shipped executable has not: "
                                        r"\.dataa$"):
        built(shipped_bytes=shipped(drop=".data"))


def test_a_control_table_omits_what_it_says_and_records_it():
    c_text, m = built(omit=("emulated_sites",))
    assert m["omitted"] == ["emulated_sites"]
    assert m["readset"]["runs"] == 2 and m["readset"]["bytes"] == 16 + 13
    assert '.omitted = "emulated_sites",' in c_text
    with pytest.raises(T.Refusal, match="only"):
        built(omit=("headers",))


def test_the_c_table_carries_no_byte_outside_the_runs_and_names_every_field():
    c_text, m = built()
    body = c_text.split("overlay_bytes[", 1)[1].split("};", 1)[0]
    assert body.count("0x") == m["readset"]["bytes"]
    assert "SA2_EXE_SEC_UNMAPPED" in c_text and "sa2_exe_overlay_install" in c_text
    assert "    {.va = 0x00401ffc, .len = 4, .offset = 36}," in c_text
    rows = c_text.split("overlay_runs[", 1)[1].split("};", 1)[0].splitlines()[1:]
    assert all(r.strip().startswith("{.va = ") for r in rows)


# ------------------------------------------------------------------ the runtime's loader

DRIVER = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "guest_memory.h"
#include "sa2_exe_overlay.h"

/* A JSON string: the refusal lines quote what they name. */
static const char *js(const char *s) {
    static char out[4][2048];
    static int k;
    char *o = out[k = (k + 1) % 4], *e = o + sizeof out[0] - 3;
    *o++ = '"';
    for (; *s && o < e; s++) {
        if (*s == '"' || *s == '\\') *o++ = '\\';
        *o++ = *s;
    }
    *o++ = '"';
    *o = 0;
    return out[k];
}

static size_t slurp(const char *path, uint8_t *buf, size_t cap) {
    FILE *f = fopen(path, "rb");
    size_t n = fread(buf, 1, cap, f);
    fclose(f);
    return n;
}

/* argv: file, mode, then arguments.
 *   shipped | unpacked | env   init, then read guest VAs ("0xVA" or "0xVA+LEN"); prints a JSON
 *                              line, then the overlay report
 *   verify                     verify the file, mapping nothing
 *   flips                      verify the file with each named byte offset flipped in turn
 *   load-verify FILE2          init the file as shipped, then verify FILE2 */
int main(int argc, char **argv) {
    static uint8_t buf[1 << 16];
    if (!strcmp(argv[2], "verify")) {
        int rc = sa2_exe_overlay_verify(buf, slurp(argv[1], buf, sizeof buf), argv[1]);
        printf("{\"rc\": %d, \"refusal\": %s, \"mapped\": %d}\n", rc,
               js(sa2_exe_overlay_verify_refusal()), sa2_guest.base != NULL);
        return 0;
    }
    if (!strcmp(argv[2], "flips")) {
        size_t n = slurp(argv[1], buf, sizeof buf);
        printf("[");
        for (int i = 3; i < argc; i++) {
            size_t at = strtoul(argv[i], NULL, 0);
            buf[at] ^= 0xFFu;
            printf("%s%d", i > 3 ? ", " : "", sa2_exe_overlay_verify(buf, n, argv[1]));
            buf[at] ^= 0xFFu;
        }
        printf("]\n");
        return 0;
    }
    if (!strcmp(argv[2], "load-verify")) {
        sa2_guest_select_shipped(1);
        int rc = sa2_guest_init(argv[1]);
        int vrc = sa2_exe_overlay_verify(buf, slurp(argv[3], buf, sizeof buf), argv[3]);
        printf("{\"rc\": %d, \"loaded\": %d, \"refusal\": %s, \"verify_rc\": %d, "
               "\"verify_refusal\": %s}\n", rc, sa2_exe_overlay_loaded(),
               js(sa2_exe_overlay_refusal()), vrc, js(sa2_exe_overlay_verify_refusal()));
        return 0;
    }
    if (!strcmp(argv[2], "shipped")) sa2_guest_select_shipped(1);
    else if (!strcmp(argv[2], "unpacked")) sa2_guest_select_shipped(0);
    sa2_guest_poison_from_env();
    int rc = sa2_guest_init(argv[1]);
    printf("{\"rc\": %d, \"loaded\": %d, \"refusal\": %s", rc, sa2_exe_overlay_loaded(),
           js(sa2_exe_overlay_refusal()));
    if (rc == SA2_OK) {
        printf(", \"image_size\": %u, \"sections\": [", sa2_guest.image_size);
        for (unsigned i = 0; i < sa2_guest.section_count; i++)
            printf("%s\"%s\"", i ? ", " : "", sa2_guest.sections[i].name);
        printf("], \"reads\": {");
        for (int i = 3; i < argc; i++) {
            char *end;
            uint32_t va = (uint32_t)strtoul(argv[i], &end, 0);
            uint32_t len = *end == '+' ? (uint32_t)strtoul(end + 1, NULL, 0) : 1u;
            uint8_t b = 0;
            sa2_peek(va, 1, &b);
            printf("%s\"%s\": [%u, %d, \"%s\"]", i > 3 ? ", " : "", argv[i], b,
                   sa2_guest_is_poison(va, len), sa2_guest_region_of(va));
        }
        printf("}, \"text_unwritten\": %llu, \"first\": %u",
               (unsigned long long)sa2_guest_fine.reads, sa2_guest_fine.first);
    }
    printf("}\n");
    sa2_exe_overlay_report(stdout);
    return 0;
}
"""

QUERIES = ["0x401100", "0x40110f", "0x401110", "0x401150", "0x401ffc+4", "0x401ffe+4",
           "0x401ffa+4", "0x400ffe+4", "0x403000", "0x404000", "0x405000", "0x400000+2",
           "0x400080+4", "0x40003c+4"]


def malformed(c_text: str, old: str, new: str) -> str:
    assert c_text.count(old) == 1, old
    return c_text.replace(old, new)


@pytest.fixture(scope="module")
def loader(tmp_path_factory):
    """Drivers linking the synthetic table, none, and three malformed tables."""
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    d = tmp_path_factory.mktemp("overlay")
    c_text, manifest = built()
    tables = {
        "table": c_text,
        "version": malformed(c_text, f".version = {T.TABLE_VERSION},", ".version = 1,"),
        "overlap": malformed(c_text, ".va = 0x00401200, .len = 16,", ".va = 0x00401108, .len = 16,"),
        "adjacent": malformed(c_text, ".va = 0x00401200, .len = 16,", ".va = 0x00401110, .len = 16,"),
    }
    (d / "driver.c").write_text(DRIVER)
    env = zig_env(d)
    srcs = [str(d / "driver.c"), str(RUNTIME / "guest_memory.c"), str(RUNTIME / "sa2_exe_overlay.c")]
    exes = {}
    for name, text in [*tables.items(), ("none", None)]:
        extra = []
        if text is not None:
            (d / f"{name}.c").write_text(text)
            extra = [str(d / f"{name}.c")]
        exe = d / name
        p = subprocess.run([str(ZIG), "cc", *CC, *srcs, *extra, "-o", str(exe)], env=env,
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        exes[name] = exe
    files = {"shipped": shipped(), "unpacked": unpacked(), "notpe": b"XY" + bytes(0x400)}
    changed = bytearray(shipped())
    rdata = next(s for s in T.layout(shipped())["sections"] if s["name"] == ".rdata")
    changed[rdata["raw_pointer"] + 0x20] ^= 1     # one byte the loader maps of .rdata
    files["changed"] = bytes(changed)
    data = next(s for s in T.layout(shipped())["sections"] if s["name"] == ".data")
    files["truncated"] = shipped()[:data["raw_pointer"] + 0x100]
    paths = {}
    for k, v in files.items():
        paths[k] = d / f"{k}.exe"
        paths[k].write_bytes(v)

    def run(exe: str, file: str, mode: str, *args: str, **env_extra) -> tuple:
        e = {k: v for k, v in os.environ.items() if k not in ("SA2_EXE_KIND", "SA2_GUEST_POISON")}
        e.update(env_extra)
        argv = list(args) or (QUERIES if mode in ("shipped", "unpacked", "env") else [])
        p = subprocess.run([str(exes[exe]), str(paths[file]), mode, *argv], env=e,
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        lines = p.stdout.splitlines()
        got = json.loads(lines[0])
        if len(lines) > 1:
            got["report"] = json.loads(lines[1])
        return got, p.stderr
    run.files = paths
    run.manifest = manifest
    return run


def test_the_shipped_file_loads_with_the_overlay_and_without_bind(loader):
    got, err = loader("table", "shipped", "shipped", SA2_GUEST_POISON="2")
    t = text_bytes()
    assert err == ""
    assert got["rc"] == 0 and got["loaded"] == 1 and got["image_size"] == 0x5000
    assert got["reads"] == {
        "0x401100": [t[0x100], 0, ".text"],     # a run: the unpacked byte, written
        "0x40110f": [t[0x10F], 0, ".text"],
        "0x401110": [0xCC, 1, ".text"],         # past the run: the fill, unwritten
        "0x401150": [0xCC, 1, ".text"],
        "0x401ffc+4": [t[0xFFC], 0, ".text"],   # the run that ends .text's bytes
        "0x401ffe+4": [t[0xFFE], 1, ".text"],   # its last 2 bytes, then the unwritten page after
        "0x401ffa+4": [0xCC, 1, ".text"],       # 2 unwritten bytes, then the run
        "0x400ffe+4": [0, 1, "image"],          # the written header page, then .text's 1st bytes
        "0x403000": [RDATA[0], 0, ".rdata"],    # data: the shipped file's, as-is
        "0x404000": [DATA[0], 0, ".dataa"],     # named as the unpacked load names it
        "0x405000": [0, 1, "free"],             # .bind: not mapped
        "0x400000+2": [0x4D, 0, "image"],
        "0x400080+4": [0, 0, "image"],
        "0x40003c+4": [LFANEW, 0, "image"],
    }
    # Only the reads that met an unwritten .text byte count, each at that byte; the page past
    # .text's end is the page tier's.
    assert got["text_unwritten"] == 4 and got["first"] == 0x401110
    r = got["report"]
    assert r["text_unwritten_reads"] == 4
    assert r["text_addresses"] == ["0x00401110", "0x00401150", "0x00401ffa", "0x00401000"]
    assert r["header_reads"] == 4
    assert r["header_read"] == [["0x000", 2], ["0x03c", 4], ["0x080", 4], ["0xffe", 2]]
    assert r["header_unverified"] == [["0x080", 4], ["0xffe", 2]]
    assert r["loaded"] is True and r["refusal"] == ""


def test_the_shipped_load_names_its_sections_as_the_unpacked_load_does(loader):
    by_ship, _ = loader("table", "shipped", "shipped")
    by_unp, _ = loader("table", "unpacked", "unpacked")
    assert by_ship["sections"] == by_unp["sections"] == [".text", ".rdata", ".dataa"]
    for q in ("0x403000", "0x404000", "0x405000"):
        assert by_ship["reads"][q] == by_unp["reads"][q]
    assert by_unp["report"]["header_unverified"] is None and by_unp["loaded"] == 0


def test_the_env_switch_is_the_api_and_the_default_is_unchanged(loader):
    by_env, _ = loader("table", "shipped", "env", SA2_EXE_KIND="shipped")
    by_api, _ = loader("table", "shipped", "shipped")
    assert by_env == by_api and by_env["rc"] == 0
    plain, err = loader("table", "unpacked", "env")
    t = text_bytes()
    assert err == "" and plain["rc"] == 0 and plain["image_size"] == 0x5000
    assert plain["reads"]["0x401150"] == [t[0x150], 0, ".text"]
    assert plain["text_unwritten"] == 0
    none, _ = loader("none", "unpacked", "env")
    assert none["report"]["table"] is None
    assert {k: v for k, v in none.items() if k != "report"} == \
        {k: v for k, v in plain.items() if k != "report"}
    assert loader("table", "unpacked", "env", SA2_EXE_KIND="unpacked")[0] == plain
    # tools/env.sh exports SA2_EXE_SHIPPED as a path; it is not the switch.
    assert loader("table", "unpacked", "env", SA2_EXE_SHIPPED=str(loader.files["shipped"]))[0] == plain


def test_the_switch_refusal_is_kept(loader):
    odd, err = loader("table", "shipped", "env", SA2_EXE_KIND="1")
    line = ('SA2_EXE_OVERLAY_REFUSED switch: SA2_EXE_KIND is "1"; it is shipped for the shipped '
            "executable, or unpacked or unset for the unpacked one")
    assert {k: odd[k] for k in ("rc", "loaded", "refusal")} == {"rc": -5, "loaded": 0, "refusal": line}
    assert odd["report"]["refusal"] == line and err == line + "\n"


def test_a_changed_data_byte_is_refused_by_name(loader):
    got, err = loader("table", "changed", "shipped")
    assert got["rc"] == -5 and got["loaded"] == 0
    assert got["refusal"].startswith(f"SA2_EXE_OVERLAY_REFUSED section-hash: "
                                     f"{loader.files['changed']}: section .rdata at 0x00403000 "
                                     "(0x100 bytes) hashes to ")
    assert got["refusal"].endswith(f", the shipped executable's to "
                                   f"{hashlib.sha256(RDATA[:0x100]).hexdigest()}")
    assert err.strip() == got["refusal"]


def test_the_unpacked_file_named_shipped_is_refused_by_its_header(loader):
    got, _ = loader("table", "unpacked", "shipped")
    assert got["refusal"] == (f"SA2_EXE_OVERLAY_REFUSED header-field: {loader.files['unpacked']}: "
                              "header field sections_count is 0x3, the shipped executable's is 0x4")
    assert got["rc"] == -5


def test_a_file_that_is_not_pe_and_a_truncated_one_are_refused_by_name(loader):
    got, _ = loader("table", "notpe", "shipped")
    assert got["refusal"] == f"SA2_EXE_OVERLAY_REFUSED not-pe: {loader.files['notpe']}: no MZ header"
    got, _ = loader("table", "truncated", "shipped")
    assert got["refusal"] == (f"SA2_EXE_OVERLAY_REFUSED section-table: {loader.files['truncated']}: "
                              "section .data at 0x00404000 maps 0x100 bytes, the shipped "
                              "executable's maps 0x200 (a truncated file?)")


@pytest.mark.parametrize("exe,why", [
    ("version", "table version 1, this runtime reads 2"),
    ("overlap", "run 1 (0x00401108+16 at byte 16) is outside .text, outside the table's bytes, or "
                "not after the run before it"),
    ("adjacent", "run 1 (0x00401110+16 at byte 16) is outside .text, outside the table's bytes, or "
                 "not after the run before it"),
])
def test_a_malformed_table_is_named_at_install_and_refuses_every_load(loader, exe, why):
    got, err = loader(exe, "shipped", "shipped")
    line = (f"SA2_EXE_OVERLAY_REFUSED table: {loader.files['shipped']}: the installed overlay "
            f"table is not well formed: {why}")
    assert got["rc"] == -5 and got["refusal"] == line
    assert err == f"sa2_exe_overlay_install: the table is not well formed: {why}\n{line}\n"


def test_verify_refuses_what_the_loader_refuses_and_maps_nothing(loader):
    assert loader("table", "shipped", "verify")[0] == {"rc": 0, "refusal": "", "mapped": 0}
    changed, err = loader("table", "changed", "verify")
    loaded, _ = loader("table", "changed", "shipped")
    assert changed == {"rc": -5, "refusal": loaded["refusal"], "mapped": 0}
    assert changed["refusal"].startswith("SA2_EXE_OVERLAY_REFUSED section-hash: ")


def test_verify_leaves_the_last_loads_state_alone(loader):
    got, _ = loader("table", "shipped", "load-verify", str(loader.files["changed"]))
    assert got["rc"] == 0 and got["loaded"] == 1 and got["refusal"] == ""
    assert got["verify_rc"] == -5
    assert got["verify_refusal"].startswith("SA2_EXE_OVERLAY_REFUSED section-hash: ")


def test_every_header_byte_the_manifest_lists_is_verified_and_no_other_is_needed(loader):
    listed = [int(o, 16) + k for o, n, _ in loader.manifest["header_verified"] for k in range(n)]
    got, _ = loader("table", "shipped", "flips", *map(str, listed))
    assert got == [-5] * len(listed)
    # Bytes the loader does not read: the DOS stub, the COFF timestamp, a section's name.
    others = [0x80, LFANEW + 8, SEC_OFF + 40 * 2 + 1]
    assert loader("table", "shipped", "flips", *map(str, others))[0] == [0, 0, 0]


def test_a_build_without_a_table_refuses_the_shipped_file(loader):
    got, err = loader("none", "shipped", "env", SA2_EXE_KIND="shipped")
    assert got["rc"] == -5 and got["loaded"] == 0
    assert got["refusal"].startswith(f"SA2_EXE_OVERLAY_REFUSED no-table: {loader.files['shipped']} "
                                     "was named the shipped executable")
    assert err == got["refusal"] + "\n"


def test_a_table_with_no_runs_still_compiles(tmp_path):
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    c_text, m = built(omit=("emulated_sites", "jump_tables"))
    assert m["readset"]["runs"] == 0 and m["readset"]["bytes"] == 0
    (tmp_path / "empty.c").write_text(c_text)
    p = subprocess.run([str(ZIG), "cc", *CC, "-c", str(tmp_path / "empty.c"), "-o",
                        str(tmp_path / "empty.o")], env=zig_env(tmp_path), capture_output=True,
                       text=True)
    assert p.returncode == 0, p.stderr


# ------------------------------------------------------------------ link_s4's overlay object


@pytest.fixture()
def link_s4(tmp_path, monkeypatch):
    """tools/s4/link_s4.py with its outputs, runtime and lifted objects in `tmp_path`: no runtime
    source, a one-line function table, no lifted object. What is left is the overlay's object."""
    if not ZIG.is_file():
        pytest.fail(f"the pinned zig is not present: {ZIG}")
    for p in ("tools/s3/whole_image", "tools/spike", "tools/s4"):
        monkeypatch.syspath_prepend(str(ROOT / p))
    spec = importlib.util.spec_from_file_location("link_s4_under_test", ROOT / "tools/s4/link_s4.py")
    L4 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L4)
    (tmp_path / "runtime").mkdir()
    (tmp_path / "runtime" / "x.h").write_text("/* a header */\n")
    (tmp_path / "s3" / "link" / "tag").mkdir(parents=True)
    (tmp_path / "s3" / "link" / "tag" / "function_table_whole.c").write_text("int ft;\n")
    (tmp_path / "build" / "tag").mkdir(parents=True)
    (tmp_path / "main.c").write_text("extern const char marker[];\n"
                                     "int main(void) { return marker[0] == 0; }\n")
    monkeypatch.setattr(L4, "OUT", tmp_path / "s4" / "link")
    monkeypatch.setattr(L4, "RUNTIME", tmp_path / "runtime")
    monkeypatch.setattr(L4, "RUNTIME_C", [])
    monkeypatch.setattr(L4, "RUNTIME_CXX", [])
    monkeypatch.setattr(L4.C, "BUILD", tmp_path / "build")
    monkeypatch.setattr(L4.C, "ROOT", tmp_path)
    for k, v in zig_env(tmp_path).items():
        if k.startswith("ZIG_"):
            monkeypatch.setenv(k, v)
    L4.tmp = tmp_path
    return L4


def overlay_table(path: Path, marker: str, age: float = 0.0) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'const char marker[] = "{marker}";\n')
    if age:
        t = time.time() - age
        os.utime(path, (t, t))
    return path


def test_an_overlay_table_older_than_the_object_is_compiled_not_reused(link_s4):
    L4, d = link_s4, link_s4.tmp
    a = overlay_table(d / "a" / "sa2_exe_overlay_table.c", "MARKER-RELEASE-TABLE")
    st = {}
    L4.phase_compile(st, "m", "tag", d / "main.c", False, [], a)
    assert st["compile_ok"] and st["compile_exe_overlay"] == {"table": str(a), "sha256": L4.C.sha256(a)}
    # A control table, written a day before the object above: by time it would be fresh.
    b = overlay_table(d / "b" / "sa2_exe_overlay_table.c", "MARKER-CONTROL-TABLE", age=86400)
    st = {}
    L4.phase_compile(st, "m", "tag", d / "main.c", False, [], b)
    job = L4.exe_overlay_object(L4.C.sha256(b))
    assert st["compile"][job]["rc"] == 0 and st["compile"][job]["reused"] is False
    L4.phase_link(st, "m", "tag", [], [], [], [], b)
    assert st["link"]["rc"] == 0, st["link"].get("stderr_tail")
    assert st["link"]["exe_overlay"] == {"table": str(b), "sha256": L4.C.sha256(b)}
    binary = (d / "s4" / "link" / "mains" / "m" / "sa2_main").read_bytes()
    assert b"MARKER-CONTROL-TABLE" in binary and b"MARKER-RELEASE-TABLE" not in binary
    # The same path rewritten with older bytes is a new object too.
    overlay_table(a, "MARKER-REWRITTEN-TABLE", age=86400)
    st = {}
    L4.phase_compile(st, "m", "tag", d / "main.c", False, [], a)
    L4.phase_link(st, "m", "tag", [], [], [], [], a)
    binary = (d / "s4" / "link" / "mains" / "m" / "sa2_main").read_bytes()
    assert b"MARKER-REWRITTEN-TABLE" in binary and b"MARKER-RELEASE-TABLE" not in binary


def test_a_table_changed_between_compile_and_link_is_refused(link_s4):
    L4, d = link_s4, link_s4.tmp
    a = overlay_table(d / "a" / "sa2_exe_overlay_table.c", "MARKER-ONE")
    st = {}
    L4.phase_compile(st, "m", "tag", d / "main.c", False, [], a)
    overlay_table(a, "MARKER-TWO")
    with pytest.raises(SystemExit, match="refusing to link: the compile phase built the exe overlay"):
        L4.phase_link(st, "m", "tag", [], [], [], [], a)
    with pytest.raises(SystemExit, match="refusing to link"):
        L4.phase_link(st, "m", "tag", [], [], [], [], None)
