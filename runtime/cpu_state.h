/* Portable guest CPU state for runtime v0 (design 3.4 "CPU state struct").
 *
 * This is the runtime's own state type: eight 32-bit GPRs, EIP, EFLAGS, the
 * x87 register stack with its control and status words, and the segment bases
 * (FS for the TLS/SEH work S3 and S4 need). It is deliberately *not* remill's
 * `State`: callers, the harness and any future host see this struct, and
 * `remill_runtime.cpp` is the single translation unit that converts it to and
 * from remill's layout. When the custom-emitter fallback of decision 1.4 is
 * taken, this struct survives and that converter is what is dropped.
 */
#ifndef SA2_RUNTIME_CPU_STATE_H
#define SA2_RUNTIME_CPU_STATE_H

#include <float.h>
#include <stdint.h>

/* The host is little-endian: the endianness audit's first item (S7 port
 * survey, section 4). The guest is, and the runtime copies guest bytes into
 * host scalars with memcpy everywhere it reads guest memory (sa2_peek,
 * gpeek32, the pk32/pe32 pairs, the remill memory intrinsics), so on a
 * big-endian host every one of those would read a byte-swapped value without
 * any error. That fails here, at compile time, rather than in a replay. */
#if !defined(__BYTE_ORDER__) || !defined(__ORDER_LITTLE_ENDIAN__)
#error "sa2 runtime: the compiler does not say the target's byte order, and the runtime needs a little-endian host"
#endif
#ifdef __cplusplus
static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__,
              "sa2 runtime: the host must be little-endian, as the guest is");
#else
_Static_assert(__BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__,
               "sa2 runtime: the host must be little-endian, as the guest is");
#endif

/* 1 where the host's `long double` is the x87 extended format itself in a
 * 16-byte, 16-aligned slot, as on an x86-64 host; 0 elsewhere. On aarch64 Linux
 * `long double` is IEEE binary128 and remill's `native_float80_t` is a `double`
 * (remill Types.h), so no host floating type can hold an x87 register there. An
 * i386 host has the format but a 12-byte, 4-aligned slot, so an array of its
 * `long double` would not line up with the 16-byte registers below; it gets 0
 * and the models. */
#if defined(__x86_64__) && LDBL_MANT_DIG == 64 && __SIZEOF_LONG_DOUBLE__ == 16
#define SA2_HOST_LDBL_IS_F80 1
#else
#define SA2_HOST_LDBL_IS_F80 0
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* An x87 extended-precision value as the x87 unit stores it with `fstp m80`:
 * ten little-endian bytes, the 64-bit significand with its explicit integer
 * bit (bit 63) first, then the sign and the 15-bit biased exponent. The bytes
 * are the value; nothing in the runtime holds an x87 register in a host
 * floating type except through the conversions below. Six bytes of padding
 * keep each register in a 16-byte, 16-aligned slot, which is where an x86-64
 * `long double` put it, so `sa2_cpu_state_t` has the layout it always had. */
typedef struct __attribute__((aligned(16))) sa2_f80 {
    uint8_t b[10];
    uint8_t pad[6];
} sa2_f80;

#ifdef __cplusplus
static_assert(sizeof(sa2_f80) == 16 && alignof(sa2_f80) == 16, "sa2_f80 is a 16-byte slot");
#else
_Static_assert(sizeof(sa2_f80) == 16 && _Alignof(sa2_f80) == 16, "sa2_f80 is a 16-byte slot");
#endif
#if SA2_HOST_LDBL_IS_F80
/* The `st` view below aliases `st80` register for register only if so. */
#ifdef __cplusplus
static_assert(sizeof(long double) == sizeof(sa2_f80) && alignof(long double) == alignof(sa2_f80),
              "the host long double fills an sa2_f80 slot exactly");
#else
_Static_assert(sizeof(long double) == sizeof(sa2_f80) && _Alignof(long double) == _Alignof(sa2_f80),
               "the host long double fills an sa2_f80 slot exactly");
#endif
#endif

/* Indices for the register accessors the native callees use. */
enum {
    SA2_EAX = 0, SA2_ECX, SA2_EDX, SA2_EBX,
    SA2_ESP, SA2_EBP, SA2_ESI, SA2_EDI,
    SA2_EIP, SA2_GPR_COUNT
};

typedef struct {
    uint32_t    gpr[SA2_GPR_COUNT];   /* eax ecx edx ebx esp ebp esi edi eip */
    uint32_t    eflags;
    uint16_t    x87_cw;               /* control word; 0x007F is the game's PC24 setting */
    uint16_t    x87_sw;
    uint8_t     x87_top;
    /* The x87 register stack, st80[0] = ST(0), as bytes. On an x86-64 host the
     * same slots are also readable as `st`, the host's own `long double`,
     * which there is the x87 format exactly; that view exists for the readers
     * that predate the bytes (harness/fixture_driver.c) and is absent on every
     * other host, so such a reader fails to compile there instead of reading
     * binary128 garbage. */
#if SA2_HOST_LDBL_IS_F80
    union {
        sa2_f80     st80[8];
        long double st[8];
    };
#else
    sa2_f80     st80[8];
#endif
    uint32_t    fs_base, gs_base, ds_base, es_base, ss_base, cs_base;
    uint32_t    mxcsr;                /* SSE control/status; 0x1F80, every exception masked,
                                         is what Windows gives every new thread */
} sa2_cpu_state_t;

/* Convenience for callers building an initial state. */
void sa2_cpu_state_reset(sa2_cpu_state_t *s);

/* The x87 unit's own conversions, made as `fld m64` and `fst m64`/`fst m32`
 * make them, rounding (where there is any) by the host's current rounding mode
 * (fegetround), which is the mode the x87 unit would use. On an x86-64 host these
 * are the host's x87 instructions; elsewhere the portable models in
 * remill_runtime.cpp (sa2_f80_soft_*, remill_runtime.h). The models are held
 * bit for bit to the x87 instructions on an x86-64 host, in every rounding mode,
 * and the same probe run under qemu-aarch64 to the same digests
 * (harness/rt_port_probe.cpp, Documentation/scripts/tests/test_runtime_port.py). */
sa2_f80 sa2_f80_from_f64(double d);   /* exact; a signalling NaN is quietened */
double  sa2_f80_to_f64(sa2_f80 v);
float   sa2_f80_to_f32(sa2_f80 v);

#ifdef __cplusplus
}
#endif
#endif
