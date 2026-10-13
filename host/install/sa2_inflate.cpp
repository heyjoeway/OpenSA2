// sa2_inflate.cpp: see sa2_inflate.h.
#include "sa2_inflate.h"

#include <cstring>
#include <memory>

// ------------------------------------------------------------------------------------ CRC-32

namespace {

struct CrcTables {
    uint32_t t[8][256];
    CrcTables() {
        for (uint32_t i = 0; i < 256; i++) {
            uint32_t c = i;
            for (int k = 0; k < 8; k++) c = (c & 1) ? 0xEDB88320u ^ (c >> 1) : c >> 1;
            t[0][i] = c;
        }
        for (uint32_t i = 0; i < 256; i++)
            for (int s = 1; s < 8; s++) t[s][i] = t[0][t[s - 1][i] & 0xFF] ^ (t[s - 1][i] >> 8);
    }
};

const CrcTables& crc_tables() {
    static const CrcTables tables;
    return tables;
}

inline uint32_t load_le32(const uint8_t* p) {
    return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}

inline uint64_t load_le64(const uint8_t* p) {
    return uint64_t(load_le32(p)) | uint64_t(load_le32(p + 4)) << 32;
}

}  // namespace

uint32_t sa2_crc32(uint32_t crc, const void* data, size_t n) {
    const auto& T = crc_tables().t;
    const uint8_t* p = static_cast<const uint8_t*>(data);
    crc = ~crc;
    while (n >= 8) {
        const uint32_t a = load_le32(p) ^ crc;
        const uint32_t b = load_le32(p + 4);
        crc = T[7][a & 0xFF] ^ T[6][(a >> 8) & 0xFF] ^ T[5][(a >> 16) & 0xFF] ^ T[4][a >> 24] ^
              T[3][b & 0xFF] ^ T[2][(b >> 8) & 0xFF] ^ T[1][(b >> 16) & 0xFF] ^ T[0][b >> 24];
        p += 8;
        n -= 8;
    }
    while (n--) crc = T[0][(crc ^ *p++) & 0xFF] ^ (crc >> 8);
    return ~crc;
}

// ----------------------------------------------------------------------------------- inflate

namespace {

constexpr int kMaxBits = 15;
constexpr int kFastBits = 10;
constexpr size_t kWindow = 32768;
constexpr size_t kChunk = size_t(1) << 20;
constexpr size_t kMaxMatch = 258;
constexpr size_t kOutCap = kWindow + kChunk + kMaxMatch;
constexpr size_t kInCap = size_t(1) << 16;

const uint16_t kLenBase[29] = {3,  4,  5,  6,  7,  8,  9,  10, 11,  13,  15,  17,  19,  23, 27,
                               31, 35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258};
const uint8_t kLenExtra[29] = {0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2,
                               2, 3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
const uint16_t kDistBase[30] = {1,    2,    3,    4,    5,    7,     9,     13,    17,  25,
                                33,   49,   65,   97,   129,  193,   257,   385,   513, 769,
                                1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577};
const uint8_t kDistExtra[30] = {0, 0, 0, 0, 1, 1, 2, 2,  3,  3,  4,  4,  5,  5,  6,
                                6, 7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13};
const uint8_t kCodeOrder[19] = {16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15};

// A canonical Huffman code. `fast` maps the next kFastBits input bits (LSB first, as deflate
// packs them) to (symbol << 4) | length for every code of at most kFastBits bits; 0 sends the
// decoder to the canonical walk over `count` and `symbol`, which also finds the longer codes.
struct Huff {
    uint16_t fast[1 << kFastBits];
    uint16_t count[kMaxBits + 1];
    uint16_t symbol[288];
    bool empty;       // no code at all
    bool codelen;     // the code-length code (zlib reads an empty one as symbol 0, one bit)
};

// zlib's inftrees.c rules. False: over-subscribed, or incomplete when that is not allowed.
bool build(Huff& h, const uint8_t* lens, int n, bool codelen) {
    std::memset(h.fast, 0, sizeof h.fast);
    std::memset(h.count, 0, sizeof h.count);
    h.codelen = codelen;
    for (int i = 0; i < n; i++) h.count[lens[i]]++;
    h.count[0] = 0;
    int max = kMaxBits;
    while (max >= 1 && h.count[max] == 0) max--;
    h.empty = max == 0;
    if (h.empty) return true;
    int left = 1;
    for (int len = 1; len <= kMaxBits; len++) {
        left <<= 1;
        left -= h.count[len];
        if (left < 0) return false;
    }
    if (left > 0 && (codelen || max != 1)) return false;

    uint16_t offs[kMaxBits + 2];
    offs[1] = 0;
    for (int len = 1; len <= kMaxBits; len++) offs[len + 1] = uint16_t(offs[len] + h.count[len]);
    for (int s = 0; s < n; s++)
        if (lens[s]) h.symbol[offs[lens[s]]++] = uint16_t(s);

    uint32_t next[kMaxBits + 1];
    uint32_t code = 0;
    next[0] = 0;
    for (int len = 1; len <= kMaxBits; len++) {
        code = (code + h.count[len - 1]) << 1;
        next[len] = code;
    }
    for (int s = 0; s < n; s++) {
        const int len = lens[s];
        if (!len) continue;
        const uint32_t c = next[len]++;
        if (len > kFastBits) continue;
        uint32_t rev = 0;
        for (int i = 0; i < len; i++) rev |= ((c >> i) & 1u) << (len - 1 - i);
        const uint16_t e = uint16_t((s << 4) | len);
        for (uint32_t k = rev; k < (1u << kFastBits); k += 1u << len) h.fast[k] = e;
    }
    return true;
}

struct FixedCodes {
    Huff lit, dist;
    FixedCodes() {
        uint8_t l[288];
        for (int i = 0; i < 144; i++) l[i] = 8;
        for (int i = 144; i < 256; i++) l[i] = 9;
        for (int i = 256; i < 280; i++) l[i] = 7;
        for (int i = 280; i < 288; i++) l[i] = 8;
        build(lit, l, 288, false);
        uint8_t d[32];
        for (int i = 0; i < 32; i++) d[i] = 5;
        build(dist, d, 32, false);
    }
};

const FixedCodes& fixed_codes() {
    static const FixedCodes codes;
    return codes;
}

class Inflater {
public:
    explicit Inflater(Sa2InflateIo& io)
        : io_(io), in_(new uint8_t[kInCap]), out_(new uint8_t[kOutCap]) {}

    Sa2InflateResult run() {
        Sa2InflateResult r;
        r.status = blocks();
        r.why = why_;
        r.out_bytes = emitted_;
        const uint64_t real_bits = bc_ - uint64_t(phantom_) * 8;
        r.in_bytes = (total_read_ - (iend_ - ip_)) - real_bits / 8;
        if (r.status != Sa2InflateStatus::Ok && r.why.empty())
            r.why = r.status == Sa2InflateStatus::Truncated ? "the input ended inside the stream"
                                                             : "the sink stopped the decoder";
        return r;
    }

private:
    // ---------------------------------------------------------------- bits
    bool fetch() {
        if (eof_) return false;
        const size_t n = io_.read(in_.get(), kInCap);
        if (!n) {
            eof_ = true;
            return false;
        }
        ip_ = 0;
        iend_ = n;
        total_read_ += n;
        return true;
    }

    // At least 57 bits in the buffer. Past the end of the input the buffer is padded with zero
    // bytes, counted in phantom_; consuming any of them is how truncation is detected.
    void refill() {
        if (bc_ > 56) return;
        if (iend_ - ip_ >= 8) {
            bb_ |= load_le64(in_.get() + ip_) << bc_;
            ip_ += (63 - bc_) >> 3;
            bc_ |= 56;
            return;
        }
        while (bc_ <= 56) {
            if (ip_ == iend_ && !fetch()) {
                bc_ += 8;
                phantom_++;
                continue;
            }
            bb_ |= uint64_t(in_[ip_++]) << bc_;
            bc_ += 8;
        }
    }

    bool overran() const { return bc_ < phantom_ * 8u; }

    uint32_t bits(unsigned n) {  // n <= 32, caller refilled
        const uint32_t v = uint32_t(bb_ & ((uint64_t(1) << n) - 1));
        bb_ >>= n;
        bc_ -= n;
        return v;
    }

    // A symbol, or -1 for a code the table does not have. The caller refilled.
    int decode(const Huff& h) {
        const uint16_t e = h.fast[bb_ & ((1u << kFastBits) - 1)];
        if (e) {
            const unsigned len = e & 15u;
            bb_ >>= len;
            bc_ -= len;
            return e >> 4;
        }
        if (h.empty) {
            // zlib's table for an empty code is one entry of one bit: the code-length code reads
            // it as symbol 0, the others as an invalid code.
            bits(1);
            return h.codelen ? 0 : -1;
        }
        int code = 0, first = 0, index = 0;
        for (int len = 1; len <= kMaxBits; len++) {
            code |= int((bb_ >> (len - 1)) & 1u);
            const int count = h.count[len];
            if (code - count < first) {
                bits(unsigned(len));
                return h.symbol[index + (code - first)];
            }
            index += count;
            first += count;
            first <<= 1;
            code <<= 1;
        }
        // Not a code. zlib's table spends one bit on it (inftrees.c, the incomplete fill).
        bits(1);
        return -1;
    }

    // ---------------------------------------------------------------- output
    bool emit(const uint8_t* p, size_t n) {
        if (!n) return true;
        if (!io_.write(p, n)) return false;
        emitted_ += n;
        return true;
    }

    // Make room for `need` more bytes, keeping the last kWindow as history.
    bool room(size_t need) {
        if (pos_ + need <= kOutCap) return true;
        if (!emit(out_.get() + flushed_, pos_ - flushed_)) return false;
        std::memmove(out_.get(), out_.get() + pos_ - kWindow, kWindow);
        pos_ = kWindow;
        flushed_ = kWindow;
        return true;
    }

    Sa2InflateStatus bad(const char* why) {
        why_ = why;
        return Sa2InflateStatus::BadData;
    }

    // ---------------------------------------------------------------- blocks
    Sa2InflateStatus blocks() {
        for (;;) {
            refill();
            const uint32_t final_block = bits(1);
            const uint32_t type = bits(2);
            if (overran()) return Sa2InflateStatus::Truncated;
            Sa2InflateStatus s;
            if (type == 0) {
                s = stored();
            } else if (type == 1) {
                s = codes(fixed_codes().lit, fixed_codes().dist);
            } else if (type == 2) {
                s = dynamic();
            } else {
                return bad("invalid block type");
            }
            if (s != Sa2InflateStatus::Ok) return s;
            if (final_block) break;
        }
        if (!emit(out_.get() + flushed_, pos_ - flushed_)) return Sa2InflateStatus::OutputStopped;
        flushed_ = pos_;
        return Sa2InflateStatus::Ok;
    }

    Sa2InflateStatus stored() {
        bits(bc_ & 7u);
        refill();
        const uint32_t len = bits(16);
        const uint32_t nlen = bits(16);
        if (overran()) return Sa2InflateStatus::Truncated;
        if (len != (~nlen & 0xFFFFu)) return bad("invalid stored block lengths");
        size_t left = len;
        // Whole bytes still in the bit buffer come first; the padding is not input.
        while (left && bc_ >= 8u + phantom_ * 8u) {
            if (!room(1)) return Sa2InflateStatus::OutputStopped;
            out_[pos_++] = uint8_t(bits(8));
            left--;
        }
        if (!left) return Sa2InflateStatus::Ok;
        if (phantom_) return Sa2InflateStatus::Truncated;
        // The buffer is empty here (bc_ == 0); what remains comes straight from the input.
        bb_ = 0;
        bc_ = 0;
        while (left) {
            if (ip_ == iend_ && !fetch()) return Sa2InflateStatus::Truncated;
            size_t n = iend_ - ip_;
            if (n > left) n = left;
            if (n > kChunk) n = kChunk;
            if (!room(n)) return Sa2InflateStatus::OutputStopped;
            std::memcpy(out_.get() + pos_, in_.get() + ip_, n);
            pos_ += n;
            ip_ += n;
            left -= n;
        }
        return Sa2InflateStatus::Ok;
    }

    Sa2InflateStatus dynamic() {
        refill();
        const unsigned nlen = bits(5) + 257;
        const unsigned ndist = bits(5) + 1;
        const unsigned ncode = bits(4) + 4;
        if (overran()) return Sa2InflateStatus::Truncated;
        if (nlen > 286 || ndist > 30) return bad("too many length or distance symbols");

        uint8_t lens[320];
        std::memset(lens, 0, sizeof lens);
        for (unsigned i = 0; i < ncode; i++) {
            refill();
            lens[kCodeOrder[i]] = uint8_t(bits(3));
        }
        if (overran()) return Sa2InflateStatus::Truncated;
        if (!build(cl_, lens, 19, true)) return bad("invalid code lengths set");

        std::memset(lens, 0, sizeof lens);
        unsigned have = 0;
        while (have < nlen + ndist) {
            refill();
            const int sym = decode(cl_);
            if (sym < 16) {
                if (overran()) return Sa2InflateStatus::Truncated;
                if (sym < 0) return bad("invalid code lengths set");
                lens[have++] = uint8_t(sym);
                continue;
            }
            unsigned copy;
            uint8_t value = 0;
            if (sym == 16) {
                copy = 3 + bits(2);
                if (overran()) return Sa2InflateStatus::Truncated;
                if (have == 0) return bad("invalid bit length repeat");
                value = lens[have - 1];
            } else if (sym == 17) {
                copy = 3 + bits(3);
                if (overran()) return Sa2InflateStatus::Truncated;
            } else {
                copy = 11 + bits(7);
                if (overran()) return Sa2InflateStatus::Truncated;
            }
            if (have + copy > nlen + ndist) return bad("invalid bit length repeat");
            while (copy--) lens[have++] = value;
        }
        if (lens[256] == 0) return bad("invalid code -- missing end-of-block");
        if (!build(lit_, lens, int(nlen), false)) return bad("invalid literal/lengths set");
        if (!build(dist_, lens + nlen, int(ndist), false)) return bad("invalid distances set");
        return codes(lit_, dist_);
    }

    Sa2InflateStatus codes(const Huff& lit, const Huff& dist) {
        for (;;) {
            refill();
            if (!room(kMaxMatch)) return Sa2InflateStatus::OutputStopped;
            const int sym = decode(lit);
            if (phantom_ && overran()) return Sa2InflateStatus::Truncated;
            if (sym < 256) {
                if (sym < 0) return bad("invalid literal/length code");
                out_[pos_++] = uint8_t(sym);
                continue;
            }
            if (sym == 256) return Sa2InflateStatus::Ok;
            const int li = sym - 257;
            if (li >= 29) return bad("invalid literal/length code");
            const size_t len = kLenBase[li] + bits(kLenExtra[li]);
            if (phantom_ && overran()) return Sa2InflateStatus::Truncated;
            const int ds = decode(dist);
            if (phantom_ && overran()) return Sa2InflateStatus::Truncated;
            if (ds < 0 || ds >= 30) return bad("invalid distance code");
            const size_t d = kDistBase[ds] + bits(kDistExtra[ds]);
            if (phantom_ && overran()) return Sa2InflateStatus::Truncated;
            if (d > pos_) return bad("invalid distance too far back");
            uint8_t* o = out_.get() + pos_;
            const uint8_t* from = o - d;
            if (d >= len) {
                std::memcpy(o, from, len);
            } else if (d == 1) {
                std::memset(o, *from, len);
            } else {
                for (size_t i = 0; i < len; i++) o[i] = from[i];
            }
            pos_ += len;
        }
    }

    Sa2InflateIo& io_;
    std::unique_ptr<uint8_t[]> in_;
    std::unique_ptr<uint8_t[]> out_;
    size_t ip_ = 0, iend_ = 0;
    uint64_t total_read_ = 0;
    bool eof_ = false;
    uint64_t bb_ = 0;
    unsigned bc_ = 0;
    unsigned phantom_ = 0;
    size_t pos_ = 0, flushed_ = 0;
    uint64_t emitted_ = 0;
    const char* why_ = "";
    Huff cl_, lit_, dist_;
};

}  // namespace

Sa2InflateResult sa2_inflate(Sa2InflateIo& io) {
    std::unique_ptr<Inflater> d(new Inflater(io));
    return d->run();
}

const char* sa2_inflate_status_name(Sa2InflateStatus s) {
    switch (s) {
    case Sa2InflateStatus::Ok: return "ok";
    case Sa2InflateStatus::Truncated: return "truncated";
    case Sa2InflateStatus::BadData: return "bad-data";
    case Sa2InflateStatus::OutputStopped: return "output-stopped";
    }
    return "unknown";
}
