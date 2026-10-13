/* Calling lifted guest code from inside a native handler. See sa2_guest_call.h. */
#include "sa2_guest_call.h"

#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_trap.h"

_Static_assert(4u + 4u * SA2_GUEST_CALL_MAX_ARGS < SA2_GUEST_CALL_SLACK,
               "the largest frame must end below the native's own return address");

static uint32_t g_calls, g_depth, g_max_depth;

static void frame_put(uint32_t a, uint32_t v, uint32_t fn) {
    if (sa2_poke(a, 4, &v) != SA2_OK) {
        sa2_trap_note("guest stdcall to %#010x: its frame word at %#010x is outside the "
                      "guest space", fn, a);
        sa2_trap_raise(SA2_TRAP_FAULT, a);
    }
}

uint32_t sa2_guest_stdcall(void *s, uint32_t fn, unsigned argc, const uint32_t *argv) {
    if (argc > SA2_GUEST_CALL_MAX_ARGS) {
        sa2_trap_note("guest stdcall to %#010x with %u arguments: the frame holds %u, and a "
                      "larger one would reach the calling native's own stack",
                      fn, argc, SA2_GUEST_CALL_MAX_ARGS);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, fn);
    }
    const sa2_lifted_fn f = sa2_ft_lookup(fn);
    if (!f) {
        sa2_trap_note("guest stdcall to %#010x: nothing is registered there, so the guest "
                      "callback was not lifted into this program", fn);
        sa2_trap_raise(SA2_TRAP_NOT_RUNNABLE, fn);
    }
    const uint32_t esp0 = sa2_rt_get_gpr(s, SA2_ESP);
    const uint32_t eip0 = sa2_rt_get_gpr(s, SA2_EIP);
    const uint32_t base = esp0 - SA2_GUEST_CALL_SLACK;
    frame_put(base, SA2_CALL_SENTINEL, fn);
    for (unsigned i = 0; i < argc; i++) frame_put(base + 4u + 4u * i, argv[i], fn);
    sa2_rt_set_gpr(s, SA2_ESP, base);
    sa2_rt_set_gpr(s, SA2_EIP, fn);
    g_calls++;
    if (++g_depth > g_max_depth) g_max_depth = g_depth;
    f(s, fn, NULL);
    g_depth--;
    const uint32_t esp1 = sa2_rt_get_gpr(s, SA2_ESP);
    const uint32_t ret = sa2_rt_get_gpr(s, SA2_EAX);
    const uint32_t want = base + 4u + 4u * argc;
    if (esp1 != want) {
        sa2_trap_note("guest stdcall to %#010x left ESP at %#010x, not %#010x: a stdcall "
                      "callee pops its return address and its %u arguments, and one that "
                      "did not has corrupted the frame the calling native returns through",
                      fn, esp1, want, argc);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, fn);
    }
    sa2_rt_set_gpr(s, SA2_ESP, esp0);
    sa2_rt_set_gpr(s, SA2_EIP, eip0);
    return ret;
}

uint32_t sa2_guest_stdcall_count(void) { return g_calls; }
uint32_t sa2_guest_stdcall_max_depth(void) { return g_max_depth; }
