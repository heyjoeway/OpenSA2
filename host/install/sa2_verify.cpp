// sa2_verify.cpp: the checks of sa2_install.h that every route applies to one file.
#include "sa2_install.h"

#include <cstdio>
#include <cstring>

#include "../player/sha256.h"
#include "sa2_platform.h"

const char* const SA2_MOD_LOADER_MARKERS[] = {"SA2ModLoader.dll", "sa2mlver.txt", "mods",
                                              "resource/gd_PC/DLL/Win32/Data_DLL_orig.dll"};
const size_t SA2_MOD_LOADER_MARKER_COUNT = sizeof SA2_MOD_LOADER_MARKERS / sizeof *SA2_MOD_LOADER_MARKERS;

namespace {

bool unhex(const std::string& h, uint8_t out[32]) {
    if (h.size() != 64) return false;
    for (size_t i = 0; i < 32; i++) {
        unsigned v = 0;
        for (int k = 0; k < 2; k++) {
            char c = h[2 * i + k];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= unsigned(c - '0');
            else if (c >= 'a' && c <= 'f') v |= unsigned(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= unsigned(c - 'A' + 10);
            else return false;
        }
        out[i] = uint8_t(v);
    }
    return true;
}

uint16_t u16(const uint8_t* p) { return uint16_t(p[0] | p[1] << 8); }
uint32_t u32(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }

std::string hex32(uint32_t v) {
    char b[16];
    std::snprintf(b, sizeof b, "%#x", v);
    return b;
}

}  // namespace

std::string sa2_hex(const uint8_t* p, size_t n) {
    static const char* d = "0123456789abcdef";
    std::string s(2 * n, '0');
    for (size_t i = 0; i < n; i++) {
        s[2 * i] = d[p[i] >> 4];
        s[2 * i + 1] = d[p[i] & 15];
    }
    return s;
}

std::string sa2_install_fold(const std::string& s) {
    std::string o = s;
    for (char& c : o)
        if (c >= 'A' && c <= 'Z') c = char(c - 'A' + 'a');
    return o;
}

namespace {

// SHA-256 of a whole file; false with the host's reason (and whether the file is simply absent).
bool hash_file(const std::string& path, uint8_t out[32], std::string* err, bool* absent) {
    FILE* f = sa2p::open_read(path);
    if (!f) {
        if (err) *err = sa2p::last_error(absent);
        return false;
    }
    Sha256 h;
    static thread_local std::vector<uint8_t> buf(1u << 20);
    size_t n;
    while ((n = std::fread(buf.data(), 1, buf.size(), f)) > 0) h.update(buf.data(), n);
    bool ok = !std::ferror(f);
    if (!ok && err) *err = sa2p::last_error(absent);
    std::fclose(f);
    return ok && unhex(h.hex(), out);
}

}  // namespace

bool sa2_sha256_file(const std::string& path, uint8_t out[32]) { return hash_file(path, out, nullptr, nullptr); }

bool sa2_exe_check_bytes(const Sa2ExeCheck& c, const uint8_t* d, size_t n, std::string* why) {
    auto fail = [&](const std::string& w) {
        if (why) *why = w;
        return false;
    };
    if (n < 0x40 || d[0] != 'M' || d[1] != 'Z') return fail("no MZ header");
    uint32_t lfanew = u32(d + 0x3C);
    if (lfanew != c.e_lfanew) return fail("e_lfanew " + hex32(lfanew) + ", expected " + hex32(c.e_lfanew));
    if (size_t(lfanew) + 24 + 128 > n || std::memcmp(d + lfanew, "PE\0\0", 4) != 0)
        return fail("no PE signature");
    uint16_t nsec = u16(d + lfanew + 6), opt_size = u16(d + lfanew + 20);
    if (nsec != c.sections_count) return fail("section count " + std::to_string(nsec) + ", expected " + std::to_string(c.sections_count));
    if (opt_size != c.optional_header_size) return fail("optional header size " + hex32(opt_size));
    const uint8_t* opt = d + lfanew + 24;
    if (u16(opt) != c.magic) return fail("optional header magic " + hex32(u16(opt)));
    if (u32(opt + 28) != c.image_base) return fail("image base " + hex32(u32(opt + 28)));
    if (u32(opt + 56) != c.size_of_image) return fail("image size " + hex32(u32(opt + 56)));
    if (u32(opt + 60) != c.size_of_headers) return fail("header size " + hex32(u32(opt + 60)));
    if (u32(opt + 104) != c.import_rva || u32(opt + 108) != c.import_size)
        return fail("import directory " + hex32(u32(opt + 104)) + "+" + hex32(u32(opt + 108)));
    if (u32(opt + 112) != c.resource_rva || u32(opt + 116) != c.resource_size)
        return fail("resource directory " + hex32(u32(opt + 112)) + "+" + hex32(u32(opt + 116)));
    size_t sec_off = size_t(lfanew) + 24 + opt_size;
    if (sec_off + 40 * size_t(nsec) > n || c.count != nsec) return fail("section table truncated");
    for (size_t i = 0; i < c.count; i++) {
        const Sa2ExeSection& want = c.sections[i];
        const uint8_t* s = d + sec_off + 40 * i;
        uint32_t vsize = u32(s + 8), va = u32(s + 12), raw = u32(s + 16), rawptr = u32(s + 20),
                 chars = u32(s + 36);
        if (va != want.va || vsize != want.virtual_size || raw != want.raw_size || chars != want.characteristics)
            return fail(std::string("section ") + want.name + " at " + hex32(want.va) + ": its table entry differs");
        if (!want.hashed) continue;
        uint64_t take = raw;
        if (rawptr >= n) take = 0;
        else if (uint64_t(rawptr) + take > n) take = n - rawptr;
        if (vsize && take > vsize) take = vsize;
        if (take != want.hashed_bytes)
            return fail(std::string("section ") + want.name + ": " + std::to_string(take) + " bytes loaded, expected " + std::to_string(want.hashed_bytes));
        Sha256 h;
        h.update(d + rawptr, size_t(take));
        uint8_t got[32];
        if (!unhex(h.hex(), got) || std::memcmp(got, want.sha256, 32) != 0)
            return fail(std::string("section ") + want.name + " at " + hex32(want.va) + ": its bytes differ");
    }
    return true;
}

namespace {

constexpr size_t kExeLimit = 256u << 20;

// The executable's check, reading the file; `io` is set when the host refused the read.
bool exe_check_file(const Sa2ExeCheck& c, const std::string& path, std::string* why, bool* io) {
    std::string data;
    if (!sa2p::read_file(path, data, kExeLimit)) {
        bool absent = false;
        std::string err = sa2p::last_error(&absent);
        sa2p::Stat st;
        if (sa2p::stat_path(path, st) && st.size > kExeLimit) {
            if (why) *why = std::to_string(st.size) + " bytes, larger than any SA2 executable";
            return false;
        }
        if (why) *why = err;
        if (io) *io = true;
        return false;
    }
    return sa2_exe_check_bytes(c, reinterpret_cast<const uint8_t*>(data.data()), data.size(), why);
}

}  // namespace

bool sa2_exe_check_file(const Sa2ExeCheck& c, const std::string& path, std::string* why) {
    return exe_check_file(c, path, why, nullptr);
}

Sa2FileResult::Kind sa2_install_check_file(const Sa2InstallEntry& e, const std::string& host_path,
                                           std::string* detail, bool* io_error) {
    auto unreadable = [&](const std::string& why, bool absent) {
        if (detail) *detail = absent ? "not found (" + why + ")" : "cannot be read: " + why;
        if (io_error && !absent) *io_error = true;
        return Sa2FileResult::Missing;
    };
    sa2p::Stat st;
    if (!sa2p::stat_path(host_path, st)) {
        bool absent = false;
        std::string err = sa2p::last_error(&absent);
        return unreadable(err, absent);
    }
    if (!st.is_file) {
        if (detail) *detail = "not a file";
        return Sa2FileResult::Missing;
    }
    if (e.exe) {
        std::string why;
        bool io = false;
        if (exe_check_file(*e.exe, host_path, &why, &io)) return Sa2FileResult::Ok;
        if (io) return unreadable(why, false);
        if (detail) *detail = "executable check: " + why;
        return Sa2FileResult::Modified;
    }
    if (st.size != e.size) {
        if (detail) *detail = std::to_string(st.size) + " bytes, expected " + std::to_string(e.size);
        return Sa2FileResult::Modified;
    }
    uint8_t got[32];
    std::string err;
    bool absent = false;
    if (!hash_file(host_path, got, &err, &absent)) return unreadable(err, absent);
    if (std::memcmp(got, e.sha256, 32) != 0) {
        if (detail) *detail = "sha256 " + sa2_hex(got, 8) + "..., expected " + sa2_hex(e.sha256, 8) + "...";
        return Sa2FileResult::Modified;
    }
    return Sa2FileResult::Ok;
}

std::string sa2_install_orig_name(const std::string& path) {
    size_t slash = path.find_last_of('/');
    size_t dot = path.find_last_of('.');
    if (dot == std::string::npos || (slash != std::string::npos && dot < slash)) return path + "_orig";
    return path.substr(0, dot) + "_orig" + path.substr(dot);
}

// runtime/sa2_module.c's pe_parse, rva_off, cstr_rva and self_name, the same checks in the same
// order and the same 64-bit bounds, so the installer accepts a substitution only when the
// runtime would make it.
Sa2PeDll sa2_pe_dll_bytes(const uint8_t* d, size_t n) {
    Sa2PeDll r;
    auto rd16 = [&](uint64_t off, uint32_t& out) {
        if (off + 2u > n) return false;
        out = uint32_t(d[off]) | uint32_t(d[off + 1]) << 8;
        return true;
    };
    auto rd32 = [&](uint64_t off, uint32_t& out) {
        if (off + 4u > n) return false;
        out = u32(d + off);
        return true;
    };
    auto no = [&](const char* why) {
        r.why = why;
        return r;
    };
    uint32_t lfanew = 0, sig = 0, machine = 0, nsect = 0, optsz = 0, chars = 0, magic = 0;
    if (n < 0x40 || d[0] != 'M' || d[1] != 'Z' || !rd32(0x3c, lfanew) || !rd32(lfanew, sig) ||
        sig != 0x00004550u)
        return no("not a PE file");
    if (!rd16(uint64_t(lfanew) + 4u, machine) || machine != 0x14cu) return no("not an i386 image");
    if (!rd16(uint64_t(lfanew) + 6u, nsect) || !rd16(uint64_t(lfanew) + 20u, optsz) ||
        !rd16(uint64_t(lfanew) + 22u, chars) || !(chars & 0x2000u))
        return no("not a DLL");
    const uint32_t opt = lfanew + 24u;
    if (!rd16(opt, magic) || magic != 0x10bu) return no("not a PE32 optional header");
    const uint32_t sect = opt + optsz;
    uint32_t entry = 0, base = 0, align = 0, soi = 0, soh = 0, ndirs = 0;
    if (!rd32(uint64_t(opt) + 16u, entry) || !rd32(uint64_t(opt) + 28u, base) ||
        !rd32(uint64_t(opt) + 32u, align) || !rd32(uint64_t(opt) + 56u, soi) ||
        !rd32(uint64_t(opt) + 60u, soh) || !rd32(uint64_t(opt) + 92u, ndirs))
        return no("a truncated optional header");
    if (ndirs > 16u) ndirs = 16u;
    uint32_t dir_rva[16] = {}, dir_size[16] = {};
    for (uint32_t i = 0; i < ndirs; i++)
        if (!rd32(uint64_t(opt) + 96u + 8u * i, dir_rva[i]) || !rd32(uint64_t(opt) + 100u + 8u * i, dir_size[i]))
            return no("a truncated data directory");
    if (uint64_t(sect) + 40u * uint64_t(nsect) > n) return no("a truncated section table");
    if (!align || (align & (align - 1u))) return no("a section alignment that is not a power of two");
    r.loads = true;

    auto rva_off = [&](uint32_t rva, uint32_t len, size_t& off) {
        const uint64_t end = uint64_t(rva) + len;
        if (end <= soh && end <= n) {
            off = rva;
            return true;
        }
        for (uint32_t i = 0; i < nsect; i++) {
            const uint64_t s = uint64_t(sect) + 40u * uint64_t(i);
            uint32_t va = u32(d + s + 12), raw = u32(d + s + 16), raw_off = u32(d + s + 20);
            if (rva < va || end > uint64_t(va) + raw) continue;
            const uint64_t o = uint64_t(raw_off) + (rva - va);
            if (o + len > n) return false;
            off = size_t(o);
            return true;
        }
        return false;
    };
    size_t off = 0, name_off = 0;
    uint32_t name_rva = 0;
    if (ndirs == 0 || !dir_size[0] || !rva_off(dir_rva[0], 40u, off) || !rd32(uint64_t(off) + 12u, name_rva) ||
        !rva_off(name_rva, 1u, name_off))
        return r;
    std::string name;
    size_t i = 0;
    for (; i + 1 < 64 && name_off + i < n && d[name_off + i]; i++) name += char(d[name_off + i]);
    if (name_off + i < n && d[name_off + i] == 0) r.self_name = name;
    return r;
}

Sa2PeDll sa2_pe_dll(const std::string& host_path) {
    std::string data;
    if (!sa2p::read_file(host_path, data, kExeLimit)) {
        Sa2PeDll r;
        r.why = "cannot be read: " + sa2p::last_error(nullptr);
        return r;
    }
    return sa2_pe_dll_bytes(reinterpret_cast<const uint8_t*>(data.data()), data.size());
}

bool sa2_same_module_name(const std::string& a, const std::string& b) {
    auto key = [](const std::string& p) {
        size_t s = p.find_last_of("/\\");
        std::string k = sa2_install_fold(s == std::string::npos ? p : p.substr(s + 1));
        if (k.size() > 4 && k.compare(k.size() - 4, 4, ".dll") == 0) k.resize(k.size() - 4);
        return k;
    };
    return key(a) == key(b);
}

const char* sa2_install_user_file(const std::string& rel) {
    const std::string f = sa2_install_fold(rel);
    if (f.compare(0, 9, "resource/") != 0) return "outside resource/";
    size_t a = 0;
    for (size_t b = f.find('/'); b != std::string::npos; a = b + 1, b = f.find('/', a))
        if (f.compare(a, b - a, "savedata") == 0) return "a user save";
    const size_t n = f.size();
    if (n > 4 && (f.compare(n - 4, 4, ".ini") == 0 || f.compare(n - 4, 4, ".cfg") == 0)) return "user config";
    return nullptr;
}

const char* sa2_install_extra_allowed(const std::string& rel, const Sa2InstallTable& table) {
    if (const char* why = sa2_install_user_file(rel)) return why;
    const size_t slash = rel.find_last_of('/');
    const size_t o = rel.rfind("_orig");
    if (o != std::string::npos && (slash == std::string::npos || o > slash)) {
        const std::string named = rel.substr(0, o) + rel.substr(o + 5);
        if (sa2_install_fold(sa2_install_orig_name(named)) == sa2_install_fold(rel)) {
            const std::string fn = sa2_install_fold(named);
            for (size_t k = 0; k < table.count; k++)
                if (sa2_install_fold(table.entries[k].path) == fn) return "an unused original";
        }
    }
    return nullptr;
}

void sa2_install_recount(Sa2InstallReport& r) {
    r.ok = r.missing = r.modified = r.substituted = r.known_missing = r.known_modified = 0;
    for (const Sa2FileResult& f : r.files) {
        switch (f.kind) {
        case Sa2FileResult::Ok: r.ok++; break;
        case Sa2FileResult::Missing: r.missing++; r.known_missing += !f.required; break;
        case Sa2FileResult::Modified: r.modified++; r.known_modified += !f.required; break;
        case Sa2FileResult::SubstitutedOrig: r.substituted++; break;
        }
    }
}

std::vector<std::string> sa2_install_refusals(const Sa2InstallReport& r) {
    std::vector<std::string> out;
    if (!r.error.empty()) out.push_back(r.error);
    for (const Sa2FileResult& f : r.files) {
        if (!f.required) continue;  // the runtime never reads it
        if (f.kind == Sa2FileResult::Missing && f.io_error)
            out.push_back(f.path + ": " + f.detail);
        else if (f.kind == Sa2FileResult::Missing)
            out.push_back(f.path + ": missing" + (f.detail.empty() ? "" : " (" + f.detail + ")"));
        else if (f.kind == Sa2FileResult::Modified)
            out.push_back(f.path + ": changed (" + f.detail + ")");
    }
    for (const Sa2Extra& x : r.extra_files)
        if (x.refused) out.push_back(x.path + ": " + x.why);
    return out;
}
