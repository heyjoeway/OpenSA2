/* Routines the function map split in two, entered through the edge between their halves.
 *
 *   usage: split_routine_probe <sonic2app-unpacked.exe>
 *   built and run by tools/s8/split_routine_probe.py, linked as the Linux game is
 *
 * Each routine below is one routine in the executable that the function map recorded as two
 * functions, the second starting at a branch target or fall-through inside the first. The first
 * half's module ends where the second's starts, so the edge between them leaves lifted code and
 * reaches __remill_missing_block, which under SA2_MB_REACHED dispatches it only as a reviewed
 * exception (Documentation/dispatch-exceptions.json). With the exception absent, every call that
 * crosses the edge stops with status 1 at the second half's address: issue #16 was the first of
 * them, at Pyramid Cave's title card.
 *
 *   FUN_007059a0 -> 0x007059af   Pyramid Cave's object-table search (the table at [0x01AF00CC])
 *   FUN_006b1650 -> 0x006b165f   Death Chamber's copy of it (the table at [0x01AEFD34])
 *   _CIatan2, _CIfmod -> 0x0082c189   the CRT's x87 domain-error handler FUN_0082c176 falling
 *                                     into its last three instructions
 *
 * The searches are called with tables built in guest memory and compared with a C model of the
 * routine read from its disassembly (32 slots, each an object whose [+0x34] points at a record
 * with a type byte at +0 and an id in the low byte of +0x10, and whose [+0x40] points at a value
 * at +4; the first object of the asked id wins until a type-3 one comes, and among type-3 ones the
 * one with the smallest value): EAX against the model, EBX, EBP, ESI and EDI kept, ESP one dword up
 * (the head's `push ecx` is popped by the body). The intrinsics are called with the operands their
 * class tables send to the handler (both infinite for atan2; a zero or infinite operand for fmod),
 * and the rest are compared with the host libm. The handler's head loads the x87 indefinite before
 * its `jg`, so a NaN in ST0 says nothing about which code ran after the edge: what the body at
 * 0x0082c189 adds is `mov byte [ebp-90h],1`, the domain-error flag the dispatcher reads back at
 * 0x00859f29 to call __87except (0x0082e49b), which sets errno to EDOM. So a crossing call must
 * return a NaN *and* leave errno at EDOM (33), read through the CRT's __errno (0x007a917c) after
 * zeroing it, and a call that does not cross must leave errno at 0. SPLIT_PROBE_MISDISPATCH=1 is
 * that oracle's falsifier: it points 0x0082c189 at the handler's other tail, the lifted extra
 * 0x0082c190 (`or cl,cl; ret`), which skips the store, and every crossing call must then fail.
 * The runtime's missing-block census counts each crossing, so every line says whether its call
 * crossed the edge, and the probe fails if an edge it exists for is never crossed.
 *
 * The last line is SPLIT_ROUTINE_PROBE_OK or SPLIT_ROUTINE_PROBE_FAIL with the counts; the exit
 * status is 0 only for OK. */
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "../runtime/cpu_state.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_call.h"
#include "../runtime/sa2_imports.h"
#include "../runtime/function_table.h"
#include "../runtime/sa2_trap.h"

extern int sa2_s4_extra_register(void) __attribute__((weak));

static uint32_t crossings(uint32_t target) {
    for (uint32_t i = 0; i < sa2_mb_census_count(); i++) {
        const sa2_mb_census_entry *e = sa2_mb_census_at(i);
        if (e->target == target) return e->hits;
    }
    return 0;
}

static unsigned g_total, g_ok, g_trapped;

static void tally(int ok, const sa2_call_result_t *r) {
    g_total++;
    g_ok += ok != 0;
    g_trapped += r->status != 0;
}

/* ------------------------------------------------------------------ the table searches */

#define SLOTS 32u
typedef struct { int present, has_rec; uint8_t type; uint32_t id; int32_t val; uint32_t va; } obj_t;

static uint32_t g_arena;        /* guest scratch: the table and its objects */

static uint32_t model(const obj_t *o, uint32_t arg) {
    uint32_t eax = 0;
    int32_t edx = 8, esi = 8;
    for (unsigned k = 0; k < SLOTS; k++) {
        if (!o[k].present || !o[k].has_rec || (o[k].id & 0xFFu) != arg) continue;
        if (eax == 0) {
            edx = (int8_t)o[k].type;
        } else {
            if (o[k].type != 3) continue;
            if (edx == 3 && o[k].val >= esi) continue;
            edx = 3;
        }
        eax = o[k].va;
        esi = o[k].val;
    }
    return eax;
}

/* Lay `o` out in guest memory: the table, then each object (0x50 bytes), its record (0x14) and
 * its value (8). Returns the table's address. */
static uint32_t place(obj_t *o) {
    const uint32_t table = g_arena;
    uint32_t at = table + SLOTS * 4u;
    for (unsigned k = 0; k < SLOTS; k++) {
        uint32_t slot = 0;
        if (o[k].present) {
            uint8_t p[0x50] = {0}, q[0x14] = {0}, v[8] = {0};
            const uint32_t pv = at, qv = at + 0x50u, vv = at + 0x64u;
            at += 0x70u;
            const uint32_t qref = o[k].has_rec ? qv : 0u;
            memcpy(p + 0x34, &qref, 4);
            memcpy(p + 0x40, &vv, 4);
            q[0] = o[k].type;
            memcpy(q + 0x10, &o[k].id, 4);
            memcpy(v + 4, &o[k].val, 4);
            sa2_poke(pv, sizeof p, p);
            sa2_poke(qv, sizeof q, q);
            sa2_poke(vv, sizeof v, v);
            o[k].va = slot = pv;
        }
        sa2_poke(table + k * 4u, 4, &slot);
    }
    return table;
}

static uint32_t g_rng = 0x2545F491u;
static uint32_t rnd(uint32_t n) {
    g_rng = g_rng * 1664525u + 1013904223u;
    return (g_rng >> 8) % n;
}

/* One call of the head at `head`, with [global] = table (0 for none) and the argument `arg`. */
static void search(const char *name, uint32_t head, uint32_t body, uint32_t global, obj_t *o,
                   int with_table, uint32_t arg, const char *label, unsigned *crossed) {
    uint32_t table = 0;
    if (with_table) table = place(o);
    sa2_poke(global, 4, &table);
    sa2_cpu_state_t st;
    sa2_call_result_t r;
    sa2_cpu_state_reset(&st);
    st.x87_cw = 0x027F;
    const uint32_t esp = SA2_GUEST_STACK_TOP - 0x2000u;
    st.gpr[SA2_ESP] = esp;
    st.gpr[SA2_EBX] = 0x11111111u;
    st.gpr[SA2_EBP] = 0x22222222u;
    st.gpr[SA2_ESI] = 0x33333333u;
    st.gpr[SA2_EDI] = 0x44444444u;
    sa2_call_place_return(&st, SA2_CALL_SENTINEL);
    sa2_poke(esp + 4u, 4, &arg);
    const uint32_t before = crossings(body);
    memset(&r, 0, sizeof r);
    sa2_call(head, &st, 5, &r);
    const uint32_t through = crossings(body) - before;
    const uint32_t want = with_table ? model(o, arg) : 0u;
    const int kept = st.gpr[SA2_EBX] == 0x11111111u && st.gpr[SA2_EBP] == 0x22222222u &&
                     st.gpr[SA2_ESI] == 0x33333333u && st.gpr[SA2_EDI] == 0x44444444u;
    const int ok = !r.status && st.gpr[SA2_EAX] == want && kept && st.gpr[SA2_ESP] == esp + 4u;
    *crossed += through != 0;
    tally(ok, &r);
    if (!ok || label)
        printf("%s(%u) %-14s status=%u exit_pc=%#010x eax=%#010x model=%#010x regs=%s esp+%d "
               "crossed=%u %s%s%s\n", name, arg, label ? label : "random", r.status, r.exit_pc,
               st.gpr[SA2_EAX], want, kept ? "kept" : "CHANGED", (int)(st.gpr[SA2_ESP] - esp),
               through, ok ? "same" : "DIFFERS", r.status ? " note=" : "", r.status ? r.note : "");
}

static void searches(const char *name, uint32_t head, uint32_t body, uint32_t global) {
    obj_t o[SLOTS];
    unsigned crossed = 0;
    memset(o, 0, sizeof o);
    search(name, head, body, global, o, 0, 7, "no table", &crossed);
    if (crossed) printf("%s: the call with no table crossed into %#010x\n", name, body);
    crossed = 0;
    search(name, head, body, global, o, 1, 7, "empty table", &crossed);
    memset(o, 0, sizeof o);
    o[9] = (obj_t){1, 1, 1, 7, 5, 0};
    search(name, head, body, global, o, 1, 7, "one match", &crossed);
    memset(o, 0, sizeof o);
    o[2] = (obj_t){1, 1, 1, 7, 5, 0};       /* first of the id: taken */
    o[3] = (obj_t){1, 1, 3, 0x107, 1, 0};   /* id 7 in the low byte: matches */
    o[7] = (obj_t){1, 1, 3, 7, 4, 0};       /* type 3, value 4 >= 1: kept out */
    o[20] = (obj_t){1, 1, 3, 7, -2, 0};     /* type 3, value -2 < 1: taken */
    o[25] = (obj_t){1, 0, 3, 7, -9, 0};     /* no record: skipped */
    o[31] = (obj_t){1, 1, 3, 8, -9, 0};     /* another id: skipped */
    search(name, head, body, global, o, 1, 7, "type 3 wins", &crossed);
    for (unsigned t = 0; t < 200; t++) {
        for (unsigned k = 0; k < SLOTS; k++) {
            /* one draw per statement: an initialiser list's order of evaluation is unspecified */
            o[k].present = rnd(2) == 0;
            o[k].has_rec = rnd(8) != 0;
            o[k].type = (uint8_t)rnd(5);
            o[k].id = rnd(3) ? 7u : rnd(300);
            o[k].val = (int32_t)rnd(17) - 4;
            o[k].va = 0;
        }
        search(name, head, body, global, o, 1, 7, NULL, &crossed);
    }
    printf("%s: %u of 203 calls with a table crossed into %#010x\n", name, crossed, body);
    if (crossed == 0) g_total++;            /* an edge never crossed is a failed probe */
}

/* ------------------------------------------------------------------ the x87 domain handler */

#define CRT_EDOM 33u
static uint32_t g_errno;        /* the guest address of this thread's errno, from __errno */

static uint32_t crt_errno(void) {
    uint32_t v = 0xFFFFFFFFu;
    sa2_peek(g_errno, 4, &v);
    return v;
}

static void intrinsic(const char *name, uint32_t va, double (*ref)(double, double), double y,
                      double x, unsigned *crossed) {
    sa2_cpu_state_t st;
    sa2_call_result_t r;
    sa2_cpu_state_reset(&st);
    st.x87_cw = 0x027F;
    const uint32_t esp = SA2_GUEST_STACK_TOP - 0x2000u;
    st.gpr[SA2_ESP] = esp;
    sa2_call_place_return(&st, SA2_CALL_SENTINEL);
    st.st80[0] = sa2_f80_from_f64(x);
    st.st80[1] = sa2_f80_from_f64(y);
    const uint32_t zero = 0;
    sa2_poke(g_errno, 4, &zero);
    const uint32_t before = crossings(0x0082c189u);
    memset(&r, 0, sizeof r);
    sa2_call(va, &st, 5, &r);
    const uint32_t through = crossings(0x0082c189u) - before;
    const double got = sa2_f80_to_f64(st.st80[0]), lib = ref(y, x);
    const uint32_t err = crt_errno();
    /* Through the handler the CRT returns the x87 indefinite (a NaN, loaded before the edge) and
     * reports the domain error the body flags; otherwise the host libm's value and no error. */
    const int ok = !r.status && (through ? isnan(got) && err == CRT_EDOM
                                         : err == 0 &&
                                           ((isnan(got) && isnan(lib)) || got == lib ||
                                            fabs(got - lib) <= 1e-12 * fmax(1.0, fabs(lib))));
    *crossed += through != 0;
    tally(ok, &r);
    printf("%s(%g, %g) status=%u exit_pc=%#010x st0=%-24.17g libm=%-24.17g errno=%u crossed=%u "
           "%s%s%s\n", name, y, x, r.status, r.exit_pc, got, lib, err, through,
           ok ? (through ? "nan and EDOM (the handler's)" : "same") : "DIFFERS",
           r.status ? " note=" : "", r.status ? r.note : "");
}

static double atan2_ref(double y, double x) { return atan2(y, x); }
static double fmod_ref(double y, double x) { return fmod(y, x); }

int main(int argc, char **argv) {
    if (argc < 2) { fprintf(stderr, "usage: split_routine_probe <sonic2app-unpacked.exe>\n"); return 2; }
    if (sa2_runtime_init(argv[1], 4096) != SA2_OK) { fprintf(stderr, "sa2_runtime_init failed\n"); return 2; }
    const int extras = sa2_s4_extra_register ? sa2_s4_extra_register() : 0;
    sa2_mb_register_generated();
    sa2_imports_config_from_env();
    sa2_imports_bind();
    sa2_missing_block_dispatch = SA2_MB_REACHED;
    sa2_mb_policy_from_env();
    printf("extras %d, edges %u, policy %s\n", extras, sa2_mb_edge_count(),
           sa2_mb_policy_name(sa2_missing_block_dispatch));
    static const uint32_t edges[] = {0x007059afu, 0x006b165fu, 0x0082c189u};
    for (unsigned k = 0; k < sizeof edges / sizeof edges[0]; k++) {
        const sa2_mb_edge *e = sa2_mb_edge_for(edges[k]);
        printf("edge %#010x: %s from %#010x, allow %u\n", edges[k],
               e ? sa2_mb_edge_name(e->kind) : "none", e ? e->source : 0u, e ? e->allow : 0u);
    }
    g_arena = sa2_guest_alloc(0x10000u, 16u);
    if (!g_arena) { printf("SPLIT_ROUTINE_PROBE_FAIL no guest scratch\n"); return 1; }

    /* The CRT's start-up first, as tools/s8/x87_fallback_probe.py does: the domain error sets
     * errno through the per-thread data. */
    static const struct { const char *name; uint32_t va, arg; } init[] = {
        {"__security_init_cookie", 0x007B5195u, 0}, {"__heap_init", 0x007B5165u, 1},
        {"__mtinit", 0x007AC68Au, 0}};
    for (unsigned k = 0; k < sizeof init / sizeof init[0]; k++) {
        sa2_cpu_state_t st;
        sa2_call_result_t r;
        sa2_cpu_state_reset(&st);
        st.x87_cw = 0x027F;
        st.gpr[SA2_ESP] = SA2_GUEST_STACK_TOP - 0x2000u;
        sa2_call_place_return(&st, SA2_CALL_SENTINEL);
        sa2_poke(st.gpr[SA2_ESP] + 4u, 4, &init[k].arg);
        sa2_call(init[k].va, &st, 5, &r);
        if (r.status || st.gpr[SA2_EAX] == 0) {
            printf("SPLIT_ROUTINE_PROBE_FAIL the CRT's %s did not run: status %u eax %#x %s\n",
                   init[k].name, r.status, st.gpr[SA2_EAX], r.note);
            return 1;
        }
    }

    {   /* errno's address for this thread: __errno returns the per-thread data's field */
        sa2_cpu_state_t st;
        sa2_call_result_t r;
        sa2_cpu_state_reset(&st);
        st.gpr[SA2_ESP] = SA2_GUEST_STACK_TOP - 0x2000u;
        sa2_call_place_return(&st, SA2_CALL_SENTINEL);
        sa2_call(0x007A917Cu, &st, 5, &r);
        g_errno = st.gpr[SA2_EAX];
        if (r.status || !g_errno) {
            printf("SPLIT_ROUTINE_PROBE_FAIL the CRT's __errno did not run: status %u eax %#x %s\n",
                   r.status, g_errno, r.note);
            return 1;
        }
        printf("errno at %#010x\n", g_errno);
    }
    {
        const char *mis = getenv("SPLIT_PROBE_MISDISPATCH");
        if (mis && *mis == '1') {
            const sa2_lifted_fn tail = sa2_ft_lookup(0x0082c190u);
            if (!tail || sa2_ft_add(0x0082c189u, tail, "sub_82c190 (misdispatch)", SA2_FN_LIFTED) != 1) {
                printf("SPLIT_ROUTINE_PROBE_FAIL could not point 0x0082c189 at 0x0082c190\n");
                return 1;
            }
            printf("misdispatch: 0x0082c189 now runs 0x0082c190's lifted code\n");
        }
    }

    searches("FUN_007059a0", 0x007059a0u, 0x007059afu, 0x01AF00CCu);
    searches("FUN_006b1650", 0x006b1650u, 0x006b165fu, 0x01AEFD34u);

    static const double I = INFINITY;
    static const struct { double y, x; } at2[] = {{I, I}, {-I, I}, {I, -I}, {-I, -I}, {1.5, 2.0}, {I, 2.0}};
    static const struct { double y, x; } fm[] = {{1.5, 0.0}, {0.0, 0.0}, {I, 2.0}, {I, 0.0}, {I, I},
                                                 {1.5, I}, {0.0, I}, {7.25, -3.0}};
    unsigned at2_crossed = 0, fm_crossed = 0;
    for (unsigned k = 0; k < sizeof at2 / sizeof at2[0]; k++)
        intrinsic("atan2", 0x00859b6au, atan2_ref, at2[k].y, at2[k].x, &at2_crossed);
    for (unsigned k = 0; k < sizeof fm / sizeof fm[0]; k++)
        intrinsic("fmod", 0x008599fau, fmod_ref, fm[k].y, fm[k].x, &fm_crossed);
    printf("atan2: %u calls crossed into 0x0082c189; fmod: %u\n", at2_crossed, fm_crossed);
    if (!at2_crossed) g_total++;
    if (!fm_crossed) g_total++;

    sa2_runtime_shutdown();
    printf("%s %u of %u as expected, %u trapped\n",
           g_ok == g_total ? "SPLIT_ROUTINE_PROBE_OK" : "SPLIT_ROUTINE_PROBE_FAIL", g_ok, g_total,
           g_trapped);
    return g_ok == g_total ? 0 : 1;
}
