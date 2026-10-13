/* Self-test for __sa2_pc_round (pc_round.c), run natively on the x86-64 host.
 *
 * Three checks, each a count of mismatches over seeded random operands:
 *  1. reference: for random doubles, (double)__sa2_pc_round(v, 0x007F) equals
 *     RoundSignificand24(v) ported verbatim from
 *     Documentation/predecessor/SonicPreciseNativeMath.cs (the C# reference
 *     the design names). A double has 53 significand bits, so both round the
 *     same value at the same 24-bit position.
 *  2. hardware: for random binary32 operand pairs, the host x87 executes
 *     fadd/fsub/fmul/fdiv/fsqrt under control word 0x037F (PC64, what remill's
 *     x86_fp80 arithmetic models), the helper rounds that under 0x007F (PC24)
 *     and 0x027F (PC53), and the result is compared bit for bit with the same
 *     instruction executed under 0x007F / 0x027F directly. This is the pass's
 *     actual claim: op-at-64-then-helper == op-at-PC. fdiv and fsqrt are
 *     where a 64-then-24 double rounding could in principle differ; the count
 *     measures it.
 *  3. edges: zero, inf, nan, and a tie at the 24-bit boundary, plus the
 *     PC=11 identity.
 * Prints one JSON object; exit status is 0 only when the reference, edge and
 * PC24 hardware counts are all 0. The PC53 hardware counts are reported and
 * do not fail the test: remill's 64-bit result then a 53-bit rounding is a
 * double rounding that x87's single rounding to 53 bits avoids, so a small
 * residue is expected there (measured, see the receipt); at 24 bits the same
 * operands never hit a 64-bit tie (pc_round.c explains why).
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

long double __sa2_pc_round(long double v, uint16_t cw);

static double round_significand_24_csharp(double value) {   /* RoundSignificand24, SonicPreciseNativeMath.cs */
    if (value == 0.0 || isnan(value) || isinf(value)) return value;
    uint64_t bits; memcpy(&bits, &value, 8);
    uint64_t sign = bits & 0x8000000000000000ull;
    uint64_t magnitude = bits & 0x7fffffffffffffffull;
    const int dropped_bits = 29;
    const uint64_t dropped_mask = (1ull << dropped_bits) - 1ull;
    const uint64_t half = 1ull << (dropped_bits - 1);
    uint64_t retained_least_bit = (magnitude >> dropped_bits) & 1ull;
    magnitude += half - 1ull + retained_least_bit;
    magnitude &= ~dropped_mask;
    bits = sign | magnitude;
    memcpy(&value, &bits, 8);
    return value;
}

static uint64_t rng_state = 0x9E3779B97F4A7C15ull;
static uint64_t rnd(void) {   /* splitmix64 */
    uint64_t z = (rng_state += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}
static double rnd_double(void) {   /* random sign, exponent in [-60, 60], random 52-bit fraction */
    uint64_t frac = rnd() & 0x000FFFFFFFFFFFFFull;
    int e = (int)(rnd() % 121) - 60;
    uint64_t bits = ((uint64_t)(e + 1023) << 52) | frac | ((rnd() & 1) << 63);
    double d; memcpy(&d, &bits, 8); return d;
}
static float rnd_float(void) {   /* random sign, exponent in [-30, 30], random 23-bit fraction */
    uint32_t frac = (uint32_t)rnd() & 0x007FFFFFu;
    int e = (int)(rnd() % 61) - 30;
    uint32_t bits = ((uint32_t)(e + 127) << 23) | frac | ((uint32_t)(rnd() & 1) << 31);
    float f; memcpy(&f, &bits, 4); return f;
}

static int same80(long double a, long double b) { return memcmp(&a, &b, 10) == 0; }

typedef long double (*op_fn)(long double, long double);
static long double op_add(long double a, long double b) { long double r; __asm__ volatile("faddp" : "=t"(r) : "0"(b), "u"(a) : "st(1)"); return r; }
static long double op_sub(long double a, long double b) { long double r; __asm__ volatile("fsubp" : "=t"(r) : "0"(b), "u"(a) : "st(1)"); return r; }
static long double op_mul(long double a, long double b) { long double r; __asm__ volatile("fmulp" : "=t"(r) : "0"(b), "u"(a) : "st(1)"); return r; }
static long double op_div(long double a, long double b) { long double r; __asm__ volatile("fdivp" : "=t"(r) : "0"(b), "u"(a) : "st(1)"); return r; }
static long double op_sqrt(long double a, long double b) { long double r; (void)b; __asm__ volatile("fsqrt" : "=t"(r) : "0"(a)); return r; }

static long double under_cw(uint16_t cw, op_fn op, long double a, long double b) {
    uint16_t host; long double r;
    __asm__ volatile("fnstcw %0" : "=m"(host));
    __asm__ volatile("fldcw %0" : : "m"(cw));
    r = op(a, b);
    __asm__ volatile("fldcw %0" : : "m"(host));
    return r;
}

int main(void) {
    const int n = 200000;
    int ref_mismatch = 0;
    for (int i = 0; i < n; i++) {
        double d = rnd_double();
        double got = (double)__sa2_pc_round((long double)d, 0x007F);
        double want = round_significand_24_csharp(d);
        if (memcmp(&got, &want, 8) != 0) ref_mismatch++;
    }
    const char *names[5] = {"fadd", "fsub", "fmul", "fdiv", "fsqrt"};
    op_fn ops[5] = {op_add, op_sub, op_mul, op_div, op_sqrt};
    int hw_mismatch[5][2] = {{0}};
    uint16_t cws[2] = {0x007F, 0x027F};
    for (int k = 0; k < 5; k++) {
        for (int i = 0; i < n; i++) {
            long double a = rnd_float(), b = rnd_float();
            if (k == 4 && a < 0) a = -a;
            long double at64 = under_cw(0x037F, ops[k], a, b);
            for (int c = 0; c < 2; c++) {
                long double direct = under_cw(cws[c], ops[k], a, b);
                long double via = __sa2_pc_round(at64, cws[c]);
                if (!same80(direct, via)) hw_mismatch[k][c]++;
            }
        }
    }
    int edge = 0;
    long double z = 0.0L, nz = -0.0L, inf = INFINITY, nan_ = NAN;
    edge += !same80(__sa2_pc_round(z, 0x007F), z);
    edge += !same80(__sa2_pc_round(nz, 0x007F), nz);
    edge += !same80(__sa2_pc_round(inf, 0x007F), inf);
    edge += !same80(__sa2_pc_round(-inf, 0x007F), -inf);
    edge += !isnan(__sa2_pc_round(nan_, 0x007F));
    long double tie = 1.0L + 0x1p-24L;               /* exactly half an ulp at 24 bits: even -> 1.0 */
    edge += !same80(__sa2_pc_round(tie, 0x007F), 1.0L);
    long double tie_odd = 1.0L + 0x1p-23L + 0x1p-24L;  /* odd retained bit: rounds up to 1 + 2^-22 */
    edge += !same80(__sa2_pc_round(tie_odd, 0x007F), 1.0L + 0x1p-22L);
    long double above = 1.0L + 0x1p-24L + 0x1p-40L;   /* above the tie -> up */
    edge += !same80(__sa2_pc_round(above, 0x007F), 1.0L + 0x1p-23L);
    long double full64 = 1.0L + 0x1p-63L;
    edge += !same80(__sa2_pc_round(full64, 0x037F), full64);     /* PC64 identity */
    edge += !same80(__sa2_pc_round(full64, 0x027F), 1.0L);       /* PC53 rounds it away */
    long double max24 = 0x1.fffffep0L;                /* 24 ones: representable, unchanged */
    edge += !same80(__sa2_pc_round(max24, 0x007F), max24);
    edge += !same80(__sa2_pc_round(0x1.ffffffp0L, 0x007F), 2.0L); /* 25 ones: carry into the exponent */

    printf("{\"rows_per_check\": %d, \"reference_mismatch\": %d, \"hardware_mismatch\": {", n, ref_mismatch);
    for (int k = 0; k < 5; k++)
        printf("%s\"%s\": {\"pc24\": %d, \"pc53\": %d}", k ? ", " : "", names[k], hw_mismatch[k][0], hw_mismatch[k][1]);
    printf("}, \"edge_mismatch\": %d}\n", edge);
    int total = ref_mismatch + edge;
    for (int k = 0; k < 5; k++) total += hw_mismatch[k][0];
    return total ? 1 : 0;
}
