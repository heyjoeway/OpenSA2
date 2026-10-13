// sha256.cpp: see sha256.h.
#include "sha256.h"

#if defined(__x86_64__) || defined(__i386__)
#include <cpuid.h>
#include <immintrin.h>
#endif

#include <cstdio>
#include <cstring>

namespace {

const uint32_t K[64] = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};

inline uint32_t ror(uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

void blocks_portable(uint32_t h[8], const uint8_t* p, size_t nblocks) {
    for (; nblocks--; p += 64) {
        uint32_t w[64];
        for (int i = 0; i < 16; i++)
            w[i] = uint32_t(p[4 * i]) << 24 | uint32_t(p[4 * i + 1]) << 16 | uint32_t(p[4 * i + 2]) << 8 | p[4 * i + 3];
        for (int i = 16; i < 64; i++) {
            const uint32_t s0 = ror(w[i - 15], 7) ^ ror(w[i - 15], 18) ^ (w[i - 15] >> 3);
            const uint32_t s1 = ror(w[i - 2], 17) ^ ror(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
        for (int i = 0; i < 64; i++) {
            const uint32_t S1 = ror(e, 6) ^ ror(e, 11) ^ ror(e, 25);
            const uint32_t ch = (e & f) ^ (~e & g);
            const uint32_t t1 = hh + S1 + ch + K[i] + w[i];
            const uint32_t S0 = ror(a, 2) ^ ror(a, 13) ^ ror(a, 22);
            const uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
            const uint32_t t2 = S0 + mj;
            hh = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }
}

#if defined(__x86_64__) || defined(__i386__)
// The SHA extensions: four rounds per sha256rnds2 pair, the message schedule
// four words at a time (W[g] from W[g-4..g-1], as Intel's reference does it).
__attribute__((target("sha,sse4.1,ssse3")))
void blocks_shani(uint32_t h[8], const uint8_t* p, size_t nblocks) {
    const __m128i MASK = _mm_set_epi64x(0x0c0d0e0f08090a0bULL, 0x0405060700010203ULL);
    __m128i tmp = _mm_loadu_si128(reinterpret_cast<const __m128i*>(&h[0]));
    __m128i state1 = _mm_loadu_si128(reinterpret_cast<const __m128i*>(&h[4]));
    tmp = _mm_shuffle_epi32(tmp, 0xB1);                  // CDAB
    state1 = _mm_shuffle_epi32(state1, 0x1B);            // EFGH
    __m128i state0 = _mm_alignr_epi8(tmp, state1, 8);    // ABEF
    state1 = _mm_blend_epi16(state1, tmp, 0xF0);         // CDGH
    for (; nblocks--; p += 64) {
        const __m128i abef = state0, cdgh = state1;
        __m128i w[4];
        for (int g = 0; g < 16; g++) {
            if (g < 4) {
                w[g] = _mm_shuffle_epi8(_mm_loadu_si128(reinterpret_cast<const __m128i*>(p + 16 * g)), MASK);
            } else {
                __m128i t = _mm_sha256msg1_epu32(w[g & 3], w[(g + 1) & 3]);            // W[g-4], W[g-3]
                t = _mm_add_epi32(t, _mm_alignr_epi8(w[(g + 3) & 3], w[(g + 2) & 3], 4)); // W[g-1], W[g-2]
                w[g & 3] = _mm_sha256msg2_epu32(t, w[(g + 3) & 3]);
            }
            __m128i msg = _mm_add_epi32(w[g & 3], _mm_loadu_si128(reinterpret_cast<const __m128i*>(&K[4 * g])));
            state1 = _mm_sha256rnds2_epu32(state1, state0, msg);
            msg = _mm_shuffle_epi32(msg, 0x0E);
            state0 = _mm_sha256rnds2_epu32(state0, state1, msg);
        }
        state0 = _mm_add_epi32(state0, abef);
        state1 = _mm_add_epi32(state1, cdgh);
    }
    tmp = _mm_shuffle_epi32(state0, 0x1B);               // FEBA
    state1 = _mm_shuffle_epi32(state1, 0xB1);            // DCHG
    state0 = _mm_blend_epi16(tmp, state1, 0xF0);         // DCBA
    state1 = _mm_alignr_epi8(state1, tmp, 8);            // HGFE
    _mm_storeu_si128(reinterpret_cast<__m128i*>(&h[0]), state0);
    _mm_storeu_si128(reinterpret_cast<__m128i*>(&h[4]), state1);
}

bool cpu_has_sha() {
    unsigned a = 0, b = 0, c = 0, d = 0;
    if (!__get_cpuid(1, &a, &b, &c, &d)) return false;
    const bool ssse3 = c & (1u << 9), sse41 = c & (1u << 19);
    if (!__get_cpuid_count(7, 0, &a, &b, &c, &d)) return false;
    return ssse3 && sse41 && (b & (1u << 29));
}
#else
void blocks_shani(uint32_t h[8], const uint8_t* p, size_t nblocks) { blocks_portable(h, p, nblocks); }
bool cpu_has_sha() { return false; }
#endif

bool g_force_portable = false;

}  // namespace

bool Sha256::hardware() {
    static const bool has = cpu_has_sha();
    return has && !g_force_portable;
}

void Sha256::force_portable(bool on) { g_force_portable = on; }

Sha256::Sha256() {
    static const uint32_t init[8] = {0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
                                     0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19};
    std::memcpy(h_, init, sizeof h_);
}

void Sha256::blocks(const uint8_t* p, size_t nblocks) {
    if (hardware()) blocks_shani(h_, p, nblocks);
    else blocks_portable(h_, p, nblocks);
}

void Sha256::update(const void* data, size_t n) {
    const uint8_t* p = static_cast<const uint8_t*>(data);
    total_ += n;
    if (fill_) {
        const size_t take = n < 64 - fill_ ? n : 64 - fill_;
        std::memcpy(buf_ + fill_, p, take);
        fill_ += take;
        p += take;
        n -= take;
        if (fill_ < 64) return;
        blocks(buf_, 1);
        fill_ = 0;
    }
    if (n >= 64) {
        blocks(p, n / 64);
        p += n / 64 * 64;
        n %= 64;
    }
    if (n) {
        std::memcpy(buf_, p, n);
        fill_ = n;
    }
}

std::string Sha256::hex() {
    const uint64_t bits = total_ * 8;
    uint8_t pad[72] = {0x80};
    const size_t padn = (fill_ < 56 ? 56 - fill_ : 120 - fill_);
    for (int i = 0; i < 8; i++) pad[padn + i] = uint8_t(bits >> (56 - 8 * i));
    update(pad, padn + 8);
    char out[65];
    for (int i = 0; i < 8; i++) std::snprintf(out + 8 * i, 9, "%08x", h_[i]);
    return std::string(out, 64);
}

std::string sha256_file(const std::string& path) {
    FILE* f = std::fopen(path.c_str(), "rb");
    if (!f) return "";
    Sha256 s;
    static uint8_t buf[1 << 20];
    size_t n;
    while ((n = std::fread(buf, 1, sizeof buf, f)) > 0) s.update(buf, n);
    const bool bad = std::ferror(f);
    std::fclose(f);
    return bad ? "" : s.hex();
}
