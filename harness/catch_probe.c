/* Drive a guest entry and catch the address the image dispatches to.
 *
 *   usage: catch_probe <sonic2app-unpacked.exe> --va 0x00406520 [--catch 0xADDR]...
 *                      [--depth N] [--timeout S]
 *
 * When a whole-image run stops with `SA2_TRAP_CONTROL_LEFT`, the trap carries
 * the target address and nothing else: not who dispatched to it, not what the
 * guest had on its stack, not whether the same address is reached again. That
 * is enough to name a wall and not enough to say what is behind it.
 *
 * The function table is the lever. A dispatch that misses traps; a dispatch to
 * an address the table *has* a row for calls that row. So registering a native
 * at the address the run stopped on turns the wall into an observation point:
 * the native records the guest's registers and the top of its stack -- which,
 * for an indirect `call`, is the return address the guest just pushed, and so
 * the site that dispatched -- and then either stops the run or steps past it.
 *
 * Two modes, because they answer different questions:
 *
 *   default   the native records and then raises the trap itself, so the run
 *             ends exactly where it would have, with the state attached.
 *   --step    the native records and then performs the `ret` the missing code
 *             would eventually have performed (EAX = 0, ESP += 4), so the run
 *             continues and the *next* unresolved address is reported. Each
 *             stepped address is one the image has not really executed, so a
 *             `--step` run's end point is a lower bound on the frontier and is
 *             labelled as such in the output. It is a survey, not a pass.
 *
 * Nothing is stubbed that is not named on the command line, so a run with no
 * `--catch` is the plain run and reproduces `harness/seh_probe.c`'s stop.
 *
 * Output is one JSON document on stdout. Exit status is 0 when the call
 * returned through its own `ret`, 1 otherwise; a caught address is not a pass.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_trap.h"

#define MAX_CATCH 32
#define STACK_WORDS 8
#define FRAME_ESP  0x03eeff00u

typedef struct {
    uint32_t va;
    uint32_t hits;
    uint32_t gpr[9];
    uint32_t stack[STACK_WORDS];
} catch_site;

static catch_site g_catch[MAX_CATCH];
static int g_ncatch;
static int g_step;
static uint32_t g_order[MAX_CATCH * 4];
static uint32_t g_norder;

static uint32_t peek32(uint32_t a) {
    uint32_t v = 0xDEADBEEFu;
    sa2_peek(a, 4, &v);
    return v;
}

static void *n_catch(void *s, uint32_t pc, void *m) {
    catch_site *c = NULL;
    for (int i = 0; i < g_ncatch; i++)
        if (g_catch[i].va == pc) { c = &g_catch[i]; break; }
    if (c) {
        if (!c->hits) {
            for (int r = 0; r < 9; r++) c->gpr[r] = sa2_rt_get_gpr(s, r);
            const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
            for (int k = 0; k < STACK_WORDS; k++) c->stack[k] = peek32(esp + 4u * (uint32_t)k);
        }
        c->hits++;
    }
    if (g_norder < (uint32_t)(sizeof g_order / sizeof g_order[0])) g_order[g_norder++] = pc;
    if (!g_step) {
        sa2_trap_note("caught at %#010x by catch_probe (region %s)", pc,
                      sa2_guest_region_of(pc));
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    /* --step: behave like a function that did nothing and returned. */
    sa2_rt_set_gpr(s, SA2_EAX, 0u);
    sa2_rt_set_gpr(s, SA2_ESP, sa2_rt_get_gpr(s, SA2_ESP) + 4u);
    return m;
}

int main(int argc, char **argv) {
    const char *pe = NULL;
    uint32_t va = 0, timeout = 20;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--va") && i + 1 < argc) va = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--timeout") && i + 1 < argc) timeout = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!strcmp(argv[i], "--step")) g_step = 1;
        else if (!strcmp(argv[i], "--catch") && i + 1 < argc && g_ncatch < MAX_CATCH)
            g_catch[g_ncatch++].va = (uint32_t)strtoul(argv[++i], NULL, 0);
        else if (!pe) pe = argv[i];
    }
    if (!pe || !va) {
        fprintf(stderr, "usage: catch_probe <exe> --va VA [--catch ADDR]... [--step]\n");
        return 2;
    }
    if (sa2_runtime_init(pe, (uint32_t)g_ncatch + 8u) != SA2_OK) {
        fprintf(stderr, "catch_probe: sa2_runtime_init(%s) failed\n", pe);
        return 2;
    }
    /* Refuse to "catch" an address the image already resolves: the native would
     * replace a real lifted function and the run would be measuring the probe. */
    int replaced = 0;
    for (int i = 0; i < g_ncatch; i++)
        if (sa2_ft_lookup(g_catch[i].va)) replaced++;
    if (replaced) {
        printf("{\"ok\": false, \"why\": \"%d of the --catch addresses already have a "
               "table row; catching one would replace lifted code\"}\n", replaced);
        return 2;
    }
    for (int i = 0; i < g_ncatch; i++)
        sa2_ft_add(g_catch[i].va, n_catch, "catch_probe", SA2_FN_NATIVE);

    sa2_cpu_state_t st;
    sa2_cpu_state_reset(&st);
    st.gpr[SA2_ESP] = FRAME_ESP;
    sa2_call_place_return(&st, SA2_CALL_SENTINEL);

    sa2_call_result_t res;
    sa2_call(va, &st, timeout, &res);

    const int returned = (res.status == SA2_TRAP_NONE) && (st.gpr[SA2_EIP] == SA2_CALL_SENTINEL);
    static const char *kReg[9] = {"eax", "ecx", "edx", "ebx", "esp", "ebp", "esi", "edi", "eip"};
    printf("{\n");
    printf("  \"va\": \"0x%08x\", \"mode\": \"%s\",\n", va, g_step ? "step" : "stop");
    printf("  \"table_rows\": %u, \"catches\": %d,\n", sa2_ft_count(), g_ncatch);
    printf("  \"trap\": {\"status\": %u, \"exit_pc\": \"0x%08x\", \"note\": \"%s\"},\n",
           res.status, res.exit_pc, res.note);
    printf("  \"returned_through_ret\": %s,\n", returned ? "true" : "false");
    printf("  \"dispatch_misses\": %u,\n", sa2_ft_misses());
    printf("  \"caught\": [\n");
    for (int i = 0; i < g_ncatch; i++) {
        const catch_site *c = &g_catch[i];
        printf("    {\"address\": \"0x%08x\", \"region\": \"%s\", \"hits\": %u",
               c->va, sa2_guest_region_of(c->va), c->hits);
        if (c->hits) {
            printf(", \"gpr\": {");
            for (int r = 0; r < 9; r++)
                printf("%s\"%s\": \"0x%08x\"", r ? ", " : "", kReg[r], c->gpr[r]);
            printf("}, \"stack_from_esp\": [");
            for (int k = 0; k < STACK_WORDS; k++)
                printf("%s{\"at\": \"0x%08x\", \"word\": \"0x%08x\", \"region\": \"%s\"}",
                       k ? ", " : "", c->gpr[SA2_ESP] + 4u * (uint32_t)k, c->stack[k],
                       sa2_guest_region_of(c->stack[k]));
            printf("]");
        }
        printf("}%s\n", i + 1 < g_ncatch ? "," : "");
    }
    printf("  ],\n");
    printf("  \"catch_order\": [");
    for (uint32_t i = 0; i < g_norder; i++)
        printf("%s\"0x%08x\"", i ? ", " : "", g_order[i]);
    printf("],\n");
    printf("  \"note\": \"a --step run's end point is a lower bound on the frontier: "
           "every stepped address is code the image did not execute\",\n");
    printf("  \"ok\": %s\n}\n", returned ? "true" : "false");
    sa2_runtime_shutdown();
    return returned ? 0 : 1;
}
