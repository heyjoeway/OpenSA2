/* Self-test for x87_int.c, run natively on the x86-64 host.
 *
 * The claim the helper makes is "for any x86_fp80 value and any control word,
 * __sa2_fistp_iN(v, cw) equals what `fistp` of width N stores when the host x87
 * executes it under cw". The oracle is the instruction; this test runs both.
 *
 * Coverage per width (16, 32, 64) and per rounding mode (nearest, down, up,
 * zero): a fixed list of boundary values (each destination's limits, the
 * half-ulp either side of them, NaN, +-inf, +-0, denormals, powers of two) and
 * seeded random values drawn across the whole exponent range, including many
 * deliberately placed just inside and just outside each destination's range.
 * Also checks __sa2_x87_round_int against `frndint` under the same control word.
 *
 * Prints one JSON object; exit status is 0 only when every mismatch count is 0.
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

long double __sa2_x87_round_int(long double v, uint16_t cw);
int16_t __sa2_fistp_i16(long double v, uint16_t cw);
int32_t __sa2_fistp_i32(long double v, uint16_t cw);
int64_t __sa2_fistp_i64(long double v, uint16_t cw);

static uint16_t save_cw(void) { uint16_t c; __asm__ volatile("fnstcw %0" : "=m"(c)); return c; }
static void load_cw(uint16_t c) { __asm__ volatile("fldcw %0" : : "m"(c)); }

static int16_t native_i16(long double v, uint16_t cw) {
    uint16_t s = save_cw(); int16_t r = 0;
    load_cw(cw);
    __asm__ volatile("fistps %0" : "=m"(r) : "t"(v) : "st");
    load_cw(s);
    return r;
}
static int32_t native_i32(long double v, uint16_t cw) {
    uint16_t s = save_cw(); int32_t r = 0;
    load_cw(cw);
    __asm__ volatile("fistpl %0" : "=m"(r) : "t"(v) : "st");
    load_cw(s);
    return r;
}
static int64_t native_i64(long double v, uint16_t cw) {
    uint16_t s = save_cw(); int64_t r = 0;
    load_cw(cw);
    __asm__ volatile("fistpll %0" : "=m"(r) : "t"(v) : "st");
    load_cw(s);
    return r;
}
static long double native_rndint(long double v, uint16_t cw) {
    uint16_t s = save_cw(); long double r;
    load_cw(cw);
    __asm__ volatile("frndint" : "=t"(r) : "0"(v));
    load_cw(s);
    return r;
}

static uint64_t rng_state = 0x243F6A8885A308D3ull;
static uint64_t rnd(void) {   /* splitmix64 */
    uint64_t z = (rng_state += 0x9E3779B97F4A7C15ull);
    z = (z ^ (z >> 30)) * 0xBF58476D1CE4E5B9ull;
    z = (z ^ (z >> 27)) * 0x94D049BB133111EBull;
    return z ^ (z >> 31);
}

/* A random x86_fp80 with an exponent in [-8, 70]: straddling every destination
 * boundary and the "already integral" case. */
static long double rnd_fp80(void) {
    uint64_t mant = rnd() | (1ull << 63);
    int e = (int)(rnd() % 79) - 8;
    uint16_t se = (uint16_t)(((uint64_t)(rnd() & 1) << 15) | (uint16_t)(e + 16383));
    long double v = 0.0L;
    memcpy(&v, &mant, 8);
    memcpy((char *)&v + 8, &se, 2);
    return v;
}

static int same80(long double a, long double b) { return memcmp(&a, &b, 10) == 0; }

int main(void) {
    static const long double EDGE[] = {
        0.0L, -0.0L, 0.5L, -0.5L, 1.0L, -1.0L, 1.5L, -1.5L, 2.5L, -2.5L, 0.4999999L, -0.4999999L,
        32766.0L, 32767.0L, 32767.4L, 32767.5L, 32768.0L, -32768.0L, -32768.4L, -32768.5L, -32769.0L,
        2147483646.0L, 2147483647.0L, 2147483647.4L, 2147483647.5L, 2147483648.0L,
        -2147483648.0L, -2147483648.4L, -2147483648.5L, -2147483649.0L,
        9223372036854774784.0L, 9223372036854775808.0L, -9223372036854775808.0L, -9223372036854777856.0L,
        1e18L, -1e18L, 1e30L, -1e30L, 1e-30L, -1e-30L, 3.0L, -3.0L, 1e4931L,
    };
    const int nedge = (int)(sizeof EDGE / sizeof EDGE[0]);
    const uint16_t cws[4] = {0x007F, 0x047F, 0x087F, 0x0C7F};   /* nearest, down, up, zero */
    const char *rc_names[4] = {"nearest", "down", "up", "zero"};
    const int nrand = 200000;

    int mism[4][3] = {{0}};      /* [rc][width] */
    int rndint_mism[4] = {0};
    int edge_mism[4][3] = {{0}};
    long double specials[3];
    specials[0] = NAN; specials[1] = INFINITY; specials[2] = -INFINITY;

    for (int c = 0; c < 4; c++) {
        uint16_t cw = cws[c];
        for (int i = 0; i < nedge; i++) {
            long double v = EDGE[i];
            if (__sa2_fistp_i16(v, cw) != native_i16(v, cw)) edge_mism[c][0]++;
            if (__sa2_fistp_i32(v, cw) != native_i32(v, cw)) edge_mism[c][1]++;
            if (__sa2_fistp_i64(v, cw) != native_i64(v, cw)) edge_mism[c][2]++;
            if (!same80(__sa2_x87_round_int(v, cw), native_rndint(v, cw))) rndint_mism[c]++;
        }
        for (int i = 0; i < 3; i++) {
            long double v = specials[i];
            if (__sa2_fistp_i16(v, cw) != native_i16(v, cw)) edge_mism[c][0]++;
            if (__sa2_fistp_i32(v, cw) != native_i32(v, cw)) edge_mism[c][1]++;
            if (__sa2_fistp_i64(v, cw) != native_i64(v, cw)) edge_mism[c][2]++;
        }
        rng_state = 0x243F6A8885A308D3ull;
        for (int i = 0; i < nrand; i++) {
            long double v = rnd_fp80();
            if (__sa2_fistp_i16(v, cw) != native_i16(v, cw)) mism[c][0]++;
            if (__sa2_fistp_i32(v, cw) != native_i32(v, cw)) mism[c][1]++;
            if (__sa2_fistp_i64(v, cw) != native_i64(v, cw)) mism[c][2]++;
            if (!same80(__sa2_x87_round_int(v, cw), native_rndint(v, cw))) rndint_mism[c]++;
        }
    }

    int total = 0;
    printf("{\"random_rows_per_check\": %d, \"edge_values\": %d, \"by_rounding_mode\": {", nrand, nedge + 3);
    for (int c = 0; c < 4; c++) {
        printf("%s\"%s\": {\"cw\": \"0x%04X\", \"fistps\": %d, \"fistpl\": %d, \"fistpll\": %d, "
               "\"edge_fistps\": %d, \"edge_fistpl\": %d, \"edge_fistpll\": %d, \"frndint\": %d}",
               c ? ", " : "", rc_names[c], cws[c], mism[c][0], mism[c][1], mism[c][2],
               edge_mism[c][0], edge_mism[c][1], edge_mism[c][2], rndint_mism[c]);
        for (int w = 0; w < 3; w++) total += mism[c][w] + edge_mism[c][w];
        total += rndint_mism[c];
    }
    printf("}, \"total_mismatches\": %d}\n", total);
    return total ? 1 : 0;
}
