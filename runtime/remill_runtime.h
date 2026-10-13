/* The `__remill_*` runtime for runtime v0 (decision 2026-09-19-G1 section 1.3,
 * "Wrap": the `__remill_*` runtime -- guest memory, `__remill_function_call`
 * through the function table, `__remill_jump`, flag helpers).
 *
 * `remill_runtime.cpp` is the only translation unit that includes remill's own
 * `State.h`, because that header is C++. It exposes this C interface so the
 * rest of the runtime, the harness and any host stay free of remill.
 */
#ifndef SA2_RUNTIME_REMILL_RUNTIME_H
#define SA2_RUNTIME_REMILL_RUNTIME_H

#include <stdint.h>

#include "cpu_state.h"

#ifdef __cplusplus
extern "C" {
#endif

/* Missing-block policy. The G2 fold replaced the original rule, which decided by the
 * *target*: "the address is a function-table entry, so this is a tail call the lift
 * did not resolve". That justification is false at one of the two addresses it fired
 * at, so the decision is made by the *reaching instruction* instead.
 *
 *   SA2_MB_TRAP    0  every missing block traps, as runtime v0 shipped.
 *   SA2_MB_TABLE   1  dispatch when the target is a function-table entry. The rule the
 *                     corpus shipped with, kept so the A/B measurement that falsified
 *                     it is reproducible without a rebuild. Not a default anywhere.
 *   SA2_MB_REACHED 2  dispatch only when the guest instruction that reaches the missing
 *                     block is an unconditional `jmp` -- the only edge a tail call can
 *                     arrive on -- or when that address carries a named exception with a
 *                     recorded reason (tools/s2/corpus_run/build.py DISPATCH_EXCEPTIONS).
 *                     A conditional branch, a conditional branch's fall-through, a call's
 *                     return fall-through and an unknown edge all trap.
 *
 * `SA2_MISSING_BLOCK_DISPATCH` in the environment overrides whatever the program set,
 * so the measurement is one env var rather than a rebuilt driver.
 */
enum { SA2_MB_TRAP = 0, SA2_MB_TABLE = 1, SA2_MB_REACHED = 2 };
extern int sa2_missing_block_dispatch;
void sa2_mb_policy_from_env(void);   /* honour SA2_MISSING_BLOCK_DISPATCH, if set */
const char *sa2_mb_policy_name(int policy);

/* The reaching-instruction edge table. Every missing block a lifted module can raise is
 * a control-flow edge that leaves the module's lifted byte range, and which kind of edge
 * it is, is a static property of the guest image. `tools/s2/corpus_run/build.py` decodes
 * every lifted range and emits this table beside the function table; the runtime never
 * guesses, and an address with no edge recorded is `SA2_MB_EDGE_UNKNOWN` and traps. */
enum {
    SA2_MB_EDGE_UNKNOWN = 0,  /* no static edge reaches this address */
    SA2_MB_EDGE_JMP     = 1,  /* jmp rel: the only shape a tail call has */
    SA2_MB_EDGE_JCC     = 2,  /* the taken edge of a conditional branch */
    SA2_MB_EDGE_FALL    = 3,  /* the fall-through out of the lifted range */
    SA2_MB_EDGE_CALL    = 4,  /* the return fall-through of a trailing call */
};

typedef struct {
    uint32_t target;    /* the missing block's guest address */
    uint32_t source;    /* the guest instruction that reaches it */
    uint8_t  kind;      /* SA2_MB_EDGE_* */
    uint8_t  allow;     /* a named exception: dispatch although the edge is not a `jmp` */
    const char *why;    /* the exception's recorded reason, NULL when there is none */
} sa2_mb_edge;

void        sa2_mb_set_edges(const sa2_mb_edge *edges, uint32_t count);
void        sa2_mb_register_generated(void);   /* weak default: no table, so every edge is UNKNOWN */
uint32_t    sa2_mb_edge_count(void);
const sa2_mb_edge *sa2_mb_edge_for(uint32_t target);   /* NULL when none is recorded */
const char *sa2_mb_edge_name(int kind);

/* The virtual processor. CPUID and RDTSC answer from a model, not from the
 * host, so two runs of the same binary agree. `sa2_cpu_model_from_env` honours
 * SA2_CPUID_EAX0/EAX1/EBX1/ECX1/EDX1; EDX1 bit 26 is SSE2, which decides which
 * of two bodies of lifted CRT code the guest runs. */
void     sa2_cpu_model_from_env(void);
uint64_t sa2_cpuid_calls(void);
uint64_t sa2_rdtsc_calls(void);
/* Instructions remill could not lift that runtime/sa2_emulate.cpp executed, and
 * how many of those continued at the next instruction's lifted entry. */
uint64_t sa2_rt_emulated(void);
uint64_t sa2_rt_emulated_continued(void);
uint32_t sa2_cpuid_edx1(void);
/* The virtual microsecond clock; `runtime/sa2_imports.c` overrides the weak
 * default with the same clock Sleep and QueryPerformanceCounter read. */
uint64_t sa2_virtual_us(void);

/* The census. Every missing block the run reaches is counted here by address, with the
 * edge that reached it and whether the policy dispatched it or trapped. No record before
 * the G2 fold counted these at all: the corpus's load path was invisible. */
typedef struct {
    uint32_t target;
    uint32_t source;
    uint8_t  kind;
    uint8_t  allow;          /* the edge carried a named exception */
    uint8_t  dispatched;     /* the policy let it through at least once */
    uint32_t hits;           /* times the missing block was reached */
    uint32_t dispatches;     /* times it was dispatched */
    uint32_t traps;          /* times it trapped */
} sa2_mb_census_entry;

uint32_t sa2_mb_census_count(void);
const sa2_mb_census_entry *sa2_mb_census_at(uint32_t i);
void     sa2_mb_census_reset(void);

/* Opaque remill `State` buffer. */
void  *sa2_rt_state_alloc(void);
void   sa2_rt_state_free(void *state);
uint32_t sa2_rt_state_size(void);

/* Convert between the runtime's own CPU state and remill's. */
void sa2_rt_state_load(void *state, const sa2_cpu_state_t *in);
void sa2_rt_state_store(const void *state, sa2_cpu_state_t *out);

/* Register-level accessors, so native callees (import stubs, the fixture's
 * native functions) are plain C. `idx` is one of the SA2_E* enumerators. */
uint32_t    sa2_rt_get_gpr(const void *state, int idx);
void        sa2_rt_set_gpr(void *state, int idx, uint32_t value);
/* An x87 register, ST(i), as its ten stored bytes (cpu_state.h sa2_f80). */
sa2_f80     sa2_rt_get_st80(const void *state, int i);
void        sa2_rt_set_st80(void *state, int i, sa2_f80 v);
#if SA2_HOST_LDBL_IS_F80
/* The same register as the x86-64 host's own `long double`, which is the x87
 * format there. Only on such a host: anywhere else no host floating type can
 * hold the register, and a caller must use the bytes. */
long double sa2_rt_get_st(const void *state, int i);
void        sa2_rt_set_st(void *state, int i, long double v);
#endif
uint16_t    sa2_rt_get_cw(const void *state);
void        sa2_rt_set_cw(void *state, uint16_t cw);

/* The guest's rounding control lives in the *host* `fenv` while lifted code runs,
 * because remill's FLDCW calls `fesetround` and its FNSTCW reads `fegetround`.
 * `sa2_rt_state_load` sets it from the control word on every entry and `sa2_call`
 * restores the host's own mode afterwards, so one row's `fldcw` cannot follow the
 * process into the next row. */
int      sa2_rt_round_from_cw(uint16_t cw);   /* x87 RC bits 10-11 -> FE_* */
uint16_t sa2_rt_cw_from_round(int mode);      /* FE_* -> x87 RC bits 10-11 */
int      sa2_rt_get_host_round(void);
void     sa2_rt_set_host_round(int mode);

/* The portable x87 models: what `fld m64`, `fst m64`, `fst m32` and `fsqrt` do,
 * bit for bit, computed with integers alone. `rc` is an x87 rounding-control
 * field (0 nearest, 1 down, 2 up, 3 toward zero: control word bits 10-11, shifted
 * down), never a host FE_* constant, so the result does not depend on the host's
 * fenv; `cw` is a whole x87 control word, of which `fsqrt` reads the rounding
 * control and the precision control (bits 8-9: 00 24 bits, 10 53, 11 64; the
 * reserved 01 rounds to 64 bits). The x87 unit's masked responses are the ones
 * modelled: an invalid operand -- a negative square root, or one of the
 * encodings the x87 unit no longer supports (an unnormal, a pseudo-NaN, a
 * pseudo-infinity) -- gives the real indefinite, a signalling NaN comes back
 * quiet, overflow and underflow round as IEEE 754 says. No exception flag is
 * modelled, as none is in the runtime (`__remill_fpu_exception_test_and_clear`).
 *
 * cpu_state.h's sa2_f80_from_f64, sa2_f80_to_f64 and sa2_f80_to_f32 are these
 * on a host without the x87 unit, with `rc` from the host's rounding mode; on
 * an x86-64 host they are the instructions themselves. */
sa2_f80  sa2_f80_soft_from_f64(double d);
double   sa2_f80_soft_to_f64(sa2_f80 v, int rc);
float    sa2_f80_soft_to_f32(sa2_f80 v, int rc);
sa2_f80  sa2_f80_soft_sqrt(sa2_f80 v, uint16_t cw);
int      sa2_rt_host_rc(void);   /* the host's rounding mode as an x87 `rc` (above) */

/* The out-of-line half of the guest-memory fast path (guest_memory.h, "the inlined fast
 * path"): the host pointer for an access of `len` bytes at guest `addr`, checked as the
 * memory intrinsics check it, the poison tier included. A fault does not return. Lifted
 * code the fast path rewrote calls these; nothing else needs to. */
uint8_t *sa2_guest_read_slow(uint32_t addr, uint32_t len);
uint8_t *sa2_guest_write_slow(uint32_t addr, uint32_t len);

/* The intrinsic inventory, so the receipt is generated rather than typed. */
enum { SA2_INTRIN_IMPLEMENTED = 0, SA2_INTRIN_STUB_TRAP = 1 };
typedef struct {
    const char *name;
    int         status;     /* SA2_INTRIN_* */
    const char *note;       /* what it does, or why it traps */
} sa2_intrinsic_info;
const sa2_intrinsic_info *sa2_rt_intrinsics(uint32_t *count);

#ifdef __cplusplus
}
#endif
#endif
