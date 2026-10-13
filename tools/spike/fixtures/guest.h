/* Guest address space for the S1 fixture harnesses: the unpacked PE image is
 * mapped at its own VA (the harness ELF is linked at 0x10000000), so lifted
 * code's 32-bit addresses are host addresses. A flat image dump (SizeOfImage bytes from
 * VA 0x400000, written by run_fixtures.py from the PE section table) is
 * loaded on top of a zero mapping that also holds the argument arenas, the
 * matrix stack and the guest stack (shapes.h). */
#ifndef SA2_SPIKE_GUEST_H
#define SA2_SPIKE_GUEST_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
int guest_map(const char *image_path);            /* 0 on success */
void guest_reset_arenas(void);                    /* zero arenas and stack, prefill A_OUT */
static inline uint8_t  *gp8(uint32_t a)  { return (uint8_t *)(uintptr_t)a; }
static inline uint32_t *gp32(uint32_t a) { return (uint32_t *)(uintptr_t)a; }
static inline float    *gpf(uint32_t a)  { return (float *)(uintptr_t)a; }
#ifdef __cplusplus
}
#endif
#endif
