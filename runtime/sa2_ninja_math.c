/* The main thread's hottest Ninja math kernels, run as native bodies over the lifted ones.
 *
 * Subtitle: three guest functions the game's main thread calls for every transformed point and
 * matrix, linked in every build with `-Wl,-wrap=` (tools/s4/link_s4.py PRODUCTION_WRAPS),
 * computed natively when the double mode is linked and the call is one the native body
 * reproduces bit for bit, and handed to the lifted body (`__real_`) otherwise:
 *
 *   0x00426CC0  njCalcPoint: a 3x4 matrix times a point or a vector (eax the matrix, ecx the
 *               input, edx the output, the byte at [esp+4] nonzero for a vector: no
 *               translation; cdecl, ret).
 *   0x00424FF0  njInvertMatrix: a 4x4 matrix's inverse by its cofactors (ecx the input, ebx the
 *               output): the 2x2 minors, the determinant, then either the identity matrix at
 *               0x01AF1A50 (|det| not above the epsilon at 0x0089E8B4) or the adjugate scaled by
 *               1/det through 0x00425350 (sixteen floats times one), copied out with
 *               `rep movsd`; ret.
 *   0x00426E40  njMultiMatrix: out = A * B on 3x4 matrices (eax A, ecx B, edx out), A or B
 *               first copied to the frame when it is the output; nothing for a null pointer.
 *
 * Why: on the user's phone the main thread is the frame's floor on every route (the S8 serial
 * study, private/generated/s8/serial-study/), and these three are among its hottest lifted
 * functions. Design 1.3 allows a patch on the lifted code that changes no game logic; this one
 * changes no bit the guest can see.
 *
 * What "the same" means. tools/s8/f80_double.py's double mode keeps each x87 register as a
 * binary64 in remill's 10-byte slot (its 8 bytes, then 2 zero bytes) and does the x87's
 * arithmetic as the host's double arithmetic in the host's rounding mode, followed at PC24 (the
 * main thread's precision, control word 0x007F from CreateDevice) by the inline rounding of the
 * significand to 24 bits under the control word's RC, the exponent kept. These bodies run only at
 * PC24, with the host's rounding mode the control word's RC (the runtime keeps it there) and no
 * flush-to-zero, and do the arithmetic in single precision instead, which gives the same bits:
 *
 *   - every operand is a float or a result, and every result is a 24-bit value (in the double
 *     mode by the PC24 rounding, here by the float type);
 *   - a product of two 24-bit values is exact in a double, so the double mode's result is its
 *     correct rounding to 24 bits under RC, which is the float product, when that is a normal
 *     float or an exact zero or subnormal;
 *   - a sum, a difference or a quotient is rounded twice in the double mode, to 53 bits in the
 *     host's mode and to 24 under RC, the same mode: twice toward zero, up or down is once, and
 *     to nearest twice is once too because 53 >= 2 * 24 + 2 (double rounding is innocuous for
 *     +, -, x, /); so it is the float operation's result, under the same proviso;
 *   - FLD m32, FSTP m32 of a float-range value and FCHS are exact both ways, and FCOM compares the
 *     same values;
 *   - a product and a sum are never fused into one multiply-add (the pragmas below, which every
 *     compiler flag but -ffp-contract=fast honours; no link of this runtime passes that flag, and
 *     the Android runtime is compiled with -ffp-contract=off besides), and no operand is a
 *     constant the compiler could fold under its own idea of the rounding mode (FLD1's 1.0 is
 *     divided by a loaded value).
 * The proviso is the float exponent range. njCalcPoint and njMultiMatrix take the native path only
 * when every float they multiply is zero or of magnitude in [2^-63, 2^63): then every product is
 * zero or normal, every sum of products below 2^128, and a sum (exact once it is below 2^-126,
 * both ways) cannot leave the range. njInvertMatrix's products of minors can underflow however
 * its inputs lie, so it clears the host's invalid, divide-by-zero, overflow and underflow flags
 * on entry and hands the call to the lifted body if any is set when it has computed everything
 * (an inexact tiny result raises underflow; an exact one is the same both ways). The host's
 * sticky flags are not the guest's: the lifted code's FWAIT reads them only to raise them again,
 * and remill's exception test answers 0 (runtime/remill_runtime.cpp).
 *
 * Everything else the lifted body leaves is reproduced as remill leaves it, read from the
 * double-mode lifted IR and remill's x86 semantics, and set at the end of each path:
 *
 *   - the register stack as remill models it: ST(i) is slot i, a push moves every slot up one
 *     (slot 7's value is lost), a pop moves every slot down one and puts the popped value in
 *     slot 7; a slot the body put a value in holds that value's double and two zero bytes, the
 *     others what they held; TOP (bits 11-13 of the FXSAVE status word) moves with each push and
 *     pop; the tag word is not touched;
 *   - the status flags: FLD m32 ORs the float's denormal test into DE and its signalling-NaN test
 *     into IE (never true here); FLD ST(i) clears IE and DE; every arithmetic instruction, FCHS
 *     and every FST/FSTP set DE to the double mode's 80-bit denormal test of their operands,
 *     which no double passes, so 0; FADD/FADDP also clear C0, C2 and C3; FCOM and FCOMP set C0,
 *     C2 and C3 by the comparison and IE to 0, and leave DE; FLD1 and FXCH touch no flag; PE, UE,
 *     OE and ZE take the runtime's exception test, which answers 0, so they keep their value; C1
 *     is not written; FNSTSW AX writes the condition codes and exception flags into the FXSAVE
 *     status word (keeping B, TOP, ES and SF) and AX;
 *   - the last x87 instruction's FOP (remill's: the first opcode byte's low two bits, then the
 *     ModRM byte) and address as FIP, the CS selector as FCS, and the last memory operand's linear
 *     address as FDP and the DS selector as FDS;
 *   - every guest memory write (the stack slots below ESP included: the pushes, the frame's
 *     temporaries, the call's return address), the GPRs, the arithmetic flags remill computes,
 *     ESP and EIP after the `ret`.
 *
 * When the lifted body runs instead (`sa2_ninja_calls` counts both, `sa2_ninja_why` why):
 *   - the exact build: `sa2_f80_state_double` (tools/s8/f80_shims_double.c's marker) is not
 *     linked, so the slots hold 80-bit images;
 *   - SA2_NINJA_NATIVE=0 in the environment (read once; otherwise a mask of the kernels that run
 *     natively: 1 njCalcPoint, 2 njInvertMatrix, 4 njMultiMatrix; unset or empty is all of
 *     them), or `sa2_ninja_mode` set to 0; SA2_NINJA_NATIVE_STATS (any value) prints the counters
 *     to stderr at exit;
 *   - the precision control is not 24 bits, or the host's rounding mode is not the control
 *     word's RC, or the host flushes or treats denormals as zero;
 *   - guest memory is not on its fast path (`sa2_guest_fast_base` NULL) or an access is outside
 *     [SA2_GUEST_NULL_GUARD, SA2_GUEST_END);
 *   - the frame, the inputs, the output and the image's constants are not apart where the native
 *     body's order of reads and writes needs them apart (njMultiMatrix's A or B may be the output
 *     exactly, as the guest's own copy handles; njCalcPoint's output may be its input);
 *   - a stack offset is used through DS (njMultiMatrix's frame copy, njInvertMatrix's adjugate)
 *     while the SS and DS bases differ, or `rep movsd` would run backwards (DF set);
 *   - a float the body reads is an infinity or a NaN, or out of the range above, or (the inverse)
 *     a host flag says a result left it. Every body computes everything before it writes
 *     anything, so it can still hand over at the end.
 *
 * The proof is a differential harness (tools/s8/native_kernels/ninja_diff.c, run by
 * Documentation/scripts/tests/test_ninja_native.py): the lifted double-mode objects as
 * `__real_`, these bodies, a million random and edge-case States and guest memories per kernel
 * under every precision and rounding control, the whole State and every touched byte compared,
 * and the cases of every guard, which must take the lifted body. The offsets below are remill's
 * State layout, which the harness checks against remill's own header through
 * `sa2_ninja_layout`.
 */
#include <fenv.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "guest_memory.h"

#pragma STDC FP_CONTRACT OFF
#if defined(__clang__)
#pragma clang fp contract(off)
#endif

#define AI static inline __attribute__((always_inline))

void *__real_sub_426cc0(void *state, uint32_t pc, void *memory);
void *__real_sub_424ff0(void *state, uint32_t pc, void *memory);
void *__real_sub_426e40(void *state, uint32_t pc, void *memory);

/* Defined only by the double mode's shims (tools/s8/f80_shims_double.c). */
extern const int sa2_f80_state_double __attribute__((weak));

/* ------------------------------------------------------------------ remill's State, by offset */

enum {
    O_CF = 2065, O_PF = 2067, O_AF = 2069, O_ZF = 2071, O_SF = 2073, O_DF = 2075, O_OF = 2077,
    O_SEG_DS = 2106, O_SEG_CS = 2110,
    O_SS_BASE = 2120, O_ES_BASE = 2136, O_DS_BASE = 2184,
    O_EAX = 2216, O_EBX = 2232, O_ECX = 2248, O_EDX = 2264, O_ESI = 2280, O_EDI = 2296,
    O_ESP = 2312, O_EIP = 2472,
    O_ST0 = 2486, O_ST_STRIDE = 16,
    O_C0 = 2737, O_C1 = 2739, O_C2 = 2741, O_C3 = 2743, O_PE = 2745, O_UE = 2747, O_OE = 2749,
    O_ZE = 2751, O_DE = 2753, O_IE = 2755,
    O_CWD = 2768, O_SWD = 2770, O_FOP = 2774, O_FIP = 2776, O_FCS = 2780, O_FDP = 2784,
    O_FDS = 2788,
};

/* Every offset above by the name remill's header gives the field, for the harness to hold to
 * offsetof(State, ...). */
const struct sa2_ninja_field { const char *field; uint32_t offset; } sa2_ninja_layout[] = {
    {"aflag.cf", O_CF}, {"aflag.pf", O_PF}, {"aflag.af", O_AF}, {"aflag.zf", O_ZF},
    {"aflag.sf", O_SF}, {"aflag.df", O_DF}, {"aflag.of", O_OF}, {"seg.ds", O_SEG_DS},
    {"seg.cs", O_SEG_CS}, {"addr.ss_base", O_SS_BASE}, {"addr.es_base", O_ES_BASE},
    {"addr.ds_base", O_DS_BASE}, {"gpr.rax", O_EAX}, {"gpr.rbx", O_EBX}, {"gpr.rcx", O_ECX},
    {"gpr.rdx", O_EDX}, {"gpr.rsi", O_ESI}, {"gpr.rdi", O_EDI}, {"gpr.rsp", O_ESP},
    {"gpr.rip", O_EIP}, {"st.elems[0].val", O_ST0}, {"st.elems[1].val", O_ST0 + O_ST_STRIDE},
    {"sw.c0", O_C0}, {"sw.c1", O_C1}, {"sw.c2", O_C2}, {"sw.c3", O_C3}, {"sw.pe", O_PE},
    {"sw.ue", O_UE}, {"sw.oe", O_OE}, {"sw.ze", O_ZE}, {"sw.de", O_DE}, {"sw.ie", O_IE},
    {"x87.fxsave.cwd", O_CWD}, {"x87.fxsave.swd", O_SWD}, {"x87.fxsave.fop", O_FOP},
    {"x87.fxsave.ip", O_FIP}, {"x87.fxsave.cs", O_FCS}, {"x87.fxsave.dp", O_FDP},
    {"x87.fxsave.ds", O_FDS},
};
const uint32_t sa2_ninja_layout_count = sizeof sa2_ninja_layout / sizeof sa2_ninja_layout[0];

/* ------------------------------------------------------------------ the switch and the counters */

enum { NJ_CALCPOINT, NJ_INVERT, NJ_MULTIPLY, NJ_KERNELS };
/* Why a call went to the lifted body; a native body answers 0 (done) or 1 + one of these. */
enum { WHY_OFF, WHY_FPU, WHY_MEMORY, WHY_OVERLAP, WHY_VALUE, WHY_SEGMENT, WHY_DF, WHY_KINDS };
static const char *const WHY_NAME[WHY_KINDS] = {"off", "fpu", "memory", "overlap", "value",
                                                "segment", "df"};

/* Calls per kernel: [k][0] native, [k][1] handed to the lifted body, and [k][why] of the second.
 * `sa2_ninja_paths` counts the native calls by the guest's path through the body (njCalcPoint:
 * point, vector; njInvertMatrix: inverse, identity; njMultiMatrix: plain, A copied, B copied,
 * a null pointer). Guest threads run one at a time (runtime/sa2_threads.h), so plain counters are
 * exact. */
uint64_t sa2_ninja_calls[NJ_KERNELS][2];
uint64_t sa2_ninja_why[NJ_KERNELS][WHY_KINDS];
const uint32_t sa2_ninja_why_kinds = WHY_KINDS;
uint64_t sa2_ninja_paths[NJ_KERNELS][4];

/* -1 until the first call reads SA2_NINJA_NATIVE; then which native bodies are on, one bit per
 * kernel (1 njCalcPoint, 2 njInvertMatrix, 4 njMultiMatrix): all of them when SA2_NINJA_NATIVE is
 * unset or empty, else the mask it gives (decimal or 0x hex; 0 is none). */
int sa2_ninja_mode = -1;

static void nj_stats_print(void) {
    static const char *const name[NJ_KERNELS] = {"njCalcPoint 0x00426CC0",
                                                 "njInvertMatrix 0x00424FF0",
                                                 "njMultiMatrix 0x00426E40"};
    static const char *const path[NJ_KERNELS][4] = {{"point", "vector", "-", "-"},
                                                    {"inverse", "identity", "-", "-"},
                                                    {"plain", "a_copied", "b_copied", "null"}};
    for (int k = 0; k < NJ_KERNELS; k++) {
        fprintf(stderr, "sa2_ninja_math: %s native %llu lifted %llu (", name[k],
                (unsigned long long)sa2_ninja_calls[k][0],
                (unsigned long long)sa2_ninja_calls[k][1]);
        for (int w = 0; w < WHY_KINDS; w++)
            fprintf(stderr, "%s%s %llu", w ? " " : "", WHY_NAME[w],
                    (unsigned long long)sa2_ninja_why[k][w]);
        fprintf(stderr, ") paths");
        for (int p = 0; p < 4; p++)
            if (path[k][p][0] != '-')
                fprintf(stderr, " %s %llu", path[k][p], (unsigned long long)sa2_ninja_paths[k][p]);
        fputc('\n', stderr);
    }
}

static int nj_mode_read(void) {
    const char *e = getenv("SA2_NINJA_NATIVE");
    const long v = e && *e ? strtol(e, NULL, 0) : (1 << NJ_KERNELS) - 1;
    sa2_ninja_mode = (int)(v & ((1 << NJ_KERNELS) - 1));
    if (getenv("SA2_NINJA_NATIVE_STATS")) atexit(nj_stats_print);
    return sa2_ninja_mode;
}

AI int nj_on(int k) {
    int m = sa2_ninja_mode;
    if (__builtin_expect(m < 0, 0)) m = nj_mode_read();
    return ((m >> k) & 1) && &sa2_f80_state_double != NULL;
}

/* The wrapper's tally: `why` 0 is a native call. */
AI void nj_count(int k, int why) {
    if (!why) {
        sa2_ninja_calls[k][0]++;
        return;
    }
    sa2_ninja_calls[k][1]++;
    sa2_ninja_why[k][why - 1]++;
}

/* ------------------------------------------------------------------ bytes */

AI uint16_t ld16(const uint8_t *p) { uint16_t v; memcpy(&v, p, 2); return v; }
AI uint32_t ld32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
AI uint64_t ld64(const uint8_t *p) { uint64_t v; memcpy(&v, p, 8); return v; }
AI float    ldf(const uint8_t *p)  { float v; memcpy(&v, p, 4); return v; }
AI void st16(uint8_t *p, uint16_t v) { memcpy(p, &v, 2); }
AI void st32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
AI void st64(uint8_t *p, uint64_t v) { memcpy(p, &v, 8); }
AI void stf(uint8_t *p, float v)     { memcpy(p, &v, 4); }
AI uint64_t dbits(double d) { uint64_t b; memcpy(&b, &d, 8); return b; }

/* [a, a + len) lies where the fast path makes the access: inside [NULL_GUARD, END). */
AI int in_fast(uint32_t a, uint32_t len) {
    return a >= SA2_GUEST_NULL_GUARD && len <= SA2_GUEST_END && a <= SA2_GUEST_END - len;
}
/* Two ranges both in_fast are disjoint. */
AI int apart(uint32_t a, uint32_t alen, uint32_t b, uint32_t blen) {
    return a + alen <= b || b + blen <= a;
}

/* The float bits a multiplying body takes natively: zero, or a magnitude in [2^-63, 2^63) (the
 * biased exponent 64 to 189). */
AI uint32_t in_range(uint32_t b) {
    return ((b & 0x7FFFFFFFu) == 0) | ((((b >> 23) & 0xFFu) - 64u) <= 125u);
}
/* Finite: not an infinity or a NaN. */
AI uint32_t finite32(uint32_t b) { return (b & 0x7F800000u) != 0x7F800000u; }
/* A denormal float (FLD m32's DE test). */
AI uint8_t denormal32(uint32_t b) { return (uint8_t)(((b & 0x7F800000u) == 0) & ((b & 0x007FFFFFu) != 0)); }

/* ------------------------------------------------------------------ the arithmetic flags */

AI uint8_t parity8(uint32_t r) { return (uint8_t)!(__builtin_popcount(r & 0xFFu) & 1); }
/* ADD's flags into the State (the bodies end with `add esp, n`). */
AI void fl_add_store(uint8_t *s, uint32_t a, uint32_t b) {
    const uint32_t r = a + b;
    s[O_CF] = (uint8_t)(r < a);
    s[O_PF] = parity8(r);
    s[O_AF] = (uint8_t)(((a ^ b ^ r) >> 4) & 1u);
    s[O_ZF] = (uint8_t)(r == 0);
    s[O_SF] = (uint8_t)(r >> 31);
    s[O_OF] = (uint8_t)((((a ^ r) & (b ^ r)) >> 31) & 1u);
}

/* ------------------------------------------------------------------ the host's floating point */

/* The host's rounding mode is the control word's RC and denormals are neither flushed nor read
 * as zero; then single-precision arithmetic rounds as the double mode's PC24 rounding does. The
 * exception flags: invalid, divide by zero, overflow and underflow, cleared and read back. */
#if defined(__x86_64__) || defined(__i386__)
AI uint32_t nj_csr(void) { uint32_t c; __asm__ volatile("stmxcsr %0" : "=m"(c) : : "memory"); return c; }
AI void nj_csr_set(uint32_t c) { __asm__ volatile("ldmxcsr %0" : : "m"(c) : "memory"); }
AI int nj_env_ok(uint16_t cw) {               /* MXCSR RC (bits 13-14) is the x87's encoding */
    const uint32_t c = nj_csr();
    return ((c >> 13) & 3u) == ((cw >> 10) & 3u) && !(c & 0x8040u);   /* FTZ, DAZ */
}
#define NJ_EXC 0x1Du                          /* IE, ZE, OE, UE */
AI void nj_exc_clear(void) { const uint32_t c = nj_csr(); if (c & NJ_EXC) nj_csr_set(c & ~NJ_EXC); }
AI int nj_exc_raised(void) { return (nj_csr() & NJ_EXC) != 0; }
#elif defined(__aarch64__)
AI uint64_t nj_fpcr(void) { uint64_t c; __asm__ volatile("mrs %0, fpcr" : "=r"(c) : : "memory"); return c; }
AI uint64_t nj_fpsr(void) { uint64_t c; __asm__ volatile("mrs %0, fpsr" : "=r"(c) : : "memory"); return c; }
AI void nj_fpsr_set(uint64_t c) { __asm__ volatile("msr fpsr, %0" : : "r"(c) : "memory"); }
AI int nj_env_ok(uint16_t cw) {               /* FPCR RMode (bits 22-23): RN, RP, RM, RZ */
    static const uint8_t x87_rc[4] = {0, 2, 1, 3};
    const uint64_t c = nj_fpcr();
    return x87_rc[(c >> 22) & 3u] == ((cw >> 10) & 3u) && !(c & ((1u << 24) | 3u));  /* FZ, AH, FIZ */
}
#define NJ_EXC 0xFu                           /* IOC, DZC, OFC, UFC */
AI void nj_exc_clear(void) { const uint64_t c = nj_fpsr(); if (c & NJ_EXC) nj_fpsr_set(c & ~(uint64_t)NJ_EXC); }
AI int nj_exc_raised(void) { return (nj_fpsr() & NJ_EXC) != 0; }
#else
AI int nj_env_ok(uint16_t cw) {
    static const int mode[4] = {FE_TONEAREST, FE_DOWNWARD, FE_UPWARD, FE_TOWARDZERO};
    return fegetround() == mode[(cw >> 10) & 3u];
}
AI void nj_exc_clear(void) { feclearexcept(FE_INVALID | FE_DIVBYZERO | FE_OVERFLOW | FE_UNDERFLOW); }
AI int nj_exc_raised(void) { return fetestexcept(FE_INVALID | FE_DIVBYZERO | FE_OVERFLOW | FE_UNDERFLOW) != 0; }
#endif

/* The values computed so far are in memory before the flags are read (a store cannot move past
 * an asm that may read it). */
#define NJ_SETTLE(p) __asm__ volatile("" : : "r"(p) : "memory")

/* ------------------------------------------------------------------ the x87, as remill keeps it */

/* remill moves every slot on a push and a pop. Here the slots stay where they are and the base
 * moves: ST(i) is physical slot (r + i) & 7. A push lowers r and writes the new slot 0, which is
 * the slot remill's push drops; a pop raises r, which leaves the popped value as ST(7), where
 * remill's pop puts it. `w` holds the physical slots the body put a value in; the others keep
 * what the caller left there. In a body every push and pop is at a fixed point, so r and w are
 * constants at each instruction once the body is inlined, and the slots are registers. The
 * instruction helpers take each instruction's FOP and addresses, as the lifted code records
 * them, for the reader; only the last ones reach the State, set by each path. */
typedef struct {
    float v[8];
    unsigned r, w;
    uint8_t c0, c2, c3;  /* the last comparison's condition codes */
} fx;

AI void f_init(fx *m) { m->r = 0; m->w = 0; m->c0 = m->c2 = m->c3 = 0; }
AI unsigned f_ph(const fx *m, int i) { return (m->r + (unsigned)i) & 7u; }
AI float f_st(const fx *m, int i) { return m->v[f_ph(m, i)]; }
AI void f_set(fx *m, int i, float x) {
    const unsigned p = f_ph(m, i);
    m->v[p] = x;
    m->w |= 1u << p;
}
AI void f_push(fx *m, float x) {
    m->r = (m->r + 7u) & 7u;
    m->v[m->r] = x;
    m->w |= 1u << m->r;
}
AI void f_pop(fx *m) { m->r = (m->r + 1u) & 7u; }

#define FX_AT uint16_t fop, uint32_t ip
#define FX_USED (void)fop; (void)ip
/* FLD m32 */
AI void f_fld_m(fx *m, float x, FX_AT, uint32_t dp) { FX_USED; (void)dp; f_push(m, x); }
/* FLD ST(i) */
AI void f_fld_st(fx *m, int i, FX_AT) { FX_USED; f_push(m, f_st(m, i)); }
/* FLD1 */
AI void f_fld1(fx *m, FX_AT) { FX_USED; f_push(m, 1.0f); }
/* FXCH ST(i) */
AI void f_fxch(fx *m, int i, FX_AT) {
    FX_USED;
    const float a = f_st(m, 0);
    f_set(m, 0, f_st(m, i));
    f_set(m, i, a);
}
/* FST m32 / FSTP m32: the value stored */
AI float f_fst_m(fx *m, int pop, FX_AT, uint32_t dp) {
    FX_USED; (void)dp;
    const float r = f_st(m, 0);
    if (pop) f_pop(m);
    return r;
}
/* FSTP ST(i) */
AI void f_fstp_st(fx *m, int i, FX_AT) { FX_USED; f_set(m, i, f_st(m, 0)); f_pop(m); }
/* FCHS */
AI void f_fchs(fx *m, FX_AT) { FX_USED; f_set(m, 0, -f_st(m, 0)); }
/* FMUL m32: ST0 = ST0 * m */
AI void f_fmul_m(fx *m, float x, FX_AT, uint32_t dp) { FX_USED; (void)dp; f_set(m, 0, f_st(m, 0) * x); }
/* FADD m32: ST0 = ST0 + m */
AI void f_fadd_m(fx *m, float x, FX_AT, uint32_t dp) { FX_USED; (void)dp; f_set(m, 0, f_st(m, 0) + x); }
/* FMUL ST0, ST(i) (D8 C8+i) */
AI void f_fmul_st0(fx *m, int i, FX_AT) { FX_USED; f_set(m, 0, f_st(m, 0) * f_st(m, i)); }
/* FMULP ST(i), ST0 */
AI void f_fmulp(fx *m, int i, FX_AT) { FX_USED; f_set(m, i, f_st(m, i) * f_st(m, 0)); f_pop(m); }
/* FADDP ST(i), ST0 */
AI void f_faddp(fx *m, int i, FX_AT) { FX_USED; f_set(m, i, f_st(m, i) + f_st(m, 0)); f_pop(m); }
/* FSUBP ST(i), ST0 (DE E8+i): ST(i) = ST(i) - ST0 */
AI void f_fsubp(fx *m, int i, FX_AT) { FX_USED; f_set(m, i, f_st(m, i) - f_st(m, 0)); f_pop(m); }
/* FSUBRP ST(i), ST0 (DE E0+i): ST(i) = ST0 - ST(i) */
AI void f_fsubrp(fx *m, int i, FX_AT) { FX_USED; f_set(m, i, f_st(m, 0) - f_st(m, i)); f_pop(m); }
/* FDIVRP ST(i), ST0 (DE F0+i): ST(i) = ST0 / ST(i) */
AI void f_fdivrp(fx *m, int i, FX_AT) { FX_USED; f_set(m, i, f_st(m, 0) / f_st(m, i)); f_pop(m); }
/* FCOM's and FCOMP's condition codes (unordered: all three, and the flags check hands it over). */
AI void f_cmp(fx *m, float a, float b) {
    const int un = !(a == a) || !(b == b);
    m->c0 = (uint8_t)(un | (a < b));
    m->c2 = (uint8_t)un;
    m->c3 = (uint8_t)(un | (a == b));
}
/* FCOM ST(i) (D8 D0+i) */
AI void f_fcom_st(fx *m, int i, FX_AT) { FX_USED; f_cmp(m, f_st(m, 0), f_st(m, i)); }
/* FCOMP m32 (D8 /3) */
AI void f_fcomp_m(fx *m, float x, FX_AT, uint32_t dp) {
    FX_USED; (void)dp;
    f_cmp(m, f_st(m, 0), x);
    f_pop(m);
}
/* FNSTSW AX: what the body tests of it, the condition codes in AH (the rest is set at the end). */
AI uint16_t f_fnstsw(const fx *m) {
    return (uint16_t)((m->c0 << 8) | (m->c2 << 10) | (m->c3 << 14));
}

/* The register stack the body leaves: slot i is ST(i), from the body's values or the caller's. */
typedef struct { uint64_t lo[8]; uint16_t hi[8]; } slots;
AI void f_slots(const fx *m, const uint8_t *s, slots *o) {
    for (int i = 0; i < 8; i++) {
        const unsigned p = f_ph(m, i);
        if ((m->w >> p) & 1u) {
            o->lo[i] = dbits((double)m->v[p]);
            o->hi[i] = 0;
        } else {
            o->lo[i] = ld64(s + O_ST0 + O_ST_STRIDE * p);
            o->hi[i] = ld16(s + O_ST0 + O_ST_STRIDE * p + 8);
        }
    }
}
/* The register stack, TOP moved by the body's pushes and pops, and the last instruction's FOP,
 * FIP and memory operand. */
AI void f_store(const fx *m, const slots *o, uint8_t *s, uint16_t swd, uint16_t fop, uint32_t ip,
                uint32_t dp) {
    for (int i = 0; i < 8; i++) {
        st64(s + O_ST0 + O_ST_STRIDE * i, o->lo[i]);
        st16(s + O_ST0 + O_ST_STRIDE * i + 8, o->hi[i]);
    }
    st16(s + O_SWD, (uint16_t)((swd & ~0x3800u) | ((swd + (m->r << 11)) & 0x3800u)));
    st16(s + O_FOP, fop);
    st32(s + O_FIP, ip);
    st32(s + O_FDP, dp);
    st16(s + O_FCS, ld16(s + O_SEG_CS));
    st16(s + O_FDS, ld16(s + O_SEG_DS));
}

/* ------------------------------------------------------------------ 0x00426CC0, njCalcPoint */

/* Its stack, E the entry ESP: three temporaries (E-0Ch to E), the return address, the flag
 * (the byte at E+4). */
#define CP_LO 0x0Cu
#define CP_LEN 0x11u
#define SHC(o) (sh + CP_LO + (o))

AI int cp_native(uint8_t *s, uint32_t pc) {
    uint8_t *g = sa2_guest_fast_base;
    const uint32_t esp = ld32(s + O_ESP), ds = ld32(s + O_DS_BASE);
    const uint32_t E = ld32(s + O_SS_BASE) + esp, F = E - CP_LO;
    const uint32_t ma = ds + ld32(s + O_EAX), va = ds + ld32(s + O_ECX), oa = ds + ld32(s + O_EDX);
    if (!g || !in_fast(F, CP_LEN) || !in_fast(ma, 0x30) || !in_fast(va, 0x0C) || !in_fast(oa, 0x0C))
        return 1 + WHY_MEMORY;
    /* The matrix and the input are read and the output written around the temporaries; the
     * output may be the input or the matrix (every read comes before the first write). */
    if (!apart(F, CP_LEN, ma, 0x30) || !apart(F, CP_LEN, va, 0x0C) || !apart(F, CP_LEN, oa, 0x0C))
        return 1 + WHY_OVERLAP;
    const int vector = g[E + 4] != 0;                                /* cmp byte [esp+10h],0 */
    uint32_t ok = 1;
    for (int r = 0; r < 3; r++)
        for (int c = 0; c < 4; c++)
            ok &= c < 3 || !vector ? in_range(ld32(g + ma + 16u * r + 4u * c)) : 1u;
    for (int c = 0; c < 3; c++) ok &= in_range(ld32(g + va + 4u * c));
    if (!ok) return 1 + WHY_VALUE;
    uint8_t sh[CP_LEN];
    memcpy(sh, g + F, CP_LEN);
    fx mx, *m = &mx;
    f_init(m);
    f_fld_m(m, ldf(g + ma + 0x4), 0x140, pc + 0x008, ma + 0x4);      /* fld [eax+4]         */
    f_fmul_m(m, ldf(g + va + 0x4), 0x049, pc + 0x00B, va + 0x4);     /* fmul [ecx+4]        */
    f_fld_m(m, ldf(g + ma + 0x0), 0x100, pc + 0x00E, ma + 0x0);      /* fld [eax]           */
    f_fmul_m(m, ldf(g + va + 0x0), 0x009, pc + 0x010, va + 0x0);     /* fmul [ecx]          */
    f_faddp(m, 1, 0x2C1, pc + 0x012);                                /* faddp               */
    f_fld_m(m, ldf(g + ma + 0x8), 0x140, pc + 0x014, ma + 0x8);      /* fld [eax+8]         */
    f_fmul_m(m, ldf(g + va + 0x8), 0x049, pc + 0x017, va + 0x8);     /* fmul [ecx+8]        */
    f_faddp(m, 1, 0x2C1, pc + 0x01A);                                /* faddp               */
    stf(SHC(-0xC), f_fst_m(m, 1, 0x11C, pc + 0x01C, E - 0xC));       /* fstp [esp]          */
    f_fld_m(m, ldf(g + ma + 0x10), 0x140, pc + 0x01F, ma + 0x10);    /* fld [eax+10h]       */
    f_fmul_m(m, ldf(g + va + 0x0), 0x009, pc + 0x022, va + 0x0);     /* fmul [ecx]          */
    f_fld_m(m, ldf(g + ma + 0x14), 0x140, pc + 0x024, ma + 0x14);    /* fld [eax+14h]       */
    f_fmul_m(m, ldf(g + va + 0x4), 0x049, pc + 0x027, va + 0x4);     /* fmul [ecx+4]        */
    f_faddp(m, 1, 0x2C1, pc + 0x02A);                                /* faddp               */
    f_fld_m(m, ldf(g + ma + 0x18), 0x140, pc + 0x02C, ma + 0x18);    /* fld [eax+18h]       */
    f_fmul_m(m, ldf(g + va + 0x8), 0x049, pc + 0x02F, va + 0x8);     /* fmul [ecx+8]        */
    f_faddp(m, 1, 0x2C1, pc + 0x032);                                /* faddp               */
    stf(SHC(-0x8), f_fst_m(m, 1, 0x15C, pc + 0x034, E - 0x8));       /* fstp [esp+4]        */
    f_fld_m(m, ldf(g + ma + 0x20), 0x140, pc + 0x038, ma + 0x20);    /* fld [eax+20h]       */
    f_fmul_m(m, ldf(g + va + 0x0), 0x009, pc + 0x03B, va + 0x0);     /* fmul [ecx]          */
    f_fld_m(m, ldf(g + ma + 0x24), 0x140, pc + 0x03D, ma + 0x24);    /* fld [eax+24h]       */
    f_fmul_m(m, ldf(g + va + 0x4), 0x049, pc + 0x040, va + 0x4);     /* fmul [ecx+4]        */
    f_faddp(m, 1, 0x2C1, pc + 0x043);                                /* faddp               */
    f_fld_m(m, ldf(g + ma + 0x28), 0x140, pc + 0x045, ma + 0x28);    /* fld [eax+28h]       */
    f_fmul_m(m, ldf(g + va + 0x8), 0x049, pc + 0x048, va + 0x8);     /* fmul [ecx+8]        */
    f_faddp(m, 1, 0x2C1, pc + 0x04B);                                /* faddp               */
    stf(SHC(-0x4), f_fst_m(m, 1, 0x15C, pc + 0x04D, E - 0x4));       /* fstp [esp+8]        */
    if (!vector) {                                                   /* jne +72h            */
        f_fld_m(m, ldf(g + ma + 0xC), 0x140, pc + 0x053, ma + 0xC);  /* fld [eax+0Ch]       */
        f_fadd_m(m, ldf(SHC(-0xC)), 0x004, pc + 0x056, E - 0xC);     /* fadd [esp]          */
        stf(SHC(-0xC), f_fst_m(m, 1, 0x11C, pc + 0x059, E - 0xC));   /* fstp [esp]          */
        f_fld_m(m, ldf(g + ma + 0x1C), 0x140, pc + 0x05C, ma + 0x1C);/* fld [eax+1Ch]       */
        f_fadd_m(m, ldf(SHC(-0x8)), 0x044, pc + 0x05F, E - 0x8);     /* fadd [esp+4]        */
        stf(SHC(-0x8), f_fst_m(m, 1, 0x15C, pc + 0x063, E - 0x8));   /* fstp [esp+4]        */
        f_fld_m(m, ldf(g + ma + 0x2C), 0x140, pc + 0x067, ma + 0x2C);/* fld [eax+2Ch]       */
        f_fadd_m(m, ldf(SHC(-0x4)), 0x044, pc + 0x06A, E - 0x4);     /* fadd [esp+8]        */
        stf(SHC(-0x4), f_fst_m(m, 1, 0x15C, pc + 0x06E, E - 0x4));   /* fstp [esp+8]        */
    }
    float out[3];
    f_fld_m(m, ldf(SHC(-0xC)), 0x104, pc + 0x072, E - 0xC);          /* fld [esp]           */
    out[0] = f_fst_m(m, 1, 0x11A, pc + 0x075, oa + 0x0);             /* fstp [edx]          */
    f_fld_m(m, ldf(SHC(-0x8)), 0x144, pc + 0x077, E - 0x8);          /* fld [esp+4]         */
    out[1] = f_fst_m(m, 1, 0x15A, pc + 0x07B, oa + 0x4);             /* fstp [edx+4]        */
    f_fld_m(m, ldf(SHC(-0x4)), 0x144, pc + 0x07E, E - 0x4);          /* fld [esp+8]         */
    out[2] = f_fst_m(m, 1, 0x15A, pc + 0x082, oa + 0x8);             /* fstp [edx+8]        */
    slots st;
    f_slots(m, s, &st);
    memcpy(g + F, sh, CP_LEN);
    memcpy(g + oa, out, sizeof out);
    /* FADDP cleared C0, C2, C3, the last FSTP DE; every FLD m32's IE test was false. */
    f_store(m, &st, s, ld16(s + O_SWD), 0x15A, pc + 0x082, oa + 0x8);
    s[O_C0] = s[O_C2] = s[O_C3] = 0;
    s[O_DE] = 0;
    fl_add_store(s, esp - 0x0C, 0x0C);                               /* add esp,0Ch         */
    st32(s + O_EIP, ld32(SHC(0)));                                   /* ret                 */
    st32(s + O_ESP, esp + 4);
    sa2_ninja_paths[NJ_CALCPOINT][vector]++;
    return 0;
}

__attribute__((used, retain))
void *__wrap_sub_426cc0(void *state, uint32_t pc, void *memory) {
    uint8_t *s = state;
    const uint16_t cw = ld16(s + O_CWD);
    const int why = !nj_on(NJ_CALCPOINT) ? 1 + WHY_OFF
                  : (cw & 0x0300u) || !nj_env_ok(cw) ? 1 + WHY_FPU : cp_native(s, pc);
    nj_count(NJ_CALCPOINT, why);
    return why ? __real_sub_426cc0(state, pc, memory) : memory;
}

/* ------------------------------------------------------------------ 0x00426E40, njMultiMatrix */

/* Its stack, E the entry ESP: the pushed esi (E-38h) and edi (E-34h), the twelve-float copy
 * (E-30h to E), the return address. */
#define MM_LO 0x38u
#define MM_LEN 0x3Cu
#define SHM(o) (sh + MM_LO + (o))

/* A copy of a matrix into the frame: twelve FLD m32 / FSTP m32 pairs from `src` (linear) to
 * E-30h; `fld0` and `fld` are the first and the other loads' FOP (their ModRM differs), `at` the
 * first load's address. The last store comes after a 4-byte `lea`. */
AI void mm_copy(fx *m, const uint8_t *g, uint8_t *sh, uint32_t E, uint32_t src, uint16_t fld0,
                uint16_t fld, uint32_t at) {
    for (int k = 0; k < 12; k++) {
        const uint32_t k7 = 7u * (uint32_t)k;
        f_fld_m(m, ldf(g + src + 4u * k), k ? fld : fld0, k ? at + k7 - 1u : at, src + 4u * k);
        stf(SHM(-0x30 + 4 * k), f_fst_m(m, 1, 0x15C, at + k7 + 2u + (k == 11 ? 4u : 0u),
                                         E - 0x30 + 4u * k));
    }
}

/* One row of the product (the loop body at +0E1h): A's row at host `pa` (linear `a`), B at host
 * `pb` (linear `b`), the output row at linear `o`; the row's four outputs, in the guest's order.
 * `at` is the loop's address. */
AI void mm_row(fx *m, const uint8_t *pa, uint32_t a, const uint8_t *pb, uint32_t b, uint32_t o,
               uint32_t at, float out[4]) {
    f_fld_m(m, ldf(pa + 0x4), 0x140, at + 0x00, a + 0x4);            /* fld [eax-4]         */
    f_fmul_m(m, ldf(pb + 0x10), 0x049, at + 0x06, b + 0x10);         /* fmul [ecx+10h]      */
    f_fld_m(m, ldf(pa + 0x0), 0x140, at + 0x0F, a + 0x0);            /* fld [eax-18h]       */
    f_fmul_m(m, ldf(pb + 0x0), 0x009, at + 0x12, b + 0x0);           /* fmul [ecx]          */
    f_faddp(m, 1, 0x2C1, at + 0x14);                                 /* faddp               */
    f_fld_m(m, ldf(pb + 0x20), 0x141, at + 0x16, b + 0x20);          /* fld [ecx+20h]       */
    f_fmul_m(m, ldf(pa + 0x8), 0x048, at + 0x19, a + 0x8);           /* fmul [eax-10h]      */
    f_faddp(m, 1, 0x2C1, at + 0x1C);                                 /* faddp               */
    out[0] = f_fst_m(m, 1, 0x15A, at + 0x1E, o + 0x0);               /* fstp [edx-10h]      */
    f_fld_m(m, ldf(pa + 0x4), 0x140, at + 0x21, a + 0x4);            /* fld [eax-14h]       */
    f_fmul_m(m, ldf(pb + 0x14), 0x049, at + 0x24, b + 0x14);         /* fmul [ecx+14h]      */
    f_fld_m(m, ldf(pa + 0x0), 0x140, at + 0x27, a + 0x0);            /* fld [eax-18h]       */
    f_fmul_m(m, ldf(pb + 0x4), 0x049, at + 0x2A, b + 0x4);           /* fmul [ecx+4]        */
    f_faddp(m, 1, 0x2C1, at + 0x2D);                                 /* faddp               */
    f_fld_m(m, ldf(pa + 0x8), 0x140, at + 0x2F, a + 0x8);            /* fld [eax-10h]       */
    f_fmul_m(m, ldf(pb + 0x24), 0x049, at + 0x32, b + 0x24);         /* fmul [ecx+24h]      */
    f_faddp(m, 1, 0x2C1, at + 0x35);                                 /* faddp               */
    out[1] = f_fst_m(m, 1, 0x15C, at + 0x37, o + 0x4);               /* fstp [esi+eax-14h]  */
    f_fld_m(m, ldf(pa + 0x4), 0x140, at + 0x3B, a + 0x4);            /* fld [eax-14h]       */
    f_fmul_m(m, ldf(pb + 0x18), 0x049, at + 0x3E, b + 0x18);         /* fmul [ecx+18h]      */
    f_fld_m(m, ldf(pa + 0x0), 0x140, at + 0x41, a + 0x0);            /* fld [eax-18h]       */
    f_fmul_m(m, ldf(pb + 0x8), 0x049, at + 0x44, b + 0x8);           /* fmul [ecx+8]        */
    f_faddp(m, 1, 0x2C1, at + 0x47);                                 /* faddp               */
    f_fld_m(m, ldf(pa + 0x8), 0x140, at + 0x49, a + 0x8);            /* fld [eax-10h]       */
    f_fmul_m(m, ldf(pb + 0x28), 0x049, at + 0x4C, b + 0x28);         /* fmul [ecx+28h]      */
    f_faddp(m, 1, 0x2C1, at + 0x4F);                                 /* faddp               */
    out[2] = f_fst_m(m, 1, 0x15C, at + 0x51, o + 0x8);               /* fstp [esi+eax-10h]  */
    f_fld_m(m, ldf(pa + 0x4), 0x140, at + 0x55, a + 0x4);            /* fld [eax-14h]       */
    f_fmul_m(m, ldf(pb + 0x1C), 0x049, at + 0x58, b + 0x1C);         /* fmul [ecx+1Ch]      */
    f_fld_m(m, ldf(pa + 0x0), 0x140, at + 0x5B, a + 0x0);            /* fld [eax-18h]       */
    f_fmul_m(m, ldf(pb + 0xC), 0x049, at + 0x5E, b + 0xC);           /* fmul [ecx+0Ch]      */
    f_faddp(m, 1, 0x2C1, at + 0x61);                                 /* faddp               */
    f_fld_m(m, ldf(pa + 0x8), 0x140, at + 0x63, a + 0x8);            /* fld [eax-10h]       */
    f_fmul_m(m, ldf(pb + 0x2C), 0x049, at + 0x66, b + 0x2C);         /* fmul [ecx+2Ch]      */
    f_faddp(m, 1, 0x2C1, at + 0x69);                                 /* faddp               */
    f_fadd_m(m, ldf(pa + 0xC), 0x040, at + 0x6B, a + 0xC);           /* fadd [eax-0Ch]      */
    out[3] = f_fst_m(m, 1, 0x15A, at + 0x6E, o + 0xC);               /* fstp [edx-4]        */
}

AI int mm_native(uint8_t *s, uint32_t pc) {
    uint8_t *g = sa2_guest_fast_base;
    const uint32_t esp = ld32(s + O_ESP), ss = ld32(s + O_SS_BASE), ds = ld32(s + O_DS_BASE);
    const uint32_t E = ss + esp, F = E - MM_LO;
    const uint32_t eax = ld32(s + O_EAX), ecx = ld32(s + O_ECX), edx = ld32(s + O_EDX);
    if (!g || !in_fast(F, MM_LEN)) return 1 + WHY_MEMORY;
    if (!eax || !ecx || !edx) {                    /* test ; je +155h: pop edi ; add esp,30h */
        st32(g + E - 0x34, ld32(s + O_EDI));                         /* push edi            */
        fl_add_store(s, esp - 0x30, 0x30);
        st32(s + O_EIP, ld32(g + E));                                /* ret                 */
        st32(s + O_ESP, esp + 4);
        sa2_ninja_paths[NJ_MULTIPLY][3]++;
        return 0;
    }
    const int copy_a = eax == edx, copy_b = ecx == edx;
    const uint32_t la = ds + eax, lb = ds + ecx, lo = ds + edx;
    if (!in_fast(la, 0x30) || !in_fast(lb, 0x30) || !in_fast(lo, 0x30)) return 1 + WHY_MEMORY;
    /* A and B are read while the output is written: each is the output (then the guest copies
     * it to the frame first) or apart from it; none may meet the frame. */
    if (!apart(F, MM_LEN, la, 0x30) || !apart(F, MM_LEN, lb, 0x30) || !apart(F, MM_LEN, lo, 0x30)
            || (!copy_a && !apart(la, 0x30, lo, 0x30)) || (!copy_b && !apart(lb, 0x30, lo, 0x30)))
        return 1 + WHY_OVERLAP;
    /* The copy is written through SS and read back through DS. */
    if ((copy_a || copy_b) && ss != ds) return 1 + WHY_SEGMENT;
    uint32_t ok = 1;
    for (int k = 0; k < 12; k++) ok &= in_range(ld32(g + la + 4u * k)) & in_range(ld32(g + lb + 4u * k));
    if (!ok) return 1 + WHY_VALUE;
    uint8_t sh[MM_LEN];
    memcpy(sh, g + F, MM_LEN);
    fx mx, *m = &mx;
    f_init(m);
    st32(SHM(-0x34), ld32(s + O_EDI));                               /* push edi            */
    if (copy_a) mm_copy(m, g, sh, E, la, 0x107, 0x147, pc + 0x022);  /* ; lea edi,[esp+4]   */
    if (copy_b) mm_copy(m, g, sh, E, lb, 0x101, 0x141, pc + 0x07D);  /* ; lea ecx,[esp+4]   */
    st32(SHM(-0x38), ld32(s + O_ESI));                               /* push esi            */
    /* Where the rows find A and B: the frame's copy (at E-30h, the same linear address through
     * DS as through SS) or the matrix itself. */
    const uint8_t *pa = copy_a ? SHM(-0x30) : g + la, *pb = copy_b ? SHM(-0x30) : g + lb;
    const uint32_t a = copy_a ? E - 0x30 : la, b = copy_b ? E - 0x30 : lb;
    float out[12];
    mm_row(m, pa, a, pb, b, lo, pc + 0x0E1, out);
    mm_row(m, pa + 0x10, a + 0x10, pb, b, lo + 0x10, pc + 0x0E1, out + 4);
    mm_row(m, pa + 0x20, a + 0x20, pb, b, lo + 0x20, pc + 0x0E1, out + 8);
    slots st;
    f_slots(m, s, &st);
    memcpy(g + F, sh, MM_LEN);
    memcpy(g + lo, out, sizeof out);
    /* FADDP and FADD cleared C0, C2, C3, the last FSTP DE; every FLD m32's IE test was false. */
    f_store(m, &st, s, ld16(s + O_SWD), 0x15A, pc + 0x14F, lo + 0x2C);
    s[O_C0] = s[O_C2] = s[O_C3] = 0;
    s[O_DE] = 0;
    fl_add_store(s, esp - 0x30, 0x30);                               /* add esp,30h         */
    st32(s + O_EAX, (copy_a ? esp - 0x30 : eax) + 0x38);
    st32(s + O_ECX, copy_b ? esp - 0x30 : ecx);
    st32(s + O_EDX, edx + 0x30);
    st32(s + O_EIP, ld32(SHM(0)));                                   /* ret                 */
    st32(s + O_ESP, esp + 4);
    sa2_ninja_paths[NJ_MULTIPLY][copy_a ? 1 : copy_b ? 2 : 0]++;
    return 0;
}

__attribute__((used, retain))
void *__wrap_sub_426e40(void *state, uint32_t pc, void *memory) {
    uint8_t *s = state;
    const uint16_t cw = ld16(s + O_CWD);
    const int why = !nj_on(NJ_MULTIPLY) ? 1 + WHY_OFF
                  : (cw & 0x0300u) || !nj_env_ok(cw) ? 1 + WHY_FPU : mm_native(s, pc);
    nj_count(NJ_MULTIPLY, why);
    return why ? __real_sub_426e40(state, pc, memory) : memory;
}

/* ------------------------------------------------------------------ 0x00424FF0, njInvertMatrix */

/* Its stack, E the entry ESP: from the return address its call of 0x00425350 pushes (E-84h:
 * that call's argument E-80h, the pushed edi E-7Ch and esi E-78h, the temporaries, the
 * adjugate at E-44h to E-4h) to its own return address. */
#define INV_LO 0x84u
#define INV_LEN 0x88u
#define SHI(o) (sh + INV_LO + (o))
/* The image's constants: 0.0f, FLT_EPSILON, and the identity matrix the game keeps. */
#define INV_K_ZERO 0x008AF004u
#define INV_K_EPS 0x0089E8B4u
#define INV_IDENTITY 0x01AF1A50u

/* One pass of 0x00425350's loop: eight of the adjugate's floats, from E + `b`, times ST(1)
 * (the scale it loaded), in place (`at` the function's address). Its accesses are DS-relative
 * stack offsets, which the caller has checked land where SS puts them. */
AI void inv_scale8(fx *m, uint8_t *sh, uint32_t E, int b, uint32_t at) {
#define EL(j) SHI(b + 4 * (j))
#define LE(j) (E + (uint32_t)(b + 4 * (j)))
    f_fld_m(m, ldf(EL(0)), 0x141, at + 0x0C, LE(0));                 /* fld [ecx-8]         */
    f_fmul_st0(m, 1, 0x0C9, at + 0x15);                              /* fmul st,st(1)       */
    stf(EL(0), f_fst_m(m, 1, 0x159, at + 0x17, LE(0)));              /* fstp [ecx-28h]      */
    f_fld_st(m, 0, 0x1C0, at + 0x1A);                                /* fld st(0)           */
    f_fmul_m(m, ldf(EL(1)), 0x049, at + 0x1C, LE(1));                /* fmul [ecx-24h]      */
    stf(EL(1), f_fst_m(m, 1, 0x159, at + 0x1F, LE(1)));              /* fstp [ecx-24h]      */
    f_fld_m(m, ldf(EL(2)), 0x141, at + 0x22, LE(2));                 /* fld [ecx-20h]       */
    f_fmul_st0(m, 1, 0x0C9, at + 0x25);                              /* fmul st,st(1)       */
    stf(EL(2), f_fst_m(m, 1, 0x159, at + 0x27, LE(2)));              /* fstp [ecx-20h]      */
    f_fld_m(m, ldf(EL(3)), 0x141, at + 0x2A, LE(3));                 /* fld [ecx-1Ch]       */
    f_fmul_st0(m, 1, 0x0C9, at + 0x2D);                              /* fmul st,st(1)       */
    stf(EL(3), f_fst_m(m, 1, 0x159, at + 0x2F, LE(3)));              /* fstp [ecx-1Ch]      */
    f_fld_m(m, ldf(EL(4)), 0x141, at + 0x32, LE(4));                 /* fld [ecx-18h]       */
    f_fmul_st0(m, 1, 0x0C9, at + 0x35);                              /* fmul st,st(1)       */
    stf(EL(4), f_fst_m(m, 1, 0x159, at + 0x37, LE(4)));              /* fstp [ecx-18h]      */
    f_fld_m(m, ldf(EL(5)), 0x141, at + 0x3A, LE(5));                 /* fld [ecx-14h]       */
    f_fmul_st0(m, 1, 0x0C9, at + 0x3D);                              /* fmul st,st(1)       */
    stf(EL(5), f_fst_m(m, 1, 0x159, at + 0x3F, LE(5)));              /* fstp [ecx-14h]      */
    f_fld_st(m, 0, 0x1C0, at + 0x42);                                /* fld st(0)           */
    f_fmul_m(m, ldf(EL(6)), 0x049, at + 0x44, LE(6));                /* fmul [ecx-10h]      */
    stf(EL(6), f_fst_m(m, 1, 0x159, at + 0x47, LE(6)));              /* fstp [ecx-10h]      */
    f_fld_m(m, ldf(EL(7)), 0x141, at + 0x4A, LE(7));                 /* fld [ecx-0Ch]       */
    f_fmul_st0(m, 1, 0x0C9, at + 0x4D);                              /* fmul st,st(1)       */
    stf(EL(7), f_fst_m(m, 1, 0x159, at + 0x4F, LE(7)));              /* fstp [ecx-0Ch]      */
#undef EL
#undef LE
}

/* The FXSAVE status word after the second FNSTSW: B, TOP, ES and SF kept, the condition codes
 * of FCOMP (C1 as it was), DE from that FLD m32 of the epsilon, IE 0 (FCOMP was ordered), PE,
 * UE, OE and ZE as they were. */
AI uint16_t inv_swd(const uint8_t *s, const fx *m, uint8_t de) {
    return (uint16_t)((ld16(s + O_SWD) & 0xB8C0u) | ((m->c0 & 1u) << 8) | ((s[O_C1] & 1u) << 9)
                      | ((m->c2 & 1u) << 10) | ((m->c3 & 1u) << 14) | ((s[O_PE] & 1u) << 5)
                      | ((s[O_UE] & 1u) << 4) | ((s[O_OE] & 1u) << 3) | ((s[O_ZE] & 1u) << 2)
                      | ((de & 1u) << 1));
}

AI int inv_native(uint8_t *s, uint32_t pc) {
    uint8_t *g = sa2_guest_fast_base;
    const uint32_t esp = ld32(s + O_ESP), ss = ld32(s + O_SS_BASE), ds = ld32(s + O_DS_BASE);
    const uint32_t E = ss + esp, F = E - INV_LO;
    const uint32_t ecx = ld32(s + O_ECX), ebx = ld32(s + O_EBX);
    const uint32_t mi = ds + ecx, dst = ld32(s + O_ES_BASE) + ebx;
    const uint32_t kz = ds + INV_K_ZERO, ke = ds + INV_K_EPS;
    if (!g || !in_fast(F, INV_LEN) || !in_fast(mi, 0x40) || !in_fast(dst, 0x40) || !in_fast(kz, 4)
            || !in_fast(ke, 4))
        return 1 + WHY_MEMORY;
    if (s[O_DF]) return 1 + WHY_DF;                                  /* rep movsd forwards  */
    /* The input and the constants are read among the frame's writes; the output is written
     * last, from the frame or the identity, which must not meet it. */
    if (!apart(F, INV_LEN, mi, 0x40) || !apart(F, INV_LEN, dst, 0x40) || !apart(F, INV_LEN, kz, 4)
            || !apart(F, INV_LEN, ke, 4))
        return 1 + WHY_OVERLAP;
    uint32_t ok = finite32(ld32(g + kz)) & finite32(ld32(g + ke));
    for (int k = 0; k < 16; k++) ok &= finite32(ld32(g + mi + 4u * k));
    if (!ok) return 1 + WHY_VALUE;
    nj_exc_clear();
    uint8_t sh[INV_LEN];
    memcpy(sh, g + F, INV_LEN);
    fx mx, *m = &mx;
    f_init(m);
    uint16_t ax;
    st32(SHI(-0x78), ld32(s + O_ESI));                               /* push esi            */
    st32(SHI(-0x7C), ld32(s + O_EDI));                               /* push edi            */
    f_fld_m(m, ldf(g + mi + 0x0), 0x101, pc + 0x003, mi + 0x0);                              /* +003 fld st,dword ptr [ecx] */
    f_fmul_m(m, ldf(g + mi + 0x14), 0x049, pc + 0x006, mi + 0x14);                           /* +006 fmul st,dword ptr [ecx+14h] */
    f_fld_m(m, ldf(g + mi + 0x10), 0x141, pc + 0x00A, mi + 0x10);                            /* +00A fld st,dword ptr [ecx+10h] */
    f_fmul_m(m, ldf(g + mi + 0x4), 0x049, pc + 0x00D, mi + 0x4);                             /* +00D fmul st,dword ptr [ecx+4] */
    f_fsubp(m, 1, 0x2E9, pc + 0x010);                                                        /* +010 fsubp */
    stf(SHI(-0x64), f_fst_m(m, 1, 0x15C, pc + 0x012, E - 0x64));                              /* +012 fstp dword ptr [esp+18h],st */
    f_fld_m(m, ldf(g + mi + 0x18), 0x141, pc + 0x016, mi + 0x18);                            /* +016 fld st,dword ptr [ecx+18h] */
    f_fmul_m(m, ldf(g + mi + 0x0), 0x009, pc + 0x019, mi + 0x0);                             /* +019 fmul st,dword ptr [ecx] */
    f_fld_m(m, ldf(g + mi + 0x10), 0x141, pc + 0x01B, mi + 0x10);                            /* +01B fld st,dword ptr [ecx+10h] */
    f_fmul_m(m, ldf(g + mi + 0x8), 0x049, pc + 0x01E, mi + 0x8);                             /* +01E fmul st,dword ptr [ecx+8] */
    f_fsubp(m, 1, 0x2E9, pc + 0x021);                                                        /* +021 fsubp */
    stf(SHI(-0x4C), f_fst_m(m, 1, 0x15C, pc + 0x023, E - 0x4C));                              /* +023 fstp dword ptr [esp+30h],st */
    f_fld_m(m, ldf(g + mi + 0x1C), 0x141, pc + 0x027, mi + 0x1C);                            /* +027 fld st,dword ptr [ecx+1Ch] */
    f_fmul_m(m, ldf(g + mi + 0x0), 0x009, pc + 0x02A, mi + 0x0);                             /* +02A fmul st,dword ptr [ecx] */
    f_fld_m(m, ldf(g + mi + 0x10), 0x141, pc + 0x02C, mi + 0x10);                            /* +02C fld st,dword ptr [ecx+10h] */
    f_fmul_m(m, ldf(g + mi + 0xC), 0x049, pc + 0x02F, mi + 0xC);                             /* +02F fmul st,dword ptr [ecx+0Ch] */
    f_fsubp(m, 1, 0x2E9, pc + 0x032);                                                        /* +032 fsubp */
    stf(SHI(-0x58), f_fst_m(m, 1, 0x15C, pc + 0x034, E - 0x58));                              /* +034 fstp dword ptr [esp+24h],st */
    f_fld_m(m, ldf(g + mi + 0x18), 0x141, pc + 0x038, mi + 0x18);                            /* +038 fld st,dword ptr [ecx+18h] */
    f_fmul_m(m, ldf(g + mi + 0x4), 0x049, pc + 0x03B, mi + 0x4);                             /* +03B fmul st,dword ptr [ecx+4] */
    f_fld_m(m, ldf(g + mi + 0x14), 0x141, pc + 0x03E, mi + 0x14);                            /* +03E fld st,dword ptr [ecx+14h] */
    f_fmul_m(m, ldf(g + mi + 0x8), 0x049, pc + 0x041, mi + 0x8);                             /* +041 fmul st,dword ptr [ecx+8] */
    f_fsubp(m, 1, 0x2E9, pc + 0x044);                                                        /* +044 fsubp */
    stf(SHI(-0x5C), f_fst_m(m, 1, 0x15C, pc + 0x046, E - 0x5C));                              /* +046 fstp dword ptr [esp+20h],st */
    f_fld_m(m, ldf(g + mi + 0x1C), 0x141, pc + 0x04A, mi + 0x1C);                            /* +04A fld st,dword ptr [ecx+1Ch] */
    f_fmul_m(m, ldf(g + mi + 0x4), 0x049, pc + 0x04D, mi + 0x4);                             /* +04D fmul st,dword ptr [ecx+4] */
    f_fld_m(m, ldf(g + mi + 0x14), 0x141, pc + 0x050, mi + 0x14);                            /* +050 fld st,dword ptr [ecx+14h] */
    f_fmul_m(m, ldf(g + mi + 0xC), 0x049, pc + 0x053, mi + 0xC);                             /* +053 fmul st,dword ptr [ecx+0Ch] */
    f_fsubp(m, 1, 0x2E9, pc + 0x056);                                                        /* +056 fsubp */
    stf(SHI(-0x60), f_fst_m(m, 1, 0x15C, pc + 0x058, E - 0x60));                              /* +058 fstp dword ptr [esp+1Ch],st */
    f_fld_m(m, ldf(g + mi + 0x1C), 0x141, pc + 0x05C, mi + 0x1C);                            /* +05C fld st,dword ptr [ecx+1Ch] */
    f_fmul_m(m, ldf(g + mi + 0x8), 0x049, pc + 0x05F, mi + 0x8);                             /* +05F fmul st,dword ptr [ecx+8] */
    f_fld_m(m, ldf(g + mi + 0x18), 0x141, pc + 0x062, mi + 0x18);                            /* +062 fld st,dword ptr [ecx+18h] */
    f_fmul_m(m, ldf(g + mi + 0xC), 0x049, pc + 0x065, mi + 0xC);                             /* +065 fmul st,dword ptr [ecx+0Ch] */
    f_fsubp(m, 1, 0x2E9, pc + 0x068);                                                        /* +068 fsubp */
    stf(SHI(-0x68), f_fst_m(m, 1, 0x15C, pc + 0x06A, E - 0x68));                              /* +06A fstp dword ptr [esp+14h],st */
    f_fld_m(m, ldf(g + mi + 0x34), 0x141, pc + 0x06E, mi + 0x34);                            /* +06E fld st,dword ptr [ecx+34h] */
    f_fmul_m(m, ldf(g + mi + 0x20), 0x049, pc + 0x071, mi + 0x20);                           /* +071 fmul st,dword ptr [ecx+20h] */
    f_fld_m(m, ldf(g + mi + 0x30), 0x141, pc + 0x074, mi + 0x30);                            /* +074 fld st,dword ptr [ecx+30h] */
    f_fmul_m(m, ldf(g + mi + 0x24), 0x049, pc + 0x077, mi + 0x24);                           /* +077 fmul st,dword ptr [ecx+24h] */
    f_fsubp(m, 1, 0x2E9, pc + 0x07A);                                                        /* +07A fsubp */
    stf(SHI(-0x6C), f_fst_m(m, 1, 0x15C, pc + 0x07C, E - 0x6C));                              /* +07C fstp dword ptr [esp+10h],st */
    f_fld_m(m, ldf(g + mi + 0x38), 0x141, pc + 0x080, mi + 0x38);                            /* +080 fld st,dword ptr [ecx+38h] */
    f_fmul_m(m, ldf(g + mi + 0x20), 0x049, pc + 0x083, mi + 0x20);                           /* +083 fmul st,dword ptr [ecx+20h] */
    f_fld_m(m, ldf(g + mi + 0x30), 0x141, pc + 0x086, mi + 0x30);                            /* +086 fld st,dword ptr [ecx+30h] */
    f_fmul_m(m, ldf(g + mi + 0x28), 0x049, pc + 0x089, mi + 0x28);                           /* +089 fmul st,dword ptr [ecx+28h] */
    f_fsubp(m, 1, 0x2E9, pc + 0x08C);                                                        /* +08C fsubp */
    stf(SHI(-0x74), f_fst_m(m, 1, 0x15C, pc + 0x08E, E - 0x74));                              /* +08E fstp dword ptr [esp+8],st */
    f_fld_m(m, ldf(g + mi + 0x3C), 0x141, pc + 0x092, mi + 0x3C);                            /* +092 fld st,dword ptr [ecx+3Ch] */
    f_fmul_m(m, ldf(g + mi + 0x20), 0x049, pc + 0x095, mi + 0x20);                           /* +095 fmul st,dword ptr [ecx+20h] */
    f_fld_m(m, ldf(g + mi + 0x30), 0x141, pc + 0x098, mi + 0x30);                            /* +098 fld st,dword ptr [ecx+30h] */
    f_fmul_m(m, ldf(g + mi + 0x2C), 0x049, pc + 0x09B, mi + 0x2C);                           /* +09B fmul st,dword ptr [ecx+2Ch] */
    f_fsubp(m, 1, 0x2E9, pc + 0x09E);                                                        /* +09E fsubp */
    stf(SHI(-0x70), f_fst_m(m, 1, 0x15C, pc + 0x0A0, E - 0x70));                              /* +0A0 fstp dword ptr [esp+0Ch],st */
    f_fld_m(m, ldf(g + mi + 0x38), 0x141, pc + 0x0A4, mi + 0x38);                            /* +0A4 fld st,dword ptr [ecx+38h] */
    f_fmul_m(m, ldf(g + mi + 0x24), 0x049, pc + 0x0A7, mi + 0x24);                           /* +0A7 fmul st,dword ptr [ecx+24h] */
    f_fld_m(m, ldf(g + mi + 0x34), 0x141, pc + 0x0AA, mi + 0x34);                            /* +0AA fld st,dword ptr [ecx+34h] */
    f_fmul_m(m, ldf(g + mi + 0x28), 0x049, pc + 0x0AD, mi + 0x28);                           /* +0AD fmul st,dword ptr [ecx+28h] */
    f_fsubp(m, 1, 0x2E9, pc + 0x0B0);                                                        /* +0B0 fsubp */
    stf(SHI(-0x48), f_fst_m(m, 1, 0x15C, pc + 0x0B2, E - 0x48));                              /* +0B2 fstp dword ptr [esp+34h],st */
    f_fld_m(m, ldf(g + mi + 0x3C), 0x141, pc + 0x0B6, mi + 0x3C);                            /* +0B6 fld st,dword ptr [ecx+3Ch] */
    f_fmul_m(m, ldf(g + mi + 0x24), 0x049, pc + 0x0B9, mi + 0x24);                           /* +0B9 fmul st,dword ptr [ecx+24h] */
    f_fld_m(m, ldf(g + mi + 0x34), 0x141, pc + 0x0BC, mi + 0x34);                            /* +0BC fld st,dword ptr [ecx+34h] */
    f_fmul_m(m, ldf(g + mi + 0x2C), 0x049, pc + 0x0BF, mi + 0x2C);                           /* +0BF fmul st,dword ptr [ecx+2Ch] */
    f_fsubp(m, 1, 0x2E9, pc + 0x0C2);                                                        /* +0C2 fsubp */
    stf(SHI(-0x50), f_fst_m(m, 1, 0x15C, pc + 0x0C4, E - 0x50));                              /* +0C4 fstp dword ptr [esp+2Ch],st */
    f_fld_m(m, ldf(g + mi + 0x3C), 0x141, pc + 0x0C8, mi + 0x3C);                            /* +0C8 fld st,dword ptr [ecx+3Ch] */
    f_fmul_m(m, ldf(g + mi + 0x28), 0x049, pc + 0x0CB, mi + 0x28);                           /* +0CB fmul st,dword ptr [ecx+28h] */
    f_fld_m(m, ldf(g + mi + 0x38), 0x141, pc + 0x0CE, mi + 0x38);                            /* +0CE fld st,dword ptr [ecx+38h] */
    f_fmul_m(m, ldf(g + mi + 0x2C), 0x049, pc + 0x0D1, mi + 0x2C);                           /* +0D1 fmul st,dword ptr [ecx+2Ch] */
    f_fsubp(m, 1, 0x2E9, pc + 0x0D4);                                                        /* +0D4 fsubp */
    stf(SHI(-0x54), f_fst_m(m, 1, 0x15C, pc + 0x0D6, E - 0x54));                              /* +0D6 fstp dword ptr [esp+28h],st */
    f_fld_m(m, ldf(SHI(-0x54)), 0x144, pc + 0x0DA, E - 0x54);                                 /* +0DA fld st,dword ptr [esp+28h] */
    f_fld_st(m, 0, 0x1C0, pc + 0x0DE);                                                       /* +0DE fld st,st(0) */
    f_fld_m(m, ldf(SHI(-0x64)), 0x144, pc + 0x0E0, E - 0x64);                                 /* +0E0 fld st,dword ptr [esp+18h] */
    f_fld_st(m, 0, 0x1C0, pc + 0x0E4);                                                       /* +0E4 fld st,st(0) */
    f_fmulp(m, 2, 0x2CA, pc + 0x0E6);                                                        /* +0E6 fmulp st(2),st */
    f_fld_m(m, ldf(SHI(-0x50)), 0x144, pc + 0x0E8, E - 0x50);                                 /* +0E8 fld st,dword ptr [esp+2Ch] */
    f_fld_st(m, 0, 0x1C0, pc + 0x0EC);                                                       /* +0EC fld st,st(0) */
    f_fld_m(m, ldf(SHI(-0x4C)), 0x144, pc + 0x0EE, E - 0x4C);                                 /* +0EE fld st,dword ptr [esp+30h] */
    f_fld_st(m, 0, 0x1C0, pc + 0x0F2);                                                       /* +0F2 fld st,st(0) */
    f_fmulp(m, 2, 0x2CA, pc + 0x0F4);                                                        /* +0F4 fmulp st(2),st */
    f_fxch(m, 4, 0x1CC, pc + 0x0F6);                                                         /* +0F6 fxch st,st(4) */
    f_fsubrp(m, 1, 0x2E1, pc + 0x0F8);                                                       /* +0F8 fsubrp */
    f_fld_m(m, ldf(SHI(-0x48)), 0x144, pc + 0x0FA, E - 0x48);                                 /* +0FA fld st,dword ptr [esp+34h] */
    f_fld_st(m, 0, 0x1C0, pc + 0x0FE);                                                       /* +0FE fld st,st(0) */
    f_fmul_m(m, ldf(SHI(-0x58)), 0x04C, pc + 0x100, E - 0x58);                                /* +100 fmul st,dword ptr [esp+24h] */
    f_faddp(m, 2, 0x2C2, pc + 0x104);                                                        /* +104 faddp st(2),st */
    f_fld_m(m, ldf(SHI(-0x70)), 0x144, pc + 0x106, E - 0x70);                                 /* +106 fld st,dword ptr [esp+0Ch] */
    f_fmul_m(m, ldf(SHI(-0x5C)), 0x04C, pc + 0x10A, E - 0x5C);                                /* +10A fmul st,dword ptr [esp+20h] */
    f_faddp(m, 2, 0x2C2, pc + 0x10E);                                                        /* +10E faddp st(2),st */
    f_fld_m(m, ldf(SHI(-0x74)), 0x144, pc + 0x110, E - 0x74);                                 /* +110 fld st,dword ptr [esp+8] */
    f_fmul_m(m, ldf(SHI(-0x60)), 0x04C, pc + 0x114, E - 0x60);                                /* +114 fmul st,dword ptr [esp+1Ch] */
    f_fsubp(m, 2, 0x2EA, pc + 0x118);                                                        /* +118 fsubp st(2),st */
    f_fld_m(m, ldf(SHI(-0x6C)), 0x144, pc + 0x11A, E - 0x6C);                                 /* +11A fld st,dword ptr [esp+10h] */
    f_fmul_m(m, ldf(SHI(-0x68)), 0x04C, pc + 0x11E, E - 0x68);                                /* +11E fmul st,dword ptr [esp+14h] */
    f_faddp(m, 2, 0x2C2, pc + 0x122);                                                        /* +122 faddp st(2),st */
    f_fxch(m, 1, 0x1C9, pc + 0x124);                                                         /* +124 fxch */
    stf(SHI(-0x54), f_fst_m(m, 1, 0x15C, pc + 0x126, E - 0x54));                              /* +126 fstp dword ptr [esp+28h],st */
    f_fld_m(m, ldf(g + ds + 0x008AF004u), 0x105, pc + 0x12A, ds + 0x008AF004u);              /* +12A fld st,dword ptr [8AF004h] */
    f_fld_m(m, ldf(SHI(-0x54)), 0x144, pc + 0x130, E - 0x54);                                 /* +130 fld st,dword ptr [esp+28h] */
    f_fcom_st(m, 1, 0x0D1, pc + 0x134);                                                      /* +134 fcom */
    ax = f_fnstsw(m);                                                                        /* +136 fnstsw ax */
    f_fstp_st(m, 1, 0x1D9, pc + 0x138);                                                      /* +138 fstp st(1),st */
    if (parity8((ax >> 8) & 5u) == 0)                             /* test ah,5 ; jp +141h */
        f_fchs(m, 0x1E0, pc + 0x13F);                                                            /* +13F fchs */
    stf(SHI(-0x64), f_fst_m(m, 1, 0x15C, pc + 0x141, E - 0x64));                              /* +141 fstp dword ptr [esp+18h],st */
    f_fld_m(m, ldf(g + ds + 0x0089E8B4u), 0x105, pc + 0x145, ds + 0x0089E8B4u);              /* +145 fld st,dword ptr [89E8B4h] */
    f_fcomp_m(m, ldf(SHI(-0x64)), 0x05C, pc + 0x14B, E - 0x64);                               /* +14B fcomp st,dword ptr [esp+18h] */
    ax = f_fnstsw(m);                                                                        /* +14F fnstsw ax */
    const uint8_t de2 = denormal32(ld32(g + ke));                    /* that FLD m32's DE   */
    slots st;
    if (!((ax >> 8) & 1u)) {                                         /* test ah,1 ; jne +176h */
        /* The identity: pop the five minors, then rep movsd from 0x01AF1A50. */
        f_fstp_st(m, 4, 0x1DC, pc + 0x156);                                                      /* +156 fstp st(4),st */
        f_fstp_st(m, 1, 0x1D9, pc + 0x15D);                                                      /* +15D fstp st(1),st */
        f_fstp_st(m, 0, 0x1D8, pc + 0x164);                                                      /* +164 fstp st(0),st */
        f_fstp_st(m, 0, 0x1D8, pc + 0x168);                                                      /* +168 fstp st(0),st */
        f_fstp_st(m, 0, 0x1D8, pc + 0x16C);                                                      /* +16C fstp st(0),st */
        const uint32_t id = ds + INV_IDENTITY;
        if (!in_fast(id, 0x40)) return 1 + WHY_MEMORY;
        if (!apart(id, 0x40, F, INV_LEN) || !apart(id, 0x40, dst, 0x40)) return 1 + WHY_OVERLAP;
        f_slots(m, s, &st);
        NJ_SETTLE(sh);
        NJ_SETTLE(&st);
        if (nj_exc_raised()) return 1 + WHY_VALUE;
        memcpy(g + F, sh, INV_LEN);
        memcpy(g + dst, g + id, 0x40);
        /* The last FSTP ST(i) left DE 0; FCOMP's condition codes and IE 0 stand. */
        f_store(m, &st, s, inv_swd(s, m, de2), 0x1D8, pc + 0x16C, E - 0x64);
        s[O_C0] = m->c0; s[O_C2] = m->c2; s[O_C3] = m->c3;
        s[O_DE] = 0;
        s[O_IE] = 0;
        fl_add_store(s, esp - 0x74, 0x74);                           /* add esp,74h         */
        st32(s + O_EAX, ebx);
        st32(s + O_ECX, 0);
        st32(s + O_EIP, ld32(SHI(0)));                               /* ret                 */
        st32(s + O_ESP, esp + 4);
        sa2_ninja_paths[NJ_INVERT][1]++;
        return 0;
    }
    /* The adjugate is written through SS and scaled and copied out through DS. */
    if (ss != ds) return 1 + WHY_SEGMENT;
    const uint16_t swd = inv_swd(s, m, de2);
    f_fld_st(m, 4, 0x1C4, pc + 0x176);                                                       /* +176 fld st,st(4) */
    f_fmul_m(m, ldf(g + mi + 0x14), 0x049, pc + 0x178, mi + 0x14);                           /* +178 fmul st,dword ptr [ecx+14h] */
    f_fld_m(m, ldf(g + mi + 0x18), 0x141, pc + 0x17B, mi + 0x18);                            /* +17B fld st,dword ptr [ecx+18h] */
    f_fmul_st0(m, 3, 0x0CB, pc + 0x17E);                                                     /* +17E fmul st,st(3) */
    f_fsubp(m, 1, 0x2E9, pc + 0x180);                                                        /* +180 fsubp */
    f_fld_m(m, ldf(g + mi + 0x1C), 0x141, pc + 0x182, mi + 0x1C);                            /* +182 fld st,dword ptr [ecx+1Ch] */
    f_fmul_st0(m, 2, 0x0CA, pc + 0x185);                                                     /* +185 fmul st,st(2) */
    f_faddp(m, 1, 0x2C1, pc + 0x187);                                                        /* +187 faddp */
    stf(SHI(-0x44), f_fst_m(m, 1, 0x15C, pc + 0x189, E - 0x44));                              /* +189 fstp dword ptr [esp+38h],st */
    f_fld_m(m, ldf(g + mi + 0x18), 0x141, pc + 0x18D, mi + 0x18);                            /* +18D fld st,dword ptr [ecx+18h] */
    f_fmul_m(m, ldf(SHI(-0x70)), 0x04C, pc + 0x190, E - 0x70);                                /* +190 fmul st,dword ptr [esp+0Ch] */
    f_fld_st(m, 5, 0x1C5, pc + 0x194);                                                       /* +194 fld st,st(5) */
    f_fmul_m(m, ldf(g + mi + 0x10), 0x049, pc + 0x196, mi + 0x10);                           /* +196 fmul st,dword ptr [ecx+10h] */
    f_fsubp(m, 1, 0x2E9, pc + 0x199);                                                        /* +199 fsubp */
    f_fld_m(m, ldf(g + mi + 0x1C), 0x141, pc + 0x19B, mi + 0x1C);                            /* +19B fld st,dword ptr [ecx+1Ch] */
    f_fmul_m(m, ldf(SHI(-0x74)), 0x04C, pc + 0x19E, E - 0x74);                                /* +19E fmul st,dword ptr [esp+8] */
    f_fsubp(m, 1, 0x2E9, pc + 0x1A2);                                                        /* +1A2 fsubp */
    stf(SHI(-0x34), f_fst_m(m, 1, 0x15C, pc + 0x1A4, E - 0x34));                              /* +1A4 fstp dword ptr [esp+48h],st */
    f_fld_st(m, 1, 0x1C1, pc + 0x1A8);                                                       /* +1A8 fld st,st(1) */
    f_fmul_m(m, ldf(g + mi + 0x10), 0x049, pc + 0x1AA, mi + 0x10);                           /* +1AA fmul st,dword ptr [ecx+10h] */
    f_fld_m(m, ldf(SHI(-0x70)), 0x144, pc + 0x1AD, E - 0x70);                                 /* +1AD fld st,dword ptr [esp+0Ch] */
    f_fmul_m(m, ldf(g + mi + 0x14), 0x049, pc + 0x1B1, mi + 0x14);                           /* +1B1 fmul st,dword ptr [ecx+14h] */
    f_fsubp(m, 1, 0x2E9, pc + 0x1B4);                                                        /* +1B4 fsubp */
    f_fld_m(m, ldf(g + mi + 0x1C), 0x141, pc + 0x1B6, mi + 0x1C);                            /* +1B6 fld st,dword ptr [ecx+1Ch] */
    f_fmul_m(m, ldf(SHI(-0x6C)), 0x04C, pc + 0x1B9, E - 0x6C);                                /* +1B9 fmul st,dword ptr [esp+10h] */
    f_faddp(m, 1, 0x2C1, pc + 0x1BD);                                                        /* +1BD faddp */
    stf(SHI(-0x24), f_fst_m(m, 1, 0x15C, pc + 0x1BF, E - 0x24));                              /* +1BF fstp dword ptr [esp+58h],st */
    f_fld_m(m, ldf(SHI(-0x74)), 0x144, pc + 0x1C3, E - 0x74);                                 /* +1C3 fld st,dword ptr [esp+8] */
    f_fmul_m(m, ldf(g + mi + 0x14), 0x049, pc + 0x1C7, mi + 0x14);                           /* +1C7 fmul st,dword ptr [ecx+14h] */
    f_fld_st(m, 1, 0x1C1, pc + 0x1CA);                                                       /* +1CA fld st,st(1) */
    f_fmul_m(m, ldf(g + mi + 0x10), 0x049, pc + 0x1CC, mi + 0x10);                           /* +1CC fmul st,dword ptr [ecx+10h] */
    f_fsubp(m, 1, 0x2E9, pc + 0x1CF);                                                        /* +1CF fsubp */
    f_fld_m(m, ldf(g + mi + 0x18), 0x141, pc + 0x1D1, mi + 0x18);                            /* +1D1 fld st,dword ptr [ecx+18h] */
    f_fmul_m(m, ldf(SHI(-0x6C)), 0x04C, pc + 0x1D4, E - 0x6C);                                /* +1D4 fmul st,dword ptr [esp+10h] */
    f_fsubp(m, 1, 0x2E9, pc + 0x1D8);                                                        /* +1D8 fsubp */
    stf(SHI(-0x14), f_fst_m(m, 1, 0x15C, pc + 0x1DA, E - 0x14));                              /* +1DA fstp dword ptr [esp+68h],st */
    f_fld_st(m, 1, 0x1C1, pc + 0x1DE);                                                       /* +1DE fld st,st(1) */
    f_fmul_m(m, ldf(g + mi + 0x8), 0x049, pc + 0x1E0, mi + 0x8);                             /* +1E0 fmul st,dword ptr [ecx+8] */
    f_fld_st(m, 5, 0x1C5, pc + 0x1E3);                                                       /* +1E3 fld st,st(5) */
    f_fmul_m(m, ldf(g + mi + 0x4), 0x049, pc + 0x1E5, mi + 0x4);                             /* +1E5 fmul st,dword ptr [ecx+4] */
    f_fsubp(m, 1, 0x2E9, pc + 0x1E8);                                                        /* +1E8 fsubp */
    f_fld_st(m, 1, 0x1C1, pc + 0x1EA);                                                       /* +1EA fld st,st(1) */
    f_fmul_m(m, ldf(g + mi + 0xC), 0x049, pc + 0x1EC, mi + 0xC);                             /* +1EC fmul st,dword ptr [ecx+0Ch] */
    f_fsubp(m, 1, 0x2E9, pc + 0x1EF);                                                        /* +1EF fsubp */
    stf(SHI(-0x40), f_fst_m(m, 1, 0x15C, pc + 0x1F1, E - 0x40));                              /* +1F1 fstp dword ptr [esp+3Ch],st */
    f_fld_m(m, ldf(g + mi + 0x0), 0x101, pc + 0x1F5, mi + 0x0);                              /* +1F5 fld st,dword ptr [ecx] */
    f_fmulp(m, 5, 0x2CD, pc + 0x1F7);                                                        /* +1F7 fmulp st(5),st */
    f_fld_m(m, ldf(SHI(-0x70)), 0x144, pc + 0x1F9, E - 0x70);                                 /* +1F9 fld st,dword ptr [esp+0Ch] */
    f_fld_st(m, 0, 0x1C0, pc + 0x1FD);                                                       /* +1FD fld st,st(0) */
    f_fmul_m(m, ldf(g + mi + 0x8), 0x049, pc + 0x1FF, mi + 0x8);                             /* +1FF fmul st,dword ptr [ecx+8] */
    f_fsubp(m, 6, 0x2EE, pc + 0x202);                                                        /* +202 fsubp st(6),st */
    f_fld_m(m, ldf(SHI(-0x74)), 0x144, pc + 0x204, E - 0x74);                                 /* +204 fld st,dword ptr [esp+8] */
    f_fmul_m(m, ldf(g + mi + 0xC), 0x049, pc + 0x208, mi + 0xC);                             /* +208 fmul st,dword ptr [ecx+0Ch] */
    f_faddp(m, 6, 0x2C6, pc + 0x20B);                                                        /* +20B faddp st(6),st */
    f_fxch(m, 5, 0x1CD, pc + 0x20D);                                                         /* +20D fxch st,st(5) */
    stf(SHI(-0x30), f_fst_m(m, 1, 0x15C, pc + 0x20F, E - 0x30));                              /* +20F fstp dword ptr [esp+4Ch],st */
    f_fld_m(m, ldf(g + mi + 0x4), 0x141, pc + 0x213, mi + 0x4);                              /* +213 fld st,dword ptr [ecx+4] */
    f_fmulp(m, 5, 0x2CD, pc + 0x216);                                                        /* +216 fmulp st(5),st */
    f_fld_m(m, ldf(g + mi + 0x0), 0x101, pc + 0x218, mi + 0x0);                              /* +218 fld st,dword ptr [ecx] */
    f_fmulp(m, 2, 0x2CA, pc + 0x21A);                                                        /* +21A fmulp st(2),st */
    f_fxch(m, 4, 0x1CC, pc + 0x21C);                                                         /* +21C fxch st,st(4) */
    f_fsubrp(m, 1, 0x2E1, pc + 0x21E);                                                       /* +21E fsubrp */
    f_fld_m(m, ldf(SHI(-0x6C)), 0x144, pc + 0x220, E - 0x6C);                                 /* +220 fld st,dword ptr [esp+10h] */
    f_fld_st(m, 0, 0x1C0, pc + 0x224);                                                       /* +224 fld st,st(0) */
    f_fmul_m(m, ldf(g + mi + 0xC), 0x049, pc + 0x226, mi + 0xC);                             /* +226 fmul st,dword ptr [ecx+0Ch] */
    f_fsubp(m, 2, 0x2EA, pc + 0x229);                                                        /* +229 fsubp st(2),st */
    f_fxch(m, 1, 0x1C9, pc + 0x22B);                                                         /* +22B fxch */
    stf(SHI(-0x20), f_fst_m(m, 1, 0x15C, pc + 0x22D, E - 0x20));                              /* +22D fstp dword ptr [esp+5Ch],st */
    f_fld_m(m, ldf(g + mi + 0x0), 0x101, pc + 0x231, mi + 0x0);                              /* +231 fld st,dword ptr [ecx] */
    f_fmulp(m, 4, 0x2CC, pc + 0x233);                                                        /* +233 fmulp st(4),st */
    f_fld_m(m, ldf(SHI(-0x74)), 0x144, pc + 0x235, E - 0x74);                                 /* +235 fld st,dword ptr [esp+8] */
    f_fmul_m(m, ldf(g + mi + 0x4), 0x049, pc + 0x239, mi + 0x4);                             /* +239 fmul st,dword ptr [ecx+4] */
    f_fsubp(m, 4, 0x2EC, pc + 0x23C);                                                        /* +23C fsubp st(4),st */
    f_fmul_m(m, ldf(g + mi + 0x8), 0x049, pc + 0x23E, mi + 0x8);                             /* +23E fmul st,dword ptr [ecx+8] */
    f_faddp(m, 3, 0x2C3, pc + 0x241);                                                        /* +241 faddp st(3),st */
    f_fxch(m, 2, 0x1CA, pc + 0x243);                                                         /* +243 fxch st,st(2) */
    stf(SHI(-0x10), f_fst_m(m, 1, 0x15C, pc + 0x245, E - 0x10));                              /* +245 fstp dword ptr [esp+6Ch],st */
    f_fld_m(m, ldf(g + mi + 0x34), 0x141, pc + 0x249, mi + 0x34);                            /* +249 fld st,dword ptr [ecx+34h] */
    f_fld_m(m, ldf(SHI(-0x68)), 0x144, pc + 0x24C, E - 0x68);                                 /* +24C fld st,dword ptr [esp+14h] */
    f_fld_st(m, 0, 0x1C0, pc + 0x250);                                                       /* +250 fld st,st(0) */
    f_fmulp(m, 2, 0x2CA, pc + 0x252);                                                        /* +252 fmulp st(2),st */
    f_fld_m(m, ldf(g + mi + 0x38), 0x141, pc + 0x254, mi + 0x38);                            /* +254 fld st,dword ptr [ecx+38h] */
    f_fld_m(m, ldf(SHI(-0x60)), 0x144, pc + 0x257, E - 0x60);                                 /* +257 fld st,dword ptr [esp+1Ch] */
    f_fld_st(m, 0, 0x1C0, pc + 0x25B);                                                       /* +25B fld st,st(0) */
    f_fmulp(m, 2, 0x2CA, pc + 0x25D);                                                        /* +25D fmulp st(2),st */
    f_fxch(m, 3, 0x1CB, pc + 0x25F);                                                         /* +25F fxch st,st(3) */
    f_fsubrp(m, 1, 0x2E1, pc + 0x261);                                                       /* +261 fsubrp */
    f_fld_m(m, ldf(g + mi + 0x3C), 0x141, pc + 0x263, mi + 0x3C);                            /* +263 fld st,dword ptr [ecx+3Ch] */
    f_fld_m(m, ldf(SHI(-0x5C)), 0x144, pc + 0x266, E - 0x5C);                                 /* +266 fld st,dword ptr [esp+20h] */
    f_fld_st(m, 0, 0x1C0, pc + 0x26A);                                                       /* +26A fld st,st(0) */
    f_fmulp(m, 2, 0x2CA, pc + 0x26C);                                                        /* +26C fmulp st(2),st */
    f_fxch(m, 2, 0x1CA, pc + 0x26E);                                                         /* +26E fxch st,st(2) */
    f_faddp(m, 1, 0x2C1, pc + 0x270);                                                        /* +270 faddp */
    stf(SHI(-0x3C), f_fst_m(m, 1, 0x15C, pc + 0x272, E - 0x3C));                              /* +272 fstp dword ptr [esp+40h],st */
    f_fld_m(m, ldf(g + mi + 0x38), 0x141, pc + 0x276, mi + 0x38);                            /* +276 fld st,dword ptr [ecx+38h] */
    f_fld_m(m, ldf(SHI(-0x58)), 0x144, pc + 0x279, E - 0x58);                                 /* +279 fld st,dword ptr [esp+24h] */
    f_fld_st(m, 0, 0x1C0, pc + 0x27D);                                                       /* +27D fld st,st(0) */
    f_fmulp(m, 2, 0x2CA, pc + 0x27F);                                                        /* +27F fmulp st(2),st */
    f_fld_m(m, ldf(g + mi + 0x30), 0x141, pc + 0x281, mi + 0x30);                            /* +281 fld st,dword ptr [ecx+30h] */
    f_fmulp(m, 4, 0x2CC, pc + 0x284);                                                        /* +284 fmulp st(4),st */
    f_fxch(m, 1, 0x1C9, pc + 0x286);                                                         /* +286 fxch */
    f_fsubrp(m, 3, 0x2E3, pc + 0x288);                                                       /* +288 fsubrp st(3),st */
    f_fld_m(m, ldf(g + mi + 0x3C), 0x141, pc + 0x28A, mi + 0x3C);                            /* +28A fld st,dword ptr [ecx+3Ch] */
    f_fmul_st0(m, 5, 0x0CD, pc + 0x28D);                                                     /* +28D fmul st,st(5) */
    f_fsubp(m, 3, 0x2EB, pc + 0x28F);                                                        /* +28F fsubp st(3),st */
    f_fxch(m, 2, 0x1CA, pc + 0x291);                                                         /* +291 fxch st,st(2) */
    stf(SHI(-0x2C), f_fst_m(m, 1, 0x15C, pc + 0x293, E - 0x2C));                              /* +293 fstp dword ptr [esp+50h],st */
    f_fld_st(m, 2, 0x1C2, pc + 0x297);                                                       /* +297 fld st,st(2) */
    f_fmul_m(m, ldf(g + mi + 0x30), 0x049, pc + 0x299, mi + 0x30);                           /* +299 fmul st,dword ptr [ecx+30h] */
    f_fld_m(m, ldf(g + mi + 0x34), 0x141, pc + 0x29C, mi + 0x34);                            /* +29C fld st,dword ptr [ecx+34h] */
    f_fmul_st0(m, 3, 0x0CB, pc + 0x29F);                                                     /* +29F fmul st,st(3) */
    f_fsubp(m, 1, 0x2E9, pc + 0x2A1);                                                        /* +2A1 fsubp */
    f_fld_m(m, ldf(g + mi + 0x3C), 0x141, pc + 0x2A3, mi + 0x3C);                            /* +2A3 fld st,dword ptr [ecx+3Ch] */
    f_fmul_st0(m, 6, 0x0CE, pc + 0x2A6);                                                     /* +2A6 fmul st,st(6) */
    f_faddp(m, 1, 0x2C1, pc + 0x2A8);                                                        /* +2A8 faddp */
    stf(SHI(-0x1C), f_fst_m(m, 1, 0x15C, pc + 0x2AA, E - 0x1C));                              /* +2AA fstp dword ptr [esp+60h],st */
    f_fld_m(m, ldf(g + mi + 0x34), 0x141, pc + 0x2AE, mi + 0x34);                            /* +2AE fld st,dword ptr [ecx+34h] */
    f_fmul_st0(m, 4, 0x0CC, pc + 0x2B1);                                                     /* +2B1 fmul st,st(4) */
    f_fld_st(m, 1, 0x1C1, pc + 0x2B3);                                                       /* +2B3 fld st,st(1) */
    f_fmul_m(m, ldf(g + mi + 0x30), 0x049, pc + 0x2B5, mi + 0x30);                           /* +2B5 fmul st,dword ptr [ecx+30h] */
    f_fsubp(m, 1, 0x2E9, pc + 0x2B8);                                                        /* +2B8 fsubp */
    f_fld_m(m, ldf(g + mi + 0x38), 0x141, pc + 0x2BA, mi + 0x38);                            /* +2BA fld st,dword ptr [ecx+38h] */
    f_fmul_st0(m, 6, 0x0CE, pc + 0x2BD);                                                     /* +2BD fmul st,st(6) */
    f_fsubp(m, 1, 0x2E9, pc + 0x2BF);                                                        /* +2BF fsubp */
    stf(SHI(-0xC), f_fst_m(m, 1, 0x15C, pc + 0x2C1, E - 0xC));                                /* +2C1 fstp dword ptr [esp+70h],st */
    f_fld_st(m, 2, 0x1C2, pc + 0x2C5);                                                       /* +2C5 fld st,st(2) */
    f_fmul_m(m, ldf(g + mi + 0x28), 0x049, pc + 0x2C7, mi + 0x28);                           /* +2C7 fmul st,dword ptr [ecx+28h] */
    f_fld_m(m, ldf(SHI(-0x68)), 0x144, pc + 0x2CA, E - 0x68);                                 /* +2CA fld st,dword ptr [esp+14h] */
    f_fmul_m(m, ldf(g + mi + 0x24), 0x049, pc + 0x2CE, mi + 0x24);                           /* +2CE fmul st,dword ptr [ecx+24h] */
    f_fsubp(m, 1, 0x2E9, pc + 0x2D1);                                                        /* +2D1 fsubp */
    f_fld_st(m, 1, 0x1C1, pc + 0x2D3);                                                       /* +2D3 fld st,st(1) */
    f_fmul_m(m, ldf(g + mi + 0x2C), 0x049, pc + 0x2D5, mi + 0x2C);                           /* +2D5 fmul st,dword ptr [ecx+2Ch] */
    f_fsubp(m, 1, 0x2E9, pc + 0x2D8);                                                        /* +2D8 fsubp */
    stf(SHI(-0x38), f_fst_m(m, 1, 0x15C, pc + 0x2DA, E - 0x38));                              /* +2DA fstp dword ptr [esp+44h],st */
    f_fld_m(m, ldf(g + mi + 0x20), 0x141, pc + 0x2DE, mi + 0x20);                            /* +2DE fld st,dword ptr [ecx+20h] */
    f_fmul_m(m, ldf(SHI(-0x68)), 0x04C, pc + 0x2E1, E - 0x68);                                /* +2E1 fmul st,dword ptr [esp+14h] */
    f_fld_st(m, 2, 0x1C2, pc + 0x2E5);                                                       /* +2E5 fld st,st(2) */
    f_fmul_m(m, ldf(g + mi + 0x28), 0x049, pc + 0x2E7, mi + 0x28);                           /* +2E7 fmul st,dword ptr [ecx+28h] */
    f_fsubp(m, 1, 0x2E9, pc + 0x2EA);                                                        /* +2EA fsubp */
    f_fld_st(m, 4, 0x1C4, pc + 0x2EC);                                                       /* +2EC fld st,st(4) */
    f_fmul_m(m, ldf(g + mi + 0x2C), 0x049, pc + 0x2EE, mi + 0x2C);                           /* +2EE fmul st,dword ptr [ecx+2Ch] */
    f_faddp(m, 1, 0x2C1, pc + 0x2F1);                                                        /* +2F1 faddp */
    stf(SHI(-0x28), f_fst_m(m, 1, 0x15C, pc + 0x2F3, E - 0x28));                              /* +2F3 fstp dword ptr [esp+54h],st */
    f_fld_m(m, ldf(g + mi + 0x24), 0x141, pc + 0x2F7, mi + 0x24);                            /* +2F7 fld st,dword ptr [ecx+24h] */
    f_fmulp(m, 2, 0x2CA, pc + 0x2FA);                                                        /* +2FA fmulp st(2),st */
    f_fld_m(m, ldf(g + mi + 0x20), 0x141, pc + 0x2FC, mi + 0x20);                            /* +2FC fld st,dword ptr [ecx+20h] */
    f_fmulp(m, 3, 0x2CB, pc + 0x2FF);                                                        /* +2FF fmulp st(3),st */
    f_fxch(m, 1, 0x1C9, pc + 0x301);                                                         /* +301 fxch */
    f_fsubrp(m, 2, 0x2E2, pc + 0x303);                                                       /* +303 fsubrp st(2),st */
    f_fld_st(m, 3, 0x1C3, pc + 0x305);                                                       /* +305 fld st,st(3) */
    f_fmul_m(m, ldf(g + mi + 0x2C), 0x049, pc + 0x307, mi + 0x2C);                           /* +307 fmul st,dword ptr [ecx+2Ch] */
    f_fsubp(m, 2, 0x2EA, pc + 0x30A);                                                        /* +30A fsubp st(2),st */
    f_fxch(m, 1, 0x1C9, pc + 0x30C);                                                         /* +30C fxch */
    stf(SHI(-0x18), f_fst_m(m, 1, 0x15C, pc + 0x30E, E - 0x18));                              /* +30E fstp dword ptr [esp+64h],st */
    f_fmul_m(m, ldf(g + mi + 0x20), 0x049, pc + 0x312, mi + 0x20);                           /* +312 fmul st,dword ptr [ecx+20h] */
    f_fld_m(m, ldf(g + mi + 0x24), 0x141, pc + 0x315, mi + 0x24);                            /* +315 fld st,dword ptr [ecx+24h] */
    f_fmulp(m, 2, 0x2CA, pc + 0x318);                                                        /* +318 fmulp st(2),st */
    f_fsubrp(m, 1, 0x2E1, pc + 0x31A);                                                       /* +31A fsubrp */
    f_fld_m(m, ldf(g + mi + 0x28), 0x141, pc + 0x31C, mi + 0x28);                            /* +31C fld st,dword ptr [ecx+28h] */
    f_fmulp(m, 2, 0x2CA, pc + 0x31F);                                                        /* +31F fmulp st(2),st */
    f_faddp(m, 1, 0x2C1, pc + 0x321);                                                        /* +321 faddp */
    stf(SHI(-0x8), f_fst_m(m, 1, 0x15C, pc + 0x323, E - 0x8));                                /* +323 fstp dword ptr [esp+74h],st */
    f_fld_m(m, ldf(SHI(-0x54)), 0x144, pc + 0x327, E - 0x54);                                 /* +327 fld st,dword ptr [esp+28h] */
    st32(SHI(-0x80), ecx);                                                                 /* +32B push ecx */
    f_fld1(m, 0x1E8, pc + 0x32C);                                                            /* +32C fld1 */
    f_fdivrp(m, 1, 0x2F1, pc + 0x332);                                                       /* +332 fdivrp */
    stf(SHI(-0x48), f_fst_m(m, 1, 0x15C, pc + 0x334, E - 0x48));                              /* +334 fstp dword ptr [esp+38h],st */
    f_fld_m(m, ldf(SHI(-0x48)), 0x144, pc + 0x338, E - 0x48);                                 /* +338 fld st,dword ptr [esp+38h] */
    stf(SHI(-0x80), f_fst_m(m, 1, 0x11C, pc + 0x33C, E - 0x80));                              /* +33C fstp dword ptr [esp],st */
    st32(SHI(-0x84), pc + 0x344);                                    /* call 00425350h      */
    {
        const uint32_t at = pc + 0x360;                              /* 0x00425350          */
        f_fld_m(m, ldf(SHI(-0x80)), 0x144, at + 0x00, E - 0x80);     /* fld [esp+4]         */
        inv_scale8(m, sh, E, -0x44, at);
        inv_scale8(m, sh, E, -0x24, at);
        f_fstp_st(m, 0, 0x1D8, at + 0x54);                           /* fstp st(0) ; ret 4  */
    }
    /* mov esi,eax ; mov ecx,10h ; mov edi,ebx ; rep movsd ; pop edi ; mov eax,ebx ; pop esi */
    f_slots(m, s, &st);
    NJ_SETTLE(sh);
    NJ_SETTLE(&st);
    if (nj_exc_raised()) return 1 + WHY_VALUE;
    memcpy(g + F, sh, INV_LEN);
    memcpy(g + dst, SHI(-0x44), 0x40);
    /* FADDP cleared C0, C2, C3, FLD ST(i) IE, the last FSTP ST(0) DE. */
    f_store(m, &st, s, swd, 0x1D8, pc + 0x3B4, E - 0x8);
    s[O_C0] = s[O_C2] = s[O_C3] = 0;
    s[O_DE] = 0;
    s[O_IE] = 0;
    fl_add_store(s, esp - 0x74, 0x74);                               /* add esp,74h         */
    st32(s + O_EAX, ebx);
    st32(s + O_ECX, 0);
    st32(s + O_EDX, 0);
    st32(s + O_EIP, ld32(SHI(0)));                                   /* ret                 */
    st32(s + O_ESP, esp + 4);
    sa2_ninja_paths[NJ_INVERT][0]++;
    return 0;
}

__attribute__((used, retain))
void *__wrap_sub_424ff0(void *state, uint32_t pc, void *memory) {
    uint8_t *s = state;
    const uint16_t cw = ld16(s + O_CWD);
    const int why = !nj_on(NJ_INVERT) ? 1 + WHY_OFF
                  : (cw & 0x0300u) || !nj_env_ok(cw) ? 1 + WHY_FPU : inv_native(s, pc);
    nj_count(NJ_INVERT, why);
    return why ? __real_sub_424ff0(state, pc, memory) : memory;
}
