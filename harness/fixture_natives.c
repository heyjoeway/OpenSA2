/* Host implementations of the callees the fixture corpus reaches but the S2
 * lifted set does not contain, and the linker-visible `sub_<va>` symbols the
 * lifted IR calls.
 *
 * Each `sub_<va>` below is a *weak* thunk that dispatches through the runtime's
 * function table by guest address. Three consequences, all wanted:
 *
 *  - When a lifted define for that address is linked into the program, its
 *    strong definition wins and the thunk disappears; the table still holds
 *    the lifted pointer, registered by the generated table file.
 *  - When it is not, the call goes through `sa2_ft_dispatch`, which either
 *    finds a native (below) or traps with the address. That is how the S1
 *    spike harness behaved at an unlifted callee, and it is what exercises the
 *    function table on every call the ten functions make: the ten contain no
 *    indirect call, so without this the table would only ever be read by
 *    `sa2_call`.
 *  - A native and a lifted function are indistinguishable to the caller, which
 *    is how S4's import stubs will be wired.
 *
 * The three natives behave as the game's own code does, and are the S1
 * harness's: `_CIsqrt` executes `fsqrt` under the *guest* control word, memset
 * fills guest memory, and 0x00459260 answers from the native angles the row
 * carries (it is the harness's oracle stub, recorded as such since S1).
 */
#include "fixture_shapes.h"

#include <stdint.h>
#include <string.h>

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_trap.h"

static sa2_fixture_call g_current;

void sa2_fixture_set_current(const sa2_fixture_call *c) { g_current = *c; }

/* Emulate the callee's `ret`: drop the return address the caller pushed. */
static void pop_return(void *s) {
    sa2_rt_set_gpr(s, SA2_ESP, sa2_rt_get_gpr(s, SA2_ESP) + 4u);
}

static uint32_t stack_word(void *s, uint32_t off) {
    uint32_t v = 0;
    sa2_peek(sa2_rt_get_gpr(s, SA2_ESP) + off, 4, &v);
    return v;
}

/* CRT `_CIsqrt` (0x008290A0): st0 = sqrt(st0) at the guest's precision. On an
 * x86-64 host the host x87 unit is borrowed for one instruction under the guest
 * control word, which is what makes a PC24 square root round like the game's;
 * on any other host the runtime's model of that instruction
 * (remill_runtime.h sa2_f80_soft_sqrt), which reads the same control word. */
static void *native_cisqrt(void *s, uint32_t pc, void *m) {
    (void)pc;
    const uint16_t guest_cw = sa2_rt_get_cw(s);
#if SA2_HOST_LDBL_IS_F80
    long double v = sa2_rt_get_st(s, 0), r;
    uint16_t host_cw;
    __asm__ volatile("fnstcw %0" : "=m"(host_cw));
    __asm__ volatile("fldcw %0" : : "m"(guest_cw));
    __asm__ volatile("fsqrt" : "=t"(r) : "0"(v));
    __asm__ volatile("fldcw %0" : : "m"(host_cw));
    sa2_rt_set_st(s, 0, r);
#else
    sa2_rt_set_st80(s, 0, sa2_f80_soft_sqrt(sa2_rt_get_st80(s, 0), guest_cw));
#endif
    pop_return(s);
    return m;
}

/* CRT `memset` (0x007ACDE0), cdecl(dst, val, n) -> eax = dst. */
static void *native_memset(void *s, uint32_t pc, void *m) {
    (void)pc;
    uint32_t dst = stack_word(s, 4), val = stack_word(s, 8), n = stack_word(s, 12);
    if (sa2_guest_fill(dst, (int)val, n) != SA2_OK) {
        sa2_trap_note("native memset(%#010x, %u, %u) leaves the guest space", dst, val, n);
        sa2_trap_raise(SA2_TRAP_FAULT, dst);
    }
    sa2_rt_set_gpr(s, SA2_EAX, dst);
    pop_return(s);
    return m;
}

/* 0x00459260: direction -> two BAM angles through edi/esi. The harness has no
 * lift of it; it answers from the native row the vector carries, and refuses
 * any direction the row did not record. */
static void *native_vec_angles(void *s, uint32_t pc, void *m) {
    uint32_t x = stack_word(s, 4), y = stack_word(s, 8), z = stack_word(s, 12);
    if (x != g_current.dir[0] || y != g_current.dir[1] || z != g_current.dir[2]) {
        sa2_trap_note("0x00459260 called with a direction the row does not carry (%08x %08x %08x)", x, y, z);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    sa2_poke(sa2_rt_get_gpr(s, SA2_EDI), 4, &g_current.angle1);
    sa2_poke(sa2_rt_get_gpr(s, SA2_ESI), 4, &g_current.angle2);
    sa2_rt_set_gpr(s, SA2_EAX, g_current.angle2);
    pop_return(s);
    return m;
}

void sa2_fixture_register_natives(void) {
    sa2_ft_add(CALLEE_CISQRT,     native_cisqrt,     "_CIsqrt",          SA2_FN_NATIVE);
    sa2_ft_add(CALLEE_MEMSET,     native_memset,     "memset",           SA2_FN_NATIVE);
    sa2_ft_add(CALLEE_VEC_ANGLES, native_vec_angles, "vector_to_angles", SA2_FN_NATIVE);
}

/* ------------------------------------------------ linker-visible thunks */
#define THUNK(sym, va)                                                        \
    void *sym(void *state, uint32_t pc, void *memory) __attribute__((weak));  \
    void *sym(void *state, uint32_t pc, void *memory) {                       \
        (void)pc;                                                             \
        return sa2_ft_dispatch((va), state, (va), memory);                    \
    }

THUNK(sub_8290a0, CALLEE_CISQRT)
THUNK(sub_7acde0, CALLEE_MEMSET)
THUNK(sub_459260, CALLEE_VEC_ANGLES)
/* Ninja helpers and the polygon builder: no lift in S2, so these miss the
 * table and trap with their own address, exactly as the S1 harness did. */
THUNK(sub_427470, 0x00427470u)
THUNK(sub_4274e0, 0x004274E0u)
THUNK(sub_4275b0, 0x004275B0u)
THUNK(sub_427680, 0x00427680u)
THUNK(sub_427750, 0x00427750u)
THUNK(sub_48b610, 0x0048B610u)
/* In-ten callees: the strong lifted definition wins when its IR is linked. */
THUNK(sub_4273b0, FN_CALC_VECTOR)
THUNK(sub_44b210, FN_PUSH_IDENTITY)

/* remill names 0x0048B9F0's recursive self-call `sub_48b9f0.1`, which is not a
 * C identifier. */
void *sa2_sub_48b9f0_recursive(void *state, uint32_t pc, void *memory) __asm__("sub_48b9f0.1");
void *sa2_sub_48b9f0_recursive(void *state, uint32_t pc, void *memory) {
    (void)pc;
    return sa2_ft_dispatch(FN_OBJECT_TREE, state, FN_OBJECT_TREE, memory);
}
