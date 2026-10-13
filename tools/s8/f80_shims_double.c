/* f80_shims_double.c: the shims of tools/s8/f80_double.py's double mode (the fast mode).
 *
 * Subtitle: tools/s7/f80_shims.c, included whole and unchanged (the exact mode's shims and the
 * x87 fenv calls the double mode also calls), and after it what the double mode adds. Compile
 * this file in place of tools/s7/f80_shims.c for a lifted image legalised by
 * tools/s8/f80_double.py: tools/s8/android_game.py `compile --f80 double` and
 * tools/s8/perf_f80.py `build --mode double` do.
 *
 * Why a file of its own: tools/s7/f80_shims.c and tools/s7/f80_legalize.py are pinned by sha256
 * in the arm64-linux target's receipts (Documentation/receipts/s7-target-arm64-linux-v1.json,
 * s7-arm64-linux-build-v1.json, s7-arm64-replays-v1.json), so an edit there, even one that
 * leaves the exact mode alone, unmeets that G7 target until all 24,036 aarch64 modules are
 * rebuilt and the qemu replays rerun. The double mode lives beside them instead.
 *
 * In the double mode the x87's registers hold binary64 values and its arithmetic is the host's
 * double arithmetic, inline in the lifted code. What the inline code does not do is here:
 *
 *   - `sa2_f80_state_double`, the marker: the runtime (runtime/remill_runtime.cpp, st_get and
 *     st_set, and the m80 calls) reads remill's register slots as doubles when it is linked,
 *     and as 80-bit images otherwise, so a runtime linked with one mode's objects cannot
 *     misread the other's;
 *   - the precision control's slow path: PC24 on a double denormal, rounded on the significand
 *     under the control word's RC with the exponent kept (the x87 does not narrow it); PC53 and
 *     PC64 are the identity on a double;
 *   - FRNDINT and FIST under the control word's RC (tools/s2/patch/x87_int.c's semantics on a
 *     double: the integer indefinite when the rounded value does not fit), and fptosi with
 *     truncation;
 *   - rint (the host's RC), trunc, floor, ceil, fmod and remainder, exactly;
 *   - the transcendentals, as the exact mode's double path computes them (design 3.4).
 * Every host floating-point operation here is exact (trunc, a difference of a value and its
 * truncation below 2^52, an increment of an integer), except the transcendentals' libm call,
 * so the host's rounding mode reaches no result except through RC where the x87 reads it.
 *
 *   source tools/env.sh
 *   zig cc -target aarch64-linux-musl -O2 -c tools/s8/f80_shims_double.c -o f80_shims.o
 */
#include "../s7/f80_shims.c"

SA2_F80_API const int sa2_f80_state_double = 1;

static int d_round_up(unsigned rc, unsigned sign, uint64_t low, uint64_t half, uint64_t lsb) {
    switch (rc) {
    case 0: return low > half || (low == half && lsb);   /* nearest, ties to even */
    case 1: return sign && low != 0;                     /* toward -inf */
    case 2: return !sign && low != 0;                    /* toward +inf */
    default: return 0;                                   /* toward zero */
    }
}

SA2_F80_API double sa2_df80_pc_round_slow(double v, uint16_t cw) {
    const unsigned pc = (cw >> 8) & 3u, rc = (cw >> 10) & 3u;
    if (pc != 0) return v;              /* 53 or 64 bits (01 reserved): a double holds no more */
    uint64_t b;
    memcpy(&b, &v, 8);
    const unsigned sign = (unsigned)(b >> 63);
    int e = (int)((b >> 52) & 0x7FFu);
    uint64_t m = b & ((1ull << 52) - 1);
    if (e == 0x7FF) return v;           /* inf, nan */
    if (e == 0) {
        if (!m) return v;               /* +-0 */
        const int sh = __builtin_clzll(m) - 11;     /* a denormal double, normalised */
        m <<= sh;
        e = 1 - sh;
    } else {
        m |= 1ull << 52;
    }
    /* value = m * 2^(e - 1075), m with 53 significant bits; keep 24, drop 29 */
    const uint64_t mask = (1ull << 29) - 1, low = m & mask;
    uint64_t kept = m & ~mask;
    if (low && d_round_up(rc, sign, low, 1ull << 28, (kept >> 29) & 1u)) {
        kept += 1ull << 29;
        if (kept >> 53) {
            kept >>= 1;
            e += 1;
        }
    }
    const double r = ldexp((double)kept, e - 1075);
    return sign ? -r : r;
}

/* v rounded to an integer under x87 RC `rc`, exactly; +-0 keeps v's sign, as FRNDINT does. */
static double d_rint_rc(double v, unsigned rc) {
    if (!(fabs(v) < 4503599627370496.0)) return v;      /* >= 2^52 (integral), inf, nan */
    const double t = trunc(v), frac = v - t;            /* both exact */
    double r = t;
    if (frac != 0) {
        switch (rc) {
        case 0: {
            const double af = fabs(frac);
            if (af > 0.5 || (af == 0.5 && ((int64_t)t & 1))) r = v < 0 ? t - 1 : t + 1;
            break;
        }
        case 1: if (frac < 0) r = t - 1; break;
        case 2: if (frac > 0) r = t + 1; break;
        default: break;
        }
    }
    return r == 0 ? copysign(0.0, v) : r;
}

SA2_F80_API double sa2_df80_round_int(double v, uint16_t cw) { return d_rint_rc(v, (cw >> 10) & 3u); }

/* The integer indefinite (INT_MIN of the width) for a NaN, an infinity or a value that does not
 * fit once rounded; `hi` is the first value above the range. */
static int64_t d_to_int(double r, double lo, double hi, int64_t indefinite) {
    if (!(r >= lo && r < hi)) return indefinite;
    return (int64_t)r;
}

SA2_F80_API int16_t sa2_df80_fistp_i16(double v, uint16_t cw) {
    return (int16_t)d_to_int(d_rint_rc(v, (cw >> 10) & 3u), -32768.0, 32768.0, INT16_MIN);
}
SA2_F80_API int32_t sa2_df80_fistp_i32(double v, uint16_t cw) {
    return (int32_t)d_to_int(d_rint_rc(v, (cw >> 10) & 3u), -2147483648.0, 2147483648.0, INT32_MIN);
}
SA2_F80_API int64_t sa2_df80_fistp_i64(double v, uint16_t cw) {
    return d_to_int(d_rint_rc(v, (cw >> 10) & 3u), -9223372036854775808.0, 9223372036854775808.0,
                    INT64_MIN);
}
SA2_F80_API int16_t sa2_df80_to_i16(double v) {
    return (int16_t)d_to_int(d_rint_rc(v, 3), -32768.0, 32768.0, INT16_MIN);
}
SA2_F80_API int32_t sa2_df80_to_i32(double v) {
    return (int32_t)d_to_int(d_rint_rc(v, 3), -2147483648.0, 2147483648.0, INT32_MIN);
}
SA2_F80_API int64_t sa2_df80_to_i64(double v) {
    return d_to_int(d_rint_rc(v, 3), -9223372036854775808.0, 9223372036854775808.0, INT64_MIN);
}

SA2_F80_API double sa2_df80_rint(double v) { return d_rint_rc(v, host_rc()); }
SA2_F80_API double sa2_df80_trunc(double v) { return d_rint_rc(v, 3); }
SA2_F80_API double sa2_df80_floor(double v) { return d_rint_rc(v, 1); }
SA2_F80_API double sa2_df80_ceil(double v) { return d_rint_rc(v, 2); }
SA2_F80_API double sa2_df80_fmod(double x, double y) { return fmod(x, y); }
SA2_F80_API double sa2_df80_remainder(double x, double y) { return remainder(x, y); }

static double d_call1(double (*fn)(double), double a) {
    const int mode = fegetround();
    if (mode != FE_TONEAREST) fesetround(FE_TONEAREST);
    const double r = fn(a);
    if (mode != FE_TONEAREST) fesetround(mode);
    return r;
}
static double d_call2(double (*fn)(double, double), double a, double b) {
    const int mode = fegetround();
    if (mode != FE_TONEAREST) fesetround(FE_TONEAREST);
    const double r = fn(a, b);
    if (mode != FE_TONEAREST) fesetround(mode);
    return r;
}
#define D1(name, k, fn) SA2_F80_API double sa2_df80_##name(double x) { t_count(k); return d_call1(fn, x); }
#define D2(name, k, fn) SA2_F80_API double sa2_df80_##name(double x, double y) { t_count(k); return d_call2(fn, x, y); }
D1(sin, T_SIN, sin)
D1(cos, T_COS, cos)
D1(tan, T_TAN, tan)
D1(atan, T_ATAN, atan)
D2(atan2, T_ATAN2, atan2)
D1(exp2, T_EXP2, exp2)
D1(log2, T_LOG2, log2)
D1(exp, T_EXP, exp)
D1(log, T_LOG, log)
D1(log10, T_LOG10, log10)
D2(pow, T_POW, pow)
D1(asin, T_ASIN, asin)
D1(acos, T_ACOS, acos)
#undef D1
#undef D2
