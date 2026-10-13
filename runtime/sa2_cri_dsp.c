/* The CRI Sound Renderer's hottest DSP kernels, run as native bodies over the lifted ones.
 *
 * Subtitle: four guest functions of the XAudio2 streaming thread (start 0x00784760), linked in
 * every build with `-Wl,-wrap=` (tools/s4/link_s4.py PRODUCTION_WRAPS), computed natively when the
 * double mode is linked and the call is one the native body reproduces bit for bit, and handed to
 * the lifted body (`__real_`) otherwise:
 *
 *   0x00842500  a biquad section (thiscall: ecx the filter, one float on the stack, ret 4; ST0
 *               the output). Called by the comb below, by CriSrDownmixer (0x008428E0) and by
 *               CriSrFilter (0x00849B60).
 *   0x00843400  a damped comb: a masked ring-buffer delay line whose feedback goes through the
 *               biquad at its +0x20 (thiscall, one float, ret 4; ST0 the delayed sample).
 *   0x00843460  the CriSrReverb network, once per sample frame from 0x00833E00 (stdcall: the
 *               reverb, the input sample, four output pointers; ret 18h): six all-pass delay
 *               stages in series, then nine combs of the result in parallel, then a matrix of the
 *               nine into the outputs. Its native body runs the combs natively too, inline, so
 *               their nine independent chains overlap.
 *   0x008418B0  mix-accumulate (cdecl: dst, src, n, gain): dst[i] = (float)(src[i] * gain +
 *               dst[i]) over n rounded up to a multiple of 8, unrolled 8 ways.
 *
 * The switch. SA2_CRI_DSP in the environment, read at the first call, says which native bodies
 * may run. Unset or empty: all four. 0: none. Anything else is read with strtol (decimal, or hex
 * after 0x), and its low four bits are the kernels that are ON:
 *
 *   bit 0 (1)  the biquad      runs natively when bit 0 is on
 *   bit 1 (2)  the comb        when bits 0 and 1 are on (its body runs the biquad inline)
 *   bit 2 (4)  the network     when bits 0, 1 and 2 are on (it runs nine combs inline)
 *   bit 3 (8)  the mix         when bit 3 is on
 *
 * A kernel whose bits are not all on runs its lifted body, and what that body calls goes through
 * its own wrapper: under 0x3 the lifted network calls the native comb nine times; under 0x1 the
 * lifted combs call the native biquad; under 0xE (the biquad off) every biquad, alone or in a comb
 * or the network, is the lifted one, and so is every comb and the network. `sa2_cri_dsp_mode`
 * holds the mask once read (-1 before; the harness sets it directly). SA2_CRI_DSP_STATS, set to
 * anything, prints each kernel's native and lifted calls, and why each lifted one was, to stderr
 * at exit (`sa2_cri_dsp_calls`, `sa2_cri_dsp_why`).
 *
 * Why: on the user's phone the streaming thread is about 27% of the serialized game path, and in
 * Metal Harbor these kernels are two thirds of it (the S8 serial study,
 * private/generated/s8/serial-study/). Design 1.3 allows a patch on the lifted code that changes
 * no game logic; this one changes no bit the guest can see.
 *
 * What "the same" means. tools/s8/f80_double.py's double mode keeps each x87 register as a
 * binary64 in remill's 10-byte slot (its 8 bytes, then 2 zero bytes) and does the x87's
 * arithmetic as the host's double arithmetic, in the host's rounding mode (which the runtime keeps
 * at the guest control word's RC), followed by the precision control's rounding (identity at PC53
 * and PC64; at PC24 the inline 24-bit rounding under the control word's RC). A native body that
 * makes the same double operations in the same order, in the same host mode and with the same
 * PC24 rounding, gets the same bits, provided no NaN appears (which NaN an operation answers with
 * can depend on its operand order, which a compiler is free to choose) and a product and a sum
 * are never fused into one multiply-add (the pragmas below, which every compiler flag but
 * -ffp-contract=fast honours; no link of this runtime passes that flag, and the Android runtime
 * is compiled with -ffp-contract=off besides). No operand here is a constant the compiler could
 * fold under its own idea of the rounding mode. Everything else the lifted body leaves is reproduced as remill
 * leaves it, read from the double-mode lifted IR:
 *
 *   - the register stack as remill models it: ST(i) is slot i, a push moves every slot up one
 *     (slot 7's value is lost), a pop moves every slot down one and puts the popped value in
 *     slot 7; TOP (bits 11-13 of the FXSAVE status word) moves with each; the tag word is not
 *     touched;
 *   - the status flags remill keeps: FLD m32 and FLD m64 OR the operand's denormal test (of a
 *     float, of a double) into DE and its signalling-NaN test into IE (a body that loads a NaN
 *     hands the call over, below, so that one is 0 whenever a body finishes); FLD
 *     ST(i) clears IE and DE; every arithmetic instruction, FCHS and every FST/FSTP set DE to the
 *     double mode's 80-bit denormal test of their operands, which no double passes, so 0;
 *     FADD/FADDP also clear C0, C2 and C3; PE, UE, OE and ZE take the runtime's exception test,
 *     which answers 0, so they keep their value; C1 is not written;
 *   - each x87 instruction's FOP (remill's: the first opcode byte's low two bits, then the ModRM
 *     byte), its address as FIP, the CS selector as FCS, and for a memory operand its linear
 *     address as FDP and the DS selector as FDS;
 *   - every guest memory write (the stack slots below ESP included), the GPRs, the arithmetic
 *     flags remill computes, ESP and EIP after the `ret`.
 *
 * When the lifted body runs instead (`sa2_cri_dsp_calls` counts both, `sa2_cri_dsp_why` why):
 *   - the exact build: `sa2_f80_state_double` (tools/s8/f80_shims_double.c's marker) is not
 *     linked, so the slots hold 80-bit images;
 *   - the switch (above) leaves one of the kernel's bits off;
 *   - guest memory is not on its fast path (`sa2_guest_fast_base` NULL) or an access is outside
 *     [SA2_GUEST_NULL_GUARD, SA2_GUEST_END);
 *   - the stack frame, the objects, the ring buffers, the outputs and the network's constants are
 *     not apart where the native body's order of reads and writes needs them apart, or two of
 *     the network's four outputs overlap without being the same float;
 *   - a value read from guest memory is an infinity or a NaN, a NaN is among what the body would
 *     leave (x_rnd says why that is enough), or a PC24 rounding meets a double denormal (the slow
 *     path, which a product or sum of floats cannot reach). The biquad, the comb and the network
 *     compute everything before they write anything, so they can still hand over at the end;
 *     the mix tests its inputs first, since with finite inputs it can make no NaN.
 *
 * The proof is a differential harness (tools/s8/native_kernels/cri_dsp_diff.c, run by
 * Documentation/scripts/tests/test_cri_dsp_native.py): the lifted double-mode objects as
 * `__real_`, these bodies, a million random and edge-case States and guest memories per kernel
 * under every precision and rounding control, the whole State and every touched byte compared,
 * and the cases of every guard, which must take the lifted body. The harness refuses lifted
 * objects that are not this checkout's double mode (cri_dsp_diff.py `lifted_objects`: the pass,
 * the shims and what they import and include, unchanged since the build), and the fast check
 * runs the test whenever this file, the pass, the shims or the harness changes. The offsets
 * below are remill's State layout, which the harness checks against remill's own header through
 * `sa2_cri_dsp_layout`.
 */
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

void *__real_sub_842500(void *state, uint32_t pc, void *memory);
void *__real_sub_843400(void *state, uint32_t pc, void *memory);
void *__real_sub_843460(void *state, uint32_t pc, void *memory);
void *__real_sub_8418b0(void *state, uint32_t pc, void *memory);

/* Defined only by the double mode's shims (tools/s8/f80_shims_double.c). */
extern const int sa2_f80_state_double __attribute__((weak));

/* ------------------------------------------------------------------ remill's State, by offset */

enum {
    O_CF = 2065, O_PF = 2067, O_AF = 2069, O_ZF = 2071, O_SF = 2073, O_OF = 2077,
    O_SEG_DS = 2106, O_SEG_CS = 2110,
    O_SS_BASE = 2120, O_DS_BASE = 2184,
    O_EAX = 2216, O_ECX = 2248, O_EDX = 2264, O_ESI = 2280, O_EDI = 2296, O_ESP = 2312,
    O_EIP = 2472,
    O_ST0 = 2486, O_ST_STRIDE = 16,
    O_C0 = 2737, O_C2 = 2741, O_C3 = 2743, O_DE = 2753, O_IE = 2755,
    O_CWD = 2768, O_SWD = 2770, O_FOP = 2774, O_FIP = 2776, O_FCS = 2780, O_FDP = 2784,
    O_FDS = 2788,
};

/* Every offset above by the name remill's header gives the field, for the harness to hold to
 * offsetof(State, ...). */
const struct sa2_cri_dsp_field { const char *field; uint32_t offset; } sa2_cri_dsp_layout[] = {
    {"aflag.cf", O_CF}, {"aflag.pf", O_PF}, {"aflag.af", O_AF}, {"aflag.zf", O_ZF},
    {"aflag.sf", O_SF}, {"aflag.of", O_OF}, {"seg.ds", O_SEG_DS}, {"seg.cs", O_SEG_CS},
    {"addr.ss_base", O_SS_BASE}, {"addr.ds_base", O_DS_BASE}, {"gpr.rax", O_EAX},
    {"gpr.rcx", O_ECX}, {"gpr.rdx", O_EDX}, {"gpr.rsi", O_ESI}, {"gpr.rdi", O_EDI},
    {"gpr.rsp", O_ESP}, {"gpr.rip", O_EIP}, {"st.elems[0].val", O_ST0},
    {"st.elems[1].val", O_ST0 + O_ST_STRIDE},
    {"sw.c0", O_C0}, {"sw.c2", O_C2}, {"sw.c3", O_C3}, {"sw.de", O_DE}, {"sw.ie", O_IE},
    {"x87.fxsave.cwd", O_CWD}, {"x87.fxsave.swd", O_SWD}, {"x87.fxsave.fop", O_FOP},
    {"x87.fxsave.ip", O_FIP}, {"x87.fxsave.cs", O_FCS}, {"x87.fxsave.dp", O_FDP},
    {"x87.fxsave.ds", O_FDS},
};
const uint32_t sa2_cri_dsp_layout_count = sizeof sa2_cri_dsp_layout / sizeof sa2_cri_dsp_layout[0];

/* ------------------------------------------------------------------ the switch and the counters */

enum { CRI_BIQUAD, CRI_COMB, CRI_NET, CRI_MIX, CRI_KERNELS };
/* Why a call went to the lifted body; a native body answers 0 (done) or 1 + one of these. */
enum { WHY_OFF, WHY_MEMORY, WHY_OVERLAP, WHY_VALUE, WHY_SIZE, WHY_KINDS };

/* Calls per kernel: [k][0] native, [k][1] handed to the lifted body, and [k][why] of the second.
 * Guest threads run one at a time (runtime/sa2_threads.h), so plain counters are exact. */
uint64_t sa2_cri_dsp_calls[CRI_KERNELS][2];
uint64_t sa2_cri_dsp_why[CRI_KERNELS][WHY_KINDS];

/* -1 until the first call reads SA2_CRI_DSP; then the kernels that are on, one bit per kernel
 * (the switch, at the top). */
int sa2_cri_dsp_mode = -1;

/* The bits a kernel's native body needs: its own and those of the kernels it runs inline. */
static const int CRI_NEEDS[CRI_KERNELS] = {
    [CRI_BIQUAD] = 1 << CRI_BIQUAD,
    [CRI_COMB] = 1 << CRI_COMB | 1 << CRI_BIQUAD,
    [CRI_NET] = 1 << CRI_NET | 1 << CRI_COMB | 1 << CRI_BIQUAD,
    [CRI_MIX] = 1 << CRI_MIX,
};

static void cri_stats_print(void) {
    static const char *const name[CRI_KERNELS] = {"biquad 0x00842500", "comb 0x00843400",
                                                  "network 0x00843460", "mix 0x008418B0"};
    for (int k = 0; k < CRI_KERNELS; k++)
        fprintf(stderr, "sa2_cri_dsp: %s native %llu lifted %llu (off %llu memory %llu "
                "overlap %llu value %llu size %llu)\n", name[k],
                (unsigned long long)sa2_cri_dsp_calls[k][0],
                (unsigned long long)sa2_cri_dsp_calls[k][1],
                (unsigned long long)sa2_cri_dsp_why[k][WHY_OFF],
                (unsigned long long)sa2_cri_dsp_why[k][WHY_MEMORY],
                (unsigned long long)sa2_cri_dsp_why[k][WHY_OVERLAP],
                (unsigned long long)sa2_cri_dsp_why[k][WHY_VALUE],
                (unsigned long long)sa2_cri_dsp_why[k][WHY_SIZE]);
}

static int cri_mode_read(void) {
    const char *e = getenv("SA2_CRI_DSP");
    const int all = (1 << CRI_KERNELS) - 1;
    sa2_cri_dsp_mode = e && *e ? (int)(strtol(e, NULL, 0) & all) : all;
    if (getenv("SA2_CRI_DSP_STATS")) atexit(cri_stats_print);
    return sa2_cri_dsp_mode;
}

AI int cri_on(int k) {
    int m = sa2_cri_dsp_mode;
    if (__builtin_expect(m < 0, 0)) m = cri_mode_read();
    return (m & CRI_NEEDS[k]) == CRI_NEEDS[k] && &sa2_f80_state_double != NULL;
}

/* The wrapper's tally: `why` 0 is a native call. */
AI void cri_count(int k, int why) {
    if (!why) {
        sa2_cri_dsp_calls[k][0]++;
        return;
    }
    sa2_cri_dsp_calls[k][1]++;
    sa2_cri_dsp_why[k][why - 1]++;
}

/* ------------------------------------------------------------------ bytes */

AI uint16_t ld16(const uint8_t *p) { uint16_t v; memcpy(&v, p, 2); return v; }
AI uint32_t ld32(const uint8_t *p) { uint32_t v; memcpy(&v, p, 4); return v; }
AI uint64_t ld64(const uint8_t *p) { uint64_t v; memcpy(&v, p, 8); return v; }
AI float    ldf(const uint8_t *p)  { float v; memcpy(&v, p, 4); return v; }
AI double   ldd(const uint8_t *p)  { double v; memcpy(&v, p, 8); return v; }
AI void st16(uint8_t *p, uint16_t v) { memcpy(p, &v, 2); }
AI void st32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
AI void st64(uint8_t *p, uint64_t v) { memcpy(p, &v, 8); }
AI void stf(uint8_t *p, float v)     { memcpy(p, &v, 4); }
AI uint64_t dbits(double d) { uint64_t b; memcpy(&b, &d, 8); return b; }
AI double   dval(uint64_t b) { double d; memcpy(&d, &b, 8); return d; }
AI uint32_t fbits(float f) { uint32_t b; memcpy(&b, &f, 4); return b; }

/* [a, a + len) lies where the fast path makes the access: inside [NULL_GUARD, END). */
AI int in_fast(uint32_t a, uint32_t len) {
    return a >= SA2_GUEST_NULL_GUARD && len <= SA2_GUEST_END && a <= SA2_GUEST_END - len;
}
/* Two ranges both in_fast are disjoint. */
AI int apart(uint32_t a, uint32_t alen, uint32_t b, uint32_t blen) {
    return a + alen <= b || b + blen <= a;
}

/* ------------------------------------------------------------------ the arithmetic flags */

typedef struct { uint8_t cf, pf, af, zf, sf, of; } aflags;

AI uint8_t parity8(uint32_t r) { return (uint8_t)!(__builtin_popcount(r & 0xFFu) & 1); }
AI aflags fl_add(uint32_t a, uint32_t b) {
    const uint32_t r = a + b;
    aflags f = {(uint8_t)(r < a), parity8(r), (uint8_t)(((a ^ b ^ r) >> 4) & 1u), (uint8_t)(r == 0),
                (uint8_t)(r >> 31), (uint8_t)((((a ^ r) & (b ^ r)) >> 31) & 1u)};
    return f;
}
AI aflags fl_sub(uint32_t a, uint32_t b) {
    const uint32_t r = a - b;
    aflags f = {(uint8_t)(a < b), parity8(r), (uint8_t)(((a ^ b ^ r) >> 4) & 1u), (uint8_t)(r == 0),
                (uint8_t)(r >> 31), (uint8_t)((((a ^ b) & (a ^ r)) >> 31) & 1u)};
    return f;
}
/* AND and TEST: CF, OF and AF 0. */
AI aflags fl_logic(uint32_t r) {
    aflags f = {0, parity8(r), 0, (uint8_t)(r == 0), (uint8_t)(r >> 31), 0};
    return f;
}
AI void fl_store(uint8_t *s, aflags f) {
    s[O_CF] = f.cf; s[O_PF] = f.pf; s[O_AF] = f.af; s[O_ZF] = f.zf; s[O_SF] = f.sf; s[O_OF] = f.of;
}

/* ------------------------------------------------------------------ the precision control */

/* The double mode's PC24 rounding of a normal or zero double's bits (__sa2_pc_round's r24): the
 * significand to 24 bits under the control word's RC, the exponent kept; nearest is ties to even
 * (half an ulp less one plus the kept lowest bit), up and down round away from zero in magnitude
 * when the sign says so (an ulp less one), toward zero drops the bits. */
AI uint64_t pc24_bits(uint64_t b, uint16_t cw) {
    const unsigned rc = (cw >> 10) & 3u;
    const int away = ((int64_t)b < 0) ? rc == 1u : rc == 2u;
    const uint64_t add = rc == 0u ? ((b >> 29) & 1u) + 0x0FFFFFFFu : (away ? 0x1FFFFFFFu : 0u);
    return (b + add) & ~0x1FFFFFFFull;
}

/* ------------------------------------------------------------------ the x87, as remill keeps it */

/* remill moves every slot on a push and a pop. Here the slots stay where they are and the base
 * moves: ST(i) is physical slot (r + i) & 7. A push lowers r and writes the new slot 0, which is
 * the slot remill's push drops; a pop raises r, which leaves the popped value as ST(7), where
 * remill's pop puts it. In a body every push and pop is at a fixed point, so r is a constant at
 * each instruction once the body is inlined, and the slots are registers. */
typedef struct {
    uint64_t lo[8];     /* a slot's bytes 0-7: the double */
    uint16_t hi[8];     /* bytes 8-9 */
    unsigned r;         /* the physical slot of ST(0) */
    unsigned computed;  /* the physical slots an arithmetic result was put in */
    uint16_t cw, swd, fop;
    uint32_t ip, dp;
    uint8_t  c0, c2, c3, de, ie;
    int      p24;       /* the control word's PC is 24 bits (a constant in each specialisation) */
    int      bad;       /* a value the native body does not reproduce exactly appeared */
} x87;

AI unsigned x_ph(const x87 *m, int i) { return (m->r + (unsigned)i) & 7u; }

AI void x_load(x87 *m, const uint8_t *s, int p24) {
    for (int i = 0; i < 8; i++) {
        m->lo[i] = ld64(s + O_ST0 + O_ST_STRIDE * i);
        m->hi[i] = ld16(s + O_ST0 + O_ST_STRIDE * i + 8);
    }
    m->r = 0;
    m->computed = 0;
    m->cw = ld16(s + O_CWD);
    m->swd = ld16(s + O_SWD);
    m->fop = ld16(s + O_FOP);
    m->ip = ld32(s + O_FIP);
    m->dp = ld32(s + O_FDP);
    m->c0 = s[O_C0]; m->c2 = s[O_C2]; m->c3 = s[O_C3]; m->de = s[O_DE]; m->ie = s[O_IE];
    m->p24 = p24;
    m->bad = 0;
}

/* After at least one x87 instruction with a memory operand: FCS and FDS are the selectors. */
AI void x_store(const x87 *m, uint8_t *s) {
    for (int i = 0; i < 8; i++) {
        st64(s + O_ST0 + O_ST_STRIDE * i, m->lo[x_ph(m, i)]);
        st16(s + O_ST0 + O_ST_STRIDE * i + 8, m->hi[x_ph(m, i)]);
    }
    st16(s + O_SWD, m->swd);
    st16(s + O_FOP, m->fop);
    st32(s + O_FIP, m->ip);
    st32(s + O_FDP, m->dp);
    st16(s + O_FCS, ld16(s + O_SEG_CS));
    st16(s + O_FDS, ld16(s + O_SEG_DS));
    s[O_C0] = m->c0; s[O_C2] = m->c2; s[O_C3] = m->c3; s[O_DE] = m->de; s[O_IE] = m->ie;
}

AI void x_push(x87 *m, uint64_t lo, uint16_t hi) {
    m->r = (m->r + 7u) & 7u;
    m->lo[m->r] = lo;
    m->hi[m->r] = hi;
    m->swd = (uint16_t)((m->swd & ~0x3800u) | ((m->swd + 0x3800u) & 0x3800u));
}

AI void x_pop(x87 *m) {
    m->r = (m->r + 1u) & 7u;
    m->swd = (uint16_t)((m->swd & ~0x3800u) | ((m->swd + 0x0800u) & 0x3800u));
}

AI int nonfinite64(uint64_t b) { return (b & 0x7FF0000000000000ull) == 0x7FF0000000000000ull; }

/* An arithmetic result after the precision control: identity unless PC24; there, a double
 * denormal is sa2_df80_pc_round_slow's (left to the lifted body), infinities and NaNs are kept.
 *
 * Which values are checked. Every value that comes in from guest memory must be finite
 * (x_f32, x_f64). Then a NaN can only be made by the arithmetic (an infinity from an overflow,
 * then infinity minus infinity or times zero), and a NaN never turns back into a number through
 * an add, a subtract, a multiply, a sign change or a conversion: every value computed from one
 * is a NaN. So a NaN that could differ between the native body and the lifted one (an operation
 * of two NaNs answers with either, by the operand order the compiler chose) shows up as a NaN
 * in what the body leaves behind, or nowhere: each body checks every float it stores
 * (i_fst_m) and the register slots it leaves (x_nan_slots), and hands a call with a NaN in
 * either to the lifted body. Infinities alone are exact. */
AI double x_rnd(x87 *m, double v) {
    uint64_t b = dbits(v);
    if (m->p24) {
        const uint64_t a = b & 0x7FFFFFFFFFFFFFFFull;
        if (a != 0 && a < 0x0010000000000000ull) m->bad = 1;
        else if (a < 0x7FF0000000000000ull) b = pc24_bits(b, m->cw);
    }
    return dval(b);
}

/* A NaN in a slot the body put an arithmetic result in (see x_rnd; a slot it only loaded or
 * copied holds the very bits it was given, whatever they are). The set of such slots is a
 * constant at the end of a body. */
AI void x_nan_slots(x87 *m) {
    for (int i = 0; i < 8; i++)
        if ((m->computed >> i) & 1u)
            m->bad |= (m->lo[i] & 0x7FFFFFFFFFFFFFFFull) > 0x7FF0000000000000ull;
}

AI double x_f32(x87 *m, float f) {
    m->bad |= (fbits(f) & 0x7F800000u) == 0x7F800000u;
    return (double)f;
}
AI double x_f64(x87 *m, double d) {
    m->bad |= nonfinite64(dbits(d));
    return d;
}

AI double x_st(const x87 *m, int i) { return dval(m->lo[x_ph(m, i)]); }
AI void x_set(x87 *m, int i, double v) {
    m->lo[x_ph(m, i)] = dbits(v);
    m->hi[x_ph(m, i)] = 0;
    m->computed |= 1u << x_ph(m, i);
}

AI void x_at(x87 *m, uint16_t fop, uint32_t ip) { m->fop = fop; m->ip = ip; }
AI void x_atm(x87 *m, uint16_t fop, uint32_t ip, uint32_t dp) { m->fop = fop; m->ip = ip; m->dp = dp; }

/* FLD m32: the lifted body ORs the float's signalling-NaN test (exponent all ones, quiet bit 0,
 * the other 22 fraction bits not all 0) into IE and its denormal test into DE. */
AI void i_fld_m(x87 *m, float f, uint16_t fop, uint32_t ip, uint32_t dp) {
    const uint32_t b = fbits(f);
    const double d = x_f32(m, f);
    m->ie |= (uint8_t)(((b & 0x7FC00000u) == 0x7F800000u) & ((b & 0x003FFFFFu) != 0));
    m->de |= (uint8_t)(((b & 0x7F800000u) == 0) & ((b & 0x007FFFFFu) != 0));
    x_push(m, dbits(d), 0);
    x_atm(m, fop, ip, dp);
}
/* FLD m64: the same tests of a double (51 fraction bits below the quiet bit). */
AI void i_fld_m64(x87 *m, double v, uint16_t fop, uint32_t ip, uint32_t dp) {
    const uint64_t b = dbits(x_f64(m, v)), a = b & 0x7FFFFFFFFFFFFFFFull;
    m->ie |= (uint8_t)(((b & 0x7FF8000000000000ull) == 0x7FF0000000000000ull)
                       & ((b & 0x0007FFFFFFFFFFFFull) != 0));
    m->de |= (uint8_t)((a != 0) & (a < 0x0010000000000000ull));
    x_push(m, b, 0);
    x_atm(m, fop, ip, dp);
}
/* FLD ST(i) */
AI void i_fld_st(x87 *m, int i, uint16_t fop, uint32_t ip) {
    const uint64_t lo = m->lo[x_ph(m, i)];
    const uint16_t hi = m->hi[x_ph(m, i)];
    x_push(m, lo, hi);
    m->ie = 0;
    m->de = 0;
    x_at(m, fop, ip);
}
/* FXCH ST(i) */
AI void i_fxch(x87 *m, int i, uint16_t fop, uint32_t ip) {
    const unsigned a = x_ph(m, 0), b = x_ph(m, i);
    const uint64_t lo = m->lo[a];
    const uint16_t hi = m->hi[a];
    m->lo[a] = m->lo[b]; m->hi[a] = m->hi[b];
    m->lo[b] = lo; m->hi[b] = hi;
    x_at(m, fop, ip);
}
/* FST m32 / FSTP m32: the value stored */
AI float i_fst_m(x87 *m, int pop, uint16_t fop, uint32_t ip, uint32_t dp) {
    const float r = (float)x_st(m, 0);
    m->bad |= (fbits(r) & 0x7FFFFFFFu) > 0x7F800000u;             /* a NaN stored: see x_rnd */
    m->de = 0;
    if (pop) x_pop(m);
    x_atm(m, fop, ip, dp);
    return r;
}
/* FSTP ST(i) */
AI void i_fstp_st(x87 *m, int i, uint16_t fop, uint32_t ip) {
    m->lo[x_ph(m, i)] = m->lo[x_ph(m, 0)];
    m->hi[x_ph(m, i)] = m->hi[x_ph(m, 0)];
    m->de = 0;
    x_pop(m);
    x_at(m, fop, ip);
}
/* FCHS */
AI void i_fchs(x87 *m, uint16_t fop, uint32_t ip) {
    x_set(m, 0, -x_st(m, 0));
    m->de = 0;
    x_at(m, fop, ip);
}
/* FMUL m32 / m64: ST0 = ST0 * m */
AI void i_fmul_m(x87 *m, float f, uint16_t fop, uint32_t ip, uint32_t dp) {
    const double v = x_f32(m, f);
    x_set(m, 0, x_rnd(m, x_st(m, 0) * v));
    m->de = 0;
    x_atm(m, fop, ip, dp);
}
AI void i_fmul_m64(x87 *m, double d, uint16_t fop, uint32_t ip, uint32_t dp) {
    const double v = x_f64(m, d);
    x_set(m, 0, x_rnd(m, x_st(m, 0) * v));
    m->de = 0;
    x_atm(m, fop, ip, dp);
}
/* FADD m32: ST0 = ST0 + m */
AI void i_fadd_m(x87 *m, float f, uint16_t fop, uint32_t ip, uint32_t dp) {
    const double v = x_f32(m, f);
    x_set(m, 0, x_rnd(m, x_st(m, 0) + v));
    m->de = 0;
    m->c0 = m->c2 = m->c3 = 0;
    x_atm(m, fop, ip, dp);
}
/* FMUL ST0, ST(i) (D8 C8+i) */
AI void i_fmul_st0(x87 *m, int i, uint16_t fop, uint32_t ip) {
    x_set(m, 0, x_rnd(m, x_st(m, 0) * x_st(m, i)));
    m->de = 0;
    x_at(m, fop, ip);
}
/* FMUL ST(i), ST0 (DC C8+i) */
AI void i_fmul_sti(x87 *m, int i, uint16_t fop, uint32_t ip) {
    x_set(m, i, x_rnd(m, x_st(m, i) * x_st(m, 0)));
    m->de = 0;
    x_at(m, fop, ip);
}
/* FMULP ST(i), ST0 */
AI void i_fmulp(x87 *m, int i, uint16_t fop, uint32_t ip) {
    x_set(m, i, x_rnd(m, x_st(m, i) * x_st(m, 0)));
    m->de = 0;
    x_pop(m);
    x_at(m, fop, ip);
}
/* FADDP ST(i), ST0 */
AI void i_faddp(x87 *m, int i, uint16_t fop, uint32_t ip) {
    x_set(m, i, x_rnd(m, x_st(m, i) + x_st(m, 0)));
    m->de = 0;
    m->c0 = m->c2 = m->c3 = 0;
    x_pop(m);
    x_at(m, fop, ip);
}
/* FSUBP ST(i), ST0 (DE E8+i): ST(i) = ST(i) - ST0 */
AI void i_fsubp(x87 *m, int i, uint16_t fop, uint32_t ip) {
    x_set(m, i, x_rnd(m, x_st(m, i) - x_st(m, 0)));
    m->de = 0;
    x_pop(m);
    x_at(m, fop, ip);
}
/* FSUBRP ST(i), ST0 (DE E0+i): ST(i) = ST0 - ST(i) */
AI void i_fsubrp(x87 *m, int i, uint16_t fop, uint32_t ip) {
    x_set(m, i, x_rnd(m, x_st(m, 0) - x_st(m, i)));
    m->de = 0;
    x_pop(m);
    x_at(m, fop, ip);
}

/* ------------------------------------------------------------------ 0x00842500, the biquad */

/* What the biquad writes into its filter, in the order of the guest's stores. */
typedef struct { float z7c, z78, z38, z34; } bq_writes;

/* The guest's 36 instructions. `o` is the filter's host bytes (ecx's linear address `lo`), `a4`
 * the float argument's slot ([esp+4], linear `la`), in and out; `pc` the function's address. The
 * filter's four writes are left in `w`; reads of the filter never follow a write of the same
 * field, so `o` is read as it was on entry. */
AI void bq_run(x87 *m, const uint8_t *o, uint32_t lo, float *a4, uint32_t la, uint32_t pc,
               bq_writes *w) {
    i_fld_m(m, ldf(o + 0x54), 0x141, pc + 0x00, lo + 0x54);        /* fld dword [ecx+54h]  */
    i_fld_m(m, *a4, 0x144, pc + 0x03, la);                          /* fld dword [esp+4]    */
    i_fld_st(m, 0, 0x1C0, pc + 0x07);                               /* fld st0              */
    i_fmulp(m, 2, 0x2CA, pc + 0x09);                                /* fmulp st2            */
    i_fxch(m, 1, 0x1C9, pc + 0x0B);                                 /* fxch                 */
    *a4 = i_fst_m(m, 1, 0x15C, pc + 0x0D, la);                      /* fstp dword [esp+4]   */
    i_fld_m(m, *a4, 0x144, pc + 0x11, la);                          /* fld dword [esp+4]    */
    i_fld_m(m, ldf(o + 0x5C), 0x141, pc + 0x15, lo + 0x5C);        /* fld dword [ecx+5Ch]  */
    i_fmul_m(m, ldf(o + 0x7C), 0x049, pc + 0x18, lo + 0x7C);       /* fmul dword [ecx+7Ch] */
    i_faddp(m, 1, 0x2C1, pc + 0x1B);                                /* faddp                */
    *a4 = i_fst_m(m, 1, 0x15C, pc + 0x1D, la);                      /* fstp dword [esp+4]   */
    i_fld_m(m, ldf(o + 0x78), 0x141, pc + 0x21, lo + 0x78);        /* fld dword [ecx+78h]  */
    w->z7c = i_fst_m(m, 1, 0x159, pc + 0x24, lo + 0x7C);            /* fstp dword [ecx+7Ch] */
    i_fld_m(m, ldf(o + 0x58), 0x141, pc + 0x27, lo + 0x58);        /* fld dword [ecx+58h]  */
    i_fmul_m(m, ldf(o + 0x78), 0x049, pc + 0x2A, lo + 0x78);       /* fmul dword [ecx+78h] */
    i_fadd_m(m, *a4, 0x044, pc + 0x2D, la);                         /* fadd dword [esp+4]   */
    *a4 = i_fst_m(m, 1, 0x15C, pc + 0x31, la);                      /* fstp dword [esp+4]   */
    w->z78 = i_fst_m(m, 1, 0x159, pc + 0x35, lo + 0x78);            /* fstp dword [ecx+78h] */
    i_fld_m(m, *a4, 0x144, pc + 0x38, la);                          /* fld dword [esp+4]    */
    i_fld_m(m, ldf(o + 0x18), 0x141, pc + 0x3C, lo + 0x18);        /* fld dword [ecx+18h]  */
    i_fmul_m(m, ldf(o + 0x38), 0x049, pc + 0x3F, lo + 0x38);       /* fmul dword [ecx+38h] */
    i_fsubp(m, 1, 0x2E9, pc + 0x42);                                /* fsubp                */
    *a4 = i_fst_m(m, 1, 0x15C, pc + 0x44, la);                      /* fstp dword [esp+4]   */
    i_fld_m(m, ldf(o + 0x34), 0x141, pc + 0x48, lo + 0x34);        /* fld dword [ecx+34h]  */
    w->z38 = i_fst_m(m, 1, 0x159, pc + 0x4B, lo + 0x38);            /* fstp dword [ecx+38h] */
    i_fld_m(m, *a4, 0x144, pc + 0x4E, la);                          /* fld dword [esp+4]    */
    i_fld_m(m, ldf(o + 0x14), 0x141, pc + 0x52, lo + 0x14);        /* fld dword [ecx+14h]  */
    i_fmul_m(m, ldf(o + 0x34), 0x049, pc + 0x55, lo + 0x34);       /* fmul dword [ecx+34h] */
    i_fsubp(m, 1, 0x2E9, pc + 0x58);                                /* fsubp                */
    *a4 = i_fst_m(m, 1, 0x15C, pc + 0x5A, la);                      /* fstp dword [esp+4]   */
    i_fld_m(m, *a4, 0x144, pc + 0x5E, la);                          /* fld dword [esp+4]    */
    w->z34 = i_fst_m(m, 0, 0x151, pc + 0x62, lo + 0x34);            /* fst dword [ecx+34h]  */
    i_fmul_m(m, ldf(o + 0x08), 0x049, pc + 0x65, lo + 0x08);       /* fmul dword [ecx+8]   */
    *a4 = i_fst_m(m, 1, 0x15C, pc + 0x68, la);                      /* fstp dword [esp+4]   */
    i_fld_m(m, *a4, 0x144, pc + 0x6C, la);                          /* fld dword [esp+4]    */
}                                                                   /* ret 4                */

AI void bq_commit(uint8_t *g, uint32_t lo, const bq_writes *w) {
    stf(g + lo + 0x7C, w->z7c);
    stf(g + lo + 0x78, w->z78);
    stf(g + lo + 0x38, w->z38);
    stf(g + lo + 0x34, w->z34);
}

/* The filter's bytes the biquad reads and writes: [ecx+08h, ecx+80h). */
#define BQ_LO 0x08u
#define BQ_LEN 0x78u

AI int bq_native(uint8_t *s, uint32_t pc, int p24) {
    uint8_t *g = sa2_guest_fast_base;
    const uint32_t esp = ld32(s + O_ESP);
    const uint32_t sp = ld32(s + O_SS_BASE) + esp, lo = ld32(s + O_DS_BASE) + ld32(s + O_ECX);
    if (!g || !in_fast(sp, 8) || !in_fast(lo + BQ_LO, BQ_LEN)) return 1 + WHY_MEMORY;
    if (!apart(sp, 8, lo + BQ_LO, BQ_LEN)) return 1 + WHY_OVERLAP;
    x87 m;
    x_load(&m, s, p24);
    float a4 = ldf(g + sp + 4);
    bq_writes w;
    bq_run(&m, g + lo, lo, &a4, sp + 4, pc, &w);
    x_nan_slots(&m);
    if (m.bad) return 1 + WHY_VALUE;
    bq_commit(g, lo, &w);
    stf(g + sp + 4, a4);
    x_store(&m, s);
    st32(s + O_EIP, ld32(g + sp));
    st32(s + O_ESP, esp + 8);
    return 0;
}

__attribute__((used, retain))
void *__wrap_sub_842500(void *state, uint32_t pc, void *memory) {
    uint8_t *s = state;
    const int why = !cri_on(CRI_BIQUAD) ? 1 + WHY_OFF
                  : (ld16(s + O_CWD) & 0x0300u) ? bq_native(s, pc, 0) : bq_native(s, pc, 1);
    cri_count(CRI_BIQUAD, why);
    return why ? __real_sub_842500(state, pc, memory) : memory;
}

/* ------------------------------------------------------------------ 0x00843400, the comb */

/* The comb's object: [esi+04h] the ring, [esi+0Ch] the delay, [esi+10h] the write index,
 * [esi+14h] the index mask, [esi+1Ch] the feedback gain, the biquad at [esi+20h] (which reads
 * and writes up to esi+0A0h). */
#define CB_LO 0x04u
#define CB_LEN 0x9Cu
/* Its stack, from the biquad's return address (E-20, E the entry ESP) to its argument (E+4). */
#define CB_SP_LO 20u
#define CB_SP_LEN 28u

/* The ring as the comb will index it: the cell read, the cell written, the new write index. */
typedef struct { uint32_t ring, mask, wn, rd, wr; } cb_cells;

AI cb_cells cb_cells_of(const uint8_t *g, uint32_t lo, uint32_t ds) {
    cb_cells c;
    const uint32_t delay = ld32(g + lo + 0x0C), wi = ld32(g + lo + 0x10);
    c.ring = ld32(g + lo + 0x04);
    c.mask = ld32(g + lo + 0x14);
    c.wn = (wi + 1u) & c.mask;
    c.rd = ds + c.ring + 4u * ((wi - delay + 1u) & c.mask);
    c.wr = ds + c.ring + 4u * c.wn;
    return c;
}

typedef struct { float out; bq_writes bq; } cb_writes;

/* The comb's 30 instructions with its stack in a copy: `e` is the copy's byte at the entry ESP
 * (the return address), `el` its linear address; the object at linear `lo`, `esi` the register
 * the comb saves and restores. The stack is written in the copy; the object, the biquad and the
 * ring cell are left in `w`. Its other registers on return: eax c->wn, ecx (its ecx) + 20h, edx
 * c->ring. */
AI void cb_run(x87 *m, const uint8_t *g, uint8_t *e, uint32_t el, uint32_t lo, uint32_t esi,
               const cb_cells *c, uint32_t pc, cb_writes *w) {
    const float x = ldf(e + 4);
    /* sub esp,8 ; push esi ; mov esi,ecx ; mov eax,[esi+10h] ; sub eax,[esi+0Ch] ;
     * mov ecx,[esi+4] ; add eax,1 ; and eax,[esi+14h] ; push ecx */
    i_fld_m(m, ldf(g + c->rd), 0x104, pc + 0x16, c->rd);            /* fld dword [ecx+eax*4] */
    const float l8 = i_fst_m(m, 1, 0x15C, pc + 0x1C, el - 8);       /* fstp dword [esp+8]    */
    i_fld_m(m, l8, 0x144, pc + 0x20, el - 8);                       /* fld dword [esp+8]     */
    float arg = i_fst_m(m, 1, 0x11C, pc + 0x24, el - 16);           /* fstp dword [esp]      */
    bq_run(m, g + lo + 0x20, lo + 0x20, &arg, el - 16, pc - 0xF00u, &w->bq); /* call biquad  */
    i_fmul_m(m, ldf(g + lo + 0x1C), 0x04E, pc + 0x2C, lo + 0x1C);   /* fmul dword [esi+1Ch]  */
    /* mov eax,[esi+10h] ; mov edx,[esi+4] ; add eax,1 ; and eax,[esi+14h] */
    const float l4 = i_fst_m(m, 1, 0x15C, pc + 0x3B, el - 4);       /* fstp dword [esp+8]    */
    i_fld_m(m, l4, 0x144, pc + 0x3F, el - 4);                       /* fld dword [esp+8]     */
    i_fadd_m(m, x, 0x044, pc + 0x43, el + 4);                       /* fadd dword [esp+10h]  */
    /* mov [esi+10h],eax ; pop esi */
    const float y = i_fst_m(m, 1, 0x15C, pc + 0x4B, el + 4);        /* fstp dword [esp+0Ch]  */
    i_fld_m(m, y, 0x144, pc + 0x4F, el + 4);                        /* fld dword [esp+0Ch]   */
    w->out = i_fst_m(m, 1, 0x11C, pc + 0x53, c->wr);                /* fstp dword [edx+eax*4] */
    i_fld_m(m, l8, 0x104, pc + 0x56, el - 8);                       /* fld dword [esp]       */
    /* add esp,8 ; ret 4 */
    st32(e - 20, pc + 0x2C);        /* the call's return address */
    stf(e - 16, arg);
    st32(e - 12, esi);
    stf(e - 8, l8);
    stf(e - 4, l4);
    stf(e + 4, y);
}

AI void cb_commit(uint8_t *g, uint32_t lo, const cb_cells *c, const cb_writes *w) {
    bq_commit(g, lo + 0x20, &w->bq);
    st32(g + lo + 0x10, c->wn);
    stf(g + c->wr, w->out);
}

AI int cb_native(uint8_t *s, uint32_t pc, int p24) {
    uint8_t *g = sa2_guest_fast_base;
    const uint32_t ds = ld32(s + O_DS_BASE);
    const uint32_t esp = ld32(s + O_ESP), ecx = ld32(s + O_ECX);
    const uint32_t e = ld32(s + O_SS_BASE) + esp, lo = ds + ecx, f = e - CB_SP_LO;
    if (!g || !in_fast(f, CB_SP_LEN) || !in_fast(lo + CB_LO, CB_LEN)) return 1 + WHY_MEMORY;
    if (!apart(f, CB_SP_LEN, lo + CB_LO, CB_LEN)) return 1 + WHY_OVERLAP;
    const cb_cells c = cb_cells_of(g, lo, ds);
    if (!in_fast(c.rd, 4) || !in_fast(c.wr, 4)) return 1 + WHY_MEMORY;
    /* The ring's two cells may be one; neither may be in the frame or the object. */
    if (!apart(c.rd, 4, f, CB_SP_LEN) || !apart(c.rd, 4, lo + CB_LO, CB_LEN)
            || !apart(c.wr, 4, f, CB_SP_LEN) || !apart(c.wr, 4, lo + CB_LO, CB_LEN))
        return 1 + WHY_OVERLAP;
    x87 m;
    x_load(&m, s, p24);
    uint8_t sh[CB_SP_LEN];
    memcpy(sh, g + f, CB_SP_LEN);
    cb_writes w;
    cb_run(&m, g, sh + CB_SP_LO, e, lo, ld32(s + O_ESI), &c, pc, &w);
    x_nan_slots(&m);
    if (m.bad) return 1 + WHY_VALUE;
    memcpy(g + f, sh, CB_SP_LEN);
    cb_commit(g, lo, &c, &w);
    x_store(&m, s);
    fl_store(s, fl_add(esp - 8, 8));
    st32(s + O_EAX, c.wn);
    st32(s + O_ECX, ecx + 0x20);
    st32(s + O_EDX, c.ring);
    st32(s + O_EIP, ld32(sh + CB_SP_LO));
    st32(s + O_ESP, esp + 8);
    return 0;
}

__attribute__((used, retain))
void *__wrap_sub_843400(void *state, uint32_t pc, void *memory) {
    uint8_t *s = state;
    const int why = !cri_on(CRI_COMB) ? 1 + WHY_OFF
                  : (ld16(s + O_CWD) & 0x0300u) ? cb_native(s, pc, 0) : cb_native(s, pc, 1);
    cri_count(CRI_COMB, why);
    return why ? __real_sub_843400(state, pc, memory) : memory;
}

/* ------------------------------------------------------------------ 0x00843460, the network */

/* Its stack, E the entry ESP: from the deepest biquad return address (E-48h: the network's frame
 * E-2Ch, a comb's argument and return address, the comb's frame and the biquad's) to its last
 * argument (E+18h), as a copy `sh` with E at NET_SH_E. */
#define NET_SH_E 0x48
#define NET_SH_LEN 0x64u
/* Its object's bytes: the nine combs from esi+1Ch (each 0B4h, read from +4) to the delay stages'
 * fields, which end at esi+748h. */
#define NET_LO 0x20u
#define NET_LEN 0x728u
/* The image's constants it reads: an m32 at 0x0093290C and eight m64 between 0x00899260 and
 * 0x00905D90; one window covers them. */
#define NET_K_LO 0x00899260u
#define NET_K_LEN (0x00932910u - 0x00899260u)
#define NET_STAGES 6
#define NET_COMBS 9
#define NET_RINGS (NET_STAGES + NET_COMBS)

/* Each delay stage's fields: its write index, index mask, ring, delay. */
static const uint16_t NET_STAGE[NET_STAGES][4] = {
    {0x734, 0x738, 0x728, 0x730}, {0x680, 0x684, 0x674, 0x67C}, {0x6A4, 0x6A8, 0x698, 0x6A0},
    {0x6C8, 0x6CC, 0x6BC, 0x6C4}, {0x6EC, 0x6F0, 0x6E0, 0x6E8}, {0x710, 0x714, 0x704, 0x70C}};
#define NET_COMB(k) (0x1Cu + 0xB4u * (uint32_t)(k))

/* The fifteen rings (six stages, nine combs) are where the native order of reads and writes holds
 * when each ring's whole buffer, [ds + ring, ds + ring + 4 * (mask + 1)), is apart from every
 * other's, from the object and from the constants: then no cell any of them reads is a cell
 * another wrote earlier in the call. The buffers are the reverb's own and do not move, so the
 * answer is kept for the object, the segment base and the fifteen (ring, mask) pairs it was
 * worked out for; a call with any of them different works it out again. (One guest thread runs
 * at a time, so one copy serves.) */
static struct {
    uint32_t key[2 + 2 * NET_RINGS];
    int valid, ok;
} net_rings_seen;

static int net_rings_apart(const uint32_t *key, uint32_t lo, uint32_t ds) {
    uint32_t base[NET_RINGS], len[NET_RINGS];
    for (int j = 0; j < NET_RINGS; j++) {
        const uint32_t mask = key[2 + 2 * j + 1];
        if (mask >= (1u << 26)) return 0;
        base[j] = ds + key[2 + 2 * j];
        len[j] = 4u * (mask + 1u);
        if (!in_fast(base[j], len[j]) || !apart(base[j], len[j], lo + NET_LO, NET_LEN)
                || !apart(base[j], len[j], ds + NET_K_LO, NET_K_LEN))
            return 0;
    }
    for (int j = 0; j < NET_RINGS; j++)
        for (int k = j + 1; k < NET_RINGS; k++)
            if (!apart(base[j], len[j], base[k], len[k])) return 0;
    return 1;
}

/* One delay stage's ring cells and new write index. */
typedef struct { uint32_t wn, rd, wr; } net_cells;

AI net_cells net_stage_cells(const uint8_t *g, uint32_t lo, uint32_t ds, int k) {
    net_cells c;
    const uint32_t wi = ld32(g + lo + NET_STAGE[k][0]), mask = ld32(g + lo + NET_STAGE[k][1]);
    const uint32_t ring = ld32(g + lo + NET_STAGE[k][2]), delay = ld32(g + lo + NET_STAGE[k][3]);
    c.wn = (wi + 1u) & mask;
    c.rd = ds + ring + 4u * ((wi - delay + 1u) & mask);
    c.wr = ds + ring + 4u * c.wn;
    return c;
}

#define SH(o) (sh + NET_SH_E + (o))     /* the stack copy's byte at E + o */

AI int net_native(uint8_t *s, uint32_t pc, int p24) {
    uint8_t *g = sa2_guest_fast_base;
    const uint32_t ds = ld32(s + O_DS_BASE), esp = ld32(s + O_ESP);
    const uint32_t E = ld32(s + O_SS_BASE) + esp, F = E - NET_SH_E, K = ds + NET_K_LO;
    if (!g || !in_fast(F, NET_SH_LEN) || !in_fast(K, NET_K_LEN)) return 1 + WHY_MEMORY;
    uint8_t sh[NET_SH_LEN];
    memcpy(sh, g + F, NET_SH_LEN);
    const uint32_t esi = ld32(SH(4));                               /* mov esi,[esp+2Ch]     */
    const uint32_t lo = ds + esi;
    if (!in_fast(lo + NET_LO, NET_LEN)) return 1 + WHY_MEMORY;
    if (!apart(F, NET_SH_LEN, lo + NET_LO, NET_LEN) || !apart(F, NET_SH_LEN, K, NET_K_LEN)
            || !apart(lo + NET_LO, NET_LEN, K, NET_K_LEN))
        return 1 + WHY_OVERLAP;
    /* The four outputs: written in the guest's order at the end, after everything else; apart
     * from the frame, and the first, written before the last constants are read, from them. */
    uint32_t P[4];
    for (int i = 0; i < 4; i++) {
        P[i] = ds + ld32(SH(0x0C + 4 * i));
        if (!in_fast(P[i], 4)) return 1 + WHY_MEMORY;
        if (!apart(P[i], 4, F, NET_SH_LEN)) return 1 + WHY_OVERLAP;
    }
    if (!apart(P[0], 4, K, NET_K_LEN)) return 1 + WHY_OVERLAP;
    /* Any two of them the same float or apart: then the float the tail reloads from the first is
     * the last of the three stored there before it, which the body knows before it writes. */
    for (int i = 0; i < 4; i++)
        for (int j = i + 1; j < 4; j++)
            if (P[i] != P[j] && !apart(P[i], 4, P[j], 4)) return 1 + WHY_OVERLAP;
    /* The rings. */
    uint32_t key[2 + 2 * NET_RINGS];
    key[0] = lo;
    key[1] = ds;
    for (int k = 0; k < NET_STAGES; k++) {
        key[2 + 2 * k] = ld32(g + lo + NET_STAGE[k][2]);
        key[3 + 2 * k] = ld32(g + lo + NET_STAGE[k][1]);
    }
    for (int k = 0; k < NET_COMBS; k++) {
        key[2 + 2 * (NET_STAGES + k)] = ld32(g + lo + NET_COMB(k) + 0x04);
        key[3 + 2 * (NET_STAGES + k)] = ld32(g + lo + NET_COMB(k) + 0x14);
    }
    if (!net_rings_seen.valid || memcmp(key, net_rings_seen.key, sizeof key)) {
        memcpy(net_rings_seen.key, key, sizeof key);
        net_rings_seen.ok = net_rings_apart(key, lo, ds);
        net_rings_seen.valid = 1;
    }
    if (!net_rings_seen.ok) return 1 + WHY_OVERLAP;
    for (int j = 0; j < NET_RINGS; j++)
        if (!apart(ds + key[2 + 2 * j], 4u * (key[3 + 2 * j] + 1u), F, NET_SH_LEN))
            return 1 + WHY_OVERLAP;
    net_cells st[NET_STAGES];
    for (int k = 0; k < NET_STAGES; k++) st[k] = net_stage_cells(g, lo, ds, k);
    cb_cells cc[NET_COMBS];
    for (int k = 0; k < NET_COMBS; k++) cc[k] = cb_cells_of(g, lo + NET_COMB(k), ds);
    const uint32_t K93290C = ds + 0x0093290Cu, K905240 = ds + 0x00905240u,
                   K905D88 = ds + 0x00905D88u, K899278 = ds + 0x00899278u,
                   K899270 = ds + 0x00899270u, K905738 = ds + 0x00905738u,
                   K899268 = ds + 0x00899268u, K905748 = ds + 0x00905748u,
                   K899260 = ds + 0x00899260u;
    const uint8_t *o = g + lo;
    x87 mx, *m = &mx;
    x_load(m, s, p24);
    float out[NET_STAGES];
    cb_writes cw[NET_COMBS];

    /* sub esp,24h ; push esi ; mov esi,[esp+2Ch] ; mov eax,[esi+734h] */
    st32(SH(-0x28), ld32(s + O_ESI));
    i_fld_m(m, ldf(o + 0x744), 0x186, pc + 0x00E, lo + 0x744);       /* fld [esi+744h]       */
    i_fmul_m(m, ldf(SH(8)), 0x04C, pc + 0x014, E + 8);               /* fmul [esp+30h]       */
    /* mov ecx,[esi+738h] ; mov edx,[esi+728h] ; push edi */
    st32(SH(-0x2C), ld32(s + O_EDI));
    i_fmul_m(m, ldf(g + K93290C), 0x00D, pc + 0x025, K93290C);       /* fmul [0093290Ch]     */
    /* mov edi,eax ; sub edi,[esi+730h] ; add eax,1 */
    stf(SH(4), i_fst_m(m, 1, 0x15C, pc + 0x036, E + 4));             /* fstp [esp+30h]       */
    /* add edi,1 ; and edi,ecx */
    i_fld_m(m, ldf(g + st[0].rd), 0x104, pc + 0x03F, st[0].rd);      /* fld [edx+edi*4]      */
    /* and eax,ecx */
    stf(SH(8), i_fst_m(m, 1, 0x15C, pc + 0x044, E + 8));             /* fstp [esp+34h]       */
    /* mov [esi+734h],eax */
    i_fld_m(m, ldf(SH(4)), 0x144, pc + 0x04E, E + 4);                /* fld [esp+30h]        */
    out[0] = i_fst_m(m, 1, 0x11C, pc + 0x052, st[0].wr);             /* fstp [edx+eax*4]     */

    /* stage 1: mov eax,[esi+680h] ; ecx [684h] ; edx [674h] ; edi = (eax - [67Ch] + 1) & ecx */
    i_fld_m(m, ldf(g + st[1].rd), 0x104, pc + 0x077, st[1].rd);      /* fld [edx+edi*4]      */
    stf(SH(4), i_fst_m(m, 1, 0x15C, pc + 0x07C, E + 4));             /* fstp [esp+30h]       */
    i_fld_m(m, ldf(o + 0x68C), 0x186, pc + 0x086, lo + 0x68C);       /* fld [esi+68Ch]       */
    i_fld_m(m, ldf(SH(4)), 0x144, pc + 0x08C, E + 4);                /* fld [esp+30h]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x090);                               /* fld st0              */
    i_fmulp(m, 2, 0x2CA, pc + 0x092);                                /* fmulp st2            */
    i_fld_m(m, ldf(SH(8)), 0x144, pc + 0x094, E + 8);                /* fld [esp+34h]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x098);                               /* fld st0              */
    i_faddp(m, 3, 0x2C3, pc + 0x09A);                                /* faddp st3            */
    i_fxch(m, 2, 0x1CA, pc + 0x09C);                                 /* fxch st2             */
    out[1] = i_fst_m(m, 1, 0x11C, pc + 0x09E, st[1].wr);             /* fstp [edx+eax*4]     */

    /* stage 2 */
    i_fmul_m(m, ldf(o + 0x690), 0x08E, pc + 0x0AD, lo + 0x690);      /* fmul [esi+690h]      */
    i_fld_m(m, ldf(o + 0x68C), 0x186, pc + 0x0B9, lo + 0x68C);       /* fld [esi+68Ch]       */
    i_fmulp(m, 2, 0x2CA, pc + 0x0C7);                                /* fmulp st2            */
    i_fsubrp(m, 1, 0x2E1, pc + 0x0CF);                               /* fsubrp               */
    stf(SH(4), i_fst_m(m, 1, 0x15C, pc + 0x0D5, E + 4));             /* fstp [esp+30h]       */
    i_fld_m(m, ldf(g + st[2].rd), 0x104, pc + 0x0D9, st[2].rd);      /* fld [edx+edi*4]      */
    stf(SH(8), i_fst_m(m, 1, 0x15C, pc + 0x0E2, E + 8));             /* fstp [esp+34h]       */
    i_fld_m(m, ldf(o + 0x6B0), 0x186, pc + 0x0E6, lo + 0x6B0);       /* fld [esi+6B0h]       */
    i_fld_m(m, ldf(SH(8)), 0x144, pc + 0x0EC, E + 8);                /* fld [esp+34h]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x0F0);                               /* fld st0              */
    i_fmulp(m, 2, 0x2CA, pc + 0x0F2);                                /* fmulp st2            */
    i_fld_m(m, ldf(SH(4)), 0x144, pc + 0x0F4, E + 4);                /* fld [esp+30h]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x0F8);                               /* fld st0              */
    i_faddp(m, 3, 0x2C3, pc + 0x0FA);                                /* faddp st3            */
    i_fxch(m, 2, 0x1CA, pc + 0x0FC);                                 /* fxch st2             */
    out[2] = i_fst_m(m, 1, 0x11C, pc + 0x0FE, st[2].wr);             /* fstp [edx+eax*4]     */

    /* stage 3 */
    i_fmul_m(m, ldf(o + 0x6B4), 0x08E, pc + 0x10D, lo + 0x6B4);      /* fmul [esi+6B4h]      */
    i_fld_m(m, ldf(o + 0x6B0), 0x186, pc + 0x119, lo + 0x6B0);       /* fld [esi+6B0h]       */
    i_fmulp(m, 2, 0x2CA, pc + 0x127);                                /* fmulp st2            */
    i_fsubrp(m, 1, 0x2E1, pc + 0x12C);                               /* fsubrp               */
    stf(SH(4), i_fst_m(m, 1, 0x15C, pc + 0x130, E + 4));             /* fstp [esp+30h]       */
    i_fld_m(m, ldf(g + st[3].rd), 0x104, pc + 0x134, st[3].rd);      /* fld [edx+edi*4]      */
    stf(SH(8), i_fst_m(m, 1, 0x15C, pc + 0x137, E + 8));             /* fstp [esp+34h]       */
    i_fld_m(m, ldf(SH(8)), 0x144, pc + 0x13E, E + 8);                /* fld [esp+34h]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x144);                               /* fld st0              */
    i_fmul_m(m, ldf(o + 0x6D4), 0x08E, pc + 0x14C, lo + 0x6D4);      /* fmul [esi+6D4h]      */
    i_fld_m(m, ldf(SH(4)), 0x144, pc + 0x152, E + 4);                /* fld [esp+30h]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x156);                               /* fld st0              */
    i_faddp(m, 2, 0x2C2, pc + 0x158);                                /* faddp st2            */
    i_fxch(m, 1, 0x1C9, pc + 0x15A);                                 /* fxch                 */
    out[3] = i_fst_m(m, 1, 0x11C, pc + 0x15C, st[3].wr);             /* fstp [edx+eax*4]     */

    /* stage 4 */
    i_fld_m(m, ldf(o + 0x6D8), 0x186, pc + 0x165, lo + 0x6D8);       /* fld [esi+6D8h]       */
    i_fmulp(m, 2, 0x2CA, pc + 0x171);                                /* fmulp st2            */
    i_fmul_m(m, ldf(o + 0x6D4), 0x08E, pc + 0x181, lo + 0x6D4);      /* fmul [esi+6D4h]      */
    i_fsubp(m, 1, 0x2E9, pc + 0x18F);                                /* fsubp                */
    stf(SH(4), i_fst_m(m, 1, 0x15C, pc + 0x193, E + 4));             /* fstp [esp+30h]       */
    i_fld_m(m, ldf(g + st[4].rd), 0x104, pc + 0x197, st[4].rd);      /* fld [edx+edi*4]      */
    stf(SH(8), i_fst_m(m, 1, 0x15C, pc + 0x1A0, E + 8));             /* fstp [esp+34h]       */
    i_fld_m(m, ldf(o + 0x6F8), 0x186, pc + 0x1A4, lo + 0x6F8);       /* fld [esi+6F8h]       */
    i_fld_m(m, ldf(SH(8)), 0x144, pc + 0x1AA, E + 8);                /* fld [esp+34h]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x1AE);                               /* fld st0              */
    i_fmulp(m, 2, 0x2CA, pc + 0x1B0);                                /* fmulp st2            */
    i_fld_m(m, ldf(SH(4)), 0x144, pc + 0x1B2, E + 4);                /* fld [esp+30h]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x1B6);                               /* fld st0              */
    i_faddp(m, 3, 0x2C3, pc + 0x1B8);                                /* faddp st3            */
    i_fxch(m, 2, 0x1CA, pc + 0x1BA);                                 /* fxch st2             */
    out[4] = i_fst_m(m, 1, 0x11C, pc + 0x1BC, st[4].wr);             /* fstp [edx+eax*4]     */

    /* stage 5 */
    i_fmul_m(m, ldf(o + 0x6FC), 0x08E, pc + 0x1CB, lo + 0x6FC);      /* fmul [esi+6FCh]      */
    i_fld_m(m, ldf(o + 0x6F8), 0x186, pc + 0x1D7, lo + 0x6F8);       /* fld [esi+6F8h]       */
    i_fmulp(m, 2, 0x2CA, pc + 0x1E5);                                /* fmulp st2            */
    i_fsubrp(m, 1, 0x2E1, pc + 0x1ED);                               /* fsubrp               */
    st32(SH(-0x30), ld32(o + 0x714));                                /* push ecx ([esi+714h]) */
    stf(SH(4), i_fst_m(m, 1, 0x15C, pc + 0x1F4, E + 4));             /* fstp [esp+34h]       */
    /* lea ecx,[esi+1Ch] */
    i_fld_m(m, ldf(g + st[5].rd), 0x104, pc + 0x1FB, st[5].rd);      /* fld [edx+edi*4]      */
    stf(SH(8), i_fst_m(m, 1, 0x15C, pc + 0x204, E + 8));             /* fstp [esp+38h]       */
    i_fld_m(m, ldf(o + 0x71C), 0x186, pc + 0x208, lo + 0x71C);       /* fld [esi+71Ch]       */
    i_fld_m(m, ldf(SH(8)), 0x144, pc + 0x20E, E + 8);                /* fld [esp+38h]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x212);                               /* fld st0              */
    i_fmulp(m, 2, 0x2CA, pc + 0x214);                                /* fmulp st2            */
    i_fld_m(m, ldf(SH(4)), 0x144, pc + 0x216, E + 4);                /* fld [esp+34h]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x21A);                               /* fld st0              */
    i_faddp(m, 3, 0x2C3, pc + 0x21C);                                /* faddp st3            */
    i_fxch(m, 2, 0x1CA, pc + 0x21E);                                 /* fxch st2             */
    out[5] = i_fst_m(m, 1, 0x11C, pc + 0x220, st[5].wr);             /* fstp [edx+eax*4]     */
    i_fmul_m(m, ldf(o + 0x720), 0x08E, pc + 0x223, lo + 0x720);      /* fmul [esi+720h]      */
    i_fld_m(m, ldf(o + 0x71C), 0x186, pc + 0x229, lo + 0x71C);       /* fld [esi+71Ch]       */
    i_fmulp(m, 2, 0x2CA, pc + 0x22F);                                /* fmulp st2            */
    i_fsubrp(m, 1, 0x2E1, pc + 0x231);                               /* fsubrp               */
    stf(SH(4), i_fst_m(m, 1, 0x15C, pc + 0x233, E + 4));             /* fstp [esp+34h]       */

    /* The nine combs, each as: fld the stage output ; [push ecx] ; lea ecx ; fstp [esp] ;
     * call 0x00843400 ; fstp its result into the frame. The first comb's push came above. */
    static const uint16_t fld_at[NET_COMBS] = {0x237, 0x248, 0x25E, 0x275, 0x28C, 0x2A3, 0x2BA,
                                               0x2D1, 0x2E8};
    static const uint16_t arg_at[NET_COMBS] = {0x23B, 0x252, 0x269, 0x280, 0x297, 0x2AE, 0x2C5,
                                               0x2DC, 0x2F3};
    static const uint16_t ret_at[NET_COMBS] = {0x243, 0x25A, 0x271, 0x288, 0x29F, 0x2B6, 0x2CD,
                                               0x2E4, 0x2FB};
    /* A loop, not unrolled: one comb's body is about 4 KB of host code, and nine of them in a
     * row (measured) run slower than the loop, which stays in the instruction cache. */
    for (int k = 0; k < NET_COMBS; k++) {
        if (k == 1) st32(SH(-0x30), esi + NET_COMB(0) + 0x20);   /* push ecx, before the fld */
        i_fld_m(m, ldf(SH(4)), 0x144, pc + fld_at[k], E + 4);       /* fld [esp+30h/34h]    */
        if (k > 1) st32(SH(-0x30), esi + NET_COMB(k - 1) + 0x20);   /* push ecx             */
        stf(SH(-0x30), i_fst_m(m, 1, 0x11C, pc + arg_at[k], E - 0x30)); /* fstp [esp]       */
        st32(SH(-0x34), pc + ret_at[k]);                            /* call                 */
        cb_run(m, g, SH(-0x34), E - 0x34, lo + NET_COMB(k), esi, &cc[k], pc - 0x60u, &cw[k]);
        stf(SH(-0x24 + 4 * k), i_fst_m(m, 1, 0x15C, pc + ret_at[k], E - 0x24 + 4 * k));
    }

    /* The matrix of the nine (ESP E-2Ch: [esp+8] is the first comb's, [esp+28h] the last's). */
    i_fld_m(m, ldf(SH(-0x24)), 0x144, pc + 0x2FF, E - 0x24);         /* fld [esp+8]          */
    /* mov eax,[esp+38h] */
    i_fld_st(m, 0, 0x1C0, pc + 0x307);                               /* fld st0              */
    i_fld_m64(m, ldd(g + K905240), 0x105, pc + 0x309, K905240);      /* fld qword [905240h]  */
    i_fmul_sti(m, 1, 0x0C9, pc + 0x30F);                             /* fmul st1,st          */
    i_fld_m(m, ldf(SH(-0x20)), 0x144, pc + 0x311, E - 0x20);         /* fld [esp+0Ch]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x315);                               /* fld st0              */
    i_fld_m64(m, ldd(g + K905D88), 0x105, pc + 0x317, K905D88);      /* fld qword [905D88h]  */
    i_fmul_sti(m, 1, 0x0C9, pc + 0x31D);                             /* fmul st1,st          */
    i_fxch(m, 4, 0x1CC, pc + 0x31F);                                 /* fxch st4             */
    i_fsubrp(m, 1, 0x2E1, pc + 0x321);                               /* fsubrp               */
    i_fld_m(m, ldf(SH(-0x1C)), 0x144, pc + 0x323, E - 0x1C);         /* fld [esp+10h]        */
    i_fld_st(m, 0, 0x1C0, pc + 0x327);                               /* fld st0              */
    i_faddp(m, 2, 0x2C2, pc + 0x329);                                /* faddp st2            */
    i_fld_m(m, ldf(SH(-0x18)), 0x144, pc + 0x32B, E - 0x18);         /* fld [esp+14h]        */
    i_fmul_m64(m, ldd(g + K899278), 0x00D, pc + 0x32F, K899278);     /* fmul qword [899278h] */
    i_fsubp(m, 2, 0x2EA, pc + 0x335);                                /* fsubp st2            */
    i_fld_m(m, ldf(SH(-0x14)), 0x144, pc + 0x337, E - 0x14);         /* fld [esp+18h]        */
    i_fmul_m64(m, ldd(g + K899270), 0x00D, pc + 0x33B, K899270);     /* fmul qword [899270h] */
    i_faddp(m, 2, 0x2C2, pc + 0x341);                                /* faddp st2            */
    i_fld_m(m, ldf(SH(-0x10)), 0x144, pc + 0x343, E - 0x10);         /* fld [esp+1Ch]        */
    i_fmul_m64(m, ldd(g + K905738), 0x00D, pc + 0x347, K905738);     /* fmul qword [905738h] */
    i_faddp(m, 2, 0x2C2, pc + 0x34D);                                /* faddp st2            */
    i_fld_m(m, ldf(SH(-0x0C)), 0x144, pc + 0x34F, E - 0x0C);         /* fld [esp+20h]        */
    i_fmul_m64(m, ldd(g + K899268), 0x00D, pc + 0x353, K899268);     /* fmul qword [899268h] */
    i_faddp(m, 2, 0x2C2, pc + 0x359);                                /* faddp st2            */
    i_fld_m(m, ldf(SH(-0x08)), 0x144, pc + 0x35B, E - 0x08);         /* fld [esp+24h]        */
    i_fmul_m64(m, ldd(g + K905748), 0x00D, pc + 0x35F, K905748);     /* fmul qword [905748h] */
    i_faddp(m, 2, 0x2C2, pc + 0x365);                                /* faddp st2            */
    i_fld_m(m, ldf(SH(-0x04)), 0x144, pc + 0x367, E - 0x04);         /* fld [esp+28h]        */
    i_fld_m64(m, ldd(g + K899260), 0x105, pc + 0x36B, K899260);      /* fld qword [899260h]  */
    i_fmul_sti(m, 1, 0x0C9, pc + 0x371);                             /* fmul st1,st          */
    i_fxch(m, 3, 0x1CB, pc + 0x373);                                 /* fxch st3             */
    i_faddp(m, 1, 0x2C1, pc + 0x375);                                /* faddp                */
    const float v0 = i_fst_m(m, 1, 0x118, pc + 0x377, P[0]);         /* fstp [eax]           */
    i_fxch(m, 2, 0x1CA, pc + 0x379);                                 /* fxch st2             */
    i_fmul_m64(m, ldd(g + K899278), 0x00D, pc + 0x37B, K899278);     /* fmul qword [899278h] */
    i_fxch(m, 5, 0x1CD, pc + 0x381);                                 /* fxch st5             */
    i_fmul_m64(m, ldd(g + K899270), 0x00D, pc + 0x383, K899270);     /* fmul qword [899270h] */
    i_faddp(m, 5, 0x2C5, pc + 0x389);                                /* faddp st5            */
    /* mov ecx,[esp+3Ch] */
    i_fxch(m, 4, 0x1CC, pc + 0x38F);                                 /* fxch st4             */
    /* mov edx,[esp+40h] ; pop edi */
    i_faddp(m, 1, 0x2C1, pc + 0x396);                                /* faddp                */
    /* pop esi (ESP E-24h from here) */
    i_fld_m(m, ldf(SH(-0x18)), 0x144, pc + 0x399, E - 0x18);         /* fld [esp+0Ch]        */
    i_fmulp(m, 3, 0x2CB, pc + 0x39D);                                /* fmulp st3            */
    i_faddp(m, 2, 0x2C2, pc + 0x39F);                                /* faddp st2            */
    i_fmul_m(m, ldf(SH(-0x14)), 0x04C, pc + 0x3A1, E - 0x14);        /* fmul [esp+10h]       */
    i_faddp(m, 1, 0x2C1, pc + 0x3A5);                                /* faddp                */
    i_fld_m(m, ldf(SH(-0x10)), 0x144, pc + 0x3A7, E - 0x10);         /* fld [esp+14h]        */
    i_fmul_m64(m, ldd(g + K899268), 0x00D, pc + 0x3AB, K899268);     /* fmul qword [899268h] */
    i_faddp(m, 1, 0x2C1, pc + 0x3B1);                                /* faddp                */
    i_fld_m(m, ldf(SH(-0x0C)), 0x144, pc + 0x3B3, E - 0x0C);         /* fld [esp+18h]        */
    i_fmul_m64(m, ldd(g + K905738), 0x00D, pc + 0x3B7, K905738);     /* fmul qword [905738h] */
    i_faddp(m, 1, 0x2C1, pc + 0x3BD);                                /* faddp                */
    i_fld_m(m, ldf(SH(-0x08)), 0x144, pc + 0x3BF, E - 0x08);         /* fld [esp+1Ch]        */
    i_fmulp(m, 2, 0x2CA, pc + 0x3C3);                                /* fmulp st2            */
    i_faddp(m, 1, 0x2C1, pc + 0x3C5);                                /* faddp                */
    i_fld_m(m, ldf(SH(-0x04)), 0x144, pc + 0x3C7, E - 0x04);         /* fld [esp+20h]        */
    i_fmul_m64(m, ldd(g + K905748), 0x00D, pc + 0x3CB, K905748);     /* fmul qword [905748h] */
    i_faddp(m, 1, 0x2C1, pc + 0x3D1);                                /* faddp                */
    stf(SH(4), i_fst_m(m, 1, 0x15C, pc + 0x3D3, E + 4));             /* fstp [esp+28h]       */
    i_fld_m(m, ldf(SH(4)), 0x144, pc + 0x3D7, E + 4);                /* fld [esp+28h]        */
    const float v1 = i_fst_m(m, 0, 0x111, pc + 0x3DB, P[1]);         /* fst [ecx]            */
    i_fchs(m, 0x1E0, pc + 0x3DD);                                    /* fchs                 */
    const float v2 = i_fst_m(m, 1, 0x11A, pc + 0x3DF, P[2]);         /* fstp [edx]           */
    /* What [eax] holds now: v0, unless the second or third output is the same float. Every other
     * write of the call came before v0's (the rings, the object) or lies in the frame, which the
     * outputs are apart from. */
    const float r0 = P[2] == P[0] ? v2 : P[1] == P[0] ? v1 : v0;
    i_fld_m(m, r0, 0x100, pc + 0x3E1, P[0]);                         /* fld [eax]            */
    /* mov eax,[esp+3Ch] */
    i_fchs(m, 0x1E0, pc + 0x3E7);                                    /* fchs                 */
    const float v3 = i_fst_m(m, 1, 0x118, pc + 0x3E9, P[3]);         /* fstp [eax]           */
    /* add esp,24h ; ret 18h */
    x_nan_slots(m);
    if (m->bad) return 1 + WHY_VALUE;

    /* Nothing was written yet. Everything but the outputs, in any order (all apart): */
    memcpy(g + F, sh, NET_SH_LEN);
    for (int k = 0; k < NET_STAGES; k++) {
        st32(g + lo + NET_STAGE[k][0], st[k].wn);
        stf(g + st[k].wr, out[k]);
    }
    for (int k = 0; k < NET_COMBS; k++) cb_commit(g, lo + NET_COMB(k), &cc[k], &cw[k]);
    /* then the outputs in the guest's order, which may be the same float: */
    stf(g + P[0], v0);
    stf(g + P[1], v1);
    stf(g + P[2], v2);
    stf(g + P[3], v3);
    x_store(m, s);
    fl_store(s, fl_add(esp - 0x24, 0x24));
    st32(s + O_EAX, ld32(SH(0x18)));
    st32(s + O_ECX, ld32(SH(0x10)));
    st32(s + O_EDX, ld32(SH(0x14)));
    st32(s + O_EDI, ld32(SH(-0x2C)));
    st32(s + O_ESI, ld32(SH(-0x28)));
    st32(s + O_EIP, ld32(SH(0)));
    st32(s + O_ESP, esp + 0x1C);
    return 0;
}

__attribute__((used, retain))
void *__wrap_sub_843460(void *state, uint32_t pc, void *memory) {
    uint8_t *s = state;
    const int why = !cri_on(CRI_NET) ? 1 + WHY_OFF
                  : (ld16(s + O_CWD) & 0x0300u) ? net_native(s, pc, 0) : net_native(s, pc, 1);
    cri_count(CRI_NET, why);
    return why ? __real_sub_843460(state, pc, memory) : memory;
}

/* ------------------------------------------------------------------ 0x008418B0, the mix */

/* Every one of n floats at p finite. */
static int mix_finite(const uint8_t *p, uint32_t n) {
    uint32_t inf = 0;
    for (uint32_t i = 0; i < n; i++) inf |= (ld32(p + 4u * i) & 0x7F800000u) == 0x7F800000u;
    return !inf;
}

/* The element's two roundings (fmul st1, then fadd dword [eax-..]); with finite floats in, every
 * double here is finite, and a nonzero one is at least 2^-298 in magnitude, so PC24 never meets
 * the slow path. */
AI double mix_rnd(int p24, uint16_t cw, double v) {
    return p24 ? dval(pc24_bits(dbits(v), cw)) : v;
}

AI double mix_one(uint8_t *d, const uint8_t *src, uint32_t i, double gd, int p24, uint16_t cw) {
    const double p = mix_rnd(p24, cw, (double)ldf(src + 4u * i) * gd);
    const double q = mix_rnd(p24, cw, p + (double)ldf(d + 4u * i));
    stf(d + 4u * i, (float)q);
    return q;
}

/* The two sources the guest's order allows to be seen as one pass: dst and src the same, or
 * apart. Each returns the last element's sum, which the guest leaves in ST(6). */
#define MIX_LOOP(name, p24)                                                                    \
    static double name##_same(uint8_t *d, uint32_t n, double gd, uint16_t cw) {                \
        double q = 0;                                                                          \
        for (uint32_t i = 0; i < n; i++) q = mix_one(d, d, i, gd, p24, cw);                    \
        return q;                                                                              \
    }                                                                                          \
    static double name##_apart(uint8_t *restrict d, const uint8_t *restrict src, uint32_t n,   \
                               double gd, uint16_t cw) {                                       \
        double q = 0;                                                                          \
        for (uint32_t i = 0; i < n; i++) q = mix_one(d, src, i, gd, p24, cw);                  \
        return q;                                                                              \
    }
MIX_LOOP(mix53, 0)
MIX_LOOP(mix24, 1)

/* The most elements a call may name and still run natively: 2^24 floats. */
#define MIX_MAX_BLOCKS (1u << 21)

static int mix_native(uint8_t *s, uint32_t pc) {
    uint8_t *g = sa2_guest_fast_base;
    const uint32_t esp = ld32(s + O_ESP);
    const uint32_t sp = ld32(s + O_SS_BASE) + esp, ds = ld32(s + O_DS_BASE);
    if (!g || !in_fast(sp, 0x14)) return 1 + WHY_MEMORY;
    const uint32_t n = ld32(g + sp + 0x0C);
    if (n == 0) {                           /* test eax,eax ; jbe -> ret */
        fl_store(s, fl_logic(0));
        st32(s + O_EAX, 0);
        st32(s + O_EIP, ld32(g + sp));
        st32(s + O_ESP, esp + 4);
        return 0;
    }
    const float gain = ldf(g + sp + 0x10);
    const uint32_t dst = ld32(g + sp + 0x04), src = ld32(g + sp + 0x08);
    const uint32_t blocks = ((n - 1u) >> 3) + 1u;
    if (blocks > MIX_MAX_BLOCKS) return 1 + WHY_SIZE;
    const uint32_t count = 8u * blocks, bytes = 32u * blocks;
    const uint32_t dl = ds + dst, sl = ds + src;
    const int same = dl == sl;
    if (!in_fast(dl, bytes) || !in_fast(sl, bytes)) return 1 + WHY_MEMORY;
    if (!same && !apart(dl, bytes, sl, bytes)) return 1 + WHY_OVERLAP;
    if ((fbits(gain) & 0x7F800000u) == 0x7F800000u || !mix_finite(g + dl, count)
            || (!same && !mix_finite(g + sl, count)))
        return 1 + WHY_VALUE;
    const uint16_t cw = ld16(s + O_CWD);
    const int p24 = !(cw & 0x0300u);
    const double gd = (double)gain;
    double q;
    if (same) q = p24 ? mix24_same(g + dl, count, gd, cw) : mix53_same(g + dl, count, gd, cw);
    else q = p24 ? mix24_apart(g + dl, g + sl, count, gd, cw)
                 : mix53_apart(g + dl, g + sl, count, gd, cw);
    /* fld dword [esp+10h] ; per element fld/fmul st1/fadd/fstp ; fstp st0: slots 0-5 are as
     * they were, slot 6 the last sum, slot 7 the gain; TOP as it was. FADD cleared C0, C2, C3;
     * FSTP ST(0) left DE 0; IE keeps its value. */
    st64(s + O_ST0 + 6 * O_ST_STRIDE, dbits(q));
    st16(s + O_ST0 + 6 * O_ST_STRIDE + 8, 0);
    st64(s + O_ST0 + 7 * O_ST_STRIDE, dbits(gd));
    st16(s + O_ST0 + 7 * O_ST_STRIDE + 8, 0);
    s[O_C0] = s[O_C2] = s[O_C3] = 0;
    s[O_DE] = 0;
    st16(s + O_FOP, 0x1D8);                                 /* fstp st0                 */
    st32(s + O_FIP, pc + 0xB1);
    st16(s + O_FCS, ld16(s + O_SEG_CS));
    st32(s + O_FDP, dl + bytes - 4u);                       /* fstp dword [eax-4], last */
    st16(s + O_FDS, ld16(s + O_SEG_DS));
    fl_store(s, fl_sub(1, 1));                              /* sub edx,1 ; jne          */
    st32(s + O_EAX, dst + bytes);
    st32(s + O_ECX, src + bytes);
    st32(s + O_EDX, 0);
    st32(s + O_EIP, ld32(g + sp));                          /* ret, after the stores    */
    st32(s + O_ESP, esp + 4);
    return 0;
}

__attribute__((used, retain))
void *__wrap_sub_8418b0(void *state, uint32_t pc, void *memory) {
    const int why = !cri_on(CRI_MIX) ? 1 + WHY_OFF : mix_native(state, pc);
    cri_count(CRI_MIX, why);
    return why ? __real_sub_8418b0(state, pc, memory) : memory;
}
