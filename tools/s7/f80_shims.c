/* f80_shims.c: x87 extended-precision arithmetic in portable C, for lifted IR built for a
 * host whose long double is not the x87's (aarch64; tools/s7/f80_legalize.py).
 *
 * The lifted IR computes every x87 operation in LLVM's x86_fp80 type. An x86-64 build lowers
 * that to the host's own x87, so the guest's arithmetic is the hardware's. The AArch64 backend
 * cannot lower x86_fp80 faithfully: it aborts on fpext, turns fcmp into PowerPC __gcc_q*
 * calls, passes x86_fp80 to libm *l functions whose long double is binary128, and hands
 * fesetround the x86's rounding constants (agents-journals S7 port survey, section 2).
 * tools/s7/f80_legalize.py rewrites each such operation into a call to a function here; the
 * 80-bit value travels as the low 80 bits of an unsigned __int128 (bits 0-63 the significand
 * with its explicit integer bit, 64-78 the biased exponent, 79 the sign: the value's
 * little-endian memory image, which is what `bitcast x86_fp80 to i80` gives on every target).
 *
 * What each function computes is what the host's x87 computes for the one operation it
 * stands for, byte for byte, at the host's precision control (64 bits; the lifted code never
 * changes the host's PC, tools/spike/pc24pass rounds to the guest's PC afterwards). That is a
 * claim about each operation, not about a whole module: the x86-64 build's optimiser folds
 * some x86_fp80 operations away under IEEE identities (`fmul x, 1.0` to `x`), which a call
 * here is opaque to, so the two builds of a module can differ where such a site meets a
 * signalling NaN, an unsupported encoding or a zero under a directed rounding control
 * (tools/s7/f80_legalize.py, "Two divergence classes"):
 *
 *   - add, sub, mul, div, sqrt: the exact result rounded once to 64 significand bits in the
 *     15-bit exponent range, under the rounding control the host's fenv holds (the runtime
 *     and remill's FLDCW keep the guest's RC there: runtime/remill_runtime.cpp, "guest
 *     rounding control"), with gradual underflow, masked overflow, x87's NaN rules and the
 *     real indefinite for an invalid operation or an unsupported encoding;
 *   - compares: IEEE relations; a NaN or an unsupported encoding is unordered;
 *   - conversions: FLD m32/m64 (exact, a signaling NaN quieted), FST m32/m64 (rounded under
 *     RC), FIST with truncation (the integer indefinite out of range), FRNDINT;
 *   - fmodl, remainderl: musl's x86-64 loops over FPREM and FPREM1, so the exact remainders;
 *   - the transcendentals (sinl, cosl, tanl, atanl, atan2l, exp2l, log2l, ...): design 3.4's
 *     float model, "Transcendentals call host libm in double and round; labelled residue":
 *     the argument rounded to double to nearest, the host's double function under
 *     round-to-nearest, the double result widened exactly. Compiled with
 *     -DSA2_F80_TRANSC_X87 on an x86 host, they call the host's own long double functions
 *     instead, which is what the x86-64 build does: that variant exists only so that a
 *     behaviour-neutrality run can separate the transcendental residue from everything else.
 *   - fesetround, fegetround and the exception calls: the x86's FE_* constants (the x87 RC
 *     field in place, 0x000/0x400/0x800/0xC00; the x87 exception bits) mapped to the host's.
 *
 * No function here uses the host's floating point, except the transcendentals' libm call and
 * the fenv calls. Everything else is integer arithmetic on the bit pattern, so a host's FPU
 * (its NaN propagation, its flush-to-zero, its contraction) cannot reach the result.
 */
#include <fenv.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef unsigned __int128 u128;

/* With -DSA2_F80_COUNT every exported function counts its calls, and SA2_F80_STATS's file
 * gets them under "ops": what a run actually executed through the shims. With -DSA2_F80_SITES
 * each transcendental also counts its calls by call site, the return address its caller left
 * (`sites`), which tools/s7/arm64_replays.py reads back to the lifted function holding the
 * call. Both are off by default: the counters are a measurement build's, not the port's, and
 * neither touches a value any shim computes. */
#ifdef SA2_F80_COUNT
enum {OPC_SA2_F80_ADD, OPC_SA2_F80_SUB, OPC_SA2_F80_MUL, OPC_SA2_F80_DIV, OPC_SA2_F80_SQRT, OPC_SA2_F80_CMP, OPC_SA2_F80_FROM_F32, OPC_SA2_F80_FROM_F64, OPC_SA2_F80_TO_F32, OPC_SA2_F80_TO_F64, OPC_SA2_F80_TO_I16, OPC_SA2_F80_TO_I32, OPC_SA2_F80_TO_I64, OPC_SA2_F80_FROM_I64, OPC_SA2_F80_RINT, OPC_SA2_F80_TRUNC, OPC_SA2_F80_FLOOR, OPC_SA2_F80_CEIL, OPC_SA2_F80_FMOD, OPC_SA2_F80_REMAINDER, OPC_SA2_X87_FESETROUND, OPC_SA2_X87_FEGETROUND, OPC_SA2_X87_FETESTEXCEPT, OPC_SA2_X87_FERAISEEXCEPT, OPC_SA2_X87_FECLEAREXCEPT, OPC_COUNT};
static const char *const OPC_NAME[OPC_COUNT] = {"sa2_lf80_add", "sa2_lf80_sub", "sa2_lf80_mul", "sa2_lf80_div", "sa2_lf80_sqrt", "sa2_lf80_cmp", "sa2_lf80_from_f32", "sa2_lf80_from_f64", "sa2_lf80_to_f32", "sa2_lf80_to_f64", "sa2_lf80_to_i16", "sa2_lf80_to_i32", "sa2_lf80_to_i64", "sa2_lf80_from_i64", "sa2_lf80_rint", "sa2_lf80_trunc", "sa2_lf80_floor", "sa2_lf80_ceil", "sa2_lf80_fmod", "sa2_lf80_remainder", "sa2_x87_fesetround", "sa2_x87_fegetround", "sa2_x87_fetestexcept", "sa2_x87_feraiseexcept", "sa2_x87_feclearexcept"};
static uint64_t opc_calls[OPC_COUNT];
#define OPC(k) __atomic_fetch_add(&opc_calls[OPC_##k], 1, __ATOMIC_RELAXED)
#else
#define OPC(k) ((void)0)
#endif

#define SA2_F80_API __attribute__((visibility("default")))

enum { ZERO, DENORM, NORMAL, INF, QNAN, SNAN, UNSUP };

#define BIAS 16383
#define EMAX 0x7FFF
#define TOP (1ull << 63)
#define QBIT (1ull << 62)

typedef struct {
    unsigned s;     /* sign */
    unsigned ef;    /* biased exponent field */
    uint64_t m;     /* significand with the explicit integer bit */
} f80;

static inline f80 unpk(u128 w) {
    f80 x;
    x.m = (uint64_t)w;
    x.ef = (unsigned)(w >> 64) & 0x7FFFu;
    x.s = (unsigned)(w >> 79) & 1u;
    return x;
}

static inline u128 pk(unsigned s, unsigned ef, uint64_t m) {
    return ((u128)(((s & 1u) << 15) | (ef & 0x7FFFu)) << 64) | m;
}

static inline u128 indefinite(void) { return pk(1, EMAX, TOP | QBIT); }

/* x87 operand classes (Intel SDM vol. 1, 8.2.2): an exponent field of zero is a zero, a
 * denormal or a pseudo-denormal (integer bit set), the last two both worth m * 2^-16445; a
 * clear integer bit anywhere else is an unnormal, a pseudo-infinity or a pseudo-NaN, which
 * the 387 and later refuse as invalid operands. */
static int cls(f80 x) {
    if (x.ef == 0) return x.m == 0 ? ZERO : DENORM;
    if (!(x.m & TOP)) return UNSUP;
    if (x.ef == EMAX) {
        if ((x.m << 1) == 0) return INF;
        return (x.m & QBIT) ? QNAN : SNAN;
    }
    return NORMAL;
}

static inline int isnan_c(int c) { return c == QNAN || c == SNAN; }

/* A finite nonzero operand as value = M * 2^(E - 63) with M's bit 63 set. */
static void norm(f80 x, int32_t *E, uint64_t *M) {
    int32_t e = x.ef ? (int32_t)x.ef - BIAS : 1 - BIAS;
    uint64_t m = x.m;
    int lz = __builtin_clzll(m);
    *M = m << lz;
    *E = e - lz;
}

static inline u128 quiet(f80 x) { return pk(x.s, x.ef, x.m | QBIT); }

/* The x87's rules for a NaN result of a two-operand arithmetic instruction (Intel SDM vol. 1,
 * table 4-7): a QNaN over an SNaN; of two NaNs of one kind, the one with the larger
 * significand; an SNaN is quieted. Two significands that are equal are a tie the table does
 * not settle; the hardware returns the positive NaN whichever operand it is (measured on
 * this machine's x87 for fadd, fsub, fmul, fdiv, fprem and fprem1, both operand orders:
 * tools/s7/f80_legalize.py selftest pairs every magnitude with its negation). */
static u128 nan2(f80 a, int ca, f80 b, int cb) {
    if (isnan_c(ca) && isnan_c(cb)) {
        if (ca == SNAN && cb == QNAN) return quiet(b);
        if (ca == QNAN && cb == SNAN) return quiet(a);
        uint64_t ma = a.m | QBIT, mb = b.m | QBIT;
        if (mb > ma) return quiet(b);
        if (ma > mb) return quiet(a);
        return a.s ? quiet(b) : quiet(a);
    }
    return isnan_c(ca) ? quiet(a) : quiet(b);
}

static unsigned host_rc(void) {
    switch (fegetround()) {
#ifdef FE_DOWNWARD
    case FE_DOWNWARD: return 1;
#endif
#ifdef FE_UPWARD
    case FE_UPWARD: return 2;
#endif
#ifdef FE_TOWARDZERO
    case FE_TOWARDZERO: return 3;
#endif
    default: return 0;
    }
}

static inline int round_up(unsigned rc, unsigned s, int guard, int sticky, uint64_t lsb) {
    switch (rc) {
    case 0: return guard && (sticky || (lsb & 1));
    case 1: return s && (guard || sticky);
    case 2: return !s && (guard || sticky);
    default: return 0;
    }
}

static u128 overflow(unsigned s, unsigned rc) {
    int inf = rc == 0 || (rc == 1 && s) || (rc == 2 && !s);
    return inf ? pk(s, EMAX, TOP) : pk(s, EMAX - 1, ~0ull);
}

/* Round value = R * 2^(E - 127), R's bit 127 set, to the extended format under `rc`. */
static u128 round_pack(unsigned s, int32_t E, u128 R, unsigned rc) {
    int32_t ef = E + BIAS;
    unsigned sh = 64;
    if (ef >= EMAX) return overflow(s, rc);
    if (ef <= 0) {                       /* denormal: bit 0 has the weight of field 1's */
        int32_t extra = 1 - ef;
        sh = (extra > 70) ? 200u : 64u + (unsigned)extra;
        ef = 0;
    }
    uint64_t M;
    int guard, sticky;
    if (sh >= 129) {
        M = 0; guard = 0; sticky = R != 0;
    } else if (sh == 128) {
        M = 0; guard = (int)(R >> 127) & 1; sticky = (R << 1) != 0;
    } else {
        M = (uint64_t)(R >> sh);
        guard = (int)(R >> (sh - 1)) & 1;
        sticky = (R & (((u128)1 << (sh - 1)) - 1)) != 0;
    }
    if (round_up(rc, s, guard, sticky, M)) {
        M += 1;
        if (M == 0) {                    /* carry out of bit 63 */
            M = TOP;
            ef += 1;
            if (ef >= EMAX) return overflow(s, rc);
        } else if (ef == 0 && (M & TOP)) {
            ef = 1;                      /* a denormal rounded up into the smallest normal */
        }
    }
    return pk(s, (unsigned)ef, M);
}

/* ---------------------------------------------------------------- add, sub */

static u128 addsub(u128 aw, u128 bw, int negate_b, unsigned rc) {
    f80 a = unpk(aw), b = unpk(bw);
    int ca = cls(a), cb = cls(b);
    if (ca == UNSUP || cb == UNSUP) return indefinite();
    if (isnan_c(ca) || isnan_c(cb)) return nan2(a, ca, b, cb);
    unsigned sb = b.s ^ (unsigned)negate_b;
    if (ca == INF || cb == INF) {
        if (ca == INF && cb == INF) return a.s == sb ? pk(a.s, EMAX, TOP) : indefinite();
        return ca == INF ? pk(a.s, EMAX, TOP) : pk(sb, EMAX, TOP);
    }
    if (ca == ZERO && cb == ZERO) {
        unsigned s = (a.s == sb) ? a.s : (rc == 1);
        return pk(s, 0, 0);
    }
    if (cb == ZERO) { int32_t E; uint64_t M; norm(a, &E, &M); return round_pack(a.s, E, (u128)M << 64, rc); }
    if (ca == ZERO) { int32_t E; uint64_t M; norm(b, &E, &M); return round_pack(sb, E, (u128)M << 64, rc); }
    int32_t Ea, Eb;
    uint64_t Ma, Mb;
    unsigned sa = a.s;
    norm(a, &Ea, &Ma);
    norm(b, &Eb, &Mb);
    if (Eb > Ea || (Eb == Ea && Mb > Ma)) {      /* |a| >= |b| from here */
        int32_t te = Ea; Ea = Eb; Eb = te;
        uint64_t tm = Ma; Ma = Mb; Mb = tm;
        unsigned ts = sa; sa = sb; sb = ts;
    }
    uint32_t d = (uint32_t)(Ea - Eb);
    u128 A = (u128)Ma << 62;                     /* bit 125 has weight 2^Ea */
    u128 B = (u128)Mb << 62;
    if (d) {
        if (d >= 126) B = 1;                     /* only a sticky bit survives */
        else {
            u128 lost = B & (((u128)1 << d) - 1);
            B = (B >> d) | (lost != 0);
        }
    }
    u128 S;
    unsigned s = sa;
    if (sa == sb) S = A + B;
    else S = A - B;
    if (S == 0) return pk(rc == 1, 0, 0);       /* exact cancellation */
    int lz = (S >> 64) ? __builtin_clzll((uint64_t)(S >> 64)) : 64 + __builtin_clzll((uint64_t)S);
    /* S's leading bit at 127 - lz has weight 2^(Ea + (127 - lz) - 125) */
    int32_t E = Ea + (127 - lz) - 125;
    u128 R = S << lz;
    return round_pack(s, E, R, rc);
}

SA2_F80_API u128 sa2_lf80_add(u128 a, u128 b) { OPC(SA2_F80_ADD); return addsub(a, b, 0, host_rc()); }
SA2_F80_API u128 sa2_lf80_sub(u128 a, u128 b) { OPC(SA2_F80_SUB); return addsub(a, b, 1, host_rc()); }

/* ---------------------------------------------------------------- mul, div */

static u128 mul_rc(u128 aw, u128 bw, unsigned rc) {
    f80 a = unpk(aw), b = unpk(bw);
    int ca = cls(a), cb = cls(b);
    if (ca == UNSUP || cb == UNSUP) return indefinite();
    if (isnan_c(ca) || isnan_c(cb)) return nan2(a, ca, b, cb);
    unsigned s = a.s ^ b.s;
    if (ca == INF || cb == INF) {
        if (ca == ZERO || cb == ZERO) return indefinite();
        return pk(s, EMAX, TOP);
    }
    if (ca == ZERO || cb == ZERO) return pk(s, 0, 0);
    int32_t Ea, Eb;
    uint64_t Ma, Mb;
    norm(a, &Ea, &Ma);
    norm(b, &Eb, &Mb);
    u128 P = (u128)Ma * Mb;                      /* bit 126 has weight 2^(Ea + Eb) */
    int32_t E = Ea + Eb;
    if (P >> 127) E += 1;
    else P <<= 1;
    return round_pack(s, E, P, rc);
}

static u128 div_rc(u128 aw, u128 bw, unsigned rc) {
    f80 a = unpk(aw), b = unpk(bw);
    int ca = cls(a), cb = cls(b);
    if (ca == UNSUP || cb == UNSUP) return indefinite();
    if (isnan_c(ca) || isnan_c(cb)) return nan2(a, ca, b, cb);
    unsigned s = a.s ^ b.s;
    if (ca == INF) return cb == INF ? indefinite() : pk(s, EMAX, TOP);
    if (cb == INF) return pk(s, 0, 0);
    if (cb == ZERO) return ca == ZERO ? indefinite() : pk(s, EMAX, TOP);
    if (ca == ZERO) return pk(s, 0, 0);
    int32_t Ea, Eb;
    uint64_t Ma, Mb;
    norm(a, &Ea, &Ma);
    norm(b, &Eb, &Mb);
    int32_t E;
    u128 num;
    if (Ma >= Mb) { E = Ea - Eb; num = (u128)Ma << 63; }
    else { E = Ea - Eb - 1; num = (u128)Ma << 64; }
    u128 q1 = num / Mb, r1 = num % Mb;           /* q1 in [2^63, 2^64) */
    u128 n2 = r1 << 64;
    u128 q2 = n2 / Mb, r2 = n2 % Mb;
    u128 R = (q1 << 64) | q2 | (r2 != 0);
    return round_pack(s, E, R, rc);
}

SA2_F80_API u128 sa2_lf80_mul(u128 a, u128 b) { OPC(SA2_F80_MUL); return mul_rc(a, b, host_rc()); }
SA2_F80_API u128 sa2_lf80_div(u128 a, u128 b) { OPC(SA2_F80_DIV); return div_rc(a, b, host_rc()); }

/* ---------------------------------------------------------------- sqrt */

static u128 sqrt_rc(u128 aw, unsigned rc) {
    f80 a = unpk(aw);
    int ca = cls(a);
    if (ca == UNSUP) return indefinite();
    if (isnan_c(ca)) return quiet(a);
    if (ca == ZERO) return aw & (((u128)1 << 80) - 1);
    if (a.s) return indefinite();
    if (ca == INF) return pk(0, EMAX, TOP);
    int32_t E;
    uint64_t M;
    norm(a, &E, &M);
    /* value = M * 2^(E - 63) = N * 2^(E - 63 - sh) with an even exponent */
    unsigned sh = ((E - 63) & 1) ? 63 : 64;
    u128 N = (u128)M << sh;
    u128 rem = 0, root = 0;
    for (int i = 63; i >= 0; i--) {
        rem = (rem << 2) | ((N >> (2 * i)) & 3);
        u128 trial = (root << 2) | 1;
        if (rem >= trial) { rem -= trial; root = (root << 1) | 1; }
        else root <<= 1;
    }
    /* root in [2^63, 2^64); sqrt(N) = root + f, f in [0, 1), never exactly 1/2 */
    int32_t Er = 63 + (E - 63 - (int32_t)sh) / 2;
    u128 low = 0;
    if (rem > root) low = ((u128)1 << 63) | 1;
    else if (rem) low = 1;
    return round_pack(0, Er, ((u128)root << 64) | low, rc);
}

SA2_F80_API u128 sa2_lf80_sqrt(u128 a) { OPC(SA2_F80_SQRT); return sqrt_rc(a, host_rc()); }

/* ---------------------------------------------------------------- compare */

/* 0 equal, 1 less, 2 greater, 3 unordered; `mask` has bit k set for each outcome k the
 * fcmp predicate accepts, and the result is that bit. */
SA2_F80_API int sa2_lf80_cmp(u128 aw, u128 bw, int mask) { OPC(SA2_F80_CMP);
    f80 a = unpk(aw), b = unpk(bw);
    int ca = cls(a), cb = cls(b);
    int code;
    if (ca == UNSUP || cb == UNSUP || isnan_c(ca) || isnan_c(cb)) code = 3;
    else if (ca == ZERO && cb == ZERO) code = 0;
    else {
        /* a total order key on supported values: sign, then magnitude */
        int32_t Ea = 0, Eb = 0;
        uint64_t Ma = 0, Mb = 0;
        int za = ca == ZERO, zb = cb == ZERO;
        if (!za) { if (ca == INF) { Ea = 1 << 20; Ma = TOP; } else norm(a, &Ea, &Ma); }
        if (!zb) { if (cb == INF) { Eb = 1 << 20; Mb = TOP; } else norm(b, &Eb, &Mb); }
        int sa = za ? 0 : (a.s ? -1 : 1), sb = zb ? 0 : (b.s ? -1 : 1);
        if (sa != sb) code = sa < sb ? 1 : 2;
        else if (sa == 0) code = 0;
        else {
            int mag;                     /* -1, 0, 1: |a| against |b| */
            if (Ea != Eb) mag = Ea < Eb ? -1 : 1;
            else mag = Ma == Mb ? 0 : (Ma < Mb ? -1 : 1);
            if (sa < 0) mag = -mag;
            code = mag == 0 ? 0 : (mag < 0 ? 1 : 2);
        }
    }
    return (mask >> code) & 1;
}

/* ---------------------------------------------------------------- conversions */

/* FLD m32 / m64: exact; a signaling NaN is quieted with its payload kept. */
static u128 from_ieee(uint64_t bits, int mbits, int ebits) {
    unsigned s = (unsigned)(bits >> (mbits + ebits)) & 1u;
    unsigned e = (unsigned)(bits >> mbits) & ((1u << ebits) - 1u);
    uint64_t m = bits & ((1ull << mbits) - 1ull);
    int32_t bias = (1 << (ebits - 1)) - 1;
    if (e == (1u << ebits) - 1u) {
        if (m == 0) return pk(s, EMAX, TOP);
        return pk(s, EMAX, TOP | QBIT | (m << (63 - mbits)));
    }
    if (e == 0) {
        if (m == 0) return pk(s, 0, 0);
        int lz = __builtin_clzll(m);           /* value = m * 2^(1 - bias - mbits) */
        int32_t E = (1 - bias - mbits) + (63 - lz);
        return pk(s, (unsigned)(E + BIAS), m << lz);
    }
    return pk(s, (unsigned)((int32_t)e - bias + BIAS), TOP | (m << (63 - mbits)));
}

SA2_F80_API u128 sa2_lf80_from_f32(float f) { OPC(SA2_F80_FROM_F32);
    uint32_t u;
    memcpy(&u, &f, 4);
    return from_ieee(u, 23, 8);
}

SA2_F80_API u128 sa2_lf80_from_f64(double d) { OPC(SA2_F80_FROM_F64);
    uint64_t u;
    memcpy(&u, &d, 8);
    return from_ieee(u, 52, 11);
}

/* FST m32 / m64: rounded under `rc` to the destination's precision and range, with gradual
 * underflow and masked overflow; a NaN keeps the top of its payload, quieted; an unsupported
 * encoding stores the destination's indefinite. */
static uint64_t to_ieee(u128 w, int mbits, int ebits, unsigned rc) {
    f80 x = unpk(w);
    int c = cls(x);
    uint64_t sbit = (uint64_t)x.s << (mbits + ebits);
    uint64_t emaxf = (1ull << ebits) - 1ull;
    uint64_t qbit = 1ull << (mbits - 1);
    if (c == UNSUP) return (1ull << (mbits + ebits)) | (emaxf << mbits) | qbit;
    if (c == ZERO) return sbit;
    if (c == INF) return sbit | (emaxf << mbits);
    if (isnan_c(c)) return sbit | (emaxf << mbits) | qbit | ((x.m >> (63 - mbits)) & ((1ull << mbits) - 1ull));
    int32_t E;
    uint64_t M;
    norm(x, &E, &M);
    int32_t bias = (1 << (ebits - 1)) - 1;
    int32_t ef = E + bias;
    unsigned drop = 63u - (unsigned)mbits;      /* 40 or 11 */
    if (ef <= 0) {
        int32_t extra = 1 - ef;
        drop += (extra > 70) ? 70u : (unsigned)extra;
        ef = 0;
    }
    uint64_t kept, guard, sticky;
    if (drop >= 64) {
        kept = 0;
        guard = drop == 64 ? (M >> 63) : 0;
        sticky = drop == 64 ? ((M << 1) != 0) : (M != 0);
    } else {
        kept = M >> drop;
        guard = (M >> (drop - 1)) & 1;
        sticky = (M & ((1ull << (drop - 1)) - 1)) != 0;
    }
    if (round_up(rc, x.s, (int)guard, (int)sticky, kept)) {
        kept += 1;
        if (ef == 0) {
            if (kept >> mbits) ef = 1;          /* rounded into the smallest normal */
        } else if (kept >> (mbits + 1)) {
            kept >>= 1;
            ef += 1;
        }
    }
    if (ef >= (int32_t)emaxf) {
        int inf = rc == 0 || (rc == 1 && x.s) || (rc == 2 && !x.s);
        return inf ? (sbit | (emaxf << mbits)) : (sbit | ((emaxf - 1) << mbits) | ((1ull << mbits) - 1));
    }
    return sbit | ((uint64_t)ef << mbits) | (kept & ((1ull << mbits) - 1ull));
}

SA2_F80_API float sa2_lf80_to_f32(u128 w) { OPC(SA2_F80_TO_F32);
    uint32_t u = (uint32_t)to_ieee(w, 23, 8, host_rc());
    float f;
    memcpy(&f, &u, 4);
    return f;
}

SA2_F80_API double sa2_lf80_to_f64(u128 w) { OPC(SA2_F80_TO_F64);
    uint64_t u = to_ieee(w, 52, 11, host_rc());
    double d;
    memcpy(&d, &u, 8);
    return d;
}

static double to_f64_nearest(u128 w) {
    uint64_t u = to_ieee(w, 52, 11, 0);
    double d;
    memcpy(&d, &u, 8);
    return d;
}

/* fptosi: truncation toward zero (what the x86 backend's FISTP under a chop control word
 * stores), the integer indefinite (the destination's INT_MIN) when the value does not fit or
 * is a NaN, an infinity or an unsupported encoding. */
static int64_t to_int(u128 w, unsigned bits) {
    f80 x = unpk(w);
    int c = cls(x);
    int64_t indef = (int64_t)((uint64_t)1 << 63) >> (64 - bits);
    if (c == ZERO || c == DENORM) return 0;
    if (c != NORMAL) return indef;
    int32_t E = (int32_t)x.ef - BIAS;
    if (E < 0) return 0;
    if (E > 63) return indef;
    uint64_t mag = (E == 63) ? x.m : (x.m >> (63 - E));
    uint64_t lim = (uint64_t)1 << (bits - 1);
    if (x.s) {
        if (mag > lim) return indef;
        return (int64_t)(0 - mag);
    }
    if (mag >= lim) return indef;
    return (int64_t)mag;
}

SA2_F80_API int16_t sa2_lf80_to_i16(u128 w) { OPC(SA2_F80_TO_I16); return (int16_t)to_int(w, 16); }
SA2_F80_API int32_t sa2_lf80_to_i32(u128 w) { OPC(SA2_F80_TO_I32); return (int32_t)to_int(w, 32); }
SA2_F80_API int64_t sa2_lf80_to_i64(u128 w) { OPC(SA2_F80_TO_I64); return to_int(w, 64); }

/* FILD: exact. The pass widens every integer operand to i64 first. compiler-rt's
 * __floatsixf and __floatdixf compute the same values, but on aarch64 they and the backend
 * pass the x86_fp80 result in different places (tools/s7/f80_legalize.py, selftest). */
SA2_F80_API u128 sa2_lf80_from_i64(int64_t v) { OPC(SA2_F80_FROM_I64);
    if (v == 0) return 0;
    unsigned s = v < 0;
    uint64_t mag = s ? 0 - (uint64_t)v : (uint64_t)v;
    int lz = __builtin_clzll(mag);
    return pk(s, (unsigned)(BIAS + 63 - lz), mag << lz);
}

/* FRNDINT under `rc`: musl's x86-64 rintl is a bare frndint, and its floorl, ceill and
 * truncl are frndint under a control word whose RC field they set. */
static u128 rndint_rc(u128 w, unsigned rc) {
    f80 x = unpk(w);
    int c = cls(x);
    if (c == UNSUP) return indefinite();
    if (isnan_c(c)) return quiet(x);
    if (c == ZERO || c == INF) return pk(x.s, x.ef, x.m);
    int32_t E;
    uint64_t M;
    norm(x, &E, &M);
    if (E >= 63) return pk(x.s, x.ef, x.m);
    if (E < 0) {
        int up;
        if (rc == 0) up = (E == -1) && (M > TOP);
        else up = round_up(rc, x.s, 1, 1, 0);
        return up ? pk(x.s, BIAS, TOP) : pk(x.s, 0, 0);
    }
    unsigned drop = (unsigned)(63 - E);
    uint64_t mask = (1ull << drop) - 1;
    uint64_t low = M & mask, kept = M & ~mask;
    if (low == 0) return pk(x.s, (unsigned)(E + BIAS), M);
    int guard = (int)((low >> (drop - 1)) & 1);
    int sticky = (low & ((1ull << (drop - 1)) - 1)) != 0;
    if (round_up(rc, x.s, guard, sticky, kept >> drop)) {
        uint64_t inc = kept + (1ull << drop);
        if (inc < kept) { kept = TOP; E += 1; }
        else kept = inc;
    }
    return pk(x.s, (unsigned)(E + BIAS), kept);
}

SA2_F80_API u128 sa2_lf80_rint(u128 w) { OPC(SA2_F80_RINT); return rndint_rc(w, host_rc()); }
SA2_F80_API u128 sa2_lf80_trunc(u128 w) { OPC(SA2_F80_TRUNC); return rndint_rc(w, 3); }
SA2_F80_API u128 sa2_lf80_floor(u128 w) { OPC(SA2_F80_FLOOR); return rndint_rc(w, 1); }
SA2_F80_API u128 sa2_lf80_ceil(u128 w) { OPC(SA2_F80_CEIL); return rndint_rc(w, 2); }

/* ---------------------------------------------------------------- fmodl, remainderl */

/* FPREM (nearest = 0) or FPREM1 (nearest = 1) run to completion: the exact remainder, its
 * sign the dividend's, a zero remainder signed as the dividend. */
static u128 prem(u128 xw, u128 yw, int nearest) {
    f80 x = unpk(xw), y = unpk(yw);
    int cx = cls(x), cy = cls(y);
    if (cx == UNSUP || cy == UNSUP) return indefinite();
    if (isnan_c(cx) || isnan_c(cy)) return nan2(x, cx, y, cy);
    if (cx == INF || cy == ZERO) return indefinite();
    if (cx == ZERO) return pk(x.s, 0, 0);
    if (cy == INF) { int32_t E; uint64_t M; norm(x, &E, &M); return round_pack(x.s, E, (u128)M << 64, 0); }
    int32_t Ex, Ey;
    uint64_t Mx, My;
    norm(x, &Ex, &Mx);
    norm(y, &Ey, &My);
    unsigned s = x.s;
    if (Ex < Ey) {
        if (nearest && Ex == Ey - 1 && Mx > My) {
            /* |x| in (|y|/2, |y|): the quotient rounds to 1 */
            u128 r = ((u128)My << 1) - Mx;          /* weight 2^(Ex - 63) */
            int top = 127 - ((r >> 64) ? __builtin_clzll((uint64_t)(r >> 64)) : 64 + __builtin_clzll((uint64_t)r));
            int32_t E = Ex - 63 + top;
            return round_pack(s ^ 1u, E, r << (127 - top), 0);
        }
        return round_pack(s, Ex, (u128)Mx << 64, 0);
    }
    uint32_t d = (uint32_t)(Ex - Ey);
    u128 r = Mx % My;
    uint64_t qlow = (uint64_t)(Mx / My) & 1u;
    while (d) {
        unsigned k = d > 64 ? 64 : d;
        u128 n = r << k;
        qlow = (uint64_t)(n / My) & 1u;
        r = n % My;
        d -= k;
    }
    /* remainder = r * 2^(Ey - 63), 0 <= r < My */
    if (nearest) {
        u128 twice = r << 1;
        if (twice > My || (twice == My && qlow)) { r = My - r; s ^= 1u; }
    }
    if (r == 0) return pk(x.s, 0, 0);
    int top = 127 - ((r >> 64) ? __builtin_clzll((uint64_t)(r >> 64)) : 64 + __builtin_clzll((uint64_t)r));
    int32_t E = Ey - 63 + top;
    return round_pack(s, E, r << (127 - top), 0);
}

SA2_F80_API u128 sa2_lf80_fmod(u128 x, u128 y) { OPC(SA2_F80_FMOD); return prem(x, y, 0); }
SA2_F80_API u128 sa2_lf80_remainder(u128 x, u128 y) { OPC(SA2_F80_REMAINDER); return prem(x, y, 1); }

/* ---------------------------------------------------------------- transcendentals */

enum { T_SIN, T_COS, T_TAN, T_ATAN, T_ATAN2, T_EXP2, T_LOG2, T_EXP, T_LOG, T_LOG10, T_POW,
       T_ASIN, T_ACOS, T_COUNT };
static const char *const T_NAME[T_COUNT] = {"sinl", "cosl", "tanl", "atanl", "atan2l", "exp2l",
                                            "log2l", "expl", "logl", "log10l", "powl", "asinl",
                                            "acosl"};
static uint64_t t_calls[T_COUNT];

#ifdef SA2_F80_SITES
/* Per-site counts: an open-addressed table keyed by the caller's return address, filled with a
 * compare-and-swap so that guest threads on host threads share it. A site that finds no free
 * slot is counted in `site_overflow`, never dropped: the report says how many calls it could
 * not place, and a reader that needs every site refuses a non-zero overflow. The address of
 * sa2_lf80_sin is reported beside the sites (`anchor`), so a reader can relate the addresses to
 * the binary's symbol table whether or not the binary was loaded where it was linked. */
#define SITE_SLOTS 4096u
static uintptr_t site_ret[SITE_SLOTS];
static int site_kind[SITE_SLOTS];
static uint64_t site_calls[SITE_SLOTS];
static uint64_t site_overflow;

static void site_count(int k, uintptr_t ret) {
    uint64_t h = (uint64_t)ret * 0x9E3779B97F4A7C15ull;
    for (unsigned i = 0; i < SITE_SLOTS; i++) {
        unsigned s = (unsigned)((h >> 40) + i) & (SITE_SLOTS - 1u);
        uintptr_t cur = __atomic_load_n(&site_ret[s], __ATOMIC_ACQUIRE);
        if (cur == 0) {
            uintptr_t expected = 0;
            if (__atomic_compare_exchange_n(&site_ret[s], &expected, ret, 0, __ATOMIC_ACQ_REL,
                                            __ATOMIC_ACQUIRE)) {
                __atomic_store_n(&site_kind[s], k, __ATOMIC_RELAXED);
                cur = ret;
            } else {
                cur = expected;
            }
        }
        if (cur == ret) {
            __atomic_fetch_add(&site_calls[s], 1, __ATOMIC_RELAXED);
            return;
        }
    }
    __atomic_fetch_add(&site_overflow, 1, __ATOMIC_RELAXED);
}
#define T_COUNT_AT(k) do { t_count(k); site_count((k), (uintptr_t)__builtin_return_address(0)); } while (0)
#else
#define T_COUNT_AT(k) t_count(k)
#endif

#ifdef SA2_F80_SITES
u128 sa2_lf80_sin(u128 x);
#endif
static void t_report(void) {
    const char *path = getenv("SA2_F80_STATS");
    if (!path || !*path) return;
    FILE *f = fopen(path, "w");
    if (!f) return;
    fprintf(f, "{\"schema\": \"sa2-f80-transcendental-calls/v1\", \"semantics\": \"%s\", \"calls\": {",
#ifdef SA2_F80_TRANSC_X87
            "x87"
#else
            "double"
#endif
    );
    for (int i = 0; i < T_COUNT; i++)
        fprintf(f, "%s\"%s\": %llu", i ? ", " : "", T_NAME[i],
                (unsigned long long)__atomic_load_n(&t_calls[i], __ATOMIC_RELAXED));
    fprintf(f, "}");
#ifdef SA2_F80_SITES
    fprintf(f, ", \"anchor\": {\"symbol\": \"sa2_lf80_sin\", \"address\": \"0x%llx\"}",
            (unsigned long long)(uintptr_t)&sa2_lf80_sin);
    fprintf(f, ", \"site_overflow\": %llu, \"sites\": [",
            (unsigned long long)__atomic_load_n(&site_overflow, __ATOMIC_RELAXED));
    for (unsigned i = 0, n = 0; i < SITE_SLOTS; i++) {
        uintptr_t ret = __atomic_load_n(&site_ret[i], __ATOMIC_ACQUIRE);
        if (!ret) continue;
        fprintf(f, "%s{\"ret\": \"0x%llx\", \"fn\": \"%s\", \"calls\": %llu}", n++ ? ", " : "",
                (unsigned long long)ret, T_NAME[__atomic_load_n(&site_kind[i], __ATOMIC_RELAXED)],
                (unsigned long long)__atomic_load_n(&site_calls[i], __ATOMIC_RELAXED));
    }
    fprintf(f, "]");
#endif
#ifdef SA2_F80_COUNT
    fprintf(f, ", \"ops\": {");
    for (int i = 0; i < OPC_COUNT; i++)
        fprintf(f, "%s\"%s\": %llu", i ? ", " : "", OPC_NAME[i],
                (unsigned long long)__atomic_load_n(&opc_calls[i], __ATOMIC_RELAXED));
    fprintf(f, "}");
#endif
    fprintf(f, "}\n");
    fclose(f);
}

/* With SA2_F80_STATS naming a file, the counts are written there at exit, zeros included, so
 * a run that reached no transcendental says so rather than leaving no file. */
__attribute__((constructor)) static void t_init(void) {
    const char *path = getenv("SA2_F80_STATS");
    if (path && *path) atexit(t_report);
}

static void t_count(int k) { __atomic_fetch_add(&t_calls[k], 1, __ATOMIC_RELAXED); }


#if defined(SA2_F80_TRANSC_X87)
#if !(defined(__x86_64__) || defined(__i386__))
#error "SA2_F80_TRANSC_X87 needs a host whose long double is the x87's"
#endif
static inline long double ld(u128 w) { long double v = 0; memcpy(&v, &w, 10); return v; }
static inline u128 wd(long double v) { u128 w = 0; memcpy(&w, &v, 10); return w; }
#define T1(name, k, fn) SA2_F80_API u128 sa2_lf80_##name(u128 x) { T_COUNT_AT(k); return wd(fn##l(ld(x))); }
#define T2(name, k, fn) SA2_F80_API u128 sa2_lf80_##name(u128 x, u128 y) { T_COUNT_AT(k); return wd(fn##l(ld(x), ld(y))); }
#else
/* The host's double function under round-to-nearest, whatever RC the guest holds. */
static double call1(double (*fn)(double), double a) {
    int mode = fegetround();
    if (mode != FE_TONEAREST) fesetround(FE_TONEAREST);
    double r = fn(a);
    if (mode != FE_TONEAREST) fesetround(mode);
    return r;
}
static double call2(double (*fn)(double, double), double a, double b) {
    int mode = fegetround();
    if (mode != FE_TONEAREST) fesetround(FE_TONEAREST);
    double r = fn(a, b);
    if (mode != FE_TONEAREST) fesetround(mode);
    return r;
}
#define T1(name, k, fn) SA2_F80_API u128 sa2_lf80_##name(u128 x) { T_COUNT_AT(k); return sa2_lf80_from_f64(call1(fn, to_f64_nearest(x))); }
#define T2(name, k, fn) SA2_F80_API u128 sa2_lf80_##name(u128 x, u128 y) { T_COUNT_AT(k); return sa2_lf80_from_f64(call2(fn, to_f64_nearest(x), to_f64_nearest(y))); }
#endif
T1(sin, T_SIN, sin)
T1(cos, T_COS, cos)
T1(tan, T_TAN, tan)
T1(atan, T_ATAN, atan)
T2(atan2, T_ATAN2, atan2)
T1(exp2, T_EXP2, exp2)
T1(log2, T_LOG2, log2)
T1(exp, T_EXP, exp)
T1(log, T_LOG, log)
T1(log10, T_LOG10, log10)
T2(pow, T_POW, pow)
T1(asin, T_ASIN, asin)
T1(acos, T_ACOS, acos)

/* ---------------------------------------------------------------- fenv */

/* remill's FLDCW calls fesetround with the x87 RC field in place (cw & 0x0C00) and its
 * FNSTCW reads fegetround back the same way; on x86 those are the FE_* constants. */
SA2_F80_API int sa2_x87_fesetround(int x86) { OPC(SA2_X87_FESETROUND);
    int mode;
    switch (x86) {
    case 0x000: mode = FE_TONEAREST; break;
#ifdef FE_DOWNWARD
    case 0x400: mode = FE_DOWNWARD; break;
#endif
#ifdef FE_UPWARD
    case 0x800: mode = FE_UPWARD; break;
#endif
#ifdef FE_TOWARDZERO
    case 0xC00: mode = FE_TOWARDZERO; break;
#endif
    default: return -1;
    }
    return fesetround(mode);
}

SA2_F80_API int sa2_x87_fegetround(void) { OPC(SA2_X87_FEGETROUND); return (int)(host_rc() << 10); }

/* The x86's exception bits: invalid 1, denormal 2, zero-divide 4, overflow 8, underflow 16,
 * precision 32. The host has no denormal flag; the bit maps to nothing. */
static int exc_to_host(int x) {
    int h = 0;
#ifdef FE_INVALID
    if (x & 1) h |= FE_INVALID;
#endif
#ifdef FE_DIVBYZERO
    if (x & 4) h |= FE_DIVBYZERO;
#endif
#ifdef FE_OVERFLOW
    if (x & 8) h |= FE_OVERFLOW;
#endif
#ifdef FE_UNDERFLOW
    if (x & 16) h |= FE_UNDERFLOW;
#endif
#ifdef FE_INEXACT
    if (x & 32) h |= FE_INEXACT;
#endif
    return h;
}

static int exc_from_host(int h) {
    int x = 0;
#ifdef FE_INVALID
    if (h & FE_INVALID) x |= 1;
#endif
#ifdef FE_DIVBYZERO
    if (h & FE_DIVBYZERO) x |= 4;
#endif
#ifdef FE_OVERFLOW
    if (h & FE_OVERFLOW) x |= 8;
#endif
#ifdef FE_UNDERFLOW
    if (h & FE_UNDERFLOW) x |= 16;
#endif
#ifdef FE_INEXACT
    if (h & FE_INEXACT) x |= 32;
#endif
    return x;
}

SA2_F80_API int sa2_x87_fetestexcept(int x86) { OPC(SA2_X87_FETESTEXCEPT); return exc_from_host(fetestexcept(exc_to_host(x86))); }
SA2_F80_API int sa2_x87_feraiseexcept(int x86) { OPC(SA2_X87_FERAISEEXCEPT); return feraiseexcept(exc_to_host(x86)); }
SA2_F80_API int sa2_x87_feclearexcept(int x86) { OPC(SA2_X87_FECLEAREXCEPT); return feclearexcept(exc_to_host(x86)); }
