/* The CRT's x87 tan, sin and cos, driven through their plain C entries.
 *
 *   usage: x87_fallback_probe <sonic2app-unpacked.exe>
 *   built and run by tools/s8/x87_fallback_probe.py, linked as the Linux game is
 *
 * 0x00859abf, 0x0085963f and 0x00828fff are tan's, sin's and cos's plain C entries:
 * `lea edx,[esp+4]; call __fload_withFB`, and the x87 body is the next address, its own
 * function-table entry. The SSE2 tan, sin and cos (0x0085a0ee, 0x00859c9e, 0x0082c7ee)
 * jump to them for an argument out of their range, a NaN or an infinity, so the game
 * reaches them only then; here they are called directly, each with its double argument at
 * [esp+4], so every call crosses the call-return edge that
 * Documentation/dispatch-exceptions.json allows (with the edge refused, every call stops
 * there). The probe first runs the CRT's own start-up (the cookie, the heap, __mtinit) as
 * __tmainCRTStartup does, because the NaN and infinity paths set errno through the
 * per-thread data, whose FLS accessors that start-up encodes.
 *
 * One line per call: the trap status, ST(0) as the guest left it and the host libm's value
 * ("same" when they agree to 1e-12 relative, or are both NaN), each call made once with C2
 * clear and once with C2 set on entry (c2_then_native below). The last line is
 * X87_FALLBACK_PROBE_OK or X87_FALLBACK_PROBE_FAIL with the counts; the exit status is 0
 * only for OK. */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "../runtime/cpu_state.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_imports.h"
#include "../runtime/function_table.h"
#include "../runtime/sa2_trap.h"

extern int sa2_s4_extra_register(void) __attribute__((weak));

/* remill's State by field name, the table runtime/sa2_ninja_math.c keeps and the harness holds
 * to offsetof(State, ...). */
extern const struct sa2_ninja_field { const char *field; uint32_t offset; } sa2_ninja_layout[];
extern const uint32_t sa2_ninja_layout_count;

static uint32_t state_offset(const char *field) {
    for (uint32_t i = 0; i < sa2_ninja_layout_count; i++)
        if (!strcmp(sa2_ninja_layout[i].field, field)) return sa2_ninja_layout[i].offset;
    return 0;
}

/* A native at a scratch address that sets C2 and runs the function under test in the same
 * call, as the game runs one of these bodies with C2 as its own earlier code left it (an
 * unordered compare, FXAM of a normal number). It has to be one call: sa2_call loads a fresh
 * State each time (runtime/remill_runtime.cpp sa2_rt_state_load), while a guest thread runs
 * its whole life inside one. The function's `ret` pops the sentinel, so the call ends as a
 * direct one does. */
#define SET_C2_VA (SA2_GUEST_SCRATCH_BASE + 0x3000u)
static uint32_t g_o_c2, g_target;
static void *c2_then_native(void *s, uint32_t pc, void *m) {
    (void)pc;
    ((uint8_t *)s)[g_o_c2] = 1;
    return sa2_ft_lookup(g_target)(s, g_target, m);
}

static uint32_t call_guest(uint32_t va, uint16_t cw, const void *arg, uint32_t arg_len,
                           sa2_cpu_state_t *st, sa2_call_result_t *r) {
    sa2_cpu_state_reset(st);
    st->x87_cw = cw;
    const uint32_t esp = SA2_GUEST_STACK_TOP - 0x2000u;
    st->gpr[SA2_ESP] = esp;
    sa2_call_place_return(st, SA2_CALL_SENTINEL);
    if (arg_len) sa2_poke(esp + 4u, arg_len, arg);
    memset(r, 0, sizeof *r);
    sa2_call(va, st, 5, r);
    return r->status;
}

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: x87_fallback_probe <sonic2app-unpacked.exe>\n"); return 2; }
    if (sa2_runtime_init(argv[1], 4096) != SA2_OK) { fprintf(stderr, "sa2_runtime_init failed\n"); return 2; }
    const int extras = sa2_s4_extra_register ? sa2_s4_extra_register() : 0;
    sa2_mb_register_generated();
    sa2_imports_config_from_env();
    sa2_imports_bind();
    sa2_missing_block_dispatch = SA2_MB_REACHED;
    sa2_mb_policy_from_env();
    printf("extras %d, edges %u, policy %s\n", extras, sa2_mb_edge_count(),
           sa2_mb_policy_name(sa2_missing_block_dispatch));
    g_o_c2 = state_offset("sw.c2");
    if (!g_o_c2 || sa2_ft_add(SET_C2_VA, c2_then_native, "c2_then", SA2_FN_NATIVE) < 0) {
        printf("X87_FALLBACK_PROBE_FAIL cannot register the C2 native\n");
        return 1;
    }

    static const struct { const char *name; uint32_t va, arg; } init[] = {
        {"__security_init_cookie", 0x007B5195u, 0}, {"__heap_init", 0x007B5165u, 1},
        {"__mtinit", 0x007AC68Au, 0}};
    sa2_cpu_state_t st;
    sa2_call_result_t r;
    for (unsigned k = 0; k < sizeof init / sizeof init[0]; k++) {
        if (call_guest(init[k].va, 0x027F, &init[k].arg, 4, &st, &r) || st.gpr[SA2_EAX] == 0) {
            printf("X87_FALLBACK_PROBE_FAIL the CRT's %s did not run: status %u eax %#x %s\n",
                   init[k].name, r.status, st.gpr[SA2_EAX], r.note);
            return 1;
        }
    }

    static const struct { const char *name; uint32_t va; double (*ref)(double); } fn[] = {
        {"tan", 0x00859abfu, tan}, {"sin", 0x0085963fu, sin}, {"cos", 0x00828fffu, cos}};
    static const double xs[] = {0.5, -2.75, 12345.678, 1e6, -123456.789, 1e18, 1e300,
                                NAN, INFINITY, -INFINITY};
    static const uint16_t cws[] = {0x027F, 0x007F};   /* the CRT's default, the game's PC24 */
    /* The C2 the caller leaves: as it is, or set. The bodies branch on C2 straight after
     * FPTAN, FSIN and FCOS, which write it on the processor. */
    unsigned same = 0, trapped = 0, total = 0;
    for (unsigned w = 0; w < 2; w++)
    for (unsigned c = 0; c < sizeof cws / sizeof cws[0]; c++)
        for (unsigned f = 0; f < sizeof fn / sizeof fn[0]; f++)
            for (unsigned i = 0; i < sizeof xs / sizeof xs[0]; i++) {
                const double x = xs[i];
                g_target = fn[f].va;
                call_guest(w ? SET_C2_VA : fn[f].va, cws[c], &x, sizeof x, &st, &r);
                const double got = sa2_f80_to_f64(st.st80[0]), want = fn[f].ref(x);
                const int ok = !r.status && ((isnan(got) && isnan(want)) || got == want ||
                                             fabs(got - want) <= 1e-12 * fmax(1.0, fabs(want)));
                total++;
                same += ok;
                trapped += r.status != 0;
                printf("c2=%s cw=%04x %s(%-12g) status=%u exit_pc=%#010x st0=%-24.17g libm=%-24.17g %s%s%s\n",
                       w ? "set" : "left", cws[c], fn[f].name, x, r.status, r.exit_pc, got, want,
                       ok ? "same" : "DIFFERS",
                       r.status ? " note=" : "", r.status ? r.note : "");
            }
    sa2_runtime_shutdown();
    printf("%s %u of %u same, %u trapped\n", same == total ? "X87_FALLBACK_PROBE_OK" : "X87_FALLBACK_PROBE_FAIL",
           same, total, trapped);
    return same == total ? 0 : 1;
}
