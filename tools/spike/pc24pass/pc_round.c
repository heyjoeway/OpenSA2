/* __sa2_pc_round: x87 precision-control rounding for a post-lift IR pass.
 *
 * remill computes every x87 arithmetic op in x86_fp80 at the full 64-bit
 * significand and carries the FPU control word as state it never consults
 * (agents-journals/s1/2026-09-19-s1-lifter-spike.md). The pass in
 * pc24pass.py inserts, after each fadd/fsub/fmul/fdiv/fsqrt on x86_fp80,
 *
 *     %r = call x86_fp80 @__sa2_pc_round(x86_fp80 %unrounded, i16 %cw)
 *
 * with %cw loaded from State.x87.fxsave.cwd at the op site, so the live
 * control word decides: the CRT reloads 0x27F (PC53) around fsin/fcos
 * (design 3.4). This file is compiled to LLVM IR by the driver and the
 * definition is spliced into each patched module as an internal function, so
 * the fixture harness (tools/spike/fixtures/run_fixtures.py) links nothing
 * new.
 *
 * Semantics (Intel SDM vol. 1, 8.1.5.2 "Precision Control Field"): the
 * precision-control bits only affect FADD/FSUB/FMUL/FDIV/FSQRT and their
 * variants; the result's significand is rounded to 24 bits (PC=00), 53 bits
 * (PC=10) or kept at 64 (PC=11); PC=01 is reserved (treated as identity
 * here). The exponent range stays the 15-bit extended range: PC24 does not
 * narrow the exponent, so a PC24 result never overflows or underflows where
 * a binary32 store would. The rounding direction comes from the RC field
 * (bits 10-11): nearest-even (00, what 0x007F and 0x27F both select), down
 * (01), up (10), toward zero (11).
 *
 * Port of RoundSignificand24 from
 * Documentation/predecessor/SonicPreciseNativeMath.cs (nearest-even on the
 * dropped bits: magnitude += half - 1 + retained_lsb; magnitude &= ~dropped),
 * generalised from a 53-bit double significand dropping 29 bits to a 64-bit
 * x86_fp80 significand dropping 64 - keep bits, and with the explicit
 * integer bit of the x87 format handled (a carry out of bit 63 renormalises
 * into the exponent instead of propagating into it as the double port does
 * implicitly).
 *
 * Special values, stated so the receipt can mark each as reasoned:
 *   zero      identity (both signs; mantissa 0).
 *   inf, nan  identity (exponent 0x7FFF); the payload is untouched.
 *   denormal  (exponent field 0, below 2^-16382): x87 rounds a denormalised
 *             result at the destination precision's fixed position, i.e. at
 *             the same absolute bit as the smallest normal: bit (64 - keep)
 *             of the mantissa regardless of where the leading 1 is. That is
 *             what the fixed mask below does; a carry into bit 63 makes it
 *             the smallest normal (exponent 1). No fixture row reaches this
 *             range: every binary32 value, denormals included, is a normal
 *             x86_fp80, and gets the plain leading-bit-relative rounding.
 *   overflow  a carry out of the top bit at exponent 0x7FFE becomes +/-inf
 *             under nearest-even; under RC down/up/zero x87 would give the
 *             largest finite value of the opposite direction, which is not
 *             modelled (marked reasoned; unobserved).
 * The pass rounds the 64-bit result remill already rounded, not the exact
 * result: a 64-then-24 double rounding can differ from x87's single rounding
 * only when the 64-bit rounding lands exactly on a 24-bit tie, which cannot
 * happen for products and sums of two 24-bit-significand operands (their
 * exact results fit in 64 bits or their dropped part sits below the tie bit)
 * and has probability about 2^-40 per fdiv AT PC24, where it measures zero
 * over 200000 rows per op. That bound does NOT hold at PC53: a 64-then-53
 * double rounding lands on a tie often enough to matter, and the measured
 * rate there is about 48 per 200000 on fdiv and 42 on fsqrt, near 2^-12,
 * twenty-eight binary orders away from the PC24 figure. An earlier version
 * of this comment stated the 2^-40 bound without that qualifier; the G2
 * interrogation caught it. The class is recorded in
 * Documentation/receipts/residues/pc53-double-rounding-v1.json. The
 * reference model in pc24_reference.c has the same class of softness.
 */
#include <stdint.h>
#include <string.h>

long double __sa2_pc_round(long double v, uint16_t cw) {
    unsigned pc = (cw >> 8) & 3u;
    unsigned keep;
    if (pc == 0) keep = 24;
    else if (pc == 2) keep = 53;
    else return v;                       /* 11: 64-bit, identity; 01: reserved, identity */

    uint64_t mant;
    uint16_t se;
    memcpy(&mant, &v, 8);
    memcpy(&se, (const char *)&v + 8, 2);
    unsigned exp = se & 0x7FFFu;
    unsigned sign = se >> 15;
    if (exp == 0x7FFFu || mant == 0) return v;   /* inf, nan, zero */

    unsigned drop = 64u - keep;          /* 40 for PC24, 11 for PC53 */
    uint64_t dropped_mask = (1ull << drop) - 1ull;
    uint64_t half = 1ull << (drop - 1u);
    uint64_t low = mant & dropped_mask;
    uint64_t kept = mant & ~dropped_mask;
    if (low == 0) return v;              /* already representable */

    unsigned rc = (cw >> 10) & 3u;
    int up;
    if (rc == 0) up = low > half || (low == half && ((kept >> drop) & 1ull));   /* nearest-even */
    else if (rc == 1) up = sign;         /* toward -inf: magnitude grows only when negative */
    else if (rc == 2) up = !sign;        /* toward +inf */
    else up = 0;                         /* toward zero */

    if (up) {
        uint64_t incremented = kept + (1ull << drop);
        if (incremented < kept) {        /* carry out of bit 63: renormalise */
            incremented = 1ull << 63;
            exp += 1u;
            if (exp >= 0x7FFFu) exp = 0x7FFFu;   /* overflow: infinity (mantissa 1.000 with exponent 0x7FFF) */
        } else if (exp == 0 && (incremented >> 63)) {
            exp = 1u;                    /* denormal rounded up into the smallest normal */
        }
        kept = incremented;
    }
    se = (uint16_t)((sign << 15) | exp);
    memcpy(&v, &kept, 8);
    memcpy((char *)&v + 8, &se, 2);
    return v;
}
