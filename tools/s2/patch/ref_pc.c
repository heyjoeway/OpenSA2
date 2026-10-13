/* S2 patch: a precision-parameterised reference for the functions the PC53 column drives.
 *
 * tools/spike/fixtures/pc24_reference.c (read-only here) has exactly two models,
 * `pc24` and `fp80`, because no S1 fixture ever varied the control word from
 * 0x007F. The PC53 column needs a third: the same instruction sequence with
 * every basic op rounded once, at 53 significand bits. This file is that
 * reference, written in the S2 harness's vector format (tools/s2/patch/
 * s2_harness.cpp) so its rows line up with the lifted run field for field.
 *
 * The models:
 *   pc24  every basic op computed in `double` and then rounded to a 24-bit
 *         significand by RoundSignificand24 (the design's spec, ported from
 *         Documentation/predecessor/SonicPreciseNativeMath.cs), which is what
 *         pc24_reference.c does.
 *   pc53  every basic op computed in plain `double`. IEEE binary64 arithmetic
 *         is a single rounding to 53 significand bits, so this is x87 at PC53
 *         for every operand that is itself a double and every result inside
 *         binary64's exponent range -- which every value in these functions is,
 *         because they load and store binary32. That makes it the *single
 *         rounding* reference the lifted run is measured against: the pass
 *         rounds remill's already-64-bit-rounded result a second time, and the
 *         gap between the two columns is exactly that double rounding.
 *   pc64  every basic op in `long double`: the host x87 at PC64, what remill
 *         computes with no pass at all.
 *
 * Only the two functions the PC53 column drives are modelled. Both are leaves
 * apart from the CRT's _CIsqrt (0x008290A0), which keeps the guest precision
 * bits (`cw = (cw & 0x300) | 0x7F`), so the square root is taken at the model's
 * precision too.
 *
 * Usage: ref_pc {pc24|pc53|pc64} {length|unit_vector} vectors.bin out.bin
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define PREFIX 7
#define MODEL_PC24 24
#define MODEL_PC53 53
#define MODEL_PC64 64

typedef struct {
    char magic[8];
    uint32_t va, rows, in_words, out_words, cw, stack_args;
    uint32_t reg_eax, reg_ecx, reg_edx, reg_esi, reg_edi, flags;
} header_t;

static int model = MODEL_PC24;

static double round_significand_24(double value) {   /* RoundSignificand24, SonicPreciseNativeMath.cs */
    uint64_t bits, sign, magnitude;
    const int dropped = 29;
    const uint64_t mask = ((uint64_t)1 << dropped) - 1, half = (uint64_t)1 << (dropped - 1);
    if (value == 0.0 || isnan(value) || isinf(value)) return value;
    memcpy(&bits, &value, 8);
    sign = bits & 0x8000000000000000ull;
    magnitude = bits & 0x7fffffffffffffffull;
    magnitude += half - 1 + ((magnitude >> dropped) & 1);
    magnitude &= ~mask;
    bits = sign | magnitude;
    memcpy(&value, &bits, 8);
    return value;
}

typedef long double real;

static real MUL(real a, real b) {
    if (model == MODEL_PC64) return a * b;
    if (model == MODEL_PC53) return (real)((double)a * (double)b);
    return (real)round_significand_24((double)a * (double)b);
}
static real ADD(real a, real b) {
    if (model == MODEL_PC64) return a + b;
    if (model == MODEL_PC53) return (real)((double)a + (double)b);
    return (real)round_significand_24((double)a + (double)b);
}
static real DIV(real a, real b) {
    if (model == MODEL_PC64) return a / b;
    if (model == MODEL_PC53) return (real)((double)a / (double)b);
    return (real)round_significand_24((double)a / (double)b);
}
static real SQRT(real a) {
    if (model == MODEL_PC64) {
        real r; uint16_t host, guest = 0x037F;
        __asm__ volatile("fnstcw %0" : "=m"(host));
        __asm__ volatile("fldcw %0" : : "m"(guest));
        __asm__ volatile("fsqrt" : "=t"(r) : "0"(a));
        __asm__ volatile("fldcw %0" : : "m"(host));
        return r;
    }
    if (model == MODEL_PC53) return (real)sqrt((double)a);
    return (real)round_significand_24(sqrt((double)a));
}

static real F(uint32_t w) { float f; memcpy(&f, &w, 4); return (real)f; }
static uint32_t W(real v) { float f = (float)v; uint32_t w; memcpy(&w, &f, 4); return w; }

static void st_to_words(long double v, uint32_t *o) {
    uint8_t raw[10]; memcpy(raw, &v, 10);
    memcpy(&o[0], raw, 4); memcpy(&o[1], raw + 4, 4);
    o[2] = (uint32_t)(raw[8] | (raw[9] << 8));
}

/* 0x0077FC30 length(eax=v) -> st(0); the fstp/fld pair at 0x77FC71 rounds the
 * result to binary32 before it is reloaded, so st(0) at the ret is a float. */
static void length(const uint32_t *in, uint32_t *o, uint32_t out_words) {
    real x = F(in[0]), y = F(in[1]), z = F(in[2]);
    uint32_t sx = W(MUL(x, x)), sy = W(MUL(y, y)), sz = W(MUL(z, z));
    real sum = ADD(ADD(F(sy), F(sx)), F(sz));
    real r;
    if (!(sum > 0)) {
        /* 0x0077FC7D is past the function's first `ret`, so it is outside the lifted
         * byte range (the spike's sizing rule) and the harness records the row as
         * status 1 with that exit address. */
        o[0] = 1; o[1] = 0x0077FC7Du;
        return;
    }
    r = SQRT(sum);
    r = F(W(r));
    st_to_words(r, o + 3);
    if (out_words >= 1) o[PREFIX] = W(r);   /* the row has no arena words unless the caller asked for them */
}

/* 0x0077F3A0 njUnitVector(esi=v in/out): squares summed in x87, the root through
 * _CIsqrt, the reciprocal by fdivr, then three multiplies stored back to [esi]. */
static void unit_vector(const uint32_t *in, uint32_t *o, uint32_t out_words) {
    real x = F(in[0]), y = F(in[1]), z = F(in[2]);
    real sq = ADD(ADD(MUL(y, y), MUL(x, x)), MUL(z, z));
    if (!(sq > 0)) sq = 0;
    uint32_t len = W(SQRT(sq));
    if (out_words < 3) return;
    o[PREFIX] = in[0]; o[PREFIX + 1] = in[1]; o[PREFIX + 2] = in[2];
    st_to_words(F(len), o + 3);
    if (!(F(len) > 0)) return;
    real inv = F(W(DIV(1.0L, F(len))));
    o[PREFIX] = W(MUL(x, inv));
    o[PREFIX + 1] = W(MUL(inv, y));
    o[PREFIX + 2] = W(MUL(inv, z));
}

int main(int argc, char **argv) {
    if (argc != 5) { fprintf(stderr, "usage: %s {pc24|pc53|pc64} {length|unit_vector} vectors.bin out.bin\n", argv[0]); return 2; }
    if (!strcmp(argv[1], "pc24")) model = MODEL_PC24;
    else if (!strcmp(argv[1], "pc53")) model = MODEL_PC53;
    else if (!strcmp(argv[1], "pc64")) model = MODEL_PC64;
    else { fprintf(stderr, "unknown model %s\n", argv[1]); return 2; }
    FILE *f = fopen(argv[3], "rb");
    if (!f) { perror(argv[3]); return 1; }
    header_t h;
    if (fread(&h, sizeof h, 1, f) != 1 || memcmp(h.magic, "SA2S2V1", 8) != 0) { fprintf(stderr, "bad vector file\n"); return 1; }
    size_t nin = (size_t)h.rows * h.in_words;
    uint32_t *in = calloc(nin ? nin : 1, 4);
    if (fread(in, 4, nin, f) != nin) { fprintf(stderr, "short vector file\n"); return 1; }
    fclose(f);
    uint32_t words = PREFIX + h.out_words;
    uint32_t *out = calloc((size_t)h.rows * words, 4);
    for (uint32_t r = 0; r < h.rows; r++) {
        uint32_t *o = out + (size_t)r * words;
        o[6] = h.cw;
        if (!strcmp(argv[2], "length")) length(in + (size_t)r * h.in_words, o, h.out_words);
        else if (!strcmp(argv[2], "unit_vector")) unit_vector(in + (size_t)r * h.in_words, o, h.out_words);
        else { fprintf(stderr, "unknown function %s\n", argv[2]); return 2; }
    }
    FILE *g = fopen(argv[4], "wb");
    if (!g) { perror(argv[4]); return 1; }
    uint32_t hdr[3] = {h.va, h.rows, words};
    fwrite("SA2S2O1\0", 1, 8, g);
    fwrite(hdr, 4, 3, g);
    fwrite(out, 4, (size_t)h.rows * words, g);
    fclose(g);
    return 0;
}
