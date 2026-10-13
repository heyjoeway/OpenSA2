// sa2_zip.cpp: see sa2_zip.h.
#include "sa2_zip.h"

#include <algorithm>
#include <atomic>
#include <cstring>
#include <map>
#include <memory>
#include <mutex>
#include <set>
#include <thread>
#include <unordered_map>

#include "../player/sha256.h"
#include "sa2_inflate.h"
#include "sa2_store.h"

namespace {

constexpr uint32_t kSigLocal = 0x04034b50u;
constexpr uint32_t kSigCentral = 0x02014b50u;
constexpr uint32_t kSigEnd = 0x06054b50u;
constexpr uint32_t kSigEnd64 = 0x06064b50u;
constexpr uint32_t kSigLocator64 = 0x07064b50u;
constexpr uint64_t kMaxCentral = uint64_t(256) << 20;
constexpr uint64_t kMargin = uint64_t(64) << 20;
constexpr size_t kListMax = 20;

const char* const kExeKey = "sonic2app.exe";
const char* const kNotRead = "not read: the run stopped before this file was read";
// The user_dir's temporary directory, made and removed by each run that writes user files.
const char* const kUserTmp = ".sa2recomp-tmp";
const char* const kDllKey = "resource/gd_pc/dll/win32/data_dll.dll";

uint16_t u16(const uint8_t* p) { return uint16_t(p[0] | (p[1] << 8)); }
uint32_t u32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
uint64_t u64(const uint8_t* p) { return uint64_t(u32(p)) | uint64_t(u32(p + 4)) << 32; }

std::string lower(const std::string& s) {
    std::string o(s);
    for (auto& c : o)
        if (c >= 'A' && c <= 'Z') c = char(c + 32);
    return o;
}

bool starts_with(const std::string& s, const std::string& p) {
    return s.size() >= p.size() && s.compare(0, p.size(), p) == 0;
}

std::string hex(const uint8_t* p, size_t n) {
    static const char d[] = "0123456789abcdef";
    std::string o(n * 2, '0');
    for (size_t i = 0; i < n; i++) {
        o[2 * i] = d[p[i] >> 4];
        o[2 * i + 1] = d[p[i] & 15];
    }
    return o;
}

std::string join_limited(const std::vector<std::string>& v, const char* sep) {
    std::string o;
    for (size_t i = 0; i < v.size() && i < kListMax; i++) o += (i ? sep : "") + v[i];
    if (v.size() > kListMax) o += std::string(sep) + "and " + std::to_string(v.size() - kListMax) + " more";
    return o;
}

const char* method_name(unsigned m) {
    switch (m) {
    case 0: return "stored";
    case 1: return "shrink";
    case 6: return "implode";
    case 8: return "deflate";
    case 9: return "Deflate64";
    case 12: return "bzip2";
    case 14: return "LZMA";
    case 93: return "Zstandard";
    case 95: return "xz";
    case 96: return "JPEG";
    case 97: return "WavPack";
    case 98: return "PPMd";
    case 99: return "AES encryption";
    default: return "an unknown method";
    }
}

// What a file that is not a ZIP is, from its first bytes; "" when it is none of these.
std::string sniff(const uint8_t* p, size_t n) {
    if (n >= 6 && !std::memcmp(p, "7z\xBC\xAF\x27\x1C", 6)) return "a 7z archive";
    if (n >= 6 && !std::memcmp(p, "Rar!\x1A\x07", 6)) return "a RAR archive";
    if (n >= 2 && p[0] == 0x1F && p[1] == 0x8B) return "a gzip file";
    if (n >= 6 && !std::memcmp(p, "\xFD" "7zXZ\x00", 6)) return "an xz file";
    if (n >= 4 && !std::memcmp(p, "\x28\xB5\x2F\xFD", 4)) return "a Zstandard file";
    if (n >= 3 && !std::memcmp(p, "BZh", 3)) return "a bzip2 file";
    if (n >= 262 && !std::memcmp(p + 257, "ustar", 5)) return "a tar archive";
    return "";
}

// Whether `s` is UTF-8 in shortest form, with no surrogate and nothing past U+10FFFF.
bool utf8_valid(const std::string& s) {
    const size_t n = s.size();
    for (size_t i = 0; i < n;) {
        const unsigned char c = static_cast<unsigned char>(s[i]);
        if (c < 0x80) {
            i++;
            continue;
        }
        size_t len = 0;
        uint32_t cp = 0;
        if ((c & 0xE0) == 0xC0) len = 2, cp = c & 0x1F;
        else if ((c & 0xF0) == 0xE0) len = 3, cp = c & 0x0F;
        else if ((c & 0xF8) == 0xF0) len = 4, cp = c & 0x07;
        if (!len || i + len > n) return false;
        for (size_t k = 1; k < len; k++) {
            const unsigned char d = static_cast<unsigned char>(s[i + k]);
            if ((d & 0xC0) != 0x80) return false;
            cp = (cp << 6) | (d & 0x3F);
        }
        const uint32_t min = len == 2 ? 0x80 : len == 3 ? 0x800 : 0x10000;
        if (cp < min || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return false;
        i += len;
    }
    return true;
}

// Code page 437's upper half (0x80-0xFF) as Unicode, as Python's cp437 codec has it.
const uint16_t kCp437High[128] = {
    0x00C7, 0x00FC, 0x00E9, 0x00E2, 0x00E4, 0x00E0, 0x00E5, 0x00E7,
    0x00EA, 0x00EB, 0x00E8, 0x00EF, 0x00EE, 0x00EC, 0x00C4, 0x00C5,
    0x00C9, 0x00E6, 0x00C6, 0x00F4, 0x00F6, 0x00F2, 0x00FB, 0x00F9,
    0x00FF, 0x00D6, 0x00DC, 0x00A2, 0x00A3, 0x00A5, 0x20A7, 0x0192,
    0x00E1, 0x00ED, 0x00F3, 0x00FA, 0x00F1, 0x00D1, 0x00AA, 0x00BA,
    0x00BF, 0x2310, 0x00AC, 0x00BD, 0x00BC, 0x00A1, 0x00AB, 0x00BB,
    0x2591, 0x2592, 0x2593, 0x2502, 0x2524, 0x2561, 0x2562, 0x2556,
    0x2555, 0x2563, 0x2551, 0x2557, 0x255D, 0x255C, 0x255B, 0x2510,
    0x2514, 0x2534, 0x252C, 0x251C, 0x2500, 0x253C, 0x255E, 0x255F,
    0x255A, 0x2554, 0x2569, 0x2566, 0x2560, 0x2550, 0x256C, 0x2567,
    0x2568, 0x2564, 0x2565, 0x2559, 0x2558, 0x2552, 0x2553, 0x256B,
    0x256A, 0x2518, 0x250C, 0x2588, 0x2584, 0x258C, 0x2590, 0x2580,
    0x03B1, 0x00DF, 0x0393, 0x03C0, 0x03A3, 0x03C3, 0x00B5, 0x03C4,
    0x03A6, 0x0398, 0x03A9, 0x03B4, 0x221E, 0x03C6, 0x03B5, 0x2229,
    0x2261, 0x00B1, 0x2265, 0x2264, 0x2320, 0x2321, 0x00F7, 0x2248,
    0x00B0, 0x2219, 0x00B7, 0x221A, 0x207F, 0x00B2, 0x25A0, 0x00A0,
};

std::string cp437_to_utf8(const std::string& s) {
    std::string o;
    o.reserve(s.size() * 2);
    for (const char ch : s) {
        const unsigned char c = static_cast<unsigned char>(ch);
        const uint32_t cp = c < 0x80 ? c : kCp437High[c - 0x80];
        if (cp < 0x80) {
            o += char(cp);
        } else if (cp < 0x800) {
            o += char(0xC0 | (cp >> 6));
            o += char(0x80 | (cp & 0x3F));
        } else {
            o += char(0xE0 | (cp >> 12));
            o += char(0x80 | ((cp >> 6) & 0x3F));
            o += char(0x80 | (cp & 0x3F));
        }
    }
    return o;
}

// The extra-field block `id` among `x[0..xlen)`, as its data and size.
bool find_extra(const uint8_t* x, size_t xlen, uint16_t id, const uint8_t** v, size_t* vn) {
    size_t xp = 0;
    while (xp + 4 <= xlen) {
        const uint16_t xid = u16(x + xp), sz = u16(x + xp + 2);
        if (xp + 4 + sz > xlen) return false;
        if (xid == id) {
            *v = x + xp + 4;
            *vn = sz;
            return true;
        }
        xp += 4 + size_t(sz);
    }
    return false;
}

struct Entry {
    std::string name;  // as UTF-8 (sa2_zip.h, step 2), '\' folded to '/'
    std::string key;   // lower(name)
    uint16_t flags = 0, method = 0;
    uint32_t crc = 0;
    uint64_t csize = 0, usize = 0, offset = 0;
    bool dir = false;
    bool ignored = false;
    bool cp437 = false;          // the name was read as code page 437
    bool unicode_field = false;  // the name came from Info-ZIP's Unicode Path field (0x7075)
};

// The entry's name as UTF-8: Info-ZIP's Unicode Path field when it is present and was written
// for these very bytes (its CRC-32 matches them); else the bytes themselves when they are valid
// UTF-8 (general-purpose bit 11, or a writer such as macOS's that writes UTF-8 without it);
// else code page 437, which Windows Explorer, 7-Zip and Info-ZIP use for a name without bit 11.
void decode_name(Entry& en, const uint8_t* extra, size_t xlen) {
    const uint8_t* v = nullptr;
    size_t vn = 0;
    if (find_extra(extra, xlen, 0x7075, &v, &vn) && vn > 5 && v[0] == 1 &&
        u32(v + 1) == sa2_crc32(0, en.name.data(), en.name.size())) {
        std::string u(reinterpret_cast<const char*>(v + 5), vn - 5);
        if (u.find('\0') == std::string::npos && utf8_valid(u)) {
            en.name = u;
            en.unicode_field = true;
            return;
        }
    }
    if (utf8_valid(en.name)) return;
    en.name = cp437_to_utf8(en.name);
    en.cp437 = true;
}

struct Archive {
    Sa2File file;
    uint64_t size = 0;
    uint64_t data_end = 0;  // the central directory's start: no entry's data passes it
    bool zip64 = false;
    std::vector<Entry> entries;
};

bool ignorable(const std::string& key) {
    if (key == "__macosx" || starts_with(key, "__macosx/") ||
        key.find("/__macosx/") != std::string::npos)
        return true;
    std::string k = key;
    while (!k.empty() && k.back() == '/') k.pop_back();
    const size_t slash = k.rfind('/');
    const std::string last = slash == std::string::npos ? k : k.substr(slash + 1);
    return starts_with(last, "._") || last == ".ds_store" || last == "thumbs.db" ||
           last == "desktop.ini";
}

// Reads the end records and the central directory. 0, or the Sa2InstallFailure with `err`
// naming what is wrong.
int open_archive(const std::string& path, Archive& a, std::string& err) {
    if (!a.file.open_read(path, &err))
        return sa2_fs_kind(path) == Sa2FsKind::None ? SA2_FAIL_NOT_FOUND : SA2_FAIL_IO;
    a.size = a.file.size();
    uint8_t head[512];
    const size_t hn = size_t(std::min<uint64_t>(a.size, sizeof head));
    if (hn && !a.file.read_at(0, head, hn, &err)) return SA2_FAIL_IO;
    const std::string other = sniff(head, hn);
    if (!other.empty()) {
        err = "the file is " + other + ", not a ZIP archive; re-pack the game folder as a ZIP "
              "with Deflate or Store";
        return SA2_FAIL_NOT_FOUND;
    }
    if (a.size < 22) {
        err = "the file is not a ZIP archive (it is too short to hold one)";
        return SA2_FAIL_NOT_FOUND;
    }
    const uint64_t tail_n = std::min<uint64_t>(a.size, 22 + 65535);
    std::vector<uint8_t> tail(static_cast<size_t>(tail_n));
    if (!a.file.read_at(a.size - tail_n, tail.data(), tail.size(), &err)) return SA2_FAIL_IO;
    // The last end record whose comment reaches exactly to the end of the file, else the last
    // one whose comment fits inside it.
    long at = -1, loose = -1;
    for (long i = long(tail_n) - 22; i >= 0; i--) {
        if (u32(&tail[size_t(i)]) != kSigEnd) continue;
        const uint64_t clen = u16(&tail[size_t(i) + 20]);
        if (uint64_t(i) + 22 + clen == tail_n) {
            at = i;
            break;
        }
        if (loose < 0 && uint64_t(i) + 22 + clen <= tail_n) loose = i;
    }
    if (at < 0) at = loose;
    if (at < 0) {
        // A ZIP's first bytes are a local header: one that has them and no end record was cut.
        err = hn >= 4 && u32(head) == kSigLocal
                  ? "the archive's end is missing (no end-of-central-directory record): it is "
                    "truncated or damaged"
                  : "the file is not a ZIP archive (it has no end-of-central-directory record)";
        return hn >= 4 && u32(head) == kSigLocal ? SA2_FAIL_IO : SA2_FAIL_NOT_FOUND;
    }
    const uint8_t* e = &tail[size_t(at)];
    const uint64_t end_pos = a.size - tail_n + uint64_t(at);
    uint64_t disk = u16(e + 4), cd_disk = u16(e + 6), n_disk = u16(e + 8), n_total = u16(e + 10);
    uint64_t cd_size = u32(e + 12), cd_off = u32(e + 16);
    uint64_t dir_end = end_pos;  // the central directory must end at or before this

    // ZIP64: the locator sits just before the end record.
    if (end_pos >= 20) {
        uint8_t loc[20];
        if (!a.file.read_at(end_pos - 20, loc, 20, &err)) return SA2_FAIL_IO;
        if (u32(loc) == kSigLocator64) {
            const uint32_t z_disk = u32(loc + 4);
            const uint64_t z_off = u64(loc + 8);
            const uint32_t disks = u32(loc + 16);
            if (z_disk != 0 || disks > 1) {
                err = "the archive is split across several files (a multi-disk ZIP); join it "
                      "into one archive first";
                return SA2_FAIL_REFUSED;
            }
            uint8_t z[56];
            if (z_off + 56 > end_pos - 20 || !a.file.read_at(z_off, z, 56, &err) ||
                u32(z) != kSigEnd64) {
                if (err.empty()) err = "the archive's ZIP64 end record is missing or damaged";
                return SA2_FAIL_IO;
            }
            a.zip64 = true;
            disk = u32(z + 16);
            cd_disk = u32(z + 20);
            n_disk = u64(z + 24);
            n_total = u64(z + 32);
            cd_size = u64(z + 40);
            cd_off = u64(z + 48);
            dir_end = z_off;
        }
    }
    if (disk != 0 || cd_disk != 0 || n_disk != n_total) {
        err = "the archive is split across several files (a multi-disk ZIP); join it into one "
              "archive first";
        return SA2_FAIL_REFUSED;
    }
    if (cd_off > dir_end || cd_size > dir_end - cd_off) {
        err = "the archive's central directory lies outside the file: it is damaged or truncated";
        return SA2_FAIL_IO;
    }
    if (cd_size > kMaxCentral || n_total > cd_size / 46 + 1) {
        err = "the archive's central directory is implausibly large";
        return SA2_FAIL_IO;
    }
    a.data_end = cd_off;
    std::vector<uint8_t> cd(size_t(cd_size) + 1);
    if (cd_size && !a.file.read_at(cd_off, cd.data(), size_t(cd_size), &err)) return SA2_FAIL_IO;

    size_t p = 0;
    a.entries.reserve(size_t(n_total));
    for (uint64_t i = 0; i < n_total; i++) {
        if (p + 46 > cd_size || u32(&cd[p]) != kSigCentral) {
            err = "the archive's central directory is damaged at entry " + std::to_string(i + 1);
            return SA2_FAIL_IO;
        }
        const uint8_t* c = &cd[p];
        Entry en;
        en.flags = u16(c + 8);
        en.method = u16(c + 10);
        en.crc = u32(c + 16);
        en.csize = u32(c + 20);
        en.usize = u32(c + 24);
        const size_t nlen = u16(c + 28), xlen = u16(c + 30), clen = u16(c + 32);
        uint64_t edisk = u16(c + 34);
        en.offset = u32(c + 42);
        if (p + 46 + nlen + xlen + clen > cd_size) {
            err = "the archive's central directory is damaged at entry " + std::to_string(i + 1);
            return SA2_FAIL_IO;
        }
        en.name.assign(reinterpret_cast<const char*>(c + 46), nlen);
        // ZIP64's extra field holds, in this order, each value whose 32-bit field is all ones.
        const bool need_u = en.usize == 0xFFFFFFFFu, need_c = en.csize == 0xFFFFFFFFu,
                   need_o = en.offset == 0xFFFFFFFFu, need_d = edisk == 0xFFFFu;
        if (need_u || need_c || need_o || need_d) {
            const uint8_t* x = c + 46 + nlen;
            size_t xp = 0;
            bool found = false;
            while (xp + 4 <= xlen) {
                const uint16_t id = u16(x + xp), sz = u16(x + xp + 2);
                if (xp + 4 + sz > xlen) break;
                if (id == 0x0001) {
                    const uint8_t* v = x + xp + 4;
                    size_t vp = 0;
                    bool ok = true;
                    auto take = [&](uint64_t& out, size_t w) {
                        if (vp + w > sz) {
                            ok = false;
                            return;
                        }
                        out = w == 8 ? u64(v + vp) : u32(v + vp);
                        vp += w;
                    };
                    if (need_u) take(en.usize, 8);
                    if (need_c) take(en.csize, 8);
                    if (need_o) take(en.offset, 8);
                    if (need_d) take(edisk, 4);
                    found = ok;
                    break;
                }
                xp += 4 + size_t(sz);
            }
            if (!found) {
                err = "the archive's entry \"" + en.name + "\" has a damaged ZIP64 field";
                return SA2_FAIL_IO;
            }
            a.zip64 = true;
        }
        if (edisk != 0) {
            err = "the archive is split across several files (a multi-disk ZIP); join it into "
                  "one archive first";
            return SA2_FAIL_REFUSED;
        }
        if (en.name.find('\0') != std::string::npos) {
            err = "the archive has an entry name holding a NUL byte";
            return SA2_FAIL_REFUSED;
        }
        decode_name(en, c + 46 + nlen, xlen);
        for (auto& ch : en.name)
            if (ch == '\\') ch = '/';
        en.key = lower(en.name);
        en.dir = !en.name.empty() && en.name.back() == '/';
        en.ignored = en.name.empty() || ignorable(en.key);
        a.entries.push_back(std::move(en));
        p += 46 + nlen + xlen + clen;
    }
    return 0;
}

// ------------------------------------------------------------------------- extraction

struct Extracted {
    bool ok = false;        // the data read back whole, sizes and CRC-32 as the archive says
    bool io_error = false;  // a read or write of this machine's files failed: abort the run
    std::string why;        // the damage, or the I/O error
    uint64_t size = 0;
    std::string sha;        // hex SHA-256 of the bytes written
};

class EntryIo : public Sa2InflateIo {
public:
    EntryIo(const Sa2File& in, uint64_t off, uint64_t len, Sa2File& out, uint64_t limit)
        : in_(in), off_(off), left_(len), out_(out), limit_(limit) {}

    size_t read(uint8_t* buf, size_t cap) override {
        const size_t n = size_t(std::min<uint64_t>(cap, left_));
        if (!n) return 0;
        if (!in_.read_at(off_, buf, n, &io_why)) {
            io_error = true;
            return 0;
        }
        off_ += n;
        left_ -= n;
        return n;
    }

    bool write(const uint8_t* p, size_t n) override {
        if (n > limit_ - written) {
            over = true;
            return false;
        }
        crc = sa2_crc32(crc, p, n);
        sha.update(p, n);
        if (!out_.write(p, n, &io_why)) {
            io_error = true;
            return false;
        }
        written += n;
        return true;
    }

    bool io_error = false, over = false;
    std::string io_why;
    uint32_t crc = 0;
    uint64_t written = 0;
    Sha256 sha;

private:
    const Sa2File& in_;
    uint64_t off_, left_;
    Sa2File& out_;
    uint64_t limit_;
};

Extracted extract(const Archive& a, const Entry& e, const std::string& part) {
    Extracted x;
    auto damaged = [&](const std::string& why) {
        x.why = why;
        return x;
    };
    if (e.offset > a.data_end || a.data_end - e.offset < 30)
        return damaged("its local header lies outside the archive's data");
    uint8_t lh[30];
    if (!a.file.read_at(e.offset, lh, 30, &x.why)) {
        x.io_error = true;
        return x;
    }
    if (u32(lh) != kSigLocal) return damaged("there is no local header at its offset");
    const uint64_t data = e.offset + 30 + u16(lh + 26) + u16(lh + 28);
    if (data > a.data_end || e.csize > a.data_end - data)
        return damaged("its data runs past the start of the central directory");
    if (e.method == 0 && e.csize != e.usize)
        return damaged("it is stored, yet its compressed and uncompressed sizes differ");

    Sa2File out;
    if (!out.create(part, &x.why)) {
        x.io_error = true;
        return x;
    }
    EntryIo io(a.file, data, e.csize, out, e.usize);
    std::string bad;
    if (e.method == 0) {
        std::unique_ptr<uint8_t[]> buf(new uint8_t[size_t(1) << 20]);
        for (;;) {
            const size_t n = io.read(buf.get(), size_t(1) << 20);
            if (!n || !io.write(buf.get(), n)) break;
        }
    } else {
        const Sa2InflateResult r = sa2_inflate(io);
        if (!io.io_error && r.status != Sa2InflateStatus::Ok)
            bad = io.over ? "it inflates to more than the central directory's size"
                          : std::string("its deflate data is damaged (") +
                                sa2_inflate_status_name(r.status) + ": " + r.why + ")";
    }
    if (io.io_error) {
        out.close(nullptr);
        sa2_fs_remove_file(part, nullptr);
        x.io_error = true;
        x.why = io.io_why;
        return x;
    }
    if (bad.empty() && io.written != e.usize)
        bad = "it holds " + std::to_string(io.written) + " bytes, the central directory says " +
              std::to_string(e.usize);
    if (bad.empty() && io.crc != e.crc) bad = "its CRC-32 does not match the central directory";
    if (!bad.empty()) {
        out.close(nullptr);
        sa2_fs_remove_file(part, nullptr);
        return damaged(bad);
    }
    if (!out.sync(&x.why) || !out.close(&x.why)) {
        sa2_fs_remove_file(part, nullptr);
        x.io_error = true;
        return x;
    }
    x.ok = true;
    x.size = io.written;
    x.sha = io.sha.hex();
    return x;
}

// Size, SHA-256 and CRC-32 of a file already on disk.
bool digest_file(const std::string& path, uint64_t* size, std::string* sha, uint32_t* crc,
                 std::string* err) {
    Sa2File f;
    if (!f.open_read(path, err)) return false;
    Sha256 h;
    uint32_t c = 0;
    std::unique_ptr<uint8_t[]> buf(new uint8_t[size_t(1) << 20]);
    uint64_t off = 0;
    while (off < f.size()) {
        const size_t n = size_t(std::min<uint64_t>(f.size() - off, size_t(1) << 20));
        if (!f.read_at(off, buf.get(), n, err)) return false;
        h.update(buf.get(), n);
        c = sa2_crc32(c, buf.get(), n);
        off += n;
    }
    *size = f.size();
    *sha = h.hex();
    *crc = c;
    return true;
}

// ------------------------------------------------------------------------- planning

struct Job {
    int table = -1;            // the table entry, or -1 for an extra
    std::vector<int> sources;  // archive entries to try, in order: the file itself, its *_orig
    bool has_main = false;     // sources[0] is the file itself, not its *_orig original
    std::string main_why;      // why the file itself is not among the sources, when it is present
    bool main_present = false;
    std::string dest;          // the store path
    bool reuse = false;        // a regular file sits at `dest` in the partial store
    uint64_t size = 0;         // bytes it will write
};

struct JobResult {
    Sa2FileResult::Kind kind = Sa2FileResult::Missing;
    std::string detail;
    std::string from;          // the archive name (under the root) the stored bytes came from
    int source = -1;           // which archive entry backed it
    uint64_t size = 0;
    std::string sha;
    bool resumed = false;
    bool extracted = false;
    std::vector<std::string> damaged;
};

struct Run {
    const Archive* archive = nullptr;
    const Sa2InstallTable* table = nullptr;
    std::string partial;
    size_t root_len = 0;
    std::vector<Job> jobs;
    std::vector<JobResult> results;
    std::atomic<size_t> next{0};
    std::atomic<bool> abort{false};
    std::mutex mu;
    std::string io_error;

    void fail(const std::string& why) {
        std::lock_guard<std::mutex> lock(mu);
        if (io_error.empty()) io_error = why;
        abort = true;
    }
};

// The table's check of a file now on disk: the executable by its sections (the DRM ruling),
// every other file by size and SHA-256.
bool check_against(const Sa2InstallEntry& e, const std::string& path, uint64_t size,
                   const std::string& sha, std::string* why) {
    if (e.exe) {
        std::string w;
        if (sa2_exe_check_file(*e.exe, path, &w)) return true;
        *why = "the executable's check: " + w;
        return false;
    }
    if (size != e.size) {
        *why = "it holds " + std::to_string(size) + " bytes, the table says " +
               std::to_string(e.size);
        return false;
    }
    if (sha != hex(e.sha256, 32)) {
        *why = "its SHA-256 is " + sha.substr(0, 16) + "..., the table's " +
               hex(e.sha256, 8) + "...";
        return false;
    }
    return true;
}

void run_job(Run& run, size_t i) {
    const Job& job = run.jobs[i];
    JobResult& r = run.results[i];
    const Archive& a = *run.archive;
    const std::string dest = sa2_path_join(run.partial, job.dest);
    // The job's own temporary name, which no archive name can be: two jobs never share one,
    // and the bytes checked are the bytes renamed into place.
    const std::string part =
        sa2_path_join(sa2_path_join(run.partial, SA2_STORE_TMP_DIR), std::to_string(i));
    const Sa2InstallEntry* e = job.table >= 0 ? &run.table->entries[job.table] : nullptr;
    auto kind_of = [&](size_t k) {
        return (k == 0 && job.has_main) || !e ? Sa2FileResult::Ok : Sa2FileResult::SubstitutedOrig;
    };

    if (job.reuse) {
        // Only a job with one source reuses, so the file can only have come from sources[0].
        uint64_t size = 0;
        std::string sha, why;
        uint32_t crc = 0;
        if (!digest_file(dest, &size, &sha, &crc, &why)) {
            run.fail(why);
            return;
        }
        const Entry& src = a.entries[size_t(job.sources[0])];
        const bool keep = e ? check_against(*e, dest, size, sha, &why)
                            : size == src.usize && crc == src.crc;
        if (keep) {
            r.kind = kind_of(0);
            r.from = src.name.substr(run.root_len);
            r.detail = r.kind == Sa2FileResult::SubstitutedOrig ? r.from : "";
            r.source = job.sources[0];
            r.size = size;
            r.sha = sha;
            r.resumed = true;
            return;
        }
        if (!sa2_fs_remove_file(dest, &why)) {
            run.fail(why);
            return;
        }
    }

    std::string main_why = job.main_why;
    std::string orig_why;
    for (size_t k = 0; k < job.sources.size(); k++) {
        if (run.abort) return;
        const Entry& src = a.entries[size_t(job.sources[k])];
        const std::string from = src.name.substr(run.root_len);
        Extracted x = extract(a, src, part);
        if (x.io_error) {
            run.fail(x.why);
            return;
        }
        std::string why;
        if (!x.ok) {
            why = "damaged in the archive: " + x.why;
            r.damaged.push_back(from + ": " + x.why);
        } else if (e && !check_against(*e, part, x.size, x.sha, &why)) {
            sa2_fs_remove_file(part, nullptr);
        } else {
            std::string err;
            if (!sa2_fs_rename(part, dest, &err)) {
                run.fail(err);
                return;
            }
            r.kind = kind_of(k);
            r.detail = r.kind == Sa2FileResult::SubstitutedOrig ? from : "";
            r.from = from;
            r.source = job.sources[k];
            r.size = x.size;
            r.sha = x.sha;
            r.extracted = true;
            return;
        }
        if (k == 0 && job.has_main)
            main_why = why;
        else
            orig_why = from + " " + why;
    }
    if (job.main_present || !e) {
        r.kind = Sa2FileResult::Modified;
        r.detail = main_why;
    } else {
        r.kind = Sa2FileResult::Missing;
        r.detail = "its original " + orig_why;
    }
    if (!orig_why.empty() && job.main_present) r.detail += "; its original " + orig_why;
}

// A *.ini or *.cfg (case-blind): user config, which install-hashes-v1's rules leave unhashed
// (tools/hash_install.py's exclusions) and which the folder route lets the runtime read.
bool is_config_name(const std::string& key) {
    const size_t n = key.size();
    return n > 4 && (key.compare(n - 4, 4, ".ini") == 0 || key.compare(n - 4, 4, ".cfg") == 0);
}

// Whether `key` (folded) is the *_orig original of a table file: "dir/Name_orig.ext" for a table
// entry "dir/Name.ext". One that backs nothing is skipped, never read.
bool is_table_original(const std::string& key, const std::unordered_map<std::string, int>& by_key) {
    const size_t slash = key.rfind('/');
    const size_t o = key.rfind("_orig");
    if (o == std::string::npos || (slash != std::string::npos && o < slash)) return false;
    const std::string named = key.substr(0, o) + key.substr(o + 5);
    return lower(sa2_install_orig_name(named)) == key && by_key.count(named) != 0;
}

// Whether `dir` holds a store this route wrote (its record names the schema).
bool is_store(const std::string& dir) {
    std::string id, sha;
    return sa2_store_read_identity(dir, &id, &sha);
}

}  // namespace

bool sa2_zip_install(const std::string& zip, const std::string& store_dir,
                     const Sa2InstallTable& table, Sa2InstallReport& report) {
    return sa2_zip_install(zip, store_dir, table, report, Sa2ZipOptions(), nullptr);
}

bool sa2_zip_install(const std::string& zip, const std::string& store_dir,
                     const Sa2InstallTable& table, Sa2InstallReport& report,
                     const Sa2ZipOptions& options, Sa2ZipDetail* detail_out) {
    report = Sa2InstallReport();
    Sa2ZipDetail local;
    Sa2ZipDetail& d = detail_out ? *detail_out : local;
    d = Sa2ZipDetail();
    auto refuse = [&](int failure, const std::string& why) {
        report.error = why;
        report.failure = failure;
        return false;
    };

    // ---- the table: canonical names, each safe, unique case-blind, never file and directory
    std::unordered_map<std::string, int> by_key;
    std::map<std::string, std::string> dirs;  // lower(dir) -> its spelling in the store
    for (size_t i = 0; i < table.count; i++) {
        const std::string path = table.entries[i].path ? table.entries[i].path : "";
        std::string why;
        if (!sa2_store_safe_relpath(path, &why))
            return refuse(SA2_FAIL_IO, "the hash table's entry \"" + path +
                                           "\" is not a safe name (" + why + ")");
        if (sa2_store_reserved_name(path.substr(0, path.find('/'))))
            return refuse(SA2_FAIL_IO, "the hash table's entry \"" + path +
                                           "\" takes a name the store itself uses");
        if (!by_key.emplace(lower(path), int(i)).second)
            return refuse(SA2_FAIL_IO, "the hash table names \"" + path + "\" twice");
        for (size_t s = path.find('/'); s != std::string::npos; s = path.find('/', s + 1))
            dirs.emplace(lower(path.substr(0, s)), path.substr(0, s));
    }
    for (const auto& kv : by_key)
        if (dirs.count(kv.first))
            return refuse(SA2_FAIL_IO, "the hash table uses \"" +
                                           std::string(table.entries[kv.second].path) +
                                           "\" as both a file and a directory");

    // ---- the store and what earlier runs left beside it
    const std::string partial = sa2_store_partial_dir(store_dir);
    std::string old = partial;
    old.replace(old.size() - std::strlen(".partial"), std::strlen(".partial"), ".old");
    {
        std::string err;
        Sa2FsKind sk = sa2_fs_kind(store_dir);
        const Sa2FsKind ok_kind = sa2_fs_kind(old);
        if (ok_kind != Sa2FsKind::None) {
            // A replacement stopped between its two renames. The old store is a complete one.
            if (ok_kind != Sa2FsKind::Dir || !is_store(old))
                return refuse(SA2_FAIL_REFUSED, old + " exists and is not a store of this "
                                                      "installer; it is never written over");
            const bool ok = sk == Sa2FsKind::None ? sa2_fs_rename(old, store_dir, &err)
                                                  : sa2_fs_remove_tree(old, &err);
            if (!ok) return refuse(SA2_FAIL_IO, err);
            sk = sa2_fs_kind(store_dir);
        }
        if (sk == Sa2FsKind::Dir) {
            std::vector<std::string> f, g;
            if (!sa2_fs_walk(store_dir, &f, &g, &err)) return refuse(SA2_FAIL_IO, err);
            if ((!f.empty() || !g.empty()) && !is_store(store_dir))
                return refuse(SA2_FAIL_REFUSED,
                              store_dir + " exists and is not a store of this installer; it is "
                                          "never written over");
        } else if (sk != Sa2FsKind::None) {
            return refuse(SA2_FAIL_REFUSED, store_dir + " exists and is not a directory");
        }
    }
    const Sa2FsKind partial_kind = sa2_fs_kind(partial);
    if (partial_kind != Sa2FsKind::None && partial_kind != Sa2FsKind::Dir)
        return refuse(SA2_FAIL_REFUSED, partial + " exists and is not a directory");
    if (partial_kind == Sa2FsKind::Dir && !sa2_store_has_partial_marker(partial) &&
        !is_store(partial)) {
        // Its record is written before its marker is removed, so a partial store this route
        // made has one or the other; anything else in that place is someone's directory.
        std::vector<std::string> f, g;
        std::string err;
        if (!sa2_fs_walk(partial, &f, &g, &err)) return refuse(SA2_FAIL_IO, err);
        if (!f.empty() || !g.empty())
            return refuse(SA2_FAIL_REFUSED, partial + " exists and is not a partial store of "
                                                      "this installer; it is never written over");
    }

    // ---- the archive
    Archive a;
    {
        std::string err;
        const int failure = open_archive(zip, a, err);
        if (failure) return refuse(failure, err);
    }
    d.archive_bytes = a.size;
    d.entries = a.entries.size();
    d.zip64 = a.zip64;
    for (const auto& en : a.entries) {
        d.names_cp437 += en.cp437;
        d.names_unicode_field += en.unicode_field;
    }

    // The files not ignored, by case-blind name. Two entries under one name are refused only
    // where the route reads one of them or finds the root by it (dup_refusal); elsewhere they
    // are skipped like everything else the route never reads.
    std::unordered_map<std::string, std::vector<int>> groups;
    for (size_t i = 0; i < a.entries.size(); i++) {
        const Entry& en = a.entries[i];
        if (en.ignored || en.dir) continue;
        groups[en.key].push_back(int(i));
    }
    std::unordered_map<std::string, int> files;  // key -> its first entry
    for (const auto& g : groups) files.emplace(g.first, g.second[0]);
    std::set<std::string> dups_named;
    // The refusal for a name the route reads, when two entries have it; "" otherwise.
    auto dup_refusal = [&](const std::string& key) -> std::string {
        auto it = groups.find(key);
        if (it == groups.end() || it->second.size() < 2) return "";
        return "the archive holds \"" + a.entries[size_t(it->second[0])].name + "\" and \"" +
               a.entries[size_t(it->second[1])].name +
               "\", whose names differ only in case or not at all; the game's files are looked "
               "up case-blind, so the archive is refused";
    };

    // ---- the root
    std::vector<std::string> roots;
    const size_t exe_n = std::strlen(kExeKey);
    for (const auto& kv : files) {
        const std::string& k = kv.first;
        if (k.size() < exe_n || k.compare(k.size() - exe_n, exe_n, kExeKey) != 0) continue;
        if (k.size() > exe_n && k[k.size() - exe_n - 1] != '/') continue;
        const std::string prefix = k.substr(0, k.size() - exe_n);
        if (files.count(prefix + kDllKey)) roots.push_back(prefix);
    }
    std::sort(roots.begin(), roots.end());
    auto spelled_root = [&](const std::string& rk) {
        return a.entries[size_t(files[rk + kExeKey])].name.substr(0, rk.size());
    };
    if (roots.empty())
        return refuse(SA2_FAIL_NOT_FOUND,
                      "this archive does not hold Sonic Adventure 2: no folder in it has both "
                      "sonic2app.exe and resource/gd_PC/DLL/Win32/Data_DLL.dll");
    if (roots.size() > 1) {
        std::vector<std::string> shown;
        for (const auto& r : roots) {
            const std::string s = spelled_root(r);
            shown.push_back(s.empty() ? "the top of the archive" : "\"" + s + "\"");
        }
        return refuse(SA2_FAIL_AMBIGUOUS, "this archive holds the game more than once, under " +
                                              join_limited(shown, " and ") +
                                              "; make an archive of one copy");
    }
    const std::string root_key = roots[0];
    for (const char* k : {kExeKey, kDllKey}) {
        const std::string why = dup_refusal(root_key + k);
        if (!why.empty()) return refuse(SA2_FAIL_REFUSED, why);
    }
    const std::string root = spelled_root(root_key);
    d.root = root;

    // ---- the Mod Loader's markers, from every entry under the root, directories included
    for (size_t m = 0; m < SA2_MOD_LOADER_MARKER_COUNT; m++) {
        const std::string mk = lower(SA2_MOD_LOADER_MARKERS[m]);
        for (const auto& en : a.entries) {
            if (en.ignored || !starts_with(en.key, root_key)) continue;
            std::string rel = en.key.substr(root_key.size());
            while (!rel.empty() && rel.back() == '/') rel.pop_back();
            if (rel == mk || starts_with(rel, mk + "/")) {
                report.markers.push_back(SA2_MOD_LOADER_MARKERS[m]);
                break;
            }
        }
    }
    report.mod_loader = !report.markers.empty();

    // ---- every table entry against the archive
    std::vector<std::string> refusals;
    std::set<int> table_sources;  // archive entries a table entry names, itself or its *_orig
    std::set<int> orig_sources;
    std::vector<Job> jobs;
    report.files.resize(table.count);
    auto find_rel = [&](const std::string& rel) {
        auto it = files.find(root_key + lower(rel));
        return it == files.end() ? -1 : it->second;
    };
    // Each name the route will read is refused once if two entries have it.
    auto check_dup = [&](int k) {
        const std::string why = dup_refusal(a.entries[size_t(k)].key);
        if (!why.empty() && dups_named.insert(a.entries[size_t(k)].key).second)
            refusals.push_back(why);
    };
    for (size_t i = 0; i < table.count; i++) {
        const Sa2InstallEntry& e = table.entries[i];
        Sa2FileResult& fr = report.files[i];
        fr.path = e.path;
        fr.required = e.required;
        fr.kind = Sa2FileResult::Ok;  // provisional, until the file is read
        fr.detail = kNotRead;
        const int main = find_rel(e.path);
        const std::string orig_name = sa2_install_orig_name(e.path);
        const int orig = lower(orig_name) == lower(e.path) ? -1 : find_rel(orig_name);
        if (main >= 0) {
            table_sources.insert(main);
            check_dup(main);
        }
        if (orig >= 0) {
            table_sources.insert(orig);
            orig_sources.insert(orig);
            check_dup(orig);
        }
        if (main >= 0) {
            const std::string spelled = a.entries[size_t(main)].name.substr(root.size());
            if (spelled != fr.path) fr.found_as = spelled;
        }
        auto size_why = [&](int k) {
            const uint64_t u = a.entries[size_t(k)].usize;
            if (e.exe || u == e.size) return std::string();
            return "it holds " + std::to_string(u) + " bytes in the archive, the table says " +
                   std::to_string(e.size);
        };
        Job job;
        job.table = int(i);
        job.dest = e.path;
        job.main_present = main >= 0;
        if (main >= 0) {
            job.main_why = size_why(main);
            if (job.main_why.empty()) {
                job.sources.push_back(main);
                job.has_main = true;
            }
        }
        const std::string orig_why = orig >= 0 ? size_why(orig) : std::string();
        if (orig >= 0 && orig_why.empty()) job.sources.push_back(orig);
        if (job.sources.empty()) {
            fr.detail.clear();
            if (main >= 0) {
                fr.kind = Sa2FileResult::Modified;
                fr.detail = job.main_why;
            } else {
                fr.kind = Sa2FileResult::Missing;
            }
            if (orig >= 0)
                fr.detail += std::string(fr.detail.empty() ? "" : "; ") + "its original " +
                             orig_name + " does not match either: " + orig_why;
            continue;
        }
        job.size = a.entries[size_t(job.sources[0])].usize;
        jobs.push_back(std::move(job));
    }

    // ---- what else is under the root: unhashed shipped files, user files, the rest
    int extras_total = 0;
    std::vector<int> extra_entries, user_entries;
    for (const auto& g : groups) {
        if (!starts_with(g.first, root_key)) continue;
        for (const int k : g.second) {
            if (table_sources.count(k)) continue;
            extras_total++;
            const std::string rel = a.entries[size_t(k)].name.substr(root.size());
            const std::string rk = lower(rel);
            std::vector<std::string> comps;
            for (size_t s = 0, t = 0; s <= rk.size(); s = t + 1) {
                t = rk.find('/', s);
                if (t == std::string::npos) t = rk.size();
                comps.push_back(rk.substr(s, t - s));
            }
            bool save = false, mod = false;
            for (size_t c = 0; c + 1 < comps.size(); c++) {
                if (comps[c] == "savedata") save = true;
                if (comps[c] == "mods" || comps[c] == "crashdump") mod = true;
            }
            // The folder route's rule for an extra (sa2_install.h, sa2_install_extra_allowed),
            // which the two routes share: outside resource/ it may stay; under resource/ only a
            // save, user config or an unused original may, since the game probes resource/ for
            // files a clean install lacks and would read any other unverified.
            if (comps[0] != "resource" || comps.size() < 2) {
                if (!mod && (comps[0] == "config" || save)) {
                    d.user_files.push_back(rel);
                    user_entries.push_back(k);
                } else {
                    d.skipped.push_back(rel);
                }
            } else if (save) {
                d.user_files.push_back(rel);
                user_entries.push_back(k);
            } else if (is_config_name(rk)) {
                extra_entries.push_back(k);   // stored, as the folder route lets it be read
            } else if (is_table_original(rk, by_key)) {
                d.skipped.push_back(rel);
            } else {
                refusals.push_back("\"" + rel + "\" is not a game file, and the game would read it "
                                   "unverified under resource/; remove it from the archive");
            }
        }
    }
    auto by_name = [&](int x, int y) { return a.entries[size_t(x)].name < a.entries[size_t(y)].name; };
    std::sort(extra_entries.begin(), extra_entries.end(), by_name);
    std::sort(user_entries.begin(), user_entries.end(), by_name);
    // Where an archive-named file goes: each directory takes the table's spelling, or the first
    // spelling the archive uses; "" (with a refusal) for an unsafe name or one that would use a
    // name as both a file and a directory.
    auto place = [&](int k, std::map<std::string, std::string>& spell,
                     std::set<std::string>& taken) -> std::string {
        const std::string rel = a.entries[size_t(k)].name.substr(root.size());
        std::string why;
        if (!sa2_store_safe_relpath(rel, &why)) {
            refusals.push_back("the archive's \"" + rel + "\" is not a safe name (" + why + ")");
            return "";
        }
        std::string prefix;
        size_t s = 0;
        bool clash = false;
        for (size_t t = rel.find('/'); t != std::string::npos; t = rel.find('/', s)) {
            const std::string cand = (prefix.empty() ? "" : prefix + "/") + rel.substr(s, t - s);
            const std::string ck = lower(cand);
            if (by_key.count(ck) || taken.count(ck)) clash = true;
            auto it = spell.find(ck);
            prefix = it == spell.end() ? spell.emplace(ck, cand).first->second : it->second;
            s = t + 1;
        }
        const std::string dest = prefix + "/" + rel.substr(s);
        if (clash || spell.count(lower(dest))) {
            refusals.push_back("the archive's \"" + rel +
                               "\" uses one name as both a file and a directory");
            return "";
        }
        taken.insert(lower(dest));
        return dest;
    };
    std::set<std::string> extra_keys;
    for (int k : extra_entries) {
        check_dup(k);
        const std::string dest = place(k, dirs, extra_keys);
        if (dest.empty()) continue;
        Job job;
        job.sources.push_back(k);
        job.dest = dest;
        job.size = a.entries[size_t(k)].usize;
        jobs.push_back(std::move(job));
    }
    // User files, when there is somewhere to put them: the overlay, laid out as the game folder.
    std::vector<Job> user_jobs;
    if (!options.user_dir.empty()) {
        std::map<std::string, std::string> user_dirs = dirs;
        std::set<std::string> user_keys;
        for (int k : user_entries) {
            check_dup(k);
            const std::string dest = place(k, user_dirs, user_keys);
            if (dest.empty()) continue;
            Job job;
            job.sources.push_back(k);
            job.dest = dest;
            job.size = a.entries[size_t(k)].usize;
            user_jobs.push_back(std::move(job));
        }
    }

    // ---- every entry that will be read must be readable
    std::set<int> methods;
    std::vector<const Job*> all_jobs;
    for (const auto& job : jobs) all_jobs.push_back(&job);
    for (const auto& job : user_jobs) all_jobs.push_back(&job);
    for (const Job* jp : all_jobs) {
        for (int k : jp->sources) {
            const Entry& en = a.entries[size_t(k)];
            const std::string rel = en.name.substr(root.size());
            if ((en.flags & 0x0001) || (en.flags & 0x0040) || (en.flags & 0x2000) ||
                en.method == 99) {
                refusals.push_back("\"" + rel +
                                   "\" is encrypted; make the archive without a password");
            } else if (en.method != 0 && en.method != 8) {
                refusals.push_back("\"" + rel + "\" is compressed with " +
                                   method_name(en.method) + " (method " +
                                   std::to_string(en.method) +
                                   "), which this installer does not read; re-pack the archive "
                                   "with Deflate or Store");
            }
            if (en.flags & 0x0008) d.data_descriptors = true;
            methods.insert(en.method);
        }
    }
    d.methods.assign(methods.begin(), methods.end());

    // ---- the policy, on what the central directory already shows
    sa2_install_recount(report);
    report.extras = size_t(extras_total);
    {
        const std::vector<std::string> named = sa2_install_refusals(report);
        refusals.insert(refusals.end(), named.begin(), named.end());
        if (!refusals.empty())
            return refuse(SA2_FAIL_REFUSED,
                          "refused before extraction: " + join_limited(refusals, "; "));
    }

    // ---- the partial store: what a previous run left
    std::set<std::string> planned;
    for (const auto& job : jobs) planned.insert(job.dest);
    std::vector<std::string> stale, stale_dirs;
    uint64_t reusable = 0;
    if (partial_kind == Sa2FsKind::Dir) {
        std::vector<std::string> left, left_dirs;
        std::string err;
        if (!sa2_fs_walk(partial, &left, &left_dirs, &err)) return refuse(SA2_FAIL_IO, err);
        std::set<std::string> planned_dirs;
        for (const auto& p : planned)
            for (size_t s = p.find('/'); s != std::string::npos; s = p.find('/', s + 1))
                planned_dirs.insert(p.substr(0, s));
        std::set<std::string> present;
        for (const auto& f : left) {
            if (f == SA2_STORE_PARTIAL_MARKER) continue;  // kept: the partial store stays marked
            if (planned.count(f) && sa2_fs_kind(sa2_path_join(partial, f)) == Sa2FsKind::File)
                present.insert(f);
            else
                stale.push_back(f);
        }
        // A directory no planned file lives under goes whole, after the files in it.
        for (const auto& g : left_dirs)
            if (!planned_dirs.count(g)) {
                const size_t up = g.rfind('/');
                if (up == std::string::npos || planned_dirs.count(g.substr(0, up)))
                    stale_dirs.push_back(g);
            }
        for (auto& job : jobs) {
            if (!present.count(job.dest)) continue;
            // With two sources the file in the partial store does not say which it came from,
            // and the report must, so it is read again from the archive.
            if (job.sources.size() > 1) {
                stale.push_back(job.dest);
                continue;
            }
            job.reuse = true;
            reusable += job.size;
        }
    }

    // ---- free space
    // Saturating: a central directory may claim any size, and what an entry writes is held to
    // the size it claims, so the sum bounds what the run can write.
    auto add = [](uint64_t x, uint64_t y) { return x > UINT64_MAX - y ? UINT64_MAX : x + y; };
    uint64_t total = 0;
    for (const auto& job : jobs) total = add(total, job.size);
    for (const auto& job : user_jobs) total = add(total, job.size);
    d.bytes_needed = add(total - std::min(total, reusable), kMargin);
    if (options.free_bytes_override != UINT64_MAX) {
        d.bytes_free = options.free_bytes_override;
    } else {
        std::string err;
        if (!sa2_fs_free_bytes(sa2_path_parent(partial), &d.bytes_free, &err))
            return refuse(SA2_FAIL_IO, err);
    }
    if (d.bytes_free < d.bytes_needed)
        return refuse(SA2_FAIL_IO, "not enough free space: the store needs " +
                                       std::to_string(d.bytes_needed) + " bytes (a margin of " +
                                       std::to_string(kMargin) + " included), and " +
                                       std::to_string(d.bytes_free) +
                                       " are free on the volume holding " +
                                       sa2_path_parent(partial));

    // ---- prepare: mark the partial store, remove what is stale, make every directory
    const unsigned retries_before = sa2_fs_retry_count();
    {
        std::string err;
        if (!sa2_fs_mkdirs(partial, &err)) return refuse(SA2_FAIL_IO, err);
        if (!sa2_store_has_partial_marker(partial) &&
            (!sa2_store_write_partial_marker(partial, &err) ||
             !sa2_fs_sync_dir(sa2_path_parent(partial), &err)))
            return refuse(SA2_FAIL_IO, err);
        for (const auto& f : stale) {
            const std::string p = sa2_path_join(partial, f);
            if (sa2_fs_kind(p) == Sa2FsKind::None) continue;  // under a stale directory, gone
            const bool ok = sa2_fs_kind(p) == Sa2FsKind::Dir ? sa2_fs_remove_tree(p, &err)
                                                             : sa2_fs_remove_file(p, &err);
            if (!ok) return refuse(SA2_FAIL_IO, err);
        }
        for (const auto& g : stale_dirs)
            if (!sa2_fs_remove_tree(sa2_path_join(partial, g), &err)) return refuse(SA2_FAIL_IO, err);
        std::set<std::string> need_dirs;
        for (const auto& job : jobs)
            for (size_t s = job.dest.find('/'); s != std::string::npos; s = job.dest.find('/', s + 1))
                need_dirs.insert(job.dest.substr(0, s));
        for (const auto& nd : need_dirs) {
            const std::string p = sa2_path_join(partial, nd);
            const Sa2FsKind k = sa2_fs_kind(p);
            if (k != Sa2FsKind::None && k != Sa2FsKind::Dir && !sa2_fs_remove_file(p, &err))
                return refuse(SA2_FAIL_IO, err);
            if (!sa2_fs_mkdirs(p, &err)) return refuse(SA2_FAIL_IO, err);
        }
        // Every temporary file lives here; a stopped run's are gone with the stale directories.
        if (!sa2_fs_mkdirs(sa2_path_join(partial, SA2_STORE_TMP_DIR), &err))
            return refuse(SA2_FAIL_IO, err);
    }

    // ---- extract, the largest first
    Run run;
    run.archive = &a;
    run.table = &table;
    run.partial = partial;
    run.root_len = root.size();
    run.jobs = std::move(jobs);
    run.results.resize(run.jobs.size());
    std::vector<size_t> order(run.jobs.size());
    for (size_t i = 0; i < order.size(); i++) order[i] = i;
    std::stable_sort(order.begin(), order.end(),
                     [&](size_t x, size_t y) { return run.jobs[x].size > run.jobs[y].size; });
    unsigned threads = options.threads;
    if (!threads) {
        threads = std::thread::hardware_concurrency();
        if (!threads) threads = 1;
        if (threads > 8) threads = 8;
    }
    if (threads > order.size()) threads = unsigned(std::max<size_t>(order.size(), 1));
    auto worker = [&]() {
        for (;;) {
            if (run.abort) return;
            const size_t n = run.next.fetch_add(1);
            if (n >= order.size()) return;
            run_job(run, order[n]);
        }
    };
    if (threads <= 1) {
        worker();
    } else {
        std::vector<std::thread> pool;
        for (unsigned t = 0; t < threads; t++) pool.emplace_back(worker);
        for (auto& t : pool) t.join();
    }
    if (run.abort)
        return refuse(SA2_FAIL_IO, "the install stopped on an I/O error: " + run.io_error +
                                       "; the partial store at " + partial +
                                       " is kept, and a retry resumes it");

    d.files_verified = true;

    // ---- the results
    std::vector<std::string> damaged_needed;
    std::vector<Sa2StoreFile> extra_files;
    std::vector<int> job_of(table.count, -1);
    std::set<int> used;
    for (size_t i = 0; i < run.jobs.size(); i++) {
        const Job& job = run.jobs[i];
        const JobResult& r = run.results[i];
        d.damaged.insert(d.damaged.end(), r.damaged.begin(), r.damaged.end());
        if (r.resumed) d.resumed++;
        if (r.extracted) {
            d.extracted++;
            d.bytes_written += r.size;
        }
        if (r.source >= 0) used.insert(r.source);
        if (job.table < 0) {
            if (r.kind != Sa2FileResult::Ok) {
                damaged_needed.insert(damaged_needed.end(), r.damaged.begin(), r.damaged.end());
                continue;
            }
            d.extracted_extras.push_back(job.dest);
            Sa2StoreFile sf;
            sf.path = job.dest;
            sf.size = r.size;
            sf.sha256 = r.sha;
            sf.kind = "extra";
            sf.from = r.from;
            extra_files.push_back(sf);
            continue;
        }
        job_of[size_t(job.table)] = int(i);
        Sa2FileResult& fr = report.files[size_t(job.table)];
        fr.kind = r.kind;
        fr.detail = r.detail;
        if (table.entries[job.table].required && r.kind != Sa2FileResult::Ok &&
            r.kind != Sa2FileResult::SubstitutedOrig)
            damaged_needed.insert(damaged_needed.end(), r.damaged.begin(), r.damaged.end());
    }
    std::sort(d.damaged.begin(), d.damaged.end());
    // An *_orig original that backed nothing is skipped like any other extra.
    size_t extras = size_t(extras_total);
    for (int k : orig_sources) {
        if (used.count(k)) continue;
        extras++;
        d.skipped.push_back(a.entries[size_t(k)].name.substr(root.size()));
    }
    std::sort(d.skipped.begin(), d.skipped.end());
    std::sort(d.user_files.begin(), d.user_files.end());
    report.extras = extras;
    sa2_install_recount(report);
    d.fs_retries = sa2_fs_retry_count() - retries_before;

    std::vector<std::string> final_refusals;
    for (const auto& dm : damaged_needed) final_refusals.push_back("the archive is damaged at " + dm);
    const std::vector<std::string> named = sa2_install_refusals(report);
    final_refusals.insert(final_refusals.end(), named.begin(), named.end());
    if (!final_refusals.empty())
        return refuse(damaged_needed.empty() ? SA2_FAIL_REFUSED : SA2_FAIL_IO,
                      "refused: " + join_limited(final_refusals, "; ") + "; the partial store at " +
                          partial + " is kept, and a retry resumes it");

    // ---- the store's record: the table's files in table order, then the extras by path
    Sa2StoreRecord rec;
    rec.table_id = table.id ? table.id : "";
    rec.receipt_sha256 = table.receipt_sha256 ? table.receipt_sha256 : "";
    rec.source_kind = "zip";
    rec.source_bytes = a.size;
    rec.source_root = root;
    rec.markers = report.markers;
    for (size_t i = 0; i < table.count; i++) {
        const Sa2FileResult& fr = report.files[i];
        if (fr.kind == Sa2FileResult::Missing) {
            rec.missing.push_back(fr.path);
        } else if (fr.kind == Sa2FileResult::Modified) {
            rec.modified.push_back(fr.path);
        } else if (job_of[i] >= 0) {
            const JobResult& r = run.results[size_t(job_of[i])];
            Sa2StoreFile sf;
            sf.path = fr.path;
            sf.size = r.size;
            sf.sha256 = r.sha;
            sf.kind = fr.kind == Sa2FileResult::Ok ? "ok" : "substituted-orig";
            sf.from = r.from;
            rec.files.push_back(sf);
        }
    }
    std::sort(extra_files.begin(), extra_files.end(),
              [](const Sa2StoreFile& x, const Sa2StoreFile& y) { return x.path < y.path; });
    rec.files.insert(rec.files.end(), extra_files.begin(), extra_files.end());
    std::sort(d.extracted_extras.begin(), d.extracted_extras.end());

    // ---- user files: into the user_dir, never over a file there. Before the store's commit,
    // so a failure here leaves the partial store for the retry, which keeps what this one wrote.
    std::string err;
    if (!user_jobs.empty()) {
        const std::string tmp_dir = sa2_path_join(options.user_dir, kUserTmp);
        if (!sa2_fs_remove_tree(tmp_dir, &err) || !sa2_fs_mkdirs(tmp_dir, &err))
            return refuse(SA2_FAIL_IO, err);
        std::set<std::string> synced;
        for (size_t u = 0; u < user_jobs.size(); u++) {
            const Job& job = user_jobs[u];
            const Entry& src = a.entries[size_t(job.sources[0])];
            const std::string dest = sa2_path_join(options.user_dir, job.dest);
            // Kept when anything is at its name, or a file stands where a directory of it goes.
            bool blocked = sa2_fs_kind(dest) != Sa2FsKind::None;
            for (size_t sl = job.dest.find('/'); !blocked && sl != std::string::npos;
                 sl = job.dest.find('/', sl + 1)) {
                const Sa2FsKind k = sa2_fs_kind(sa2_path_join(options.user_dir, job.dest.substr(0, sl)));
                blocked = k != Sa2FsKind::None && k != Sa2FsKind::Dir;
            }
            if (blocked) {
                d.user_files_kept.push_back(job.dest);
                continue;
            }
            const std::string parent = sa2_path_parent(dest);
            if (!sa2_fs_mkdirs(parent, &err)) return refuse(SA2_FAIL_IO, err);
            const std::string tmp = sa2_path_join(tmp_dir, std::to_string(u));
            const Extracted x = extract(a, src, tmp);
            if (x.io_error)
                return refuse(SA2_FAIL_IO, "the install stopped on an I/O error: " + x.why +
                                               "; the partial store at " + partial +
                                               " is kept, and a retry resumes it");
            if (!x.ok)
                return refuse(SA2_FAIL_IO, "refused: the archive is damaged at " +
                                               src.name.substr(root.size()) + ": " + x.why +
                                               "; the partial store at " + partial +
                                               " is kept, and a retry resumes it");
            bool existed = false;
            if (!sa2_fs_rename_noreplace(tmp, dest, &existed, &err)) {
                if (!existed) return refuse(SA2_FAIL_IO, err);
                sa2_fs_remove_file(tmp, nullptr);
                d.user_files_kept.push_back(job.dest);
                continue;
            }
            d.user_files_written.push_back(job.dest);
            d.bytes_written += x.size;
            synced.insert(parent);
        }
        for (const auto& dir : synced)
            if (!sa2_fs_sync_dir(dir, &err)) return refuse(SA2_FAIL_IO, err);
        if (!sa2_fs_remove_tree(tmp_dir, &err)) return refuse(SA2_FAIL_IO, err);
        d.fs_retries = sa2_fs_retry_count() - retries_before;
    }

    // ---- commit: the temporary directory gone, the record last, the marker removed, every
    // directory synced, then the rename
    if (!sa2_fs_remove_tree(sa2_path_join(partial, SA2_STORE_TMP_DIR), &err))
        return refuse(SA2_FAIL_IO, err);
    if (!sa2_store_write_record(partial, rec, &err)) return refuse(SA2_FAIL_IO, err);
    // From here the record, not the marker, says the partial store is this installer's.
    if (!sa2_fs_remove_file(sa2_path_join(partial, SA2_STORE_PARTIAL_MARKER), &err))
        return refuse(SA2_FAIL_IO, err);
    {
        std::vector<std::string> sub;
        if (!sa2_fs_walk(partial, nullptr, &sub, &err)) return refuse(SA2_FAIL_IO, err);
        for (auto it = sub.rbegin(); it != sub.rend(); ++it)
            if (!sa2_fs_sync_dir(sa2_path_join(partial, *it), &err)) return refuse(SA2_FAIL_IO, err);
        if (!sa2_fs_sync_dir(partial, &err)) return refuse(SA2_FAIL_IO, err);
    }
    const Sa2FsKind sk = sa2_fs_kind(store_dir);
    if (sk == Sa2FsKind::Dir) {
        // An earlier store (checked above to be this installer's) is replaced only now that
        // its successor is complete: moved aside, the new one renamed in, the old one removed.
        // A stop between the renames is repaired by the next run (the ".old" case above).
        std::vector<std::string> f, g;
        if (!sa2_fs_walk(store_dir, &f, &g, &err)) return refuse(SA2_FAIL_IO, err);
        if (f.empty() && g.empty()) {
            if (!sa2_fs_remove_tree(store_dir, &err)) return refuse(SA2_FAIL_IO, err);
        } else if (!sa2_fs_rename(store_dir, old, &err)) {
            return refuse(SA2_FAIL_IO, err);
        }
    }
    if (!sa2_fs_rename(partial, store_dir, &err)) return refuse(SA2_FAIL_IO, err);
    if (!sa2_fs_sync_dir(sa2_path_parent(store_dir), &err)) return refuse(SA2_FAIL_IO, err);
    // The new store is in place; an old one left behind is removed by the next run.
    if (sa2_fs_kind(old) != Sa2FsKind::None) sa2_fs_remove_tree(old, nullptr);
    d.store_record = sa2_path_join(store_dir, SA2_STORE_RECORD);
    d.fs_retries = sa2_fs_retry_count() - retries_before;
    return true;
}
