/* Trace hooks for `tools/s2/corpus_run/atan2_probe.py`.
 *
 * The probe rewrites one lifted module's IR to call these at two places inside
 * the CRT's transcendental dispatcher `__trandisp2` (lifted as `sub_82c037`):
 *
 *   __sa2_trace_sw     after each `fnstsw m16` -- the x87 status word the guest
 *                      is about to read the FXAM condition codes out of, with
 *                      the st(0) it classified;
 *   __sa2_trace_disp   at the `jmp dword ptr [ebx]` that picks the special-case
 *                      tail -- the computed class byte, the table address, the
 *                      target, and both operands still on the stack.
 *
 * Nothing here interprets the bits: the raw words go to stderr and
 * `atan2_probe.py` decodes them beside what this host's x87 produces for the
 * same operands. Only the traced variant of the program links this file; the
 * shipped `corpus_driver_pc24` is unchanged.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

long double sa2_rt_get_st(const void *state, int i);

void __sa2_trace_sw(uint32_t site, uint16_t sw, uint64_t st0lo, uint16_t st0hi);
void __sa2_trace_st(void *state, uint32_t pc, uint32_t site);
void __sa2_trace_disp(uint32_t target, uint32_t ebx, uint32_t eax, uint32_t ecx,
                      uint64_t st0lo, uint16_t st0hi, uint64_t st1lo, uint16_t st1hi);

void __sa2_trace_sw(uint32_t site, uint16_t sw, uint64_t st0lo, uint16_t st0hi) {
    fprintf(stderr, "SA2TRACE sw site=%u sw=%04x st0=%04x%016llx\n",
            site, (unsigned)sw, (unsigned)st0hi, (unsigned long long)st0lo);
}

/* `site` is the lifted module's own entry address; `pc` the guest PC the trace
 * point carries (the entry PC, or the terminator's next PC). */
void __sa2_trace_st(void *state, uint32_t pc, uint32_t site) {
    unsigned char b0[16] = {0}, b1[16] = {0};
    long double v0 = sa2_rt_get_st(state, 0), v1 = sa2_rt_get_st(state, 1);
    memcpy(b0, &v0, 10);
    memcpy(b1, &v1, 10);
    fprintf(stderr, "SA2TRACE st site=%08x pc=%08x st0=%02x%02x%016llx st1=%02x%02x%016llx"
                    " (%.12Lg, %.12Lg)\n",
            site, pc, b0[9], b0[8], *(unsigned long long *)b0,
            b1[9], b1[8], *(unsigned long long *)b1, v0, v1);
}

void __sa2_trace_disp(uint32_t target, uint32_t ebx, uint32_t eax, uint32_t ecx,
                      uint64_t st0lo, uint16_t st0hi, uint64_t st1lo, uint16_t st1hi) {
    fprintf(stderr,
            "SA2TRACE disp target=%08x table=%08x eax=%08x ecx=%08x "
            "st0=%04x%016llx st1=%04x%016llx\n",
            target, ebx, eax, ecx,
            (unsigned)st0hi, (unsigned long long)st0lo,
            (unsigned)st1hi, (unsigned long long)st1lo);
}
