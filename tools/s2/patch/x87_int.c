/* __sa2_x87_round_int / __sa2_fistp_i16|i32|i64: x87's float-to-integer, for a post-lift IR pass.
 *
 * Design 3.4: "`fistp` family uses a helper reproducing x87's out-of-range
 * result (0x80000000) rather than the host's saturation." 699 sites in the
 * image (423 `fistpl`, 272 `fistpll`, 4 `fistps`; s1-opcode-histogram-v1.json).
 *
 * What remill v5.0.7 emits for FIST/FISTP (measured, tools/s2/patch/probes.py):
 * `llvm.nearbyint.f80` for the rounding, then `fptosi x86_fp80 to iN` for the
 * conversion, with an explicit `select` that substitutes INT_MIN on one side
 * only. Two things are borrowed from the host there. `nearbyint` follows the
 * host's rounding mode, which remill sets from a guest `fldcw` by calling
 * `fesetround`; and `fptosi` of a value outside the destination's range is
 * *poison* in LLVM, so what an out-of-range `fistp` produces is whatever the
 * backend's instruction selection happens to emit. On this host, at -O0 through
 * -O3, that happened to be x87's own answer (the receipt records the measurement);
 * poison is still not a semantics anyone can hold across compilers, and the
 * host-rounding-mode path is wrong the moment a guest control word is not the
 * one the host is in. This helper replaces both, so neither is borrowed.
 *
 * Everything here is integer arithmetic on the x86_fp80 bit pattern. There is no
 * floating-point operation in this file, for two reasons: the pass instruments
 * every `x86_fp80` arithmetic op it splices, so an op here would be rounded by
 * the precision-control helper; and a helper that computed in floating point
 * would inherit the host rounding mode it exists to avoid.
 *
 * Semantics (Intel SDM vol. 1, 8.3.2 and vol. 2 FIST/FISTP):
 *   - the source is rounded to an integer under the RC field of the control
 *     word (bits 10-11): nearest-even, down, up, toward zero. The precision
 *     control field does not apply: the destination is an integer.
 *   - if the rounded value does not fit the destination, or the source is a NaN
 *     or an infinity or an unsupported encoding, the invalid-operation
 *     exception is signalled; with IE masked (every control word in this game is
 *     0x_07F or 0x_27F, all exceptions masked) the destination receives the
 *     *integer indefinite* value: 0x8000, 0x80000000, 0x8000000000000000, which
 *     is INT_MIN of the destination width for all three.
 *   - the boundary is the destination's range *after* rounding, so
 *     2147483647.4 stores 2147483647 and 2147483647.5 stores 0x80000000 under
 *     round-to-nearest: the rounded value 2147483648 does not fit.
 *
 * tools/s2/patch/x87_int_test.c is the self-test: the same conversion executed by
 * this host's x87 under the same control word, over seeded random and boundary
 * values, for all three widths and all four rounding modes.
 */
#include <stdint.h>
#include <string.h>

#define INDEF16 ((int16_t)0x8000)
#define INDEF32 ((int32_t)0x80000000)
#define INDEF64 ((int64_t)0x8000000000000000ll)

/* Decompose an x86_fp80 into sign, 15-bit exponent field and the 64-bit
 * significand (the integer bit is explicit in this format). */
static void unpack80(long double v, unsigned *sign, unsigned *exp, uint64_t *mant) {
    uint64_t m;
    uint16_t se;
    memcpy(&m, &v, 8);
    memcpy(&se, (const char *)&v + 8, 2);
    *mant = m;
    *exp = se & 0x7FFFu;
    *sign = se >> 15;
}

static long double pack80(unsigned sign, unsigned exp, uint64_t mant) {
    long double v = 0.0L;
    uint16_t se = (uint16_t)((sign << 15) | (exp & 0x7FFFu));
    memcpy(&v, &mant, 8);
    memcpy((char *)&v + 8, &se, 2);
    return v;
}

/* Round `mant` (with `drop` low bits to discard) under rounding control `rc` and
 * sign `sign`; returns 1 when the magnitude must be incremented by one ulp at
 * the retained position. `low` is the discarded part, `half` the tie value and
 * `lsb` the retained least-significant bit. */
static int round_up(unsigned rc, unsigned sign, uint64_t low, uint64_t half, uint64_t lsb) {
    switch (rc) {
    case 0: return low > half || (low == half && lsb);   /* nearest, ties to even */
    case 1: return sign && low != 0;                     /* toward -inf */
    case 2: return !sign && low != 0;                    /* toward +inf */
    default: return 0;                                   /* toward zero */
    }
}

/* st(0) rounded to an integral value, in the same x86_fp80 format, under the
 * control word's RC field. This is what remill lowers to `llvm.nearbyint.f80`;
 * NaN, infinity and unsupported encodings pass through unchanged, as
 * `nearbyint` leaves them, and the conversion below turns them into the
 * indefinite value. */
long double __sa2_x87_round_int(long double v, uint16_t cw) {
    unsigned sign, exp, rc = (cw >> 10) & 3u;
    uint64_t mant;
    unpack80(v, &sign, &exp, &mant);
    if (exp == 0x7FFFu) return v;                        /* inf, nan */
    if (exp == 0 && mant == 0) return v;                 /* +-0 */
    int e = (int)exp - 16383;                            /* value = mant * 2^(e-63) */
    if (e >= 63) return v;                               /* already integral */
    if (e < 0) {                                         /* |v| < 1: 0 or +-1 */
        int up;
        if (rc == 0) {
            /* |v| > 0.5 rounds to 1; |v| == 0.5 rounds to 0 (even). |v| == 0.5
             * is exactly e == -1 with the significand 1.000...  */
            up = (e == -1) && (mant > (1ull << 63));
        } else {
            up = round_up(rc, sign, 1, 0, 0);
        }
        return up ? pack80(sign, 16383u, 1ull << 63) : pack80(sign, 0, 0);
    }
    unsigned drop = (unsigned)(63 - e);                  /* 1..63 fractional bits */
    uint64_t mask = (drop == 64) ? ~0ull : ((1ull << drop) - 1ull);
    uint64_t low = mant & mask;
    uint64_t kept = mant & ~mask;
    if (low == 0) return v;
    if (round_up(rc, sign, low, 1ull << (drop - 1u), (kept >> drop) & 1ull)) {
        uint64_t inc = kept + (1ull << drop);
        if (inc < kept) {                                /* carry out of bit 63 */
            kept = 1ull << 63;
            exp += 1u;
        } else {
            kept = inc;
        }
    } else {
        kept &= ~mask;
    }
    return pack80(sign, exp, kept);
}

/* The magnitude of an *already integral* x86_fp80 as a uint64, or 0 with
 * `*ok = 0` when it does not fit in 64 bits. */
static uint64_t integral_magnitude(long double v, int *ok, unsigned *sign_out) {
    unsigned sign, exp;
    uint64_t mant;
    *ok = 1;
    unpack80(v, &sign, &exp, &mant);
    *sign_out = sign;
    if (exp == 0x7FFFu) { *ok = 0; return 0; }           /* inf, nan */
    if (exp == 0) return 0;                              /* zero or denormal, already rounded to 0 */
    int e = (int)exp - 16383;
    if (e < 0) return 0;
    if (e > 63) { *ok = 0; return 0; }
    if (e == 63) return mant;                            /* value = mant, up to 2^64-1 */
    return mant >> (63 - e);                             /* the low bits are zero: the value is integral */
}

static int64_t convert(long double v, uint16_t cw, uint64_t limit_pos, uint64_t limit_neg, int64_t indefinite) {
    int ok;
    unsigned sign;
    long double r = __sa2_x87_round_int(v, cw);
    uint64_t mag = integral_magnitude(r, &ok, &sign);
    if (!ok) return indefinite;
    if (sign) {
        if (mag > limit_neg) return indefinite;
        return (int64_t)(0ull - mag);
    }
    if (mag > limit_pos) return indefinite;
    return (int64_t)mag;
}

int16_t __sa2_fistp_i16(long double v, uint16_t cw) {
    return (int16_t)convert(v, cw, 0x7FFFull, 0x8000ull, INDEF16);
}

int32_t __sa2_fistp_i32(long double v, uint16_t cw) {
    return (int32_t)convert(v, cw, 0x7FFFFFFFull, 0x80000000ull, INDEF32);
}

int64_t __sa2_fistp_i64(long double v, uint16_t cw) {
    return convert(v, cw, 0x7FFFFFFFFFFFFFFFull, 0x8000000000000000ull, INDEF64);
}
