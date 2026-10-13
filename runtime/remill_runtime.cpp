/* The `__remill_*` runtime for runtime v0.
 *
 * Every intrinsic the lifted IR declares is implemented here against guest
 * memory (guest_memory.h) and the function table (function_table.h). The set
 * was enumerated from the `declare` lines of the 500-function sample's IR
 * under `private/generated/s2/build_sample/lift/` rather than guessed;
 * `tools/s2/wrap/intrinsic_inventory.py` re-derives it and checks it against
 * `sa2_rt_intrinsics()` below, so the receipt's inventory is generated.
 *
 * This and `sa2_emulate.cpp` are the translation units that include remill's
 * own `State.h` (v5.0.7, from `private/lifters/remill`, which is C++) and know
 * remill's register layout. Everything else in the runtime sees
 * `sa2_cpu_state_t` (cpu_state.h).
 *
 * Widths: remill's `addr_t` is 64-bit when its headers are compiled for the
 * x86-64 host, but the lifted i386 IR passes 32-bit guest addresses, so every
 * intrinsic below takes `uint32_t` explicitly and `Intrinsics.h` is *not*
 * included. The whole runtime is built for a 64-bit host (x86_64-linux-musl,
 * and for S7 aarch64-linux-musl) for the same reason the S1 harness was: the IR
 * hardcodes host sizes for its 80-bit operand copies, and an i386 build silently
 * zeroes every x87 store.
 *
 * x87 registers are bytes. An x87 value is the ten bytes `fstp m80` stores
 * (cpu_state.h sa2_f80), and every path below moves those bytes and never a
 * host floating type: remill's `native_float80_t` is a `double` on aarch64
 * (remill Types.h) and `long double` is binary128 there, so a ten-byte copy into
 * either is garbage that compiles. The conversions a caller needs are the x87
 * unit's own (sa2_f80_to_f64 and its siblings), made by the host's x87
 * instructions where there is one and by the integer models at the end of this
 * file where there is not.
 */
#include <cfenv>
#include <cstdarg>
#include <cstdint>
#include <cstdlib>
#include <cstdio>
#include <cstring>

#include "remill/Arch/X86/Runtime/State.h"

extern "C" {
#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_trap.h"
}

/* runtime/sa2_emulate.cpp, which S4's link supplies. Weak because S2's closed
 * builds (tools/s2/corpus_run/build.py, tools/s2/wrap/wrap.py) compile this file
 * without it, and there the hypercall keeps the trap it always had. Declared
 * from its own header and then marked weak, so the two cannot drift apart. */
extern "C" {
#include "sa2_emulate.h"
}
#pragma weak sa2_emulate

struct Memory;
typedef uint32_t gaddr;

/* tools/s8/f80_shims_double.c's double-mode marker (see st_get below): defined only by the
 * double mode's shims, which are what a double-mode lifted image links. */
extern "C" const int sa2_f80_state_double __attribute__((weak));

/* The address after the instruction this thread last emulated, until the
 * lifted code's __remill_error consumes it. Per thread, as guest threads are
 * host threads. */
static thread_local gaddr t_emulated_next;
static uint64_t g_emulated, g_emulated_continued;

/* ------------------------------------------------------------------ helpers */
namespace {

/* An access outside the guest space traps here, out of line and cold, so that `checked`
 * is a bounds test and one branch, and every memory intrinsic below inlines it whole.
 * As plain `inline` helpers each guest read or write was two nested calls (S5 profile). */
__attribute__((noinline, cold, noreturn))
void checked_fault(gaddr a, uint32_t len, const char *what) {
    sa2_trap_note("%s of %u byte(s) at guest %#010x is outside the guest space (region %s)",
                  what, len, a, sa2_guest_region_of(a));
    sa2_trap_raise(SA2_TRAP_FAULT, a);
}

__attribute__((always_inline)) inline uint8_t *checked(gaddr a, uint32_t len, const char *what) {
    uint8_t *p = sa2_gp(a, len);
    if (__builtin_expect(p == nullptr, 0)) checked_fault(a, len, what);
    return p;
}

/* The poison tier (guest_memory.h). Off by default and one predictable branch when on, so
 * the hot path of 1.2 million rows is unchanged.
 *
 * The guest-memory fast path (guest_memory.h, "the inlined fast path") makes every access
 * these two would accept without calling them, whenever `sa2_guest_fast_base` is set. A
 * check added to either must leave that base NULL whenever it can refuse an access, or be
 * made in the fast path too (tools/s2/corpus_run/state_local.py `_fast_helper`). */
__attribute__((always_inline)) inline uint8_t *checked_read(gaddr a, uint32_t len) {
    uint8_t *p = checked(a, len, "read");
    if (__builtin_expect(sa2_guest_poison != SA2_POISON_OFF, 0)
            && sa2_guest_is_poison(a, len)) {
        sa2_guest_poison_reads++;
        if (!sa2_guest_poison_first) sa2_guest_poison_first = a;
        if (sa2_guest_poison == SA2_POISON_TRAP) {
            sa2_trap_note("read of %u byte(s) at guest %#010x (region %s) from a page nothing "
                          "has written: not the PE's file-initial bytes and not any plan's poke",
                          len, a, sa2_guest_region_of(a));
            sa2_trap_raise(SA2_TRAP_FAULT, a);
        }
    }
    return p;
}

__attribute__((always_inline)) inline uint8_t *checked_write(gaddr a, uint32_t len) {
    uint8_t *p = checked(a, len, "write");
    if (__builtin_expect(sa2_guest_poison != SA2_POISON_OFF, 0))
        sa2_guest_note_write(a, len);
    return p;
}

/* ST(i)'s ten bytes, in and out of remill's `State`. In a build of the lifted image in
 * tools/s8/f80_double.py's double mode the register slots hold a binary64 (its eight bytes,
 * then two zero bytes) and the mode's shims define `sa2_f80_state_double`; the accessors then
 * convert, exactly from the slot and to nearest into it, so every caller still sees the x87's
 * ten bytes. */
inline bool st_is_double() { return &sa2_f80_state_double != nullptr; }

/* The double mode's two conversions, between the x87's ten bytes and the double a slot holds:
 * sa2_f80_soft_to_f64 to nearest and sa2_f80_soft_from_f64 (exact), except that a NaN keeps
 * its quiet bit both ways. A signalling NaN stays signalling, its payload's top 51 bits kept
 * and its lowest bit set if they were all zero (still a NaN), because remill's FLD m80 tests the
 * loaded image for one (tools/s8/f80_double.py reads it back as the 80-bit image of the double,
 * `__sa2_dimg_lo` and `__sa2_dimg_hi`) and the exact mode's sticky IE follows from it. */
inline double dbl_of_f80(const sa2_f80 &v) {
    uint64_t m;
    uint16_t se;
    std::memcpy(&m, v.b, 8);
    std::memcpy(&se, v.b + 8, 2);
    if ((se & 0x7FFF) == 0x7FFF && (m >> 63) && !(m & (1ull << 62)) && (m & ((1ull << 62) - 1))) {
        uint64_t bits = (static_cast<uint64_t>(se >> 15) << 63) | 0x7FF0000000000000ull
                      | ((m >> 11) & ((1ull << 51) - 1));
        if (!(bits & ((1ull << 51) - 1))) bits |= 1;
        double d;
        std::memcpy(&d, &bits, 8);
        return d;
    }
    return sa2_f80_soft_to_f64(v, 0);
}

inline sa2_f80 f80_of_dbl(double d) {
    sa2_f80 v = sa2_f80_soft_from_f64(d);
    uint64_t bits;
    std::memcpy(&bits, &d, 8);
    if ((bits & 0x7FF0000000000000ull) == 0x7FF0000000000000ull && (bits & ((1ull << 51) - 1))
            && !(bits & (1ull << 51))) {
        uint64_t m;
        std::memcpy(&m, v.b, 8);
        m &= ~(1ull << 62);
        std::memcpy(v.b, &m, 8);
    }
    return v;
}

inline sa2_f80 st_get(const State *s, int i) {
    sa2_f80 v;
    std::memset(&v, 0, sizeof v);
    if (st_is_double()) {
        double d;
        std::memcpy(&d, &s->st.elems[i].val, 8);
        return f80_of_dbl(d);
    }
    std::memcpy(v.b, &s->st.elems[i].val, 10);
    return v;
}

inline void st_set(State *s, int i, const sa2_f80 &v) {
    if (st_is_double()) {
        const double d = dbl_of_f80(v);
        uint8_t slot[10] = {0};
        std::memcpy(slot, &d, 8);
        std::memcpy(&s->st.elems[i].val, slot, 10);
        return;
    }
    std::memcpy(&s->st.elems[i].val, v.b, 10);
}

}  // namespace

/* ------------------------------------------------- guest rounding control
 *
 * remill's lifted code does not carry the rounding mode in `State`: its FLDCW
 * semantic calls `fesetround` and its FNSTCW reads `fegetround`, so the guest's
 * rounding-control field round-trips through *process-global host* state. Two
 * consequences the S2 runtime shipped with:
 *
 *   * `sa2_rt_state_load` wrote the caller's control word into `State` and never
 *     told the host, so a guest control word with a non-zero rounding control was
 *     silently ignored: every op rounded to nearest whatever the fixture asked for.
 *   * `sa2_call` never restored the host mode, so one row that executed `fldcw`
 *     with a non-zero rounding field left the process in that mode and every later
 *     row in the process inherited it.
 *
 * The fix is both halves: the mode is set from the guest control word on every
 * entry, and `sa2_call` restores the host's own mode on the way out (sa2_call.c).
 * x87's RC field (control word bits 10-11) is 00 nearest, 01 down, 10 up, 11
 * truncate; on x86 the FE_* constants are those two bits in place, which is the
 * identity remill's `fesetround(cw & 0x0C00)` relies on -- this mapping is
 * explicit rather than relying on it.
 */
extern "C" int sa2_rt_round_from_cw(uint16_t cw) {
    switch (cw & 0x0C00u) {
    case 0x0000u: return FE_TONEAREST;
    case 0x0400u: return FE_DOWNWARD;
    case 0x0800u: return FE_UPWARD;
    default:      return FE_TOWARDZERO;
    }
}

extern "C" uint16_t sa2_rt_cw_from_round(int mode) {
    switch (mode) {
    case FE_DOWNWARD:   return 0x0400u;
    case FE_UPWARD:     return 0x0800u;
    case FE_TOWARDZERO: return 0x0C00u;
    default:            return 0x0000u;
    }
}

extern "C" int sa2_rt_get_host_round(void) { return fegetround(); }
extern "C" void sa2_rt_set_host_round(int mode) { fesetround(mode); }

/* ------------------------------------------------------------------ state */
extern "C" {

void sa2_cpu_state_reset(sa2_cpu_state_t *s) {
    std::memset(s, 0, sizeof *s);
    s->x87_cw = 0x007F;     /* design 2.3: the game's collision code runs at PC24 */
    /* Windows starts every thread with MXCSR 0x1F80: all six SSE exceptions masked,
     * round to nearest. The memset left 0, all unmasked, and remill's STMXCSR reads
     * the mask bits from `State`, so the CRT's math dispatchers (_CItan at
     * 0x00859A70 and its siblings), which take the SSE2 implementation only when
     * `stmxcsr & 0x1F80` is 0x1F80, took the x87 one instead: the game's projection
     * matrix came out one ulp from native from the first tick (2026-09-23, the
     * widened boot replay; agents-journals/s4/2026-09-23-boot-replay-widened.md). */
    s->mxcsr = 0x1F80;
    s->eflags = 0x00000202;
    /* Thread 0's TEB (runtime/guest_memory.h). The memset left this 0, which put
     * every `FS:[0]` access in the null guard: the SEH prologue of the first
     * guest function driven at image scale trapped SA2_TRAP_FAULT at 0x00000000
     * before executing anything of its own. The contents are laid down by
     * `sa2_guest_init`; this is the register that points at them. A per-thread
     * state sets this to `sa2_guest_teb(thread_index)` instead. */
    s->fs_base = SA2_GUEST_TEB_BASE;
}

void *sa2_rt_state_alloc(void) {
    void *p = nullptr;
    if (posix_memalign(&p, 16, sizeof(State)) != 0) return nullptr;
    std::memset(p, 0, sizeof(State));
    return p;
}

void sa2_rt_state_free(void *state) { free(state); }
uint32_t sa2_rt_state_size(void) { return (uint32_t)sizeof(State); }

void sa2_rt_state_load(void *raw, const sa2_cpu_state_t *in) {
    State *s = (State *)raw;
    std::memset(s, 0, sizeof *s);
    s->gpr.rax.dword = in->gpr[SA2_EAX];
    s->gpr.rcx.dword = in->gpr[SA2_ECX];
    s->gpr.rdx.dword = in->gpr[SA2_EDX];
    s->gpr.rbx.dword = in->gpr[SA2_EBX];
    s->gpr.rsp.dword = in->gpr[SA2_ESP];
    s->gpr.rbp.dword = in->gpr[SA2_EBP];
    s->gpr.rsi.dword = in->gpr[SA2_ESI];
    s->gpr.rdi.dword = in->gpr[SA2_EDI];
    s->gpr.rip.dword = in->gpr[SA2_EIP];
    s->addr.ss_base.dword = in->ss_base;
    s->addr.cs_base.dword = in->cs_base;
    s->addr.ds_base.dword = in->ds_base;
    s->addr.es_base.dword = in->es_base;
    s->addr.fs_base.dword = in->fs_base;
    s->addr.gs_base.dword = in->gs_base;
    s->x87.fxsave.cwd.flat = in->x87_cw;
    s->x87.fxsave.swd.flat = in->x87_sw;
    /* remill's STMXCSR takes the exception masks and flags from here and only the
     * rounding bits from the host (see sa2_cpu_state_reset). */
    s->x87.fxsave.mxcsr.flat = in->mxcsr;
    /* remill reads the rounding mode from the host, not from `State`: without this
     * the guest's rounding control is ignored (see sa2_rt_round_from_cw above). */
    fesetround(sa2_rt_round_from_cw(in->x87_cw));
    for (int i = 0; i < 8; i++) st_set(s, i, in->st80[i]);
    s->aflag.cf = (in->eflags >> 0) & 1u;
    s->aflag.pf = (in->eflags >> 2) & 1u;
    s->aflag.af = (in->eflags >> 4) & 1u;
    s->aflag.zf = (in->eflags >> 6) & 1u;
    s->aflag.sf = (in->eflags >> 7) & 1u;
    s->aflag.df = (in->eflags >> 10) & 1u;
    s->aflag.of = (in->eflags >> 11) & 1u;
}

void sa2_rt_state_store(const void *raw, sa2_cpu_state_t *out) {
    const State *s = (const State *)raw;
    out->gpr[SA2_EAX] = s->gpr.rax.dword;
    out->gpr[SA2_ECX] = s->gpr.rcx.dword;
    out->gpr[SA2_EDX] = s->gpr.rdx.dword;
    out->gpr[SA2_EBX] = s->gpr.rbx.dword;
    out->gpr[SA2_ESP] = s->gpr.rsp.dword;
    out->gpr[SA2_EBP] = s->gpr.rbp.dword;
    out->gpr[SA2_ESI] = s->gpr.rsi.dword;
    out->gpr[SA2_EDI] = s->gpr.rdi.dword;
    out->gpr[SA2_EIP] = s->gpr.rip.dword;
    out->ss_base = s->addr.ss_base.dword;
    out->cs_base = s->addr.cs_base.dword;
    out->ds_base = s->addr.ds_base.dword;
    out->es_base = s->addr.es_base.dword;
    out->fs_base = s->addr.fs_base.dword;
    out->gs_base = s->addr.gs_base.dword;
    /* The rounding-control field the guest is actually running under lives in the
     * host's `fenv`, because that is where remill's FLDCW put it. Merge it back so a
     * caller reading `x87_cw` sees the guest's own control word and not whatever
     * survived remill's mask. */
    out->x87_cw = (uint16_t)((s->x87.fxsave.cwd.flat & (uint16_t)~0x0C00u)
                             | sa2_rt_cw_from_round(fegetround()));
    out->x87_sw = s->x87.fxsave.swd.flat;
    /* As STMXCSR builds it: the rounding field (bits 13-14) from the host, the
     * rest from `State`. */
    out->mxcsr = (s->x87.fxsave.mxcsr.flat & ~0x6000u)
               | ((uint32_t)(sa2_rt_cw_from_round(fegetround()) >> 10) << 13);
    for (int i = 0; i < 8; i++) out->st80[i] = st_get(s, i);
    out->eflags = 0x00000002u
                | ((uint32_t)s->aflag.cf << 0) | ((uint32_t)s->aflag.pf << 2)
                | ((uint32_t)s->aflag.af << 4) | ((uint32_t)s->aflag.zf << 6)
                | ((uint32_t)s->aflag.sf << 7) | ((uint32_t)s->aflag.df << 10)
                | ((uint32_t)s->aflag.of << 11);
}

uint32_t sa2_rt_get_gpr(const void *raw, int idx) {
    const State *s = (const State *)raw;
    switch (idx) {
    case SA2_EAX: return s->gpr.rax.dword;
    case SA2_ECX: return s->gpr.rcx.dword;
    case SA2_EDX: return s->gpr.rdx.dword;
    case SA2_EBX: return s->gpr.rbx.dword;
    case SA2_ESP: return s->gpr.rsp.dword;
    case SA2_EBP: return s->gpr.rbp.dword;
    case SA2_ESI: return s->gpr.rsi.dword;
    case SA2_EDI: return s->gpr.rdi.dword;
    case SA2_EIP: return s->gpr.rip.dword;
    default: return 0;
    }
}

void sa2_rt_set_gpr(void *raw, int idx, uint32_t v) {
    State *s = (State *)raw;
    switch (idx) {
    case SA2_EAX: s->gpr.rax.dword = v; break;
    case SA2_ECX: s->gpr.rcx.dword = v; break;
    case SA2_EDX: s->gpr.rdx.dword = v; break;
    case SA2_EBX: s->gpr.rbx.dword = v; break;
    case SA2_ESP: s->gpr.rsp.dword = v; break;
    case SA2_EBP: s->gpr.rbp.dword = v; break;
    case SA2_ESI: s->gpr.rsi.dword = v; break;
    case SA2_EDI: s->gpr.rdi.dword = v; break;
    case SA2_EIP: s->gpr.rip.dword = v; break;
    default: break;
    }
}

sa2_f80 sa2_rt_get_st80(const void *raw, int i)       { return st_get((const State *)raw, i); }
void    sa2_rt_set_st80(void *raw, int i, sa2_f80 v)   { st_set((State *)raw, i, v); }
#if SA2_HOST_LDBL_IS_F80
long double sa2_rt_get_st(const void *raw, int i) {
    long double v = 0;
    const sa2_f80 b = st_get((const State *)raw, i);
    std::memcpy(&v, b.b, 10);
    return v;
}
void sa2_rt_set_st(void *raw, int i, long double v) {
    sa2_f80 b;
    std::memset(&b, 0, sizeof b);
    std::memcpy(b.b, &v, 10);
    st_set((State *)raw, i, b);
}
#endif
uint16_t    sa2_rt_get_cw(const void *raw)               { return ((const State *)raw)->x87.fxsave.cwd.flat; }
void        sa2_rt_set_cw(void *raw, uint16_t cw)        { ((State *)raw)->x87.fxsave.cwd.flat = cw; }

/* ------------------------------------------------------- memory intrinsics */
/* Each one-to-eight-byte intrinsic tests what `checked_read` and `checked_write` test -- the
 * range `sa2_gp` accepts and the poison tier off -- and makes the access with no frame; every
 * other access tail-calls the out-of-line checked path, the intrinsics' own `checked_read` and
 * `checked_write`, so a fault, its note and the poison tier are what they were. The lifted
 * functions State localization did not rewrite call these for every guest access (the S8
 * profile: in the double mode a fifth of Metal Harbor's time, much of it the frame the cold
 * path's calls made every call set up). */
/* The test is the inlined fast path's own (guest_memory.h): `sa2_guest_fast_base`, which is
 * the base exactly while guest memory is mapped and the poison tier is off, and the range
 * `sa2_gp` accepts as one unsigned compare: one load and two tests an access, where `sa2_gp`'s
 * pointer and `sa2_guest_poison` were two loads and three (the thermal unit, 2026-10-12: these
 * intrinsics were a quarter of the game thread's samples in a live stage). Whatever the fast
 * test refuses goes to the checked path, which makes the same access or the same fault. */
#define SA2_FAST_IN(a, n) \
    ((uint32_t)((a) - SA2_GUEST_NULL_GUARD) <= (uint32_t)(SA2_GUEST_END - SA2_GUEST_NULL_GUARD - (n)))
#define RD(sfx, type)                                                        \
    __attribute__((noinline, cold)) static type read_slow_##sfx(gaddr a) {  \
        type v; std::memcpy(&v, checked_read(a, sizeof(type)), sizeof v); \
        return v;                                                            \
    }                                                                        \
    type __remill_read_memory_##sfx(Memory *, gaddr a) {                     \
        const uint8_t *b = sa2_guest_fast_base;                              \
        if (__builtin_expect(b != nullptr && SA2_FAST_IN(a, sizeof(type)), 1)) { \
            type v; std::memcpy(&v, b + a, sizeof v);                        \
            return v;                                                        \
        }                                                                    \
        return read_slow_##sfx(a);                                           \
    }
#define WR(sfx, type)                                                        \
    __attribute__((noinline, cold)) static Memory *write_slow_##sfx(Memory *m, gaddr a, type v) { \
        std::memcpy(checked_write(a, sizeof(type)), &v, sizeof v);           \
        return m;                                                            \
    }                                                                        \
    Memory *__remill_write_memory_##sfx(Memory *m, gaddr a, type v) {        \
        uint8_t *b = sa2_guest_fast_base;                                    \
        if (__builtin_expect(b != nullptr && SA2_FAST_IN(a, sizeof(type)), 1)) { \
            std::memcpy(b + a, &v, sizeof v);                                \
            return m;                                                        \
        }                                                                    \
        return write_slow_##sfx(m, a, v);                                    \
    }

RD(8, uint8_t)  RD(16, uint16_t) RD(32, uint32_t) RD(64, uint64_t)
RD(f32, float)  RD(f64, double)
WR(8, uint8_t)  WR(16, uint16_t) WR(32, uint32_t) WR(64, uint64_t)
WR(f32, float)  WR(f64, double)
#undef RD
#undef WR

/* The out-of-line half of the inlined guest-memory fast path (guest_memory.h, "the inlined
 * fast path"; tools/s2/corpus_run/state_local.py). A lifted function the fast path rewrote
 * calls one of these for every access it does not make inline -- an address outside
 * [SA2_GUEST_NULL_GUARD, SA2_GUEST_END - len], or any access while `sa2_guest_fast_base` is
 * NULL -- and then makes the access through the pointer returned. Each is the intrinsics'
 * own `checked_read` or `checked_write`, so the fault and its note, and the poison tier's
 * test, count and mark, are the ones the intrinsics above give the same access. Out of line
 * and cold: with the poison tier off, a mapped guest reaches them only to trap. */
extern "C" __attribute__((noinline, cold)) uint8_t *sa2_guest_read_slow(gaddr a, uint32_t len) {
    return checked_read(a, len);
}

extern "C" __attribute__((noinline, cold)) uint8_t *sa2_guest_write_slow(gaddr a, uint32_t len) {
    return checked_write(a, len);
}

/* The 80-bit forms move the ten stored bytes, never a host `long double`'s
 * padding: the guest's `fld`/`fstp m80` touch exactly ten bytes.
 *
 * The lifted IR passes the operand as a pointer to the 16-byte slot its x86-64
 * lift gave a `native_float80_t` (`ptr nonnull align 16 dereferenceable(16)`),
 * and these are C symbols, so a byte pointer is the same ABI on every host.
 * remill's own signature, `native_float80_t &`, is not used: on aarch64 that is
 * a `double &`, and assigning through it would store eight bytes of a
 * conversion where the IR expects ten bytes of x87 value. */
Memory *__remill_read_memory_f80(Memory *m, gaddr a, uint8_t *out) {
    std::memcpy(out, checked_read(a, 10), 10);
    return m;
}

Memory *__remill_write_memory_f80(Memory *m, gaddr a, const uint8_t *v) {
    std::memcpy(checked_write(a, 10), v, 10);
    return m;
}

/* The same in tools/s8/f80_double.py's double mode, where the operand slot holds a binary64:
 * FLD m80 rounds the guest's ten bytes to nearest into it, FSTP m80 widens it exactly, and a
 * signalling NaN stays one both ways (dbl_of_f80, f80_of_dbl). The slot is the 16 bytes the IR
 * passes (dereferenceable(16)). */
Memory *__remill_read_memory_f80_dbl(Memory *m, gaddr a, uint8_t *out) {
    sa2_f80 v;
    std::memset(&v, 0, sizeof v);
    std::memcpy(v.b, checked_read(a, 10), 10);
    const double d = dbl_of_f80(v);
    std::memset(out, 0, 16);
    std::memcpy(out, &d, 8);
    return m;
}

Memory *__remill_write_memory_f80_dbl(Memory *m, gaddr a, const uint8_t *v) {
    double d;
    std::memcpy(&d, v, 8);
    const sa2_f80 f = f80_of_dbl(d);
    std::memcpy(checked_write(a, 10), f.b, 10);
    return m;
}

/* --------------------------------------------------- flags, compares, x87 */
/* remill's own runtime defines these as identities: the lifted code has
 * already computed the value, and the intrinsic exists so an analysis can
 * recognise it. Redefining them as anything else changes semantics. */
bool __remill_flag_computation_zero(bool r, ...)     { return r; }
bool __remill_flag_computation_sign(bool r, ...)     { return r; }
bool __remill_flag_computation_carry(bool r, ...)    { return r; }
bool __remill_flag_computation_overflow(bool r, ...) { return r; }
bool __remill_compare_eq(bool r)  { return r; }
bool __remill_compare_neq(bool r) { return r; }
bool __remill_compare_sgt(bool r) { return r; }
bool __remill_compare_sge(bool r) { return r; }
bool __remill_compare_slt(bool r) { return r; }
bool __remill_compare_sle(bool r) { return r; }
bool __remill_compare_ugt(bool r) { return r; }
bool __remill_compare_uge(bool r) { return r; }
bool __remill_compare_ult(bool r) { return r; }
bool __remill_compare_ule(bool r) { return r; }

/* No x87 exception is raised to the guest in v0: the game masks all of them
 * in its control word (0x007F) and never reads the status word's exception
 * bits on the fixture paths. Recorded as a limit. */
uint32_t __remill_fpu_exception_test_and_clear(uint32_t, uint32_t) { return 0; }

uint8_t  __remill_undefined_8(void)  { return 0; }
uint16_t __remill_undefined_16(void) { return 0; }
uint32_t __remill_undefined_32(void) { return 0; }
uint64_t __remill_undefined_64(void) { return 0; }
float    __remill_undefined_f32(void) { return 0.0f; }
double   __remill_undefined_f64(void) { return 0.0; }
#if defined(_WIN32)
/* The lifted IR declares this `{ i64, i16 } @__remill_undefined_f80()`, which the Win64
 * backend returns in RAX and DX. A C++ function returning remill's 10-byte float80_t returns
 * it through a hidden pointer in RCX (the Windows x64 rule for an aggregate over 8 bytes)
 * that the IR's callers never pass, so it stored through whatever RCX held: a guest worker of
 * the boot replay faulted at host address 0x208 here under Wine (2026-10-11). The value is
 * zero either way, so on this host it is written in the IR's own convention. */
__asm__(".text\n"
        ".globl __remill_undefined_f80\n"
        ".def __remill_undefined_f80; .scl 2; .type 32; .endef\n"
        ".p2align 4\n"
        "__remill_undefined_f80:\n"
        "    xorl %eax, %eax\n"
        "    xorl %edx, %edx\n"
        "    ret\n");
#else
float80_t __remill_undefined_f80(void) { return float80_t(); }
#endif

/* ----------------------------------------------------------- control flow */
/* `__remill_function_call(state, pc, memory)`: the return address is already
 * on the guest stack and `pc` is the *target* (remill stores the target into
 * `gpr.rip` before the call, and the argument is a load of it). Dispatch
 * through the function table; the callee's own `ret` pops the return address
 * and the caller's IR resumes at RETURN_PC. */
Memory *__remill_function_call(State *s, gaddr pc, Memory *m) {
    (void)s;
    return (Memory *)sa2_ft_dispatch(pc, (void *)s, pc, (void *)m);
}

/* A computed jump: `pc` is the target. Tail-dispatch through the same table. */
Memory *__remill_jump(State *s, gaddr pc, Memory *m) {
    return (Memory *)sa2_ft_dispatch(pc, (void *)s, pc, (void *)m);
}

/* A `ret`: the lifted code has already popped the return address into
 * `gpr.rip`. `sa2_call` compares it with the sentinel it pushed. */
Memory *__remill_function_return(State *, gaddr, Memory *m) { return m; }

/* The lifted function reached an address it has no block for. In S2 this is
 * either a callee outside the lifted set or a defect in the lift; both are
 * traps with the address, never a silent fall-through.
 *
 * Missing-block policy. The S2 wrap receipt left this open: *"Once whole-image
 * linking lands, a missing block whose address *is* an entry is a tail call the
 * lift did not resolve, and dispatching it is probably right; that is a decision
 * with evidence behind it, not a default."* The corpus run adopted that rule and
 * the G2 interrogation falsified its justification: the rule fires at two
 * addresses and at one of them no `jmp` reaches the missing block at all -- it is
 * a conditional branch's fall-through out of the lifted range, dispatched as if it
 * were a tail call into the next function's prologue.
 *
 * So the decision is made by the reaching instruction (remill_runtime.h). The
 * table of edges is static -- which instruction can reach a given missing block is
 * a property of the guest image and of the byte range each module was lifted from
 * -- and `tools/s2/corpus_run/build.py` decodes it and emits it beside the function
 * table. An address the table does not name is UNKNOWN and traps, so a lift whose
 * range moved fails loudly instead of silently dispatching somewhere new.
 *
 * Every reached address is counted, with its edge, whatever the policy does; the
 * driver writes the census out, because no record before this counted the load path
 * the corpus actually runs on. */
int sa2_missing_block_dispatch = SA2_MB_TRAP;

const char *sa2_mb_policy_name(int policy) {
    switch (policy) {
    case SA2_MB_TRAP:    return "trap (every missing block)";
    case SA2_MB_TABLE:   return "table (dispatch when the target is a function-table entry)";
    case SA2_MB_REACHED: return "reached (dispatch only a jmp edge)";
    default:             return "unknown";
    }
}

void sa2_mb_policy_from_env(void) {
    const char *v = std::getenv("SA2_MISSING_BLOCK_DISPATCH");
    if (v && *v) sa2_missing_block_dispatch = (int)std::strtol(v, nullptr, 0);
}

/* ------------------------------------------------------ the edge table */
static const sa2_mb_edge *g_mb_edges;
static uint32_t           g_mb_edge_count;

/* What `__remill_missing_block` found for a pc: its edge and its census row (nullptr when the
 * census was full when the pc was first reached, which it stays until the next reset). The
 * census is a list searched from the front, full at its 256 rows in a live stage, where the hot
 * targets sat as far down as row 206 and the targets past it were searched to the end at every
 * hit; with the edge table's binary search that was 1,800 searches a frame (the thermal unit,
 * 2026-10-12). This remembers both, so the census and its counts are what they were. Any change
 * to the edges or the census forgets everything (g_mb_hot_gen). */
struct mb_hot {
    gaddr pc;
    uint32_t gen;
    const sa2_mb_edge *edge;
    sa2_mb_census_entry *slot;
};
static constexpr uint32_t kMbHotBits = 11;
static mb_hot   g_mb_hot[1u << kMbHotBits];
static uint32_t g_mb_hot_gen = 1;

void sa2_mb_set_edges(const sa2_mb_edge *edges, uint32_t count) {
    g_mb_edges = edges;
    g_mb_edge_count = count;
    g_mb_hot_gen++;
}

uint32_t sa2_mb_edge_count(void) { return g_mb_edge_count; }

const sa2_mb_edge *sa2_mb_edge_for(uint32_t target) {
    /* The generated table is sorted by target; a few dozen entries, so a binary
     * search keeps this off the profile even when the policy is consulted per row. */
    uint32_t lo = 0, hi = g_mb_edge_count;
    while (lo < hi) {
        uint32_t mid = lo + (hi - lo) / 2;
        if (g_mb_edges[mid].target < target) lo = mid + 1;
        else hi = mid;
    }
    if (lo < g_mb_edge_count && g_mb_edges[lo].target == target) return &g_mb_edges[lo];
    return nullptr;
}

const char *sa2_mb_edge_name(int kind) {
    switch (kind) {
    case SA2_MB_EDGE_JMP:  return "jmp";
    case SA2_MB_EDGE_JCC:  return "jcc-taken";
    case SA2_MB_EDGE_FALL: return "fall-through";
    case SA2_MB_EDGE_CALL: return "call-return";
    default:               return "unknown";
    }
}

/* ---------------------------------------------------------- the census */
#define SA2_MB_CENSUS_MAX 256
static sa2_mb_census_entry g_mb_census[SA2_MB_CENSUS_MAX];
static uint32_t            g_mb_census_count;

uint32_t sa2_mb_census_count(void) { return g_mb_census_count; }

const sa2_mb_census_entry *sa2_mb_census_at(uint32_t i) {
    return i < g_mb_census_count ? &g_mb_census[i] : nullptr;
}

void sa2_mb_census_reset(void) {
    std::memset(g_mb_census, 0, sizeof g_mb_census);
    g_mb_census_count = 0;
    g_mb_hot_gen++;
}

static sa2_mb_census_entry *mb_census_slot(gaddr pc, const sa2_mb_edge *edge) {
    for (uint32_t i = 0; i < g_mb_census_count; i++)
        if (g_mb_census[i].target == pc) return &g_mb_census[i];
    if (g_mb_census_count >= SA2_MB_CENSUS_MAX) return nullptr;
    sa2_mb_census_entry *e = &g_mb_census[g_mb_census_count++];
    e->target = pc;
    e->source = edge ? edge->source : 0u;
    e->kind = edge ? edge->kind : (uint8_t)SA2_MB_EDGE_UNKNOWN;
    e->allow = edge ? edge->allow : (uint8_t)0;
    return e;
}

Memory *__remill_missing_block(State *state, gaddr pc, Memory *memory) {
    mb_hot *hot = &g_mb_hot[(pc * 2654435761u) >> (32 - kMbHotBits)];
    if (hot->pc != pc || hot->gen != g_mb_hot_gen) {
        hot->pc = pc;
        hot->edge = sa2_mb_edge_for(pc);
        hot->slot = mb_census_slot(pc, hot->edge);
        hot->gen = g_mb_hot_gen;
    }
    const sa2_mb_edge *edge = hot->edge;
    const int kind = edge ? (int)edge->kind : SA2_MB_EDGE_UNKNOWN;
    sa2_mb_census_entry *slot = hot->slot;
    if (slot) slot->hits++;

    int dispatch = 0;
    switch (sa2_missing_block_dispatch) {
    case SA2_MB_TABLE:   dispatch = sa2_ft_lookup(pc) != nullptr; break;
    case SA2_MB_REACHED: dispatch = (kind == SA2_MB_EDGE_JMP) || (edge && edge->allow); break;
    default:             dispatch = 0; break;
    }
    if (dispatch) {
        if (slot) { slot->dispatches++; slot->dispatched = 1; }
        return (Memory *)sa2_ft_dispatch(pc, state, pc, memory);
    }
    if (slot) slot->traps++;
    sa2_trap_note("no lifted block at %#010x (region %s); reached by %s at %#010x; policy %s%s",
                  pc, sa2_guest_region_of(pc), sa2_mb_edge_name(kind),
                  edge ? edge->source : 0u, sa2_mb_policy_name(sa2_missing_block_dispatch),
                  sa2_ft_lookup(pc) ? "; the address is a function-table entry, which is not by itself a tail call" : "");
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
}

/* After an instruction remill could not lift, its trace ends in
 * __remill_error at the next instruction; when the runtime has just emulated
 * that instruction on this thread, this continues there instead of trapping.
 * The continuation is an entry `lift_extra.py --seed-continuations` lifted.
 *
 * A guaranteed tail call, not a call: the lifted code reaches here by a tail
 * call too, so a codec loop that emulates an instruction every iteration runs
 * in constant host stack, where a call would add frames per instruction. */
Memory *__remill_error(State *state, gaddr pc, Memory *memory) {
    if (t_emulated_next && pc == t_emulated_next) {
        t_emulated_next = 0;
        sa2_lifted_fn fn = sa2_ft_lookup(pc);
        if (fn) {
            g_emulated_continued++;
            auto next = reinterpret_cast<Memory *(*)(State *, gaddr, Memory *)>(fn);
            [[clang::musttail]] return next(state, pc, memory);
        }
        sa2_trap_note("emulated the instruction before %#010x (region %s), and there is no "
                      "lifted continuation at %#010x: tools/s4/lift_extra.py "
                      "--seed-continuations lifts one", pc, sa2_guest_region_of(pc), pc);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    sa2_trap_note("lifted code raised __remill_error at %#010x (region %s)", pc, sa2_guest_region_of(pc));
    sa2_trap_raise(SA2_TRAP_FAULT, pc);
}

extern "C" uint64_t sa2_rt_emulated(void) { return g_emulated; }
extern "C" uint64_t sa2_rt_emulated_continued(void) { return g_emulated_continued; }

/* ------------------------------------------------------- barriers, atomics */
/* Single guest thread in v0: every barrier is an identity and an atomic
 * region is ordinary code. S4's thread strategy (decision section 3) replaces
 * these when the CRI workers run. */
Memory *__remill_barrier_load_load(Memory *m)   { return m; }
Memory *__remill_barrier_load_store(Memory *m)  { return m; }
Memory *__remill_barrier_store_load(Memory *m)  { return m; }
Memory *__remill_barrier_store_store(Memory *m) { return m; }
Memory *__remill_atomic_begin(Memory *m)        { return m; }
Memory *__remill_atomic_end(Memory *m)          { return m; }
Memory *__remill_delay_slot_begin(Memory *m)    { return m; }
Memory *__remill_delay_slot_end(Memory *m)      { return m; }

/* ------------------------------------------------------ stubbed with a trap */
/* Not declared by any of the 500-function sample's IR. They exist so a future
 * lift links and stops with an address instead of an undefined symbol. */
/* The virtual microsecond clock. `runtime/sa2_imports.c` owns the real one --
 * the same clock Sleep, GetTickCount and QueryPerformanceCounter read -- and
 * defines a strong override. The weak default here is a monotone counter, so a
 * program that links this file without the import stubs (the S2 corpus driver
 * does exactly that) still links and still gets a clock that never goes
 * backwards. */
extern "C" __attribute__((weak)) uint64_t sa2_virtual_us(void) {
    static uint64_t t;
    return ++t;
}

/* CPUID and RDTSC: what machine the guest is told it is running on.
 *
 * Design 3.5 virtualises time; this virtualises the processor, for the same
 * reason and with the same consequence -- two runs of the same binary must
 * answer identically or no replay is deterministic. Nothing here reads the
 * host CPU.
 *
 * The values are *invented*: no capture of the original exists to agree with,
 * and unlike a Win32 return there is no Win32 contract that fixes them either.
 * They are therefore a named placeholder with an environment override rather
 * than a constant presented as a measurement. The model is a Core 2-era
 * GenuineIntel reporting FPU, TSC, CX8, CMOV, MMX, FXSR, SSE and SSE2, which is
 * what the 2012 PC build's minimum target had.
 *
 * The one that matters is SSE2 (leaf 1, EDX bit 26): `__sse2_available_init` at
 * 0x007ae71f tests it and the CRT's string and memory routines dispatch on the
 * result, so the bit chooses which of two bodies of lifted guest code runs.
 * SA2_CPUID_EDX1 moves it without a rebuild, which is what makes "does the
 * SSE2 path lift and run" an experiment rather than an argument. */
static uint32_t g_cpuid_eax0 = 1u;
static uint32_t g_cpuid_eax1 = 0x000006F6u;   /* family 6, model 15, stepping 6 */
static uint32_t g_cpuid_ebx1 = 0x00000800u;   /* CLFLUSH line size 64 bytes, no brand id */
static uint32_t g_cpuid_ecx1 = 0x00000000u;   /* nothing above SSE2 is claimed */
static uint32_t g_cpuid_edx1 = 0x07888111u;   /* FPU TSC CX8 CMOV CLFSH MMX FXSR SSE SSE2 */
static uint64_t g_cpuid_calls, g_rdtsc_calls;

uint64_t sa2_cpuid_calls(void) { return g_cpuid_calls; }
uint64_t sa2_rdtsc_calls(void) { return g_rdtsc_calls; }
uint32_t sa2_cpuid_edx1(void)  { return g_cpuid_edx1; }

void sa2_cpu_model_from_env(void) {
    struct { const char *name; uint32_t *slot; } k[] = {
        {"SA2_CPUID_EAX0", &g_cpuid_eax0}, {"SA2_CPUID_EAX1", &g_cpuid_eax1},
        {"SA2_CPUID_EBX1", &g_cpuid_ebx1}, {"SA2_CPUID_ECX1", &g_cpuid_ecx1},
        {"SA2_CPUID_EDX1", &g_cpuid_edx1},
    };
    for (auto &e : k) {
        const char *v = std::getenv(e.name);
        if (v && *v) *e.slot = (uint32_t)std::strtoul(v, nullptr, 0);
    }
}

Memory *__remill_sync_hyper_call(State *s, Memory *m, uint32_t call) {
    switch (call) {
    case 258u: {   /* SyncHyperCall::kX86CPUID */
        const uint32_t leaf = s->gpr.rax.dword;
        g_cpuid_calls++;
        if (leaf == 0u) {
            s->gpr.rax.dword = g_cpuid_eax0;
            s->gpr.rbx.dword = 0x756E6547u;   /* "Genu" */
            s->gpr.rdx.dword = 0x49656E69u;   /* "ineI" */
            s->gpr.rcx.dword = 0x6C65746Eu;   /* "ntel" */
        } else if (leaf == 1u) {
            s->gpr.rax.dword = g_cpuid_eax1;
            s->gpr.rbx.dword = g_cpuid_ebx1;
            s->gpr.rcx.dword = g_cpuid_ecx1;
            s->gpr.rdx.dword = g_cpuid_edx1;
        } else {
            /* Leaves above the reported maximum: all zero, which is what an
             * Intel processor returns and is not a guess. */
            s->gpr.rax.dword = s->gpr.rbx.dword = s->gpr.rcx.dword = s->gpr.rdx.dword = 0u;
        }
        return m;
    }
    case 259u:     /* kX86ReadTSC */
    case 260u: {   /* kX86ReadTSCP */
        g_rdtsc_calls++;
        /* The virtual clock at the reported processor frequency, so the counter
         * and QueryPerformanceCounter cannot disagree about elapsed time. */
        const uint64_t tsc = sa2_virtual_us() * 2000ull;   /* 2 GHz, virtual */
        s->gpr.rax.dword = (uint32_t)tsc;
        s->gpr.rdx.dword = (uint32_t)(tsc >> 32);
        if (call == 260u) s->gpr.rcx.dword = 0u;           /* TSC_AUX: processor 0 */
        return m;
    }
    case 256u: {   /* kX86EmulateInstruction: remill has no semantics for it */
        t_emulated_next = 0;
        if (!sa2_emulate) {
            sa2_trap_note("__remill_sync_hyper_call 256 (emulate) at %#010x; this build links "
                          "no emulator", s->gpr.rip.dword);
            sa2_trap_raise(SA2_TRAP_FAULT, s->gpr.rip.dword);
        }
        uint32_t next = 0, fault = 0;
        char why[200];
        const int st = sa2_emulate(s, &next, &fault, why, sizeof why);
        if (st == SA2_EMU_OK) {
            /* The lifted code now tail-calls __remill_error at the next
             * instruction, which continues there (below). */
            t_emulated_next = next;
            g_emulated++;
            return m;
        }
        sa2_trap_note("__remill_sync_hyper_call 256 (emulate) at %#010x: %s",
                      s->gpr.rip.dword, why);
        sa2_trap_raise(SA2_TRAP_FAULT, st == SA2_EMU_FAULT ? fault : s->gpr.rip.dword);
    }
    default:
        sa2_trap_note("__remill_sync_hyper_call %u at %#010x (not cpuid, rdtsc or emulate; "
                      "the runtime has no defensible answer for it)",
                      call, s ? s->gpr.rip.dword : 0u);
        sa2_trap_raise(SA2_TRAP_FAULT, s ? s->gpr.rip.dword : 0u);
    }
}

Memory *__remill_async_hyper_call(State *s, gaddr ret_addr, Memory *m) {
    (void)m;
    sa2_trap_note("__remill_async_hyper_call (interrupt/syscall) returning to %#010x; not implemented in v0", ret_addr);
    sa2_trap_raise(SA2_TRAP_FAULT, ret_addr);
}

long double __remill_read_memory_f128(Memory *, gaddr a) {
    sa2_trap_note("__remill_read_memory_f128 at %#010x; the guest has no 128-bit float path in v0", a);
    sa2_trap_raise(SA2_TRAP_FAULT, a);
}

Memory *__remill_write_memory_f128(Memory *, gaddr a, long double) {
    sa2_trap_note("__remill_write_memory_f128 at %#010x; the guest has no 128-bit float path in v0", a);
    sa2_trap_raise(SA2_TRAP_FAULT, a);
}

#define IO_STUB(sfx, type)                                                    \
    type __remill_read_io_port_##sfx(Memory *, gaddr a) {                     \
        sa2_trap_note("__remill_read_io_port_" #sfx " at %#010x; no I/O ports in v0", a); \
        sa2_trap_raise(SA2_TRAP_FAULT, a);                                    \
    }                                                                         \
    Memory *__remill_write_io_port_##sfx(Memory *, gaddr a, type) {           \
        sa2_trap_note("__remill_write_io_port_" #sfx " at %#010x; no I/O ports in v0", a); \
        sa2_trap_raise(SA2_TRAP_FAULT, a);                                    \
    }
IO_STUB(8, uint8_t) IO_STUB(16, uint16_t) IO_STUB(32, uint32_t)
#undef IO_STUB

/* ------------------------------------------------------------- inventory */
static const sa2_intrinsic_info kIntrinsics[] = {
    {"__remill_read_memory_8",   SA2_INTRIN_IMPLEMENTED, "bounds-checked guest read"},
    {"__remill_read_memory_16",  SA2_INTRIN_IMPLEMENTED, "bounds-checked guest read"},
    {"__remill_read_memory_32",  SA2_INTRIN_IMPLEMENTED, "bounds-checked guest read"},
    {"__remill_read_memory_64",  SA2_INTRIN_IMPLEMENTED, "bounds-checked guest read"},
    {"__remill_read_memory_f32", SA2_INTRIN_IMPLEMENTED, "bounds-checked guest read"},
    {"__remill_read_memory_f64", SA2_INTRIN_IMPLEMENTED, "bounds-checked guest read"},
    {"__remill_read_memory_f80", SA2_INTRIN_IMPLEMENTED, "bounds-checked guest read of exactly ten bytes"},
    {"__remill_write_memory_8",   SA2_INTRIN_IMPLEMENTED, "bounds-checked guest write"},
    {"__remill_write_memory_16",  SA2_INTRIN_IMPLEMENTED, "bounds-checked guest write"},
    {"__remill_write_memory_32",  SA2_INTRIN_IMPLEMENTED, "bounds-checked guest write"},
    {"__remill_write_memory_64",  SA2_INTRIN_IMPLEMENTED, "bounds-checked guest write"},
    {"__remill_write_memory_f32", SA2_INTRIN_IMPLEMENTED, "bounds-checked guest write"},
    {"__remill_write_memory_f64", SA2_INTRIN_IMPLEMENTED, "bounds-checked guest write"},
    {"__remill_write_memory_f80", SA2_INTRIN_IMPLEMENTED, "bounds-checked guest write of exactly ten bytes"},
    {"__remill_flag_computation_zero",     SA2_INTRIN_IMPLEMENTED, "identity, as remill's own runtime defines it"},
    {"__remill_flag_computation_sign",     SA2_INTRIN_IMPLEMENTED, "identity, as remill's own runtime defines it"},
    {"__remill_flag_computation_carry",    SA2_INTRIN_IMPLEMENTED, "identity, as remill's own runtime defines it"},
    {"__remill_flag_computation_overflow", SA2_INTRIN_IMPLEMENTED, "identity, as remill's own runtime defines it"},
    {"__remill_compare_eq",  SA2_INTRIN_IMPLEMENTED, "identity"},
    {"__remill_compare_neq", SA2_INTRIN_IMPLEMENTED, "identity"},
    {"__remill_compare_sgt", SA2_INTRIN_IMPLEMENTED, "identity"},
    {"__remill_compare_sge", SA2_INTRIN_IMPLEMENTED, "identity"},
    {"__remill_compare_slt", SA2_INTRIN_IMPLEMENTED, "identity"},
    {"__remill_compare_sle", SA2_INTRIN_IMPLEMENTED, "identity"},
    {"__remill_compare_ugt", SA2_INTRIN_IMPLEMENTED, "identity"},
    {"__remill_compare_uge", SA2_INTRIN_IMPLEMENTED, "identity"},
    {"__remill_compare_ult", SA2_INTRIN_IMPLEMENTED, "identity"},
    {"__remill_compare_ule", SA2_INTRIN_IMPLEMENTED, "identity"},
    {"__remill_fpu_exception_test_and_clear", SA2_INTRIN_IMPLEMENTED,
     "returns 0: the guest masks every x87 exception in cw 0x007F and no fixture path reads the exception bits"},
    {"__remill_undefined_8",   SA2_INTRIN_IMPLEMENTED, "zero"},
    {"__remill_undefined_16",  SA2_INTRIN_IMPLEMENTED, "zero"},
    {"__remill_undefined_32",  SA2_INTRIN_IMPLEMENTED, "zero"},
    {"__remill_undefined_64",  SA2_INTRIN_IMPLEMENTED, "zero"},
    {"__remill_undefined_f32", SA2_INTRIN_IMPLEMENTED, "zero"},
    {"__remill_undefined_f64", SA2_INTRIN_IMPLEMENTED, "zero"},
    {"__remill_undefined_f80", SA2_INTRIN_IMPLEMENTED, "zero"},
    {"__remill_function_call",   SA2_INTRIN_IMPLEMENTED, "dispatch through the function table; a miss traps with the address"},
    {"__remill_jump",            SA2_INTRIN_IMPLEMENTED, "dispatch through the function table; a miss traps with the address"},
    {"__remill_function_return", SA2_INTRIN_IMPLEMENTED, "identity; sa2_call compares gpr.rip with the sentinel it pushed"},
    {"__remill_missing_block",   SA2_INTRIN_IMPLEMENTED,
     "counts the address in the missing-block census with the guest instruction that reaches it, then applies sa2_missing_block_dispatch: SA2_MB_REACHED dispatches only a `jmp` edge (a tail call), SA2_MB_TABLE reproduces the withdrawn target-membership rule, SA2_MB_TRAP traps everything"},
    {"__remill_error",           SA2_INTRIN_IMPLEMENTED,
     "continues at the next instruction when the runtime has just emulated the one before it "
     "and a lifted entry is there; traps with the address otherwise"},
    {"__remill_barrier_load_load",   SA2_INTRIN_IMPLEMENTED, "identity: one guest thread in v0"},
    {"__remill_barrier_load_store",  SA2_INTRIN_IMPLEMENTED, "identity: one guest thread in v0"},
    {"__remill_barrier_store_load",  SA2_INTRIN_IMPLEMENTED, "identity: one guest thread in v0"},
    {"__remill_barrier_store_store", SA2_INTRIN_IMPLEMENTED, "identity: one guest thread in v0"},
    {"__remill_atomic_begin",     SA2_INTRIN_IMPLEMENTED, "identity: one guest thread in v0"},
    {"__remill_atomic_end",       SA2_INTRIN_IMPLEMENTED, "identity: one guest thread in v0"},
    {"__remill_delay_slot_begin", SA2_INTRIN_IMPLEMENTED, "identity: x86 has no delay slots"},
    {"__remill_delay_slot_end",   SA2_INTRIN_IMPLEMENTED, "identity: x86 has no delay slots"},
    {"__remill_sync_hyper_call",  SA2_INTRIN_IMPLEMENTED,
     "cpuid (leaf 0 and 1) and rdtsc/rdtscp answer from a virtual processor model; 256 "
     "(emulate) runs runtime/sa2_emulate.cpp where it is linked; every other hypercall still "
     "traps by number"},
    {"__remill_async_hyper_call", SA2_INTRIN_STUB_TRAP,
     "interrupts and syscalls: the guest is a Windows PE whose OS calls are imports, not interrupts; S4 work"},
    {"__remill_read_memory_f128",  SA2_INTRIN_STUB_TRAP, "no 128-bit float path in the guest; not declared by the sample"},
    {"__remill_write_memory_f128", SA2_INTRIN_STUB_TRAP, "no 128-bit float path in the guest; not declared by the sample"},
    {"__remill_read_io_port_8",   SA2_INTRIN_STUB_TRAP, "no I/O ports in a user-mode guest"},
    {"__remill_read_io_port_16",  SA2_INTRIN_STUB_TRAP, "no I/O ports in a user-mode guest"},
    {"__remill_read_io_port_32",  SA2_INTRIN_STUB_TRAP, "no I/O ports in a user-mode guest"},
    {"__remill_write_io_port_8",  SA2_INTRIN_STUB_TRAP, "no I/O ports in a user-mode guest"},
    {"__remill_write_io_port_16", SA2_INTRIN_STUB_TRAP, "no I/O ports in a user-mode guest"},
    {"__remill_write_io_port_32", SA2_INTRIN_STUB_TRAP, "no I/O ports in a user-mode guest"},
};

/* The generated edge table overrides this; a program that links none (the wrap's
 * per-function drivers) still builds, and every missing block there is UNKNOWN, which
 * is the trap the wrap receipt recorded. */
__attribute__((weak)) void sa2_mb_register_generated(void) {}

const sa2_intrinsic_info *sa2_rt_intrinsics(uint32_t *count) {
    if (count) *count = (uint32_t)(sizeof kIntrinsics / sizeof kIntrinsics[0]);
    return kIntrinsics;
}

}  // extern "C"

/* ------------------------------------------------------------ x87 models
 *
 * The x87 unit's conversions and square root, computed with integers alone, so
 * that a host without the x87 unit (aarch64) gets the bits an x86 host's
 * instructions give. They are the conversions cpu_state.h declares wherever
 * SA2_HOST_LDBL_IS_F80 is 0; on an x86-64 host the instructions are used, and
 * these are held to them there, bit for bit, in every rounding and precision
 * control, with planted defects as the controls (harness/rt_port_probe.cpp,
 * Documentation/scripts/tests/test_runtime_port.py).
 *
 * Classes, as the x87 unit since the 387 reads an extended operand: a zero; a
 * denormal (exponent 0, integer bit 0) and a pseudo-denormal (exponent 0,
 * integer bit 1), both of weight 2^-16382; a normal (integer bit 1); an infinity
 * and a NaN (exponent all ones, integer bit 1); and the unsupported encodings,
 * an unnormal (exponent neither 0 nor all ones, integer bit 0) and a
 * pseudo-infinity or pseudo-NaN (exponent all ones, integer bit 0), which are
 * invalid operands whose masked response is the real indefinite. */
namespace {

struct F80 {
    uint64_t m;     /* the significand, integer bit 63 included */
    uint16_t se;    /* sign (bit 15) and biased exponent */
};

inline F80 f80_unpack(const sa2_f80 &v) {
    F80 f;
    std::memcpy(&f.m, v.b, 8);
    std::memcpy(&f.se, v.b + 8, 2);
    return f;
}

inline sa2_f80 f80_pack(uint64_t m, uint16_t se) {
    sa2_f80 v;
    std::memset(&v, 0, sizeof v);
    std::memcpy(v.b, &m, 8);
    std::memcpy(v.b + 8, &se, 2);
    return v;
}

constexpr uint64_t kF80Int = 1ull << 63;     /* the explicit integer bit */
constexpr uint64_t kF80Quiet = 1ull << 62;   /* a NaN's quiet bit */
constexpr int kF80Bias = 16383;

/* The real indefinite: what a masked invalid operation leaves. */
inline sa2_f80 f80_indefinite() { return f80_pack(0xC000000000000000ull, 0xFFFFu); }

enum class Cls { Zero, Finite, Inf, NaN, Invalid };

/* The class of `f`, and for a finite non-zero value `sig` and `exp2` with
 * |value| = sig * 2^exp2. */
Cls f80_class(const F80 &f, uint64_t &sig, int &exp2) {
    const int e = f.se & 0x7FFF;
    const bool j = (f.m >> 63) != 0;
    if (e == 0x7FFF) {
        if (!j) return Cls::Invalid;
        return (f.m << 1) ? Cls::NaN : Cls::Inf;
    }
    if (e == 0) {
        if (!f.m) return Cls::Zero;
        sig = f.m;
        exp2 = 1 - kF80Bias - 63;
        return Cls::Finite;
    }
    if (!j) return Cls::Invalid;
    sig = f.m;
    exp2 = e - kF80Bias - 63;
    return Cls::Finite;
}

/* Whether a value whose dropped part is `rbits` out of a unit of `half` * 2
 * rounds away from zero, for a rounding-control field `rc` (0 nearest even, 1
 * down, 2 up, 3 toward zero). `odd` is the kept part's last bit; `sticky` says
 * there is something non-zero below `rbits` as well. */
inline bool round_away(int rc, bool neg, uint64_t rbits, uint64_t half, bool sticky, bool odd) {
    const bool inexact = rbits || sticky;
    switch (rc & 3) {
    case 0:  return rbits > half || (rbits == half && (sticky || odd));
    case 1:  return neg && inexact;
    case 2:  return !neg && inexact;
    default: return false;
    }
}

/* |value| = sig * 2^exp2 (sig not 0) rounded to the IEEE binary format with `p`
 * significand bits (the hidden one included) and exponents [1 - emax, emax]:
 * its encoding without the sign. Underflow denormalises and rounds once, as the
 * x87 unit's masked underflow does; overflow gives infinity or the largest
 * finite value, as the rounding direction says. */
uint64_t round_ieee(uint64_t sig, int exp2, bool neg, int rc, int p, int emax) {
    const int emin = 1 - emax;
    const uint64_t inf = static_cast<uint64_t>(2 * emax + 1) << (p - 1);
    const int ue = exp2 + (63 - __builtin_clzll(sig));       /* the leading bit's exponent */
    if (ue > emax) {
        switch (rc & 3) {
        case 0:  return inf;
        case 1:  return neg ? inf : inf - 1;
        case 2:  return neg ? inf - 1 : inf;
        default: return inf - 1;
        }
    }
    const bool normal = ue >= emin;
    const int lsb = (normal ? ue : emin) - (p - 1);          /* the last kept bit's weight */
    const int sh = lsb - exp2;                               /* bits of sig below it */
    uint64_t q;
    if (sh <= 0) {
        q = sig << -sh;                                      /* exact */
    } else {
        uint64_t rbits, half;
        bool sticky = false;
        if (sh < 64) {
            q = sig >> sh;
            rbits = sig & ((1ull << sh) - 1);
            half = 1ull << (sh - 1);
        } else if (sh == 64) {
            q = 0;
            rbits = sig;
            half = 1ull << 63;
        } else {                                             /* below half the last bit */
            q = 0;
            rbits = 0;
            half = 1;
            sticky = true;
        }
        if (round_away(rc, neg, rbits, half, sticky, q & 1)) q++;
    }
    /* q is at most 2^p; a carry into the next exponent, or from the largest
     * denormal into the smallest normal, is the encoding's own increment. */
    return normal ? (static_cast<uint64_t>(ue - emin) << (p - 1)) + q : q;
}

/* fst m64 and fst m32 share all but the format. */
uint64_t f80_to_ieee(const sa2_f80 &v, int rc, int p, int emax, int width) {
    const F80 f = f80_unpack(v);
    const bool neg = (f.se >> 15) != 0;
    const uint64_t sign = static_cast<uint64_t>(neg) << (width - 1);
    const uint64_t inf = static_cast<uint64_t>(2 * emax + 1) << (p - 1);
    const uint64_t quiet = 1ull << (p - 2);
    uint64_t sig = 0;
    int exp2 = 0;
    switch (f80_class(f, sig, exp2)) {
    case Cls::Zero:    return sign;
    case Cls::Inf:     return sign | inf;
    case Cls::NaN:     /* the payload's top bits, quietened */
        return sign | inf | quiet | ((f.m << 1) >> (64 - (p - 1)));
    case Cls::Invalid: /* the indefinite: negative, quiet, nothing else */
        return (1ull << (width - 1)) | inf | quiet;
    case Cls::Finite:  break;
    }
    return sign | round_ieee(sig, exp2, neg, rc, p, emax);
}

/* The integer square root of a 128-bit n, and its remainder n - r*r. */
void isqrt128(unsigned __int128 n, uint64_t &root, unsigned __int128 &rem) {
    unsigned __int128 res = 0, bit = static_cast<unsigned __int128>(1) << 126;
    while (bit > n) bit >>= 2;
    while (bit) {
        if (n >= res + bit) {
            n -= res + bit;
            res = (res >> 1) + bit;
        } else {
            res >>= 1;
        }
        bit >>= 2;
    }
    root = static_cast<uint64_t>(res);
    rem = n;
}

}  // namespace

extern "C" {

int sa2_rt_host_rc(void) { return sa2_rt_cw_from_round(std::fegetround()) >> 10; }

sa2_f80 sa2_f80_soft_from_f64(double d) {
    uint64_t bits;
    std::memcpy(&bits, &d, 8);
    const uint16_t sign = static_cast<uint16_t>((bits >> 63) << 15);
    const int e = static_cast<int>((bits >> 52) & 0x7FF);
    const uint64_t frac = bits & ((1ull << 52) - 1);
    if (e == 0x7FF) {
        if (!frac) return f80_pack(kF80Int, static_cast<uint16_t>(sign | 0x7FFF));
        /* a NaN keeps its payload and is quiet afterwards, as `fld m64` makes it */
        return f80_pack(kF80Int | kF80Quiet | (frac << 11), static_cast<uint16_t>(sign | 0x7FFF));
    }
    if (e == 0) {
        if (!frac) return f80_pack(0, sign);
        const int top = 63 - __builtin_clzll(frac);           /* a denormal double, normalised */
        return f80_pack(frac << (63 - top),
                        static_cast<uint16_t>(sign | (top - 1074 + kF80Bias)));
    }
    return f80_pack(kF80Int | (frac << 11), static_cast<uint16_t>(sign | (e - 1023 + kF80Bias)));
}

double sa2_f80_soft_to_f64(sa2_f80 v, int rc) {
    const uint64_t bits = f80_to_ieee(v, rc, 53, 1023, 64);
    double d;
    std::memcpy(&d, &bits, 8);
    return d;
}

float sa2_f80_soft_to_f32(sa2_f80 v, int rc) {
    const uint32_t bits = static_cast<uint32_t>(f80_to_ieee(v, rc, 24, 127, 32));
    float f;
    std::memcpy(&f, &bits, 4);
    return f;
}

/* fsqrt: correctly rounded to the precision control's width, in the extended
 * exponent range (precision control narrows the significand only), by the
 * rounding control. A square root's result is always a normal extended value,
 * so there is no overflow or underflow to round. */
sa2_f80 sa2_f80_soft_sqrt(sa2_f80 v, uint16_t cw) {
    const F80 f = f80_unpack(v);
    const bool neg = (f.se >> 15) != 0;
    uint64_t sig = 0;
    int exp2 = 0;
    switch (f80_class(f, sig, exp2)) {
    case Cls::Zero:    return f80_pack(f.m, f.se);           /* sqrt(-0) is -0 */
    case Cls::NaN:     return f80_pack(f.m | kF80Quiet, f.se);
    case Cls::Invalid: return f80_indefinite();
    case Cls::Inf:     return neg ? f80_indefinite() : f80_pack(f.m, f.se);
    case Cls::Finite:  break;
    }
    if (neg) return f80_indefinite();
    const int lz = __builtin_clzll(sig);
    sig <<= lz;
    exp2 -= lz;
    /* sqrt(sig * 2^exp2) = sqrt(n) * 2^kr with n a 128-bit integer whose root
     * has exactly 64 bits: the exponent made even by folding one bit into n. */
    unsigned __int128 n;
    int kr;
    if ((exp2 & 1) == 0) {
        n = static_cast<unsigned __int128>(sig) << 64;
        kr = (exp2 - 64) / 2;
    } else {
        n = static_cast<unsigned __int128>(sig) << 63;
        kr = (exp2 - 63) / 2;
    }
    uint64_t r;
    unsigned __int128 rem;
    isqrt128(n, r, rem);
    const int pc = (cw >> 8) & 3;
    const int p = pc == 0 ? 24 : pc == 2 ? 53 : 64;
    const int d = 64 - p;
    const int rc = (cw >> 10) & 3;
    uint64_t q;
    bool away;
    if (d == 0) {
        /* sqrt(n) lies strictly between r and r + 1 when rem is not 0, and is
         * above r + 1/2 exactly when rem > r: (r + 1/2)^2 = r^2 + r + 1/4. */
        q = r;
        switch (rc) {
        case 0:  away = rem > r; break;
        case 2:  away = rem != 0; break;
        default: away = false; break;
        }
    } else {
        q = r >> d;
        const uint64_t rbits = r & ((1ull << d) - 1);
        away = round_away(rc, false, rbits, 1ull << (d - 1), rem != 0, q & 1);
    }
    /* q has p bits; rounding it up can carry into bit p, which is the next
     * exponent's leading bit. Put the leading bit at 63. */
    int e = kr + 63 + kF80Bias;
    uint64_t m;
    if (away && q == (p == 64 ? ~0ull : (1ull << p) - 1)) {
        m = kF80Int;
        e += 1;
    } else {
        m = (q + (away ? 1u : 0u)) << d;
    }
    return f80_pack(m, static_cast<uint16_t>(e));
}

/* cpu_state.h's conversions: the host's x87 instructions on an x86-64 host, the
 * models above, at the host's rounding mode, anywhere else. */
#if SA2_HOST_LDBL_IS_F80
sa2_f80 sa2_f80_from_f64(double d) {
    const long double x = d;
    sa2_f80 v;
    std::memset(&v, 0, sizeof v);
    std::memcpy(v.b, &x, 10);
    return v;
}

double sa2_f80_to_f64(sa2_f80 v) {
    long double x = 0;
    std::memcpy(&x, v.b, 10);
    return static_cast<double>(x);
}

float sa2_f80_to_f32(sa2_f80 v) {
    long double x = 0;
    std::memcpy(&x, v.b, 10);
    return static_cast<float>(x);
}
#else
sa2_f80 sa2_f80_from_f64(double d) { return sa2_f80_soft_from_f64(d); }
double  sa2_f80_to_f64(sa2_f80 v)  { return sa2_f80_soft_to_f64(v, sa2_rt_host_rc()); }
float   sa2_f80_to_f32(sa2_f80 v)  { return sa2_f80_soft_to_f32(v, sa2_rt_host_rc()); }
#endif

}  // extern "C"
