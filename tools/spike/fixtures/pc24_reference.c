/* PC24 reference for the ten spike functions (tools/spike/fixtures).
 *
 * Each function's arithmetic is written in the installed instruction order
 * (private/generated/spike/functions/*.objdump) with two selectable models:
 *   pc24 : every basic x87 op computed in double and rounded to a 24-bit
 *          significand with RoundSignificand24 ported from
 *          Documentation/predecessor/SonicPreciseNativeMath.cs (the spec);
 *          sqrt = RoundSignificand24(sqrt(double)).
 *   fp80 : every op in long double (64-bit significand, the host x87 at its
 *          default control word), rounded only at the float stores; sqrt is
 *          fsqrt under control word 0x007F because the CRT's _CIsqrt keeps the
 *          guest precision bits (0x82c215: cw = (cw & 0x300) | 0x7F). This is
 *          what a lifter that ignores precision control computes; a lifted
 *          result equal to fp80 but not pc24 is the RESIDUE class.
 * Output rows follow shapes.h: status, exit_pc, payload. The exit a row is
 * predicted to take (a jump beyond the first ret, or a call to a helper no IR
 * provides) is emitted as status 1 with that address.
 *
 * Usage: pc24_reference {pc24|fp80} vectors.bin out.bin
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "shapes.h"
#include "vectors.h"

typedef long double real;
static int fp80;

static double round_significand_24(double value) {
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
static real R(double v) { return (real)round_significand_24(v); }
static real F(uint32_t w) { float f; memcpy(&f, &w, 4); return (real)f; }
static uint32_t W(real v) { float f = (float)v; uint32_t w; memcpy(&w, &f, 4); return w; }
static real MUL(real a, real b) { return fp80 ? a * b : R((double)a * (double)b); }
static real ADD(real a, real b) { return fp80 ? a + b : R((double)a + (double)b); }
static real DIV(real a, real b) { return fp80 ? a / b : R((double)a / (double)b); }
static real SQRT(real a) {
    if (fp80) {
        real r; uint16_t host, guest = 0x007F;
        __asm__ volatile("fnstcw %0" : "=m"(host));
        __asm__ volatile("fldcw %0" : : "m"(guest));
        __asm__ volatile("fsqrt" : "=t"(r) : "0"(a));
        __asm__ volatile("fldcw %0" : : "m"(host));
        return r;
    }
    return R(sqrt((double)a));
}

static void calc_vector(const uint32_t *m, const uint32_t *v, uint32_t *out) {
    real v0 = F(v[0]), v1 = F(v[1]), v2 = F(v[2]);
    uint32_t t0 = W(ADD(ADD(MUL(F(m[4]), v0), MUL(F(m[5]), v1)), MUL(F(m[6]), v2)));
    uint32_t t1 = W(ADD(ADD(MUL(F(m[8]), v0), MUL(F(m[9]), v1)), MUL(F(m[10]), v2)));
    out[0] = W(ADD(ADD(MUL(F(m[1]), v1), MUL(F(m[0]), v0)), MUL(F(m[2]), v2)));
    out[1] = t0; out[2] = t1;
}

static void calc_point(const uint32_t *m, const uint32_t *p, uint32_t flag, uint32_t *out) {
    real p0 = F(p[0]), p1 = F(p[1]), p2 = F(p[2]);
    uint32_t d[3];
    d[0] = W(ADD(ADD(MUL(F(m[1]), p1), MUL(F(m[0]), p0)), MUL(F(m[2]), p2)));
    d[1] = W(ADD(ADD(MUL(F(m[4]), p0), MUL(F(m[5]), p1)), MUL(F(m[6]), p2)));
    d[2] = W(ADD(ADD(MUL(F(m[8]), p0), MUL(F(m[9]), p1)), MUL(F(m[10]), p2)));
    if ((flag & 0xFF) == 0)
        for (int r = 0; r < 3; r++) d[r] = W(ADD(F(m[r * 4 + 3]), F(d[r])));
    memcpy(out, d, 12);
}

static const uint32_t IDENTITY[12] = {0x3F800000, 0, 0, 0, 0, 0x3F800000, 0, 0, 0, 0, 0x3F800000, 0};

static void run(uint32_t va, const uint32_t *in, uint32_t *out) {
    uint32_t *status = out, *exit_pc = out + 1, *pay = out + 2;
    *status = 0; *exit_pc = 0;
    switch (va) {
    case FN_CALC_VECTOR: calc_vector(in, in + 12, pay); break;
    case FN_CALC_POINT: calc_point(in, in + 12, in[15], pay); break;
    case FN_OBJECT_TREE:
        /* push = copy of the parent (no identity reset), then Translate(pos): the first
           Ninja helper, which no lifted IR provides. The stack top at that exit holds
           the parent copy. */
        memcpy(pay, in, 48); *status = 1; *exit_pc = 0x00427470u; break;
    case FN_CLAMP_YAW: {
        uint32_t a = in[0] & 0xFFFF, c = in[1] & 0xFFFF; int32_t lim = (int32_t)in[2];
        int32_t d = (int16_t)((a - c) & 0xFFFF);
        pay[0] = 0;
        if (!(d > lim) && d >= -lim) { *status = 1; *exit_pc = 0x004469A2u; break; }
        if ((((a - c) & 0xFFFF) & 0x8000) == 0) { *status = 1; *exit_pc = 0x0044699Au; break; }
        pay[0] = (uint32_t)((int32_t)c - lim) & 0xFFFF;
        break; }
    case FN_VECTOR_ANGLE: {
        const uint32_t *a = in, *b = in + 3;
        uint32_t s1 = W(MUL(F(a[1]), F(b[1]))), s0 = W(MUL(F(a[0]), F(b[0])));
        real sum = ADD(F(s1), F(s0));
        uint32_t s2 = W(MUL(F(a[2]), F(b[2])));
        uint32_t dot = W(ADD(sum, F(s2)));
        pay[1] = dot; pay[0] = 0;
        if (F(dot) <= -1.0L) pay[0] = 0x8000; else { *status = 1; *exit_pc = 0x00446A35u; }
        break; }
    case FN_UNIT_VECTOR: {
        real x = F(in[0]), y = F(in[1]), z = F(in[2]);
        real sq = ADD(ADD(MUL(y, y), MUL(x, x)), MUL(z, z));
        if (!(sq > 0)) sq = 0;
        uint32_t len = W(SQRT(sq));
        memcpy(pay, in, 12);
        if (!(F(len) > 0)) break;
        real inv = F(W(DIV(1.0L, F(len))));
        pay[0] = W(MUL(x, inv)); pay[1] = W(MUL(inv, y)); pay[2] = W(MUL(inv, z));
        break; }
    case FN_LENGTH: {
        real x = F(in[0]), y = F(in[1]), z = F(in[2]);
        uint32_t sx = W(MUL(x, x)), sy = W(MUL(y, y)), sz = W(MUL(z, z));
        real sum = ADD(ADD(F(sy), F(sx)), F(sz));
        pay[0] = 0;
        if (!(sum > 0)) { *status = 1; *exit_pc = 0x0077FC7Du; break; }
        pay[0] = W(SQRT(sum));
        break; }
    case FN_ROTATE_ENTITY: {
        uint32_t top = A_MSTACK + 0x30u * 3;   /* pushed from frame 2 */
        uint32_t angX = in[3], angY = in[4], angZ = in[5];
        memcpy(pay, in, 12); pay[3] = top;
        if (angZ) { *status = 1; *exit_pc = 0x00427680u; break; }
        if (angX) { *status = 1; *exit_pc = 0x004274E0u; break; }
        if ((uint32_t)(0u - angY)) { *status = 1; *exit_pc = 0x004275B0u; break; }
        calc_vector(IDENTITY, in, pay);
        pay[3] = top - 0x30u;
        break; }
    case FN_TRANSFORM_DIR: {
        uint32_t top = A_MSTACK + 0x30u * 3;
        uint32_t yaw = in[6], angle1 = in[7], angle2 = in[8];
        memcpy(pay, in + 3, 12); pay[3] = top;
        if (angle2) { *status = 1; *exit_pc = 0x00427680u; break; }
        if (angle1) { *status = 1; *exit_pc = 0x004274E0u; break; }
        if ((uint32_t)(0u - yaw)) { *status = 1; *exit_pc = 0x004275B0u; break; }
        calc_vector(IDENTITY, in + 3, pay);
        pay[3] = top - 0x30u;
        break; }
    case FN_PUSH_IDENTITY: {
        uint32_t k = in[12] % A_MSTACK_FRAMES, lim = in[13] % A_MSTACK_FRAMES;
        uint32_t top = A_MSTACK + 0x30u * k, limit = A_MSTACK + 0x30u * lim;
        if (top + 0x30u <= limit) top += 0x30u;
        pay[0] = 1; pay[1] = top;
        memcpy(pay + 2, IDENTITY, 48);
        break; }
    default: *status = 3; break;
    }
}

int main(int argc, char **argv) {
    if (argc != 4) { fprintf(stderr, "usage: %s {pc24|fp80} vectors.bin out.bin\n", argv[0]); return 2; }
    fp80 = strcmp(argv[1], "fp80") == 0;
    vectors_t v;
    if (vectors_read(argv[2], &v) != 0) return 1;
    const shape_t *sh = shape_for(v.va);
    if (!sh || sh->in_words != v.in_words || sh->out_words != v.out_words) { fprintf(stderr, "shape mismatch\n"); return 1; }
    uint32_t words = 2 + v.out_words;
    for (uint32_t r = 0; r < v.rows; r++)
        run(v.va, v.in + (size_t)r * v.in_words, v.out + (size_t)r * words);
    return vectors_write(argv[3], &v);
}
