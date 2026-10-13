"""host/install's ZIP route (sa2_zip, sa2_inflate, sa2_store): what it accepts, what it refuses.

The route reads a ZIP of the user's game folder, verifies every file against the install hash
table and extracts it into the installer's own store (host/install/sa2_zip.h says how). These
tests build it with g++ under AddressSanitizer and UndefinedBehaviorSanitizer, with a driver
embedded below, and hold it to:

  * the deflate decoder against Python's zlib, at levels 0-9 and every strategy, on varied
    synthetic data, and under truncation and bit-flip fuzzing, where it must agree with zlib on
    accept, reject and truncated, and never touch memory it does not own;
  * archives made by Python's zipfile, Info-ZIP zip and bsdtar: no top folder, a
    "Sonic Adventure 2/" folder, deeper nesting, a non-ASCII folder, data descriptors, ZIP64
    (Info-ZIP's -fz, and one written here with every central-directory size in ZIP64's field),
    backslash names and macOS's __MACOSX/ and ._ entries;
  * names: UTF-8 with bit 11, Info-ZIP's Unicode Path field, and code page 437 without either;
  * the refusals: not a ZIP, not SA2, the game twice, duplicate and case-only names among the
    files it reads (and not among those it skips), encryption, a split archive, methods other
    than stored and deflate, damaged and truncated data (an extra's caught by its CRC-32 alone),
    unsafe names, a missing or changed file the game reads, an unhashed file under resource/
    the game would read unverified (the folder route's rule for an extra), a mismatched *_orig
    original, too little free space, and a directory this route did not write in the place of
    the store, of its ".old" or of its ".partial";
  * the executable, checked by its sections (the DRM ruling) and never by its whole-file hash;
  * the user's policy of 2026-10-11 (Documentation/decisions/2026-10-11-S7-user-answers.md,
    answer 3) on a Mod Loader install: its extras skipped, a matching Data_DLL_orig.dll stored
    under the canonical name;
  * the store: destinations named by the table, temporary names no archive name can take, a
    partial store resumed and cleaned (each kept file verified again), the same record bytes
    whether a run started fresh or resumed, and an earlier store replaced only once its
    successor is complete;
  * user files written into a user directory (the overlay) only when asked, never over a file.

The synthetic game is random bytes generated here; nothing shipped is read or written.
`SA2_INSTALL_SRC` points the build at another copy of host/ (a unit's snapshot).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import shutil
import struct
import subprocess
import tarfile
import zipfile
import zlib
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
HOST = Path(os.environ.get("SA2_INSTALL_SRC", ROOT / "host"))
INSTALL = HOST / "install"
PLAYER = HOST / "player"
# The route, the core's shared checks it calls (sa2_verify.cpp, on the core's platform layer),
# and the player's SHA-256.
SOURCES = [INSTALL / "sa2_zip.cpp", INSTALL / "sa2_inflate.cpp", INSTALL / "sa2_store.cpp",
           INSTALL / "sa2_verify.cpp", INSTALL / "sa2_platform_posix.cpp", PLAYER / "sha256.cpp"]
ZIG = Path(os.environ.get("SA2_ZIG") or ROOT / "private/predecessor-tools/zig/zig-x86_64-linux-0.15.2/zig")

# The driver: inflate, crc, install and safe, each printing what the route returned.
DRIVER_CPP = r'''// The ZIP route's test driver: inflate files, or install a ZIP against a TSV table (or the
// linked generated table, with SA2_ZIP_REAL_TABLE), and print what happened as JSON lines.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include "sa2_inflate.h"
#include "sa2_install.h"
#include "sa2_store.h"
#include "sa2_zip.h"
#include "sha256.h"

namespace {

struct FileIo : Sa2InflateIo {
    std::vector<uint8_t> in;
    size_t at = 0, chunk = 4096;
    uint32_t crc = 0;
    uint64_t n = 0;
    Sha256 sha;
    size_t read(uint8_t* buf, size_t cap) override {
        size_t k = in.size() - at;
        if (k > cap) k = cap;
        if (k > chunk) k = chunk;
        if (!k) return 0;
        std::memcpy(buf, in.data() + at, k);
        at += k;
        return k;
    }
    bool write(const uint8_t* p, size_t k) override {
        crc = sa2_crc32(crc, p, k);
        sha.update(p, k);
        n += k;
        return true;
    }
};

bool slurp(const char* path, std::vector<uint8_t>& out) {
    FILE* f = std::fopen(path, "rb");
    if (!f) return false;
    uint8_t buf[65536];
    size_t k;
    while ((k = std::fread(buf, 1, sizeof buf, f)) > 0) out.insert(out.end(), buf, buf + k);
    std::fclose(f);
    return true;
}

bool unhex(const std::string& h, uint8_t out[32]) {
    if (h.size() != 64) return false;
    for (size_t i = 0; i < 32; i++) out[i] = uint8_t(std::strtoul(h.substr(2 * i, 2).c_str(), nullptr, 16));
    return true;
}

std::string q(const std::string& s) { return "\"" + sa2_json_escape(s) + "\""; }

std::string list(const std::vector<std::string>& v) {
    std::string o = "[";
    for (size_t i = 0; i < v.size(); i++) o += (i ? "," : "") + q(v[i]);
    return o + "]";
}

const char* kind_name(Sa2FileResult::Kind k) {
    switch (k) {
    case Sa2FileResult::Ok: return "ok";
    case Sa2FileResult::Missing: return "missing";
    case Sa2FileResult::Modified: return "modified";
    case Sa2FileResult::SubstitutedOrig: return "substituted-orig";
    }
    return "?";
}

struct TsvTable {
    std::string id, receipt;
    std::vector<std::string> paths;
    std::vector<Sa2InstallEntry> entries;
    std::vector<std::string> sec_names;
    std::vector<Sa2ExeSection> sections;
    Sa2ExeCheck exe{};
    std::string exe_path;
    Sa2InstallTable table{};
};

uint32_t num(const std::string& s) { return uint32_t(std::strtoul(s.c_str(), nullptr, 10)); }

bool load_tsv(const char* path, TsvTable& t) {
    std::vector<uint8_t> raw;
    if (!slurp(path, raw)) return false;
    std::string text(raw.begin(), raw.end());
    size_t p = 0;
    std::vector<std::vector<std::string>> rows;
    while (p < text.size()) {
        size_t e = text.find('\n', p);
        if (e == std::string::npos) e = text.size();
        std::string line = text.substr(p, e - p);
        p = e + 1;
        if (line.empty()) continue;
        std::vector<std::string> cols;
        size_t s = 0;
        for (;;) {
            size_t t2 = line.find('\t', s);
            cols.push_back(line.substr(s, t2 == std::string::npos ? std::string::npos : t2 - s));
            if (t2 == std::string::npos) break;
            s = t2 + 1;
        }
        rows.push_back(cols);
    }
    if (rows.empty() || rows[0].size() < 3 || rows[0][0] != "id") return false;
    t.id = rows[0][1];
    t.receipt = rows[0][2];
    // Rows: "required size sha256 path" per entry; "exe path <header fields>" and its
    // "sec name va vsize raw chars hashed hashed_bytes sha256" rows give one entry the
    // executable's check by sections (sa2_install.h).
    t.paths.reserve(rows.size());
    t.sec_names.reserve(rows.size());
    std::vector<size_t> entry_rows;
    for (size_t i = 1; i < rows.size(); i++) {
        const std::vector<std::string>& r = rows[i];
        if (r[0] == "exe") {
            if (r.size() < 13) return false;
            t.exe_path = r[1];
            t.exe.e_lfanew = num(r[2]);
            t.exe.sections_count = uint16_t(num(r[3]));
            t.exe.optional_header_size = uint16_t(num(r[4]));
            t.exe.magic = uint16_t(num(r[5]));
            t.exe.image_base = num(r[6]);
            t.exe.size_of_image = num(r[7]);
            t.exe.size_of_headers = num(r[8]);
            t.exe.import_rva = num(r[9]);
            t.exe.import_size = num(r[10]);
            t.exe.resource_rva = num(r[11]);
            t.exe.resource_size = num(r[12]);
            t.exe.ruling = "the test's";
        } else if (r[0] == "sec") {
            if (r.size() < 9) return false;
            t.sec_names.push_back(r[1]);
            Sa2ExeSection sec{};
            sec.va = num(r[2]);
            sec.virtual_size = num(r[3]);
            sec.raw_size = num(r[4]);
            sec.characteristics = num(r[5]);
            sec.hashed = r[6] == "1";
            sec.hashed_bytes = num(r[7]);
            if (!unhex(r[8], sec.sha256)) return false;
            sec.why_not_hashed = sec.hashed ? "" : "the test's";
            t.sections.push_back(sec);
        } else {
            if (r.size() < 4) return false;
            t.paths.push_back(r[3]);
            entry_rows.push_back(i);
        }
    }
    for (size_t j = 0; j < t.sections.size(); j++) t.sections[j].name = t.sec_names[j].c_str();
    t.exe.sections = t.sections.data();
    t.exe.count = t.sections.size();
    for (size_t j = 0; j < entry_rows.size(); j++) {
        const std::vector<std::string>& r = rows[entry_rows[j]];
        Sa2InstallEntry e{};
        e.path = t.paths[j].c_str();
        e.required = r[0] == "1";
        e.size = std::strtoull(r[1].c_str(), nullptr, 10);
        if (!unhex(r[2], e.sha256)) return false;
        e.exe = !t.exe_path.empty() && t.paths[j] == t.exe_path ? &t.exe : nullptr;
        t.entries.push_back(e);
    }
    t.table.id = t.id.c_str();
    t.table.receipt_sha256 = t.receipt.c_str();
    t.table.entries = t.entries.data();
    t.table.count = t.entries.size();
    t.table.generated_by = "test";
    return true;
}

int install(int argc, char** argv, const Sa2InstallTable* linked) {
    // install ZIP STORE (TABLE.tsv | -) [--threads N] [--free BYTES] [--user-dir DIR]
    if (argc < 5) return 2;
    TsvTable tsv;
    const Sa2InstallTable* table = linked;
    if (std::strcmp(argv[4], "-") != 0) {
        if (!load_tsv(argv[4], tsv)) {
            std::fprintf(stderr, "cannot load table %s\n", argv[4]);
            return 2;
        }
        table = &tsv.table;
    }
    if (!table) return 2;
    Sa2ZipOptions opt;
    for (int i = 5; i + 1 < argc; i += 2) {
        if (!std::strcmp(argv[i], "--threads")) opt.threads = unsigned(std::atoi(argv[i + 1]));
        if (!std::strcmp(argv[i], "--free")) opt.free_bytes_override = std::strtoull(argv[i + 1], nullptr, 10);
        if (!std::strcmp(argv[i], "--user-dir")) opt.user_dir = argv[i + 1];
    }
    Sa2InstallReport r;
    Sa2ZipDetail d;
    const bool ok = sa2_zip_install(argv[2], argv[3], *table, r, opt, &d);
    std::string o = "{";
    o += "\"ok\":" + std::string(ok ? "true" : "false");
    o += ",\"failure\":" + std::to_string(r.failure);
    o += ",\"error\":" + q(r.error);
    o += ",\"counts\":{\"ok\":" + std::to_string(r.ok) + ",\"missing\":" + std::to_string(r.missing) +
         ",\"modified\":" + std::to_string(r.modified) + ",\"substituted\":" + std::to_string(r.substituted) +
         ",\"extras\":" + std::to_string(r.extras) + ",\"known_missing\":" + std::to_string(r.known_missing) +
         ",\"known_modified\":" + std::to_string(r.known_modified) + "}";
    o += ",\"mod_loader\":" + std::string(r.mod_loader ? "true" : "false");
    o += ",\"markers\":" + list(r.markers);
    o += ",\"root\":" + q(d.root);
    o += ",\"zip64\":" + std::string(d.zip64 ? "true" : "false");
    o += ",\"data_descriptors\":" + std::string(d.data_descriptors ? "true" : "false");
    std::vector<std::string> methods;
    for (int m : d.methods) methods.push_back(std::to_string(m));
    o += ",\"methods\":" + list(methods);
    o += ",\"extracted_extras\":" + list(d.extracted_extras);
    o += ",\"skipped\":" + list(d.skipped);
    o += ",\"user_files\":" + list(d.user_files);
    o += ",\"user_files_written\":" + list(d.user_files_written);
    o += ",\"user_files_kept\":" + list(d.user_files_kept);
    o += ",\"files_verified\":" + std::string(d.files_verified ? "true" : "false");
    o += ",\"names_cp437\":" + std::to_string(d.names_cp437);
    o += ",\"names_unicode_field\":" + std::to_string(d.names_unicode_field);
    o += ",\"fs_retries\":" + std::to_string(d.fs_retries);
    o += ",\"damaged\":" + list(d.damaged);
    o += ",\"resumed\":" + std::to_string(d.resumed);
    o += ",\"extracted\":" + std::to_string(d.extracted);
    o += ",\"bytes_written\":" + std::to_string(d.bytes_written);
    o += ",\"bytes_needed\":" + std::to_string(d.bytes_needed);
    o += ",\"entries\":" + std::to_string(d.entries);
    o += ",\"archive_bytes\":" + std::to_string(d.archive_bytes);
    o += ",\"store_record\":" + q(d.store_record);
    o += ",\"files\":[";
    for (size_t i = 0; i < r.files.size(); i++) {
        const Sa2FileResult& f = r.files[i];
        o += std::string(i ? "," : "") + "{\"path\":" + q(f.path) + ",\"kind\":" + q(kind_name(f.kind)) +
             ",\"detail\":" + q(f.detail) + ",\"found_as\":" + q(f.found_as) +
             ",\"required\":" + (f.required ? "true" : "false") + "}";
    }
    o += "]}\n";
    std::fputs(o.c_str(), stdout);
    return 0;
}

}  // namespace

#ifdef SA2_ZIP_REAL_TABLE
static const Sa2InstallTable* linked_table() { return &sa2_install_table(); }
#else
static const Sa2InstallTable* linked_table() { return nullptr; }
#endif

int main(int argc, char** argv) {
    if (argc >= 2 && !std::strcmp(argv[1], "inflate")) {
        // inflate [--chunk N] FILE...: one line per file
        int i = 2;
        size_t chunk = 4096;
        if (argc > 3 && !std::strcmp(argv[2], "--chunk")) {
            chunk = size_t(std::strtoull(argv[3], nullptr, 10));
            i = 4;
        }
        for (; i < argc; i++) {
            FileIo io;
            io.chunk = chunk ? chunk : 1;
            if (!slurp(argv[i], io.in)) {
                std::printf("%s\terror\n", argv[i]);
                continue;
            }
            const Sa2InflateResult r = sa2_inflate(io);
            std::printf("%s\t%s\t%llu\t%08x\t%s\t%llu\t%s\n", argv[i], sa2_inflate_status_name(r.status),
                        (unsigned long long)io.n, io.crc, io.sha.hex().c_str(),
                        (unsigned long long)r.in_bytes, r.why.c_str());
        }
        return 0;
    }
    if (argc >= 3 && !std::strcmp(argv[1], "crc")) {
        std::vector<uint8_t> d;
        if (!slurp(argv[2], d)) return 2;
        // Split the buffer at every offset modulo 17 to exercise the chained form.
        uint32_t c = 0;
        size_t at = 0, step = 1;
        while (at < d.size()) {
            size_t k = d.size() - at < step ? d.size() - at : step;
            c = sa2_crc32(c, d.data() + at, k);
            at += k;
            step = step % 17 + 1;
        }
        std::printf("%08x\n", c);
        return 0;
    }
    if (argc >= 2 && !std::strcmp(argv[1], "install")) return install(argc, argv, linked_table());
    if (argc >= 3 && !std::strcmp(argv[1], "safe")) {
        for (int i = 2; i < argc; i++) {
            std::string why;
            const bool ok = sa2_store_safe_relpath(argv[i], &why);
            std::printf("%s\t%s\n", ok ? "safe" : "unsafe", why.c_str());
        }
        return 0;
    }
    std::fprintf(stderr, "usage: driver inflate|crc|install|safe ...\n");
    return 2;
}
'''


# ------------------------------------------------------------------------- the build

@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    cxx = shutil.which("g++")
    assert cxx, "g++ is not on PATH: the route is built with it under the sanitizers"
    for s in SOURCES:
        assert s.is_file(), f"{s} is missing"
    out = tmp_path_factory.mktemp("instzip-build")
    src = out / "driver.cpp"
    src.write_text(DRIVER_CPP)
    exe = out / "driver"
    cmd = [cxx, "-std=c++17", "-O1", "-g", "-fsanitize=address,undefined",
           "-fno-sanitize-recover=all", "-fno-omit-frame-pointer", "-Wall", "-Wextra", "-pthread",
           f"-I{INSTALL}", f"-I{PLAYER}", *map(str, SOURCES), str(src), "-o", str(exe)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-6000:]
    for own in ("sa2_zip.", "sa2_inflate.", "sa2_store."):
        assert own not in r.stderr, f"the build warns about the route's own files:\n{r.stderr[-6000:]}"
    return exe


def run(driver: Path, *args) -> str:
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=1:abort_on_error=0",
               UBSAN_OPTIONS="print_stacktrace=1")
    r = subprocess.run([str(driver), *map(str, args)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr[-6000:]
    assert "Sanitizer" not in r.stderr and "runtime error" not in r.stderr, r.stderr[-6000:]
    return r.stdout


def test_the_route_compiles_for_windows_with_the_pinned_zig(tmp_path):
    assert ZIG.is_file(), f"the pinned zig is not at {ZIG}"
    env = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(tmp_path / "g"),
               ZIG_LOCAL_CACHE_DIR=str(tmp_path / "l"))
    for f in ("sa2_zip.cpp", "sa2_inflate.cpp", "sa2_store.cpp"):
        r = subprocess.run([str(ZIG), "c++", "-target", "x86_64-windows-gnu", "-std=c++17",
                            "-O2", "-Wall", "-Wextra", "-Werror", f"-I{INSTALL}", f"-I{PLAYER}",
                            "-c", str(INSTALL / f), "-o", str(tmp_path / (f + ".obj"))],
                           capture_output=True, text=True, env=env)
        assert r.returncode == 0, f"{f}:\n{r.stderr[-4000:]}"
        assert (tmp_path / (f + ".obj")).stat().st_size > 0


# ------------------------------------------------------------------------- inflate

def raw_deflate(data: bytes, level: int = 6, strategy: int = zlib.Z_DEFAULT_STRATEGY,
                zdict: bytes | None = None) -> bytes:
    c = (zlib.compressobj(level, zlib.DEFLATED, -15, 9, strategy, zdict) if zdict else
         zlib.compressobj(level, zlib.DEFLATED, -15, 9, strategy))
    return c.compress(data) + c.flush()


def zlib_outcome(b: bytes) -> tuple[str, bytes | None]:
    d = zlib.decompressobj(-15)
    try:
        out = d.decompress(b)
    except zlib.error:
        return "bad-data", None
    return ("ok", out) if d.eof else ("truncated", None)


def inflate_lines(driver: Path, files: list[Path], chunk: int) -> dict[str, list[str]]:
    out = run(driver, "inflate", "--chunk", chunk, *files)
    rows = {}
    for line in out.splitlines():
        cols = line.split("\t")
        rows[cols[0]] = cols[1:]
    assert len(rows) == len(files)
    return rows


def synthetic_data(rnd: random.Random) -> dict[str, bytes]:
    words = [rnd.randbytes(rnd.randrange(1, 9)) for _ in range(300)]
    return {
        "empty": b"",
        "one": b"a",
        "text": b"".join(b"line %d of the synthetic stage table\n" % i for i in range(8000)),
        "random": rnd.randbytes(200_000),
        "zeros": bytes(1_500_000),
        "runs": b"".join(bytes([rnd.randrange(4)]) * rnd.randrange(1, 400) for _ in range(4000)),
        "words": b"".join(rnd.choice(words) for _ in range(60_000)),
        "mixed": b"".join(rnd.randbytes(rnd.randrange(1, 300)) + b"abc" * rnd.randrange(0, 120)
                          for _ in range(2500)),
        # A 32,000-byte period: matches near the 32 KiB window's edge, over 1 MiB, so they
        # cross the decoder's output-chunk slides.
        "far": rnd.randbytes(32_000) * 40,
    }


STRATEGIES = {"default": zlib.Z_DEFAULT_STRATEGY, "filtered": zlib.Z_FILTERED,
              "huffman": zlib.Z_HUFFMAN_ONLY, "rle": zlib.Z_RLE, "fixed": zlib.Z_FIXED}


def test_inflate_matches_zlib_at_every_level_and_strategy(driver, tmp_path):
    datas = synthetic_data(random.Random(11))
    files, want = [], {}
    for name, data in datas.items():
        for level in range(10):
            for sname, strategy in STRATEGIES.items():
                f = tmp_path / f"{name}-{level}-{sname}.raw"
                comp = raw_deflate(data, level, strategy)
                f.write_bytes(comp)
                files.append(f)
                want[str(f)] = (data, len(comp))
    assert len(files) == len(datas) * 10 * len(STRATEGIES)
    for chunk in (7, 65536):
        rows = inflate_lines(driver, files, chunk)
        for f, (data, n_in) in want.items():
            status, n, crc, sha, used, why = rows[f]
            assert (status, int(n), int(crc, 16), sha, int(used)) == (
                "ok", len(data), zlib.crc32(data), hashlib.sha256(data).hexdigest(), n_in), (f, why)


def test_crc32_matches_zlib_in_chained_pieces(driver, tmp_path):
    rnd = random.Random(5)
    for n in (0, 1, 7, 8, 9, 4096, 100_003):
        data = rnd.randbytes(n)
        f = tmp_path / f"c{n}"
        f.write_bytes(data)
        assert int(run(driver, "crc", f), 16) == zlib.crc32(data)


def test_hand_made_streams_are_judged_as_zlib_judges_them(driver, tmp_path):
    rnd = random.Random(3)
    dictionary = rnd.randbytes(2000)
    payload = dictionary[500:1500] * 3
    stream = raw_deflate(b"tail data " * 50)
    cases = {
        "fixed-empty": (b"\x03\x00", ("ok", 0), ""),
        "block-type-3": (b"\x07", ("bad-data", 0), "invalid block type"),
        "stored": (b"\x01\x05\x00\xfa\xffhello", ("ok", 5), ""),
        "stored-bad-nlen": (b"\x01\x05\x00\x00\x00hello", ("bad-data", 0), "invalid stored block lengths"),
        # A truncated stream's output is not handed on: nothing of it reaches the store.
        "stored-cut": (b"\x01\x05\x00\xfa\xffhel", ("truncated", 0), "the input ended inside the stream"),
        "nothing": (b"", ("truncated", 0), "the input ended inside the stream"),
        # A preset dictionary the decoder does not have: the first match reaches before byte 0.
        "too-far-back": (raw_deflate(payload, 9, zdict=dictionary), ("bad-data", None),
                         "invalid distance too far back"),
        "trailing-bytes": (stream + b"JUNK", ("ok", 500), ""),
    }
    files = []
    for name, (data, _, _) in cases.items():
        f = tmp_path / f"{name}.raw"
        f.write_bytes(data)
        files.append(f)
    rows = inflate_lines(driver, files, 3)
    for f, (name, (data, (status, n), why)) in zip(files, cases.items()):
        got = rows[str(f)]
        assert got[0] == status, (name, got)
        assert zlib_outcome(data)[0] == status, name
        if n is not None:
            assert int(got[1]) == n, (name, got)
        assert got[5] == why, (name, got)
    assert int(rows[str(tmp_path / "trailing-bytes.raw")][4]) == len(stream)


def test_truncation_and_bit_flips_agree_with_zlib_and_stay_in_bounds(driver, tmp_path):
    rnd = random.Random(7)
    seeds = [raw_deflate(b"hello world " * 400 + rnd.randbytes(1500), 6),
             raw_deflate(rnd.randbytes(4000), 9),
             raw_deflate(b"abc" * 3000, 1, zlib.Z_FIXED),
             raw_deflate(bytes(range(256)) * 30, 0),
             raw_deflate(b"".join(rnd.randbytes(rnd.randrange(1, 40)) + b"xyz" * rnd.randrange(0, 20)
                                  for _ in range(300)), 6, zlib.Z_HUFFMAN_ONLY),
             raw_deflate(synthetic_data(rnd)["words"][:30_000], 9, zlib.Z_RLE)]
    cases: dict[str, bytes] = {}
    for s in seeds:
        for _ in range(300):
            b = bytearray(s)
            r = rnd.random()
            if r < 0.4:
                for _ in range(rnd.randrange(1, 4)):
                    b[rnd.randrange(len(b))] ^= 1 << rnd.randrange(8)
            elif r < 0.7:
                b = b[:rnd.randrange(len(b))]
            elif r < 0.85:
                p = rnd.randrange(len(b))
                b[p] = rnd.randrange(256)
                b = b[:rnd.randrange(p, len(b) + 1)]
            else:
                b = bytearray(rnd.randbytes(rnd.randrange(1, 200)))
            f = tmp_path / f"f{len(cases)}.raw"
            f.write_bytes(bytes(b))
            cases[str(f)] = bytes(b)
    files = [Path(f) for f in cases]
    tally: Counter = Counter()
    for chunk in (1, 4096):
        rows = inflate_lines(driver, files, chunk)
        for f, data in cases.items():
            zs, zout = zlib_outcome(data)
            status, n, _, sha = rows[f][:4]
            tally[(zs, status)] += 1
            assert status == zs, (f, zs, rows[f])
            if zs == "ok":
                assert sha == hashlib.sha256(zout).hexdigest() and int(n) == len(zout), f
    # Every class turned up, so the agreement is not vacuous.
    for cls in ("ok", "bad-data", "truncated"):
        assert tally[(cls, cls)] >= 100, tally


# ------------------------------------------------------------------------- the synthetic game

FA, SA = 0x200, 0x1000
TEXT, DATA = 0x60000020, 0xC0000040


def pe_image(code: bytes, data: bytes, bind: bytes) -> bytes:
    """A PE32 image laid out as SA2's is for the executable's check: code (.text, which the
    release build overlays), data (.data, which the runtime reads from the file) and SteamStub's
    wrapper (.bind), each at its own 0x1000-aligned RVA."""
    secs = [(b".text", TEXT, code), (b".data", DATA, data), (b".bind", TEXT, bind)]
    lfanew, opt_size, headers = 0x80, 0xE0, 0x400
    layout, rva, raw_ptr = [], SA, headers
    for name, chars, body in secs:
        size = (len(body) + FA - 1) // FA * FA
        layout.append((name, chars, body, rva, raw_ptr, size))
        raw_ptr += size
        rva += (max(len(body), 1) + SA - 1) // SA * SA
    image = bytearray(raw_ptr)
    image[0:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, lfanew)
    image[lfanew:lfanew + 4] = b"PE\0\0"
    struct.pack_into("<HHIIIHH", image, lfanew + 4, 0x14C, len(secs), 0, 0, 0, opt_size, 0x0102)
    opt = lfanew + 24
    struct.pack_into("<H", image, opt, 0x10B)
    struct.pack_into("<I", image, opt + 16, SA)
    struct.pack_into("<I", image, opt + 28, 0x400000)
    struct.pack_into("<II", image, opt + 32, SA, FA)
    struct.pack_into("<II", image, opt + 56, rva, headers)
    struct.pack_into("<I", image, opt + 92, 16)
    for i, (name, chars, body, r, ptr, size) in enumerate(layout):
        sh = opt + opt_size + 40 * i
        image[sh:sh + 8] = name.ljust(8, b"\0")
        struct.pack_into("<IIII", image, sh + 8, max(len(body), 1), r, size, ptr)
        struct.pack_into("<I", image, sh + 36, chars)
        image[ptr:ptr + len(body)] = body
    return bytes(image)


def exe_rows(path: str, image: bytes) -> list[str]:
    """The table's check of `image` by sections, as the driver's TSV takes it: every header
    field the runtime reads, and every section's table entry, the code and wrapper sections
    unhashed, the rest hashed over the bytes the loader copies."""
    u16 = lambda o: struct.unpack_from("<H", image, o)[0]
    u32 = lambda o: struct.unpack_from("<I", image, o)[0]
    lf = u32(0x3C)
    nsec, opt_size = u16(lf + 6), u16(lf + 20)
    opt = lf + 24
    rows = ["\t".join(map(str, ["exe", path, lf, nsec, opt_size, u16(opt), u32(opt + 28),
                                 u32(opt + 56), u32(opt + 60), u32(opt + 104), u32(opt + 108),
                                 u32(opt + 112), u32(opt + 116)]))]
    for i in range(nsec):
        sh = opt + opt_size + 40 * i
        name = image[sh:sh + 8].rstrip(b"\0").decode()
        vsize, va, raw, ptr = struct.unpack_from("<IIII", image, sh + 8)
        chars = u32(sh + 36)
        hashed = name not in (".text", ".bind")
        take = 0 if ptr >= len(image) else min(raw, len(image) - ptr)
        if vsize:
            take = min(take, vsize)
        sha = hashlib.sha256(image[ptr:ptr + take]).hexdigest() if hashed else "0" * 64
        rows.append("\t".join(map(str, ["sec", name, va, vsize, raw, chars, int(hashed),
                                         take if hashed else 0, sha])))
    return rows


def game_files(seed: int = 0) -> dict[str, bytes]:
    rnd = random.Random(seed)
    return {
        "Launcher.exe": b"MZ" + rnd.randbytes(4_000),
        "resource/Shader/win32/obj.pak": rnd.randbytes(2_000) * 8,
        "resource/gd_PC/ADX/stg13.adx": bytes(120_000),
        "resource/gd_PC/DLL/Win32/Data_DLL.dll": b"MZ" + rnd.randbytes(60_000),
        "resource/gd_PC/PRS/sub/obj.prs": rnd.randbytes(30_000),
        "resource/gd_PC/one.bin": b"x",
        "resource/gd_PC/stg13_dat.bin": b"".join(b"line %d of the stage table\n" % i
                                                 for i in range(6000)),
        "sonic2app.exe": pe_image(rnd.randbytes(40_000), rnd.randbytes(30_000), rnd.randbytes(9_000)),
        "steam_api.dll": b"MZ" + rnd.randbytes(3_000),
    }


EXE = "sonic2app.exe"


def wrapped(image: bytes, *, code: bool = False, bind: bool = False, data: bool = False) -> bytes:
    """`image` with one byte of each named section changed, its layout kept."""
    out = bytearray(image)
    lf = struct.unpack_from("<I", out, 0x3C)[0]
    sh0 = lf + 24 + struct.unpack_from("<H", out, lf + 20)[0]
    for i, (name, want) in enumerate(((".text", code), (".data", data), (".bind", bind))):
        ptr = struct.unpack_from("<I", out, sh0 + 40 * i + 20)[0]
        if want:
            out[ptr + 100] ^= 0xFF
    return bytes(out)


GAME = game_files()
UNHASHED = {
    "resource/gd_PC/StageSelectConfig.ini": b"[StageSelect]\nlevel=13\n",
    "resource/gd_PC/SAVEDATA/SONIC2B__S01": b"save" * 100,
    "Config/UserConfig.cfg": b'<Configs FullScreen="0" />\n',
    "redist/DXSETUP.exe": b"MZ" + bytes(500),
    "installscript.vdf": b'"InstallScript" {}\n',
    "steam_appid.txt": b"213610",
}
REQUIRED = {p for p in GAME if p.startswith("resource/") or p == "sonic2app.exe"}
USER_FILES = ["Config/UserConfig.cfg", "resource/gd_PC/SAVEDATA/SONIC2B__S01"]
SKIPPED = ["installscript.vdf", "redist/DXSETUP.exe", "steam_appid.txt"]
EXTRACTED_EXTRAS = ["resource/gd_PC/StageSelectConfig.ini"]
RECORD = "sa2recomp-store.json"
DLL = "resource/gd_PC/DLL/Win32/Data_DLL.dll"
ORIG = "resource/gd_PC/DLL/Win32/Data_DLL_orig.dll"
FAIL_REFUSED, FAIL_NOT_FOUND, FAIL_IO, FAIL_AMBIGUOUS = 3, 4, 5, 6


def write_table(t: Path, game: dict[str, bytes], extra_rows: tuple[str, ...] = ()) -> Path:
    """The TSV table of `game`, as tools/s7/install_table.py lays the real one out: every file
    by size and SHA-256, but the executable by its sections (its hash all zero)."""
    lines = ["id\tinstall-hashes-test\t" + "ab" * 32]
    for p in sorted(game):
        req = p.startswith("resource/") or p == EXE
        sha = "0" * 64 if p == EXE else hashlib.sha256(game[p]).hexdigest()
        lines.append(f"{int(req)}\t{len(game[p])}\t{sha}\t{p}")
    lines += exe_rows(EXE, game[EXE]) + list(extra_rows)
    t.write_text("\n".join(lines) + "\n")
    return t


@pytest.fixture(scope="module")
def table(tmp_path_factory) -> Path:
    return write_table(tmp_path_factory.mktemp("instzip-table") / "table.tsv", GAME)


def install(driver: Path, archive: Path, store: Path, table: Path, *extra) -> dict:
    return json.loads(run(driver, "install", archive, store, table, *extra))


def pyzip(path: Path, members: dict[str, bytes], prefix: str = "",
          method: int = zipfile.ZIP_DEFLATED, level: int | None = 6) -> Path:
    with zipfile.ZipFile(path, "w", compression=method, compresslevel=level) as z:
        for name, data in members.items():
            z.writestr(prefix + name, data)
    return path


def tree(base: Path, members: dict[str, bytes]) -> Path:
    for name, data in members.items():
        p = base / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return base


def store_files(store: Path) -> dict[str, bytes]:
    return {p.relative_to(store).as_posix(): p.read_bytes()
            for p in sorted(store.rglob("*")) if p.is_file() and p.name != RECORD}


def assert_installed(rep: dict, store: Path, game: dict[str, bytes] = GAME,
                     extras: tuple[str, ...] = tuple(EXTRACTED_EXTRAS),
                     extra_data: dict[str, bytes] | None = None) -> None:
    assert rep["ok"] is True and rep["failure"] == 0 and rep["error"] == "", rep["error"]
    want = dict(game)
    for e in extras:
        want[e] = {**UNHASHED, **(extra_data or {})}[e]
    assert store_files(store) == want
    # No directory but the ones those files live in.
    dirs = {p.relative_to(store).as_posix() for p in store.rglob("*") if p.is_dir()}
    assert dirs == {"/".join(f.split("/")[:i]) for f in want for i in range(1, f.count("/") + 1)}
    rec = json.loads((store / RECORD).read_text())
    assert rec["schema"] == "sa2-store/v1" and rec["complete"] is True
    assert rec["table_id"] == "install-hashes-test"
    assert [f["path"] for f in rec["files"]] == [p for p in sorted(GAME) if p in game] + list(extras)
    for f in rec["files"]:
        assert f["sha256"] == hashlib.sha256(want[f["path"]]).hexdigest()
        assert hashlib.sha256((store / f["path"]).read_bytes()).hexdigest() == f["sha256"]
    assert not Path(str(store) + ".partial").exists() and not Path(str(store) + ".old").exists()
    assert rep["files_verified"] is True
    assert not [f for f in rep["files"] if f["detail"].startswith("not read")]


# ------------------------------------------------------------------------- archive layouts

def zip64_by_hand(path: Path, members: dict[str, bytes], prefix: str = "",
                  claims: dict[str, int] | None = None) -> Path:
    """Every size and offset of the central directory in ZIP64's extra field, and ZIP64's end
    records, as a writer of an archive past 4 GiB lays them out. `claims` puts another
    uncompressed size in the central directory for the members it names."""
    out, cd = bytearray(), bytearray()
    for name, data in members.items():
        nb = (prefix + name).encode()
        comp = raw_deflate(data)
        crc = zlib.crc32(data)
        off = len(out)
        lx = struct.pack("<HHQQ", 1, 16, len(data), len(comp))
        out += struct.pack("<IHHHHHIIIHH", 0x04034B50, 45, 0, 8, 0, 0x21, crc, 0xFFFFFFFF,
                           0xFFFFFFFF, len(nb), len(lx)) + nb + lx + comp
        usize = (claims or {}).get(name, len(data))
        cx = struct.pack("<HHQQQ", 1, 24, usize, len(comp), off)
        cd += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 45, 45, 0, 8, 0, 0x21, crc,
                          0xFFFFFFFF, 0xFFFFFFFF, len(nb), len(cx), 0, 0, 0, 0,
                          0xFFFFFFFF) + nb + cx
    cd_off = len(out)
    out += cd
    z64 = len(out)
    n = len(members)
    out += struct.pack("<IQHHIIQQQQ", 0x06064B50, 44, 45, 45, 0, 0, n, n, len(cd), cd_off)
    out += struct.pack("<IIQI", 0x07064B50, 0, z64, 1)
    out += struct.pack("<IHHHHIIH", 0x06054B50, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFF, 0xFFFFFFFF,
                       0xFFFFFFFF, 0)
    path.write_bytes(bytes(out))
    if not claims:
        with zipfile.ZipFile(path) as z:   # Python's reader agrees it is a ZIP64 archive
            assert z.read(prefix + "sonic2app.exe") == members["sonic2app.exe"]
    return path


def need(tool: str) -> str:
    exe = shutil.which(tool)
    if not exe:
        pytest.skip(f"{tool} is not installed here")
    return exe


def build_layout(kind: str, tmp: Path) -> tuple[Path, str]:
    """The archive of `kind` holding GAME and UNHASHED, and the root the route should find."""
    members = {**GAME, **UNHASHED}
    a = tmp / f"{kind}.zip"
    if kind == "python-top":
        return pyzip(a, members), ""
    if kind == "python-folder":
        return pyzip(a, members, "Sonic Adventure 2/"), "Sonic Adventure 2/"
    if kind == "python-deep":
        p = "steamapps/common/Sonic Adventure 2/"
        return pyzip(a, members, p), p
    if kind == "python-non-ascii":
        p = "Sonic Adventure 2 – copie/"
        return pyzip(a, members, p), p
    if kind == "python-stored":
        return pyzip(a, members, "SA2/", zipfile.ZIP_STORED, None), "SA2/"
    if kind == "python-mixed-levels":
        with zipfile.ZipFile(a, "w") as z:
            for i, (name, data) in enumerate(members.items()):
                z.writestr(name, data, compress_type=(zipfile.ZIP_STORED if i % 3 == 0 else
                                                      zipfile.ZIP_DEFLATED), compresslevel=i % 10)
        return a, ""
    if kind == "python-backslashes":
        with zipfile.ZipFile(a, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in members.items():
                info = zipfile.ZipInfo(("Sonic Adventure 2/" + name).replace("/", "\\"))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data)
        return a, "Sonic Adventure 2/"
    if kind == "python-macos":
        with zipfile.ZipFile(a, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in members.items():
                z.writestr("Sonic Adventure 2/" + name, data)
                z.writestr("__MACOSX/Sonic Adventure 2/._" + name.rsplit("/", 1)[-1], b"\0\5\x16\7")
            z.writestr("Sonic Adventure 2/resource/gd_PC/.DS_Store", b"\0\0\0\1Bud1")
            z.writestr("Sonic Adventure 2/resource/gd_PC/._one.bin", b"\0\5\x16\7")
        return a, "Sonic Adventure 2/"
    if kind == "python-directory-entries":
        with zipfile.ZipFile(a, "w", zipfile.ZIP_DEFLATED) as z:
            dirs = sorted({n.rsplit("/", 1)[0] + "/" for n in members if "/" in n})
            for d in dirs:
                z.writestr("Sonic Adventure 2/" + d, b"")
            for name, data in members.items():
                z.writestr("Sonic Adventure 2/" + name, data)
        return a, "Sonic Adventure 2/"
    if kind == "zip64-by-hand":
        return zip64_by_hand(a, members, "Sonic Adventure 2/"), "Sonic Adventure 2/"
    src = tree(tmp / f"{kind}-tree" / "Sonic Adventure 2", members)
    if kind == "infozip-top":
        subprocess.run([need("zip"), "-q", "-r", str(a), "."], cwd=src, check=True)
        return a, ""
    if kind == "infozip-folder":
        subprocess.run([need("zip"), "-q", "-r", str(a), "Sonic Adventure 2"], cwd=src.parent,
                       check=True)
        return a, "Sonic Adventure 2/"
    if kind == "infozip-data-descriptors":
        z = subprocess.run([need("zip"), "-q", "-r", "-", "Sonic Adventure 2"], cwd=src.parent,
                           check=True, capture_output=True)
        # Written to a pipe, Info-ZIP cannot seek back, so the sizes follow the data.
        a.write_bytes(z.stdout)
        return a, "Sonic Adventure 2/"
    if kind == "infozip-zip64":
        subprocess.run([need("zip"), "-q", "-r", "-fz", str(a), "Sonic Adventure 2"],
                       cwd=src.parent, check=True)
        return a, "Sonic Adventure 2/"
    if kind == "bsdtar":
        subprocess.run([need("bsdtar"), "-a", "-cf", str(a), "-C", str(src), "."], check=True)
        return a, "./"
    raise AssertionError(kind)


LAYOUTS = ["python-top", "python-folder", "python-deep", "python-non-ascii", "python-stored",
           "python-mixed-levels", "python-backslashes", "python-macos",
           "python-directory-entries", "zip64-by-hand", "infozip-top", "infozip-folder",
           "infozip-data-descriptors", "infozip-zip64", "bsdtar"]


@pytest.mark.parametrize("kind", LAYOUTS)
def test_each_layout_installs_the_verified_game(driver, table, tmp_path, kind):
    archive, root = build_layout(kind, tmp_path)
    store = tmp_path / "data" / "game"
    rep = install(driver, archive, store, table)
    assert_installed(rep, store)
    assert rep["root"] == root
    assert rep["counts"] == {"ok": 9, "missing": 0, "modified": 0, "substituted": 0,
                             "extras": len(UNHASHED), "known_missing": 0, "known_modified": 0}
    assert [f["kind"] for f in rep["files"]] == ["ok"] * len(GAME)
    assert rep["mod_loader"] is False and rep["markers"] == []
    assert rep["user_files"] == USER_FILES
    assert rep["skipped"] == SKIPPED
    assert rep["extracted_extras"] == EXTRACTED_EXTRAS
    assert rep["damaged"] == [] and rep["resumed"] == 0
    assert rep["extracted"] == len(GAME) + 1
    assert rep["data_descriptors"] is (kind in ("infozip-data-descriptors", "bsdtar"))
    assert rep["zip64"] is (kind in ("zip64-by-hand", "infozip-zip64"))
    if kind == "python-stored":
        assert rep["methods"] == ["0"]
    elif kind == "python-mixed-levels":
        assert rep["methods"] == ["0", "8"]
    elif kind.startswith("python") or kind == "zip64-by-hand":
        assert rep["methods"] == ["8"]
    else:  # Info-ZIP stores what deflate cannot shrink
        assert "8" in rep["methods"] and set(rep["methods"]) <= {"0", "8"}


def test_one_thread_and_eight_write_the_same_store_and_record(driver, table, tmp_path):
    archive = pyzip(tmp_path / "g.zip", {**GAME, **UNHASHED}, "Sonic Adventure 2/")
    a, b = tmp_path / "a" / "game", tmp_path / "b" / "game"
    assert_installed(install(driver, archive, a, table, "--threads", 1), a)
    assert_installed(install(driver, archive, b, table, "--threads", 8), b)
    assert store_files(a) == store_files(b)
    assert (a / RECORD).read_bytes() == (b / RECORD).read_bytes()


def test_a_differently_cased_directory_takes_the_table_s_spelling(driver, table, tmp_path):
    members = {**GAME, "RESOURCE/GD_PC/new.ini": b"n=1\n", "resource/gd_pc/dll/Extra.cfg": b"e"}
    store = tmp_path / "game"
    rep = install(driver, pyzip(tmp_path / "g.zip", members), store, table)
    assert rep["ok"], rep["error"]
    assert rep["extracted_extras"] == ["resource/gd_PC/DLL/Extra.cfg", "resource/gd_PC/new.ini"]
    assert sorted(p.name for p in (store / "resource").iterdir()) == ["Shader", "gd_PC"]


# ------------------------------------------------------------------------- refusals

def flip_inside(archive: Path, name: str) -> Path:
    """Flip one byte in the middle of `name`'s compressed data."""
    with zipfile.ZipFile(archive) as z:
        info = z.getinfo(name)
    b = bytearray(archive.read_bytes())
    lh = info.header_offset
    n, m = struct.unpack("<HH", b[lh + 26:lh + 30])
    b[lh + 30 + n + m + info.compress_size // 2] ^= 0x55
    archive.write_bytes(bytes(b))
    return archive


INI = "resource/gd_PC/StageSelectConfig.ini"


def damaged_extra(a: Path, members: dict[str, bytes], method: int) -> Path:
    """`members` with StageSelectConfig.ini stored, or deflated in stored blocks (level 0, so
    the stream still inflates), and one byte of its data flipped: only its CRC-32 tells."""
    with zipfile.ZipFile(a, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            if name == INI:
                z.writestr(name, data, compress_type=method, compresslevel=0)
            else:
                z.writestr(name, data)
    return flip_inside(a, INI)


def refusal_case(kind: str, tmp: Path) -> tuple[Path, list]:
    """An archive the route must refuse, and any extra driver arguments."""
    members = {**GAME, **UNHASHED}
    tmp.mkdir(parents=True, exist_ok=True)
    a = tmp / f"{kind}.zip"
    if kind == "not-a-zip":
        a.write_bytes(random.Random(1).randbytes(5000))
    elif kind == "7z":
        src = tree(tmp / "t", GAME)
        subprocess.run([need("7z"), "a", "-bd", "-y", str(tmp / "g.7z"), "."], cwd=src,
                       check=True, capture_output=True)
        a = tmp / "g.7z"
    elif kind == "tar":
        a = tmp / "g.tar"
        with tarfile.open(a, "w") as t:
            t.add(tree(tmp / "t", GAME), arcname="Sonic Adventure 2")
    elif kind == "gzip":
        a = tmp / "g.zip.gz"
        import gzip
        a.write_bytes(gzip.compress(pyzip(tmp / "g.zip", GAME).read_bytes()))
    elif kind == "not-sa2":
        pyzip(a, {k: v for k, v in members.items() if k != DLL})
    elif kind == "the-game-twice":
        pyzip(a, {**{"copy one/" + k: v for k, v in GAME.items()},
                  **{"copy two/" + k: v for k, v in GAME.items()}})
    elif kind == "a-duplicate-name":
        with zipfile.ZipFile(a, "w", zipfile.ZIP_DEFLATED) as z:
            for name, data in members.items():
                z.writestr(name, data)
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                z.writestr("resource/gd_PC/one.bin", b"y")
    elif kind == "a-case-only-duplicate":
        pyzip(a, {**members, "resource/gd_PC/ONE.bin": b"x"})
    elif kind == "encrypted":
        src = tree(tmp / "t", members)
        subprocess.run([need("zip"), "-q", "-r", "-P", "secret", str(a), "."], cwd=src, check=True)
    elif kind == "split":
        big = {**members, "resource/gd_PC/big.bin": random.Random(2).randbytes(300_000)}
        src = tree(tmp / "t", big)
        subprocess.run([need("zip"), "-q", "-r", "-s", "64k", str(a), "."], cwd=src, check=True)
        assert (tmp / f"{kind}.z01").is_file()
    elif kind == "bzip2":
        pyzip(a, members, method=zipfile.ZIP_BZIP2, level=9)
    elif kind == "lzma":
        pyzip(a, members, method=zipfile.ZIP_LZMA, level=None)
    elif kind == "truncated":
        b = pyzip(a, members).read_bytes()
        a.write_bytes(b[:len(b) // 2])
    elif kind == "damaged-data":
        flip_inside(pyzip(a, members), "resource/gd_PC/stg13_dat.bin")
    elif kind == "damaged-stored-extra":
        damaged_extra(a, members, zipfile.ZIP_STORED)
    elif kind == "damaged-deflated-extra":
        damaged_extra(a, members, zipfile.ZIP_DEFLATED)
    elif kind == "exe-data-changed":
        pyzip(a, {**members, EXE: wrapped(GAME[EXE], data=True)})
    elif kind == "case-only-duplicate-of-the-exe":
        pyzip(a, {**members, "SONIC2APP.EXE": GAME[EXE]})
    elif kind == "a-file-the-game-would-read-unverified":
        pyzip(a, {**members, "resource/gd_PC/prs/forMovie.pak": b"a mod's movie pack"})
    elif kind == "an-original-of-no-table-file":
        pyzip(a, {**members, "resource/gd_PC/stray_orig.bin": b"o"})
    elif kind == "a-partial-copy-under-resource":
        pyzip(a, {**members, "resource/gd_PC/stg13_dat.bin.part": b"half"})
    elif kind == "case-only-duplicate-of-an-extra":
        pyzip(a, {**members, "resource/gd_PC/stageselectconfig.INI": b"other"})
    elif kind == "unsafe-dotdot":
        pyzip(a, {**members, "resource/../../evil.ini": b"evil"})
    elif kind == "unsafe-device":
        pyzip(a, {**members, "resource/gd_PC/CON.ini": b"evil"})
    elif kind == "unsafe-colon":
        pyzip(a, {**members, "resource/gd_PC/a:b.ini": b"evil"})
    elif kind == "unsafe-trailing-dot":
        pyzip(a, {**members, "resource/gd_PC/x./y.ini": b"evil"})
    elif kind == "file-and-directory":
        pyzip(a, {**members, "resource/gd_PC/one.bin/inner.ini": b"evil"})
    elif kind == "missing-required":
        pyzip(a, {k: v for k, v in members.items() if k != "resource/gd_PC/stg13_dat.bin"})
    elif kind == "changed-size":
        pyzip(a, {**members, "resource/gd_PC/PRS/sub/obj.prs": b"modded"})
    elif kind == "changed-same-size":
        d = bytearray(GAME["resource/gd_PC/PRS/sub/obj.prs"])
        d[100] ^= 1
        pyzip(a, {**members, "resource/gd_PC/PRS/sub/obj.prs": bytes(d)})
    elif kind == "orig-mismatched":
        pyzip(a, {**members, DLL: b"MZ proxy", ORIG: b"MZ" + bytes(60_000)})
    elif kind == "a-huge-claimed-size":
        zip64_by_hand(a, {**members, "resource/gd_PC/big.ini": b"small"},
                      claims={"resource/gd_PC/big.ini": 1 << 62})
    elif kind == "more-data-than-claimed":
        zip64_by_hand(a, members, claims={"resource/gd_PC/StageSelectConfig.ini": 4})
    elif kind == "no-free-space":
        pyzip(a, members)
        return a, ["--free", 1000]
    else:
        raise AssertionError(kind)
    return a, []


REFUSALS = {
    "not-a-zip": (FAIL_NOT_FOUND, "the file is not a ZIP archive"),
    "7z": (FAIL_NOT_FOUND, "the file is a 7z archive, not a ZIP archive"),
    "tar": (FAIL_NOT_FOUND, "the file is a tar archive, not a ZIP archive"),
    "gzip": (FAIL_NOT_FOUND, "the file is a gzip file, not a ZIP archive"),
    "not-sa2": (FAIL_NOT_FOUND, "this archive does not hold Sonic Adventure 2"),
    "the-game-twice": (FAIL_AMBIGUOUS, 'more than once, under "copy one/" and "copy two/"'),
    "a-duplicate-name": (FAIL_REFUSED, '"resource/gd_PC/one.bin" and "resource/gd_PC/one.bin"'),
    "a-case-only-duplicate": (FAIL_REFUSED, "differ only in case or not at all"),
    "encrypted": (FAIL_REFUSED, '"sonic2app.exe" is encrypted'),
    "split": (FAIL_REFUSED, "multi-disk"),
    "bzip2": (FAIL_REFUSED, "is compressed with bzip2 (method 12)"),
    "lzma": (FAIL_REFUSED, "is compressed with LZMA (method 14)"),
    "truncated": (FAIL_IO, "the archive's end is missing"),
    "damaged-data": (FAIL_IO, "the archive is damaged at resource/gd_PC/stg13_dat.bin"),
    "damaged-stored-extra": (FAIL_IO, f"the archive is damaged at {INI}: its CRC-32 does not match"),
    "damaged-deflated-extra": (FAIL_IO, f"the archive is damaged at {INI}: its CRC-32 does not match"),
    # Checked by sections: its data section is one the runtime reads from the file.
    "exe-data-changed": (FAIL_REFUSED, "sonic2app.exe: changed (the executable's check: section "
                                       ".data at 0x"),
    "case-only-duplicate-of-the-exe": (FAIL_REFUSED, "differ only in case or not at all"),
    "case-only-duplicate-of-an-extra": (FAIL_REFUSED, "differ only in case or not at all"),
    # The folder route's rule for an extra: under resource/ the game would read it unverified.
    "a-file-the-game-would-read-unverified": (FAIL_REFUSED, '"resource/gd_PC/prs/forMovie.pak" is '
                                                            "not a game file, and the game would "
                                                            "read it unverified under resource/"),
    "an-original-of-no-table-file": (FAIL_REFUSED, '"resource/gd_PC/stray_orig.bin" is not a game file'),
    "a-partial-copy-under-resource": (FAIL_REFUSED, '"resource/gd_PC/stg13_dat.bin.part" is not a game file'),
    "unsafe-dotdot": (FAIL_REFUSED, "\"resource/../../evil.ini\" is not a safe name (a '..' path component)"),
    "unsafe-device": (FAIL_REFUSED, "the device name 'CON.ini'"),
    "unsafe-colon": (FAIL_REFUSED, "the character ':'"),
    "unsafe-trailing-dot": (FAIL_REFUSED, "a path component ending in a dot or a space"),
    "file-and-directory": (FAIL_REFUSED, "uses one name as both a file and a directory"),
    "missing-required": (FAIL_REFUSED, "resource/gd_PC/stg13_dat.bin: missing"),
    "changed-size": (FAIL_REFUSED, "resource/gd_PC/PRS/sub/obj.prs: changed (it holds 6 bytes"),
    "changed-same-size": (FAIL_REFUSED, "resource/gd_PC/PRS/sub/obj.prs: changed (its SHA-256 is"),
    "orig-mismatched": (FAIL_REFUSED, f"{DLL}: changed (it holds 8 bytes in the archive"),
    "no-free-space": (FAIL_IO, "not enough free space"),
    # What an entry may write is the size it claims, and the claims must fit the volume.
    "a-huge-claimed-size": (FAIL_IO, "not enough free space"),
    "more-data-than-claimed": (FAIL_IO, "StageSelectConfig.ini: it inflates to more than the "
                                        "central directory's size"),
}
# Refused on what the central directory shows: nothing is written, not even the partial store.
BEFORE_EXTRACTION = set(REFUSALS) - {"damaged-data", "changed-same-size", "orig-mismatched",
                                      "more-data-than-claimed", "damaged-stored-extra",
                                      "damaged-deflated-extra", "exe-data-changed"}


@pytest.mark.parametrize("kind", sorted(REFUSALS))
def test_each_refusal_names_its_reason_and_writes_nothing_it_should_not(driver, table, tmp_path,
                                                                        kind):
    archive, extra = refusal_case(kind, tmp_path / "in")
    data = tmp_path / "data"
    data.mkdir()
    store = data / "game"
    rep = install(driver, archive, store, table, *extra)
    failure, says = REFUSALS[kind]
    assert rep["ok"] is False
    assert (rep["failure"], says in rep["error"]) == (failure, True), rep["error"]
    assert not store.exists()
    assert not list(tmp_path.rglob("evil.*")) and not list(tmp_path.rglob("inner.ini"))
    partial = data / "game.partial"
    if kind in BEFORE_EXTRACTION:
        assert list(data.iterdir()) == []
        # Nothing was read: no file is reported as checked.
        assert rep["files_verified"] is False
        assert all(f["detail"].startswith("not read") for f in rep["files"] if f["kind"] == "ok")
    else:
        assert partial.is_dir() and "a retry resumes it" in rep["error"]
        assert (partial / "sa2recomp-partial").is_file()
        assert not list(partial.rglob("*.part")) and not list((partial / ".sa2-tmp").iterdir())
    if kind in ("missing-required", "changed-size", "changed-same-size", "orig-mismatched",
                "exe-data-changed"):
        named = {f["path"]: f["kind"] for f in rep["files"] if f["kind"] != "ok"}
        assert named == {"missing-required": {"resource/gd_PC/stg13_dat.bin": "missing"},
                         "changed-size": {"resource/gd_PC/PRS/sub/obj.prs": "modified"},
                         "changed-same-size": {"resource/gd_PC/PRS/sub/obj.prs": "modified"},
                         "orig-mismatched": {DLL: "modified"},
                         "exe-data-changed": {EXE: "modified"}}[kind]


def test_a_directory_this_route_did_not_write_is_never_written_over(driver, table, tmp_path):
    store = tmp_path / "game"
    store.mkdir()
    (store / "notes.txt").write_text("mine")
    rep = install(driver, pyzip(tmp_path / "g.zip", GAME), store, table)
    assert (rep["ok"], rep["failure"]) == (False, FAIL_REFUSED)
    assert "is never written over" in rep["error"]
    assert [p.name for p in store.iterdir()] == ["notes.txt"]
    assert not (tmp_path / "game.partial").exists()


@pytest.mark.parametrize("where", [".old", ".partial"])
def test_a_directory_beside_the_store_this_route_did_not_write_is_left_alone(driver, table,
                                                                           tmp_path, where):
    store = tmp_path / "data" / "game"
    theirs = tmp_path / "data" / ("game" + where)
    (theirs / "photos").mkdir(parents=True)
    (theirs / "photos/holiday.jpg").write_bytes(b"precious")
    (theirs / "notes.txt").write_text("mine")
    rep = install(driver, pyzip(tmp_path / "g.zip", {**GAME, **UNHASHED}), store, table)
    assert (rep["ok"], rep["failure"]) == (False, FAIL_REFUSED)
    assert f"{theirs} exists and is not a" in rep["error"] and "never written over" in rep["error"]
    assert (theirs / "photos/holiday.jpg").read_bytes() == b"precious"
    assert sorted(p.name for p in theirs.iterdir()) == ["notes.txt", "photos"]
    assert not store.exists()


def test_an_empty_directory_in_the_store_s_place_is_taken_over(driver, table, tmp_path):
    store = tmp_path / "game"
    store.mkdir()
    assert_installed(install(driver, pyzip(tmp_path / "g.zip", {**GAME, **UNHASHED}), store,
                             table), store)


# ------------------------------------------------------------------------- the executable

@pytest.mark.parametrize("copy", ["wrapped", "longer", "both"])
def test_the_executable_is_checked_by_its_sections_and_not_its_whole_hash(driver, table,
                                                                          tmp_path, copy):
    """The DRM ruling: the code section (which the release build overlays) and SteamStub's
    section may differ, and so may the file's length past its sections; the table's size is no
    reason to refuse it before it is read."""
    exe = {"wrapped": wrapped(GAME[EXE], code=True, bind=True),
           "longer": GAME[EXE] + b"overlay data past the last section" * 30,
           "both": wrapped(GAME[EXE], code=True, bind=True) + bytes(777)}[copy]
    assert exe != GAME[EXE]
    game = {**GAME, EXE: exe}
    store = tmp_path / "game"
    rep = install(driver, pyzip(tmp_path / "g.zip", {**game, **UNHASHED}), store, table)
    assert_installed(rep, store, game)
    assert next(f for f in rep["files"] if f["path"] == EXE)["kind"] == "ok"
    assert (store / EXE).read_bytes() == exe


# ------------------------------------------------------------------------- the user's policy

MOD_LOADER = {"SA2ModLoader.dll": b"MZ loader", "sa2mlver.txt": b"8\n", "Codes.lst": b"; codes\n",
              "mods/Better Cam/mod.ini": b"Name=cam\n", "mods/Better Cam/gd_PC/x.prs": b"p",
              "extlib/BASS/bass.dll": b"MZ", "Patches.json": b"{}", "CrashDump/x.dmp": b"d",
              "mods/Better Cam/SAVEDATA/cam.sav": b"a mod's own save"}
MARKERS = ["SA2ModLoader.dll", "sa2mlver.txt", "mods", ORIG]


@pytest.mark.parametrize("proxy", ["other-size", "same-size", "absent"])
def test_a_mod_loader_install_runs_on_the_matching_original(driver, table, tmp_path, proxy):
    members = {**GAME, **UNHASHED, **MOD_LOADER, ORIG: GAME[DLL]}
    if proxy == "other-size":
        members[DLL] = b"MZ the Mod Loader's proxy"
    elif proxy == "same-size":
        members[DLL] = b"MZ" + bytes(len(GAME[DLL]) - 2)
    else:
        del members[DLL]
    if proxy == "absent":
        # Without Data_DLL.dll the folder is not the game's: the root needs both files.
        rep = install(driver, pyzip(tmp_path / "g.zip", members), tmp_path / "game", table)
        assert (rep["ok"], rep["failure"]) == (False, FAIL_NOT_FOUND)
        return
    store = tmp_path / "game"
    rep = install(driver, pyzip(tmp_path / "g.zip", members, "Sonic Adventure 2/"), store, table)
    assert_installed(rep, store)
    assert rep["mod_loader"] is True and rep["markers"] == MARKERS
    dll = next(f for f in rep["files"] if f["path"] == DLL)
    assert (dll["kind"], dll["detail"]) == ("substituted-orig", ORIG)
    assert rep["counts"]["substituted"] == 1 and rep["counts"]["ok"] == 8
    assert (store / DLL).read_bytes() == GAME[DLL] and not (store / ORIG).exists()
    rec = json.loads((store / RECORD).read_text())
    entry = next(f for f in rec["files"] if f["path"] == DLL)
    assert (entry["kind"], entry["from"]) == ("substituted-orig", ORIG)
    assert rec["markers"] == MARKERS
    for name in ("SA2ModLoader.dll", "sa2mlver.txt", "Codes.lst", "mods/Better Cam/mod.ini",
                 "extlib/BASS/bass.dll", "Patches.json", "CrashDump/x.dmp",
                 "mods/Better Cam/SAVEDATA/cam.sav"):
        assert name in rep["skipped"] and not (store / name).exists()
    assert rep["counts"]["extras"] == len(UNHASHED) + len(MOD_LOADER)


def test_files_the_runtime_never_reads_are_reported_and_not_refused(driver, table, tmp_path):
    members = {**GAME, **UNHASHED, "steam_api.dll": b"MZ a replaced steam_api"}
    del members["Launcher.exe"]
    store = tmp_path / "game"
    rep = install(driver, pyzip(tmp_path / "g.zip", members), store, table)
    want = {k: v for k, v in GAME.items() if k not in ("Launcher.exe", "steam_api.dll")}
    assert_installed(rep, store, want)
    assert (rep["counts"]["known_missing"], rep["counts"]["known_modified"]) == (1, 1)
    rec = json.loads((store / RECORD).read_text())
    assert (rec["missing"], rec["modified"]) == (["Launcher.exe"], ["steam_api.dll"])


# ------------------------------------------------------------------------- converging

def test_a_refused_run_s_partial_store_is_resumed_and_converges(driver, table, tmp_path):
    bad = dict(GAME)
    d = bytearray(bad["resource/gd_PC/PRS/sub/obj.prs"])
    d[7] ^= 0x80
    bad["resource/gd_PC/PRS/sub/obj.prs"] = bytes(d)
    store = tmp_path / "data" / "game"
    rep = install(driver, pyzip(tmp_path / "bad.zip", {**bad, **UNHASHED}), store, table)
    assert (rep["ok"], rep["failure"]) == (False, FAIL_REFUSED)
    partial = tmp_path / "data" / "game.partial"
    kept = sorted(p.relative_to(partial).as_posix() for p in partial.rglob("*") if p.is_file())
    assert "sa2recomp-partial" in kept   # the partial store is marked as this installer's
    kept.remove("sa2recomp-partial")
    assert "resource/gd_PC/PRS/sub/obj.prs" not in kept and len(kept) == len(GAME)  # 8 + the ini

    # Junk a stopped run could leave, and a planned file with the wrong bytes.
    (partial / "resource/gd_PC/half.bin.part").write_bytes(b"half")
    (partial / "junk").mkdir()
    (partial / "junk/old.bin").write_bytes(b"old")
    (partial / RECORD).write_text("{}")
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me")
    os.symlink(outside, partial / "resource/gd_PC/link.bin")
    (partial / "resource/gd_PC/one.bin").write_bytes(b"z")
    (partial / ".sa2-tmp/3").write_bytes(b"a stopped run's temporary file")
    # An extra is kept only when its size and CRC-32 still match the archive's.
    tampered = UNHASHED[INI].replace(b"13", b"99")
    assert len(tampered) == len(UNHASHED[INI]) and tampered != UNHASHED[INI]
    (partial / INI).write_bytes(tampered)

    rep = install(driver, pyzip(tmp_path / "good.zip", {**GAME, **UNHASHED}), store, table)
    assert_installed(rep, store)
    # Read again: one.bin, obj.prs and the ini; resumed: the other seven table files.
    assert rep["resumed"] == len(GAME) - 2 and rep["extracted"] == 3
    assert (store / INI).read_bytes() == UNHASHED[INI]
    assert outside.read_text() == "keep me"

    fresh = tmp_path / "fresh" / "game"
    assert_installed(install(driver, tmp_path / "good.zip", fresh, table), fresh)
    assert (store / RECORD).read_bytes() == (fresh / RECORD).read_bytes()


def test_an_earlier_store_is_replaced_only_by_a_complete_one(driver, table, tmp_path):
    store = tmp_path / "game"
    first = pyzip(tmp_path / "one.zip", {**GAME, **UNHASHED}, "Sonic Adventure 2/")
    assert_installed(install(driver, first, store, table), store)
    before = (store / RECORD).read_bytes()

    # A bad archive leaves the installed store as it was.
    rep = install(driver, pyzip(tmp_path / "bad.zip", {**GAME, "sonic2app.exe": b"MZ"}), store,
                  table)
    assert (rep["ok"], rep["failure"]) == (False, FAIL_REFUSED)
    assert (store / RECORD).read_bytes() == before and store_files(store)[DLL] == GAME[DLL]

    # A good one replaces it whole: the record names the new archive's layout.
    second = pyzip(tmp_path / "two.zip", {**GAME, **UNHASHED}, "deeper/Sonic Adventure 2/")
    assert_installed(install(driver, second, store, table), store)
    assert json.loads((store / RECORD).read_text())["source"]["root"] == "deeper/Sonic Adventure 2/"

    # A run that stopped between the replacement's two renames left only "<store>.old".
    os.rename(store, str(store) + ".old")
    assert_installed(install(driver, first, store, table), store)
    assert (store / RECORD).read_bytes() == before


@pytest.mark.parametrize("listed", ["as-extras", "in-the-table"])
def test_no_file_s_name_is_a_temporary_file_s_name(driver, tmp_path, listed):
    """Files named "<table file>.part", as an interrupted copy leaves them, beside a table file
    large enough to still be written when they are. As extras under resource/ they are not game
    files, so the archive is refused by name before anything is written; listed in the table
    they are verified and stored like any other, and the store holds every table file's own
    bytes, at 1 thread and at 8, with every record entry in the store with its hash: the
    temporary names are the store's own."""
    rnd = random.Random(4)
    big = "resource/gd_PC/big.bin"
    game = {**GAME, big: rnd.randbytes(24 << 20)}
    parts = {big + ".part": b"NOT THE GAME'S BYTES\n",
             DLL + ".part": b"MZ a half-copied DLL",
             "resource/gd_PC/one.bin.part": b"P" * 50,   # larger than one.bin: written first
             EXE + ".part": b"MZ"}
    if listed == "in-the-table":
        game.update(parts)
    table = write_table(tmp_path / "table.tsv", game)
    archive = pyzip(tmp_path / "g.zip", {**game, **UNHASHED, **parts}, level=1)
    for threads in (1, 8):
        store = tmp_path / f"t{threads}" / "game"
        rep = install(driver, archive, store, table, "--threads", threads)
        if listed == "as-extras":
            assert (rep["ok"], rep["failure"]) == (False, FAIL_REFUSED)
            for name in parts:
                if name.startswith("resource/"):
                    assert f'"{name}" is not a game file' in rep["error"], name
            assert not store.parent.exists() or list(store.parent.iterdir()) == []
            continue
        assert rep["ok"] is True, rep["error"]
        got = store_files(store)
        for name, data in game.items():
            assert got[name] == data, (threads, name)
        rec = json.loads((store / RECORD).read_text())
        assert sorted(f["path"] for f in rec["files"]) == sorted([*game, INI])
        for f in rec["files"]:
            assert hashlib.sha256((store / f["path"]).read_bytes()).hexdigest() == f["sha256"]
        assert rep["extracted_extras"] == [INI]
        assert not (store / ".sa2-tmp").exists() and not (store / "sa2recomp-partial").exists()


def test_user_config_under_resource_is_stored_and_unused_originals_skipped(driver, table, tmp_path):
    """Of the unhashed files under resource/, user config (*.ini, *.cfg, which the receipt's
    rules leave unhashed) is stored, as the folder route lets the runtime read it, and an unused
    *_orig original of a table file is skipped; anything else refuses (see REFUSALS)."""
    config = {"resource/gd_PC/Extra.cfg": b"c", "resource/gd_PC/sub/Other.INI": b"i"}
    unused = {"resource/gd_PC/one_orig.bin": b"x"}   # one.bin itself matches
    store = tmp_path / "game"
    rep = install(driver, pyzip(tmp_path / "g.zip", {**GAME, **UNHASHED, **config, **unused}),
                  store, table)
    assert_installed(rep, store, extras=tuple(sorted([INI, *config])), extra_data=config)
    assert "resource/gd_PC/one_orig.bin" in rep["skipped"]
    assert rep["counts"]["extras"] == len(UNHASHED) + len(config) + len(unused)


def test_two_names_differing_in_case_are_refused_only_where_the_route_reads_them(driver, table,
                                                                                tmp_path):
    members = {**GAME, **UNHASHED, "mods/Cam/readme.txt": b"a", "mods/cam/README.txt": b"b",
               "other folder/x.txt": b"1", "OTHER FOLDER/X.TXT": b"2"}
    store = tmp_path / "game"
    rep = install(driver, pyzip(tmp_path / "g.zip", members, "Sonic Adventure 2/"), store, table)
    assert_installed(rep, store)
    assert {"mods/Cam/readme.txt", "mods/cam/README.txt", "other folder/x.txt",
            "OTHER FOLDER/X.TXT"} <= set(rep["skipped"])


def test_a_table_name_the_store_uses_itself_is_a_bad_table(driver, tmp_path):
    game = {**GAME, "sa2recomp-partial": b"x"}
    table = write_table(tmp_path / "table.tsv", game)
    rep = install(driver, pyzip(tmp_path / "g.zip", game), tmp_path / "game", table)
    assert (rep["ok"], rep["failure"]) == (False, FAIL_IO)
    assert "takes a name the store itself uses" in rep["error"]


# ------------------------------------------------------------------------- user files

def test_user_files_go_to_the_user_directory_only_when_asked_and_never_over_a_file(driver, table,
                                                                                  tmp_path):
    archive = pyzip(tmp_path / "g.zip", {**GAME, **UNHASHED}, "Sonic Adventure 2/")
    overlay = tmp_path / "data" / "overlay"
    # Not asked: listed, not written.
    store = tmp_path / "data" / "game"
    rep = install(driver, archive, store, table)
    assert_installed(rep, store)
    assert rep["user_files"] == USER_FILES and rep["user_files_written"] == []
    assert not overlay.exists()

    # A refused run writes none.
    bad = pyzip(tmp_path / "bad.zip", {**UNHASHED, **{k: v for k, v in GAME.items() if k != DLL},
                                      DLL: b"MZ proxy"})
    rep = install(driver, bad, tmp_path / "data" / "other", table, "--user-dir", overlay)
    assert (rep["ok"], rep["failure"]) == (False, FAIL_REFUSED) and not overlay.exists()

    # Asked: written at their game-folder paths (the table's spelling of resource/gd_PC), and
    # a file the user already has is kept.
    (overlay / "Config").mkdir(parents=True)
    (overlay / "Config/UserConfig.cfg").write_bytes(b"the user's own settings")
    rep = install(driver, archive, store, table, "--user-dir", overlay)
    assert_installed(rep, store)
    assert rep["user_files_written"] == ["resource/gd_PC/SAVEDATA/SONIC2B__S01"]
    assert rep["user_files_kept"] == ["Config/UserConfig.cfg"]
    assert (overlay / "Config/UserConfig.cfg").read_bytes() == b"the user's own settings"
    assert (overlay / "resource/gd_PC/SAVEDATA/SONIC2B__S01").read_bytes() == \
        UNHASHED["resource/gd_PC/SAVEDATA/SONIC2B__S01"]
    assert sorted(p.relative_to(overlay).as_posix() for p in overlay.rglob("*") if p.is_file()) == \
        ["Config/UserConfig.cfg", "resource/gd_PC/SAVEDATA/SONIC2B__S01"]
    # Again: everything is kept, nothing is written.
    rep = install(driver, archive, store, table, "--user-dir", overlay)
    assert rep["ok"] and rep["user_files_written"] == [] and rep["user_files_kept"] == USER_FILES


def test_a_damaged_user_file_stops_the_run_and_the_retry_converges(driver, table, tmp_path):
    save = "resource/gd_PC/SAVEDATA/SONIC2B__S01"
    a = tmp_path / "g.zip"
    with zipfile.ZipFile(a, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in {**GAME, **UNHASHED}.items():
            z.writestr(name, data, compress_type=zipfile.ZIP_STORED if name == save else
                       zipfile.ZIP_DEFLATED)
    flip_inside(a, save)
    overlay, store = tmp_path / "overlay", tmp_path / "data" / "game"
    rep = install(driver, a, store, table, "--user-dir", overlay)
    assert (rep["ok"], rep["failure"]) == (False, FAIL_IO)
    assert f"the archive is damaged at {save}: its CRC-32 does not match" in rep["error"]
    assert not store.exists() and not (overlay / save).exists()
    assert not (overlay / ".sa2recomp-tmp").exists() or not list((overlay / ".sa2recomp-tmp").iterdir())
    good = pyzip(tmp_path / "good.zip", {**GAME, **UNHASHED})
    rep = install(driver, good, store, table, "--user-dir", overlay)
    assert_installed(rep, store)
    assert rep["resumed"] == len(GAME) + 1   # the partial store was complete
    assert sorted(rep["user_files_written"] + rep["user_files_kept"]) == USER_FILES
    assert (overlay / save).read_bytes() == UNHASHED[save]
    assert not (overlay / ".sa2recomp-tmp").exists()


# ------------------------------------------------------------------------- names

def zip_by_hand(path: Path, members: list[tuple[bytes, bytes, int, bytes]]) -> Path:
    """Stored members (name bytes, data, general-purpose flags, central extra field) exactly as
    given: what a writer that does not set bit 11 lays out."""
    out, cd = bytearray(), bytearray()
    for nb, data, flags, extra in members:
        crc, off = zlib.crc32(data), len(out)
        out += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags, 0, 0, 0x21, crc, len(data),
                           len(data), len(nb), 0) + nb + data
        cd += struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, flags, 0, 0, 0x21, crc,
                          len(data), len(data), len(nb), len(extra), 0, 0, 0, 0, off) + nb + extra
    cd_off = len(out)
    out += cd + struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(members), len(members), len(cd),
                            cd_off, 0)
    path.write_bytes(bytes(out))
    return path


def unicode_path_field(name_bytes: bytes, utf8: str, crc: int | None = None) -> bytes:
    """Info-ZIP's Unicode Path extra field (0x7075): version 1, the CRC-32 of the header's name
    bytes, the UTF-8 name."""
    body = struct.pack("<BI", 1, zlib.crc32(name_bytes) if crc is None else crc) + utf8.encode()
    return struct.pack("<HH", 0x7075, len(body)) + body


def test_names_are_read_as_utf8_the_unicode_path_field_or_code_page_437(driver, table, tmp_path):
    root = "Sonic Adventure 2 \u00c4/".encode("cp437")         # b"... \x8e/", bit 11 clear
    members = [(root + k.encode(), v, 0, b"") for k, v in {**GAME, **UNHASHED}.items()]
    extras = {"resource/gd_PC/caf\u00e9.ini": (b"resource/gd_PC/caf\x82.ini", b"c", 0, b""),
              # The field was written for these bytes: its name wins over code page 437.
              "resource/gd_PC/\u65e5\u672c.ini": (b"resource/gd_PC/??.ini", b"j", 0,
                                                unicode_path_field(root + b"resource/gd_PC/??.ini",
                                                                   "Sonic Adventure 2 \u00c4/resource/gd_PC/\u65e5\u672c.ini")),
              # A field whose CRC-32 does not match the name is stale, and ignored.
              "resource/gd_PC/stale\u00e7.ini": (b"resource/gd_PC/stale\x87.ini", b"s", 0,
                                                unicode_path_field(b"other", "Sonic Adventure 2 \u00c4/x.txt")),
              # UTF-8 with bit 11, beside the others.
              "resource/gd_PC/\u00e9t\u00e9.ini": ("resource/gd_PC/\u00e9t\u00e9.ini".encode(), b"u", 0x800, b"")}
    for _, (nb, data, flags, extra) in extras.items():
        members.append((root + nb if not flags else "Sonic Adventure 2 \u00c4/".encode() + nb,
                        data, flags, extra))
    archive = zip_by_hand(tmp_path / "g.zip", members)
    store = tmp_path / "game"
    rep = install(driver, archive, store, table)
    assert rep["ok"] is True, rep["error"]
    assert rep["root"] == "Sonic Adventure 2 \u00c4/"
    want_extras = sorted([INI, *extras])
    assert rep["extracted_extras"] == want_extras
    for name, (_, data, _, _) in extras.items():
        assert (store / name).read_bytes() == data
    rec = json.loads((store / RECORD).read_text())
    assert [f["path"] for f in rec["files"] if f["kind"] == "extra"] == want_extras
    assert rec["source"]["root"] == "Sonic Adventure 2 \u00c4/"
    assert rep["names_unicode_field"] == 1
    # Every name without bit 11 or a matching field but one ASCII-free: the root's, under it.
    assert rep["names_cp437"] == len(members) - 2



SAFE = {
    "resource/gd_PC/StageSelectConfig.ini": True,
    "resource/gd_PC/a b/c.d.e": True,
    "resource/gd_PC/\u00e9t\u00e9.bin": True,
    "": False, "/etc/passwd": False, "C:/x": False, "c:x": False, "a//b": False, "a/./b": False,
    "a/../b": False, "..": False, "a/": False, "a\\b": False, "a/b:c": False, "a/b*": False,
    "a/b?": False, 'a/"b"': False, "a/<b>": False, "a/b|c": False, "a/b.": False, "a/b ": False,
    "a/NUL": False, "a/con.txt": False, "a/COM1.bin": False, "a/lpt9": False, "a/CLOCK$": False,
    "a/\x01b": False, "a/" + "x" * 256: False, "a/COM10": True, "a/CONSOLE": True,
}


def test_the_lexical_guard_takes_the_vfs_rules(driver):
    names = list(SAFE)
    out = run(driver, "safe", *names).splitlines()
    assert len(out) == len(names)
    got = {n: line.split("\t")[0] == "safe" for n, line in zip(names, out)}
    assert got == SAFE
