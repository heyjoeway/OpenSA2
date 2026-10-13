/* Thread-row reuse's unit probe (runtime/sa2_threads.h SA2_MAX_GUEST_THREADS,
 * sa2_sched_spawn), after the 2026-10-02 review of the reuse change.
 *
 * runtime/sa2_threads.c and runtime/sa2_trap.c, the production files, linked
 * with stand-ins for the rest of the runtime (flat guest memory, a virtual
 * clock, an import counter, sa2_call running host routines as guest threads),
 * with the C library's malloc and free wrapped so the probe can count the
 * 64 KiB signal stacks sa2_trap_thread_init allocates and sa2_trap_thread_fini
 * frees:
 *
 *   zig cc -target x86_64-linux-musl -O2 -I runtime harness/thread_reuse_probe.c \
 *          runtime/sa2_threads.c runtime/sa2_trap.c \
 *          -Wl,-wrap=malloc -Wl,-wrap=free -o probe
 *   ./probe order TRACE SEED ROUNDS   -> one JSON object on stdout, the trace in TRACE
 *   ./probe trapkeep                  -> one JSON object on stdout
 *
 * order   The review's workload (its unit probe,
 *         private/generated/s5/newfile/threads/review/unit/unit_reuse.c): thread 0
 *         creates short and long workers from a seeded generator, some suspended
 *         and resumed at once, and two long-lived waiters on an event a periodic
 *         timer pulses, the second once low rows are being reused; it yields,
 *         sleeps, closes finished threads' handles, some late, and notes a tick
 *         each round. Every guest-visible event goes into TRACE keyed by the
 *         thread's creation number (from its id), never by its row, so two runs
 *         compare byte for byte whatever rows they used.
 * trapkeep SA2_THREAD_ROWS=5: four worker rows. A worker that ends in a trap of
 *         its own, one that calls ExitThread, one that calls ExitThread and whose
 *         FLS callback then traps, and one that returns, every handle closed; then
 *         six workers one after another, each closed when it ends; then workers
 *         held open until a creation is refused.
 *
 * Documentation/scripts/tests/test_thread_reuse.py builds it and holds: the
 * traces with rows reused hundreds of times equal to the trace with a row per
 * thread, and SA2_THREADS_BREAK=rowindex making them differ (S2); the trapped
 * worker's row never reused and its note kept (S1); every finished worker's
 * host thread freeing its signal stack and its State after its last call (S3). */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <unistd.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "sa2_call.h"
#include "sa2_threads.h"
#include "sa2_trap.h"

/* ------------------------------------------------- malloc and free, counted */

#define ALT_SIZE ((size_t)1 << 16)    /* sa2_trap_thread_init's signal stack */
#define ALT_MAX  1024
void *__real_malloc(size_t n);
void  __real_free(void *p);
static void    *g_alt_live[ALT_MAX];
static unsigned g_alt_made, g_alt_freed, g_alt_lost;
static int      g_alt_lock;
static void alt_lock(void)   { while (__atomic_exchange_n(&g_alt_lock, 1, __ATOMIC_ACQUIRE)) {} }
static void alt_unlock(void) { __atomic_store_n(&g_alt_lock, 0, __ATOMIC_RELEASE); }

void *__wrap_malloc(size_t n) {
    void *p = __real_malloc(n);
    if (p && n == ALT_SIZE) {
        alt_lock();
        int put = 0;
        for (int i = 0; i < ALT_MAX && !put; i++)
            if (!g_alt_live[i]) { g_alt_live[i] = p; put = 1; }
        if (put) g_alt_made++; else g_alt_lost++;
        alt_unlock();
    }
    return p;
}

void __wrap_free(void *p) {
    if (p) {
        alt_lock();
        for (int i = 0; i < ALT_MAX; i++)
            if (g_alt_live[i] == p) { g_alt_live[i] = NULL; g_alt_freed++; break; }
        alt_unlock();
    }
    __real_free(p);
}

/* ------------------------------------------------------------- stand-ins */

static uint64_t g_clock_us;
static uint32_t g_calls;
uint64_t sa2_virtual_us(void) { return g_clock_us; }
void sa2_virtual_advance_to_us(uint64_t us) { if (us > g_clock_us) g_clock_us = us; }
uint32_t sa2_import_calls(void) { return g_calls; }
int sa2_event_signal(uint32_t h, int pulse) { (void)h; (void)pulse; return 1; }
#define FN_FLSTRAP 0x6000u
#define FLS_TRAPPING 3u   /* the FLS index whose callback traps (trapkeep's third worker) */
uint32_t sa2_fls_callback(uint32_t i) { return i == FLS_TRAPPING ? FN_FLSTRAP : 0; }
void sa2_cpu_state_reset(sa2_cpu_state_t *s) { memset(s, 0, sizeof *s); }
uint32_t sa2_guest_teb(unsigned index) {
    if (index >= SA2_GUEST_TEB_SLOTS) return 0;
    return SA2_GUEST_TEB_BASE + index * SA2_GUEST_TEB_SIZE;
}
int sa2_guest_teb_init_stack(unsigned index, uint32_t lim, uint32_t base) {
    (void)index; (void)lim; (void)base;
    return SA2_OK;
}
int sa2_guest_tstack(unsigned slot, uint32_t *low, uint32_t *top) {
    if (slot >= SA2_GUEST_TSTACK_SLOTS) return SA2_E_RANGE;
    uint32_t base = SA2_GUEST_TSTACK_BASE + slot * SA2_GUEST_TSTACK_SIZE;
    if (low) *low = base + SA2_GUEST_TSTACK_GUARD;
    if (top) *top = base + SA2_GUEST_TSTACK_SIZE;
    return SA2_OK;
}
int sa2_guest_fill(uint32_t a, int b, uint32_t n) { (void)a; (void)b; (void)n; return SA2_OK; }
int sa2_peek(uint32_t addr, uint32_t len, void *out) {
    (void)addr;
    if (len == 4) { uint32_t v = SA2_SEH_END_OF_CHAIN; memcpy(out, &v, 4); }
    else memset(out, 0, len);
    return SA2_OK;
}
int sa2_poke(uint32_t addr, uint32_t len, const void *in) { (void)addr; (void)len; (void)in; return SA2_OK; }

/* sa2_call's per-thread State, as runtime/sa2_call.c keeps it: counted here, and
 * a call made after the thread freed it is a defect the probe reports. */
static _Thread_local int t_state_freed;
static unsigned g_state_frees, g_calls_after_free;
void sa2_call_thread_fini(void) {
    t_state_freed = 1;
    __atomic_add_fetch(&g_state_frees, 1, __ATOMIC_SEQ_CST);
}

/* ------------------------------------------------------------------ trace */

static FILE *g_tr;
static uint32_t g_base_tid;
static unsigned cn_of(int idx) {           /* creation number of the thread on row idx */
    uint32_t tid = sa2_sched_tid(idx);
    return tid ? (tid - g_base_tid) / 4u : 9999u;
}
static unsigned me_cn(void) { return cn_of(sa2_sched_current()); }
static void tr(const char *what, long v) {
    if (g_tr) fprintf(g_tr, "%u %s %ld us=%llu\n", me_cn(), what, v, (unsigned long long)g_clock_us);
}
static void fake_import(void) {
    g_calls++;
    int me = sa2_sched_current();
    sa2_sched_note_esp(sa2_sched_stack_top(me) - 64u);
}

/* -------------------------------------------------------------- routines */

#define FN_SHORT  0x1000u
#define FN_LONG   0x2000u
#define FN_WAITER 0x3000u
#define FN_TRAP   0x4000u
#define FN_EXIT   0x5000u
#define FN_EXITFLS 0x5100u
#define EVENT_H   0x7f001000u
#define TRAP_PC   0x00500000u

static unsigned g_dirty;

static void check_clean(void) {
    int me = sa2_sched_current();
    if (sa2_sched_tls(me)[5] || sa2_sched_fls(me)[7]) { g_dirty++; tr("DIRTY", 1); }
    sa2_sched_tls(me)[5] = sa2_sched_tid(me);
    sa2_sched_fls(me)[7] = sa2_sched_tid(me);
    tr("stack", (long)sa2_sched_stack_top(me));
}

static uint32_t routine(uint32_t va, uint32_t param) {
    check_clean();
    if (va == FN_SHORT) {
        for (uint32_t k = 0; k < param; k++) { fake_import(); tr("short", k); sa2_sched_yield("y"); }
        return 100u + param;
    }
    if (va == FN_LONG) {
        for (uint32_t k = 0; k < param; k++) {
            fake_import(); tr("long", k);
            if (k % 3 == 0) sa2_sched_sleep_ms(1 + k % 4); else sa2_sched_yield("y");
        }
        return 200u + param;
    }
    if (va == FN_WAITER) {
        for (uint32_t k = 0; k < param; k++) {
            fake_import();
            int w = sa2_sched_block(SA2_WAIT_EVENT, EVENT_H, 0, "ev");
            tr("woke", w);
        }
        return 300u;
    }
    return 0;
}

int sa2_call(uint32_t va, sa2_cpu_state_t *st, uint32_t timeout_s, sa2_call_result_t *r) {
    (void)timeout_s;
    if (t_state_freed) __atomic_add_fetch(&g_calls_after_free, 1, __ATOMIC_SEQ_CST);
    memset(r, 0, sizeof *r);
    r->status = SA2_TRAP_NONE;
    if (va == FN_TRAP) {
        /* A trap of the thread's own: a guest access refused, as a lifted
         * function's would be. Not ExitThread. */
        fake_import();
        r->status = SA2_TRAP_FAULT;
        r->exit_pc = TRAP_PC;
        snprintf(r->note, sizeof r->note, "probe: a guest access the runtime refused at %#010x",
                 TRAP_PC);
        return -1;
    }
    if (va == FN_EXIT || va == FN_EXITFLS) {
        /* ExitThread(7): the code is recorded, then control leaves the lifted code.
         * FN_EXITFLS first gives the thread a value at the FLS index whose callback
         * traps, so the thread's end runs that callback and ends in its trap. */
        fake_import();
        if (va == FN_EXITFLS) sa2_sched_fls(sa2_sched_current())[FLS_TRAPPING] = 0x00001234u;
        sa2_sched_exit_current(7);
        r->status = SA2_TRAP_CONTROL_LEFT;
        snprintf(r->note, sizeof r->note, "probe: ExitThread(7)");
        return -1;
    }
    if (va == FN_FLSTRAP) {
        r->status = SA2_TRAP_NOT_RUNNABLE;
        snprintf(r->note, sizeof r->note, "probe: no function for the FLS callback");
        return -1;
    }
    st->gpr[SA2_EAX] = routine(va, sa2_sched_param(sa2_sched_current()));
    return 0;
}

/* ------------------------------------------------------------- the driver */

#define MAXL 600
static int      l_idx[MAXL];      /* the row CreateThread gave logical thread n */
static int      l_closed[MAXL];
static int      l_n;
static unsigned l_refused;
static const char *l_refused_why = "";
static uint32_t g_rng = 12345u;
static uint32_t rnd(uint32_t n) { g_rng = g_rng * 1103515245u + 12345u; return (g_rng >> 16) % n; }

static int held(void) {             /* not closed: running, or ended and still held */
    int h = 0;
    for (int n = 1; n <= l_n; n++) if (l_idx[n] >= 0 && !l_closed[n]) h++;
    return h;
}

static int spawn(uint32_t fn, uint32_t param, int suspended) {
    const char *why = NULL;
    if (l_n + 1 >= MAXL) return -1;
    int idx = sa2_sched_spawn(fn, param, suspended, &why);
    l_n++;
    l_idx[l_n] = idx;
    if (idx < 0) {
        l_refused++;
        l_refused_why = why ? why : "?";
        if (g_tr) fprintf(g_tr, "0 REFUSED %s\n", l_refused_why);
        return -1;
    }
    if (g_tr) fprintf(g_tr, "0 spawn cn=%u fn=%#x p=%u susp=%d\n", cn_of(idx), fn, param, suspended);
    if (suspended) sa2_sched_resume(idx);   /* the game's wrapper resumes at once */
    return idx;
}

static void close_one(int n) {
    if (g_tr) fprintf(g_tr, "0 close cn=%d exit=%u\n", n, sa2_sched_exit_code(l_idx[n]));
    l_closed[n] = 1;
    sa2_sched_handle_closed(l_idx[n]);
}

static void close_finished(int all) {
    for (int n = 1; n <= l_n; n++) {
        if (l_idx[n] < 0 || l_closed[n]) continue;
        if (!sa2_sched_finished(l_idx[n])) continue;
        if (!all && rnd(4) == 0) continue;   /* some closes are late */
        close_one(n);
    }
}

/* The host side only: wait until every started worker's host thread has made
 * its signal stack (before it first takes the token) and every finished one
 * has run its two frees (after it hands the token on and unlocks), so the
 * counts below do not depend on host timing. Gives up after ten seconds, and
 * the counts then say what was missing. */
static void await_counts(unsigned started, unsigned finished) {
    for (int i = 0; i < 10000; i++) {
        unsigned made, freed;
        alt_lock(); made = g_alt_made; freed = g_alt_freed; alt_unlock();
        if (made >= started && freed >= finished
            && __atomic_load_n(&g_state_frees, __ATOMIC_SEQ_CST) >= finished) return;
        struct timespec ts = {0, 1000000};
        nanosleep(&ts, NULL);
    }
}

static void print_frees(unsigned started, unsigned finished) {
    await_counts(started, finished);
    alt_lock();
    unsigned made = g_alt_made, freed = g_alt_freed, lost = g_alt_lost, live = 0;
    for (int i = 0; i < ALT_MAX; i++) if (g_alt_live[i]) live++;
    alt_unlock();
    printf("\"started\": %u, \"finished\": %u, \"signal_stacks\": {\"made\": %u, "
           "\"freed\": %u, \"live\": %u, \"untracked\": %u}, \"state_frees\": %u, "
           "\"calls_after_free\": %u",
           started, finished, made, freed, live, lost,
           __atomic_load_n(&g_state_frees, __ATOMIC_SEQ_CST),
           __atomic_load_n(&g_calls_after_free, __ATOMIC_SEQ_CST));
}

static void jstr(const char *s) {
    putchar('"');
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') putchar('\\');
        if ((unsigned char)*s >= 0x20) putchar(*s);
    }
    putchar('"');
}

static int order(const char *path, uint32_t seed, int rounds) {
    g_tr = fopen(path, "w");
    if (!g_tr) return 2;
    g_rng = seed;
    sa2_timer_set(7, 1, EVENT_H, 0, 0x0021u);   /* TIME_PERIODIC | TIME_CALLBACK_EVENT_PULSE */
    for (int round = 0; round < rounds; round++) {
        /* two long-lived waiters, the second created when low rows are being reused */
        if (round == 3 || round == 40) spawn(FN_WAITER, 1000000u, 0);
        int k = 1 + (int)rnd(3);
        for (int i = 0; i < k && held() < 9; i++) {
            uint32_t r = rnd(10);
            if (r < 6) spawn(FN_SHORT, 1 + rnd(4), (int)rnd(2));
            else spawn(FN_LONG, 3 + rnd(20), (int)rnd(2));
        }
        int y = (int)rnd(6);
        for (int i = 0; i < y; i++) {
            sa2_sched_yield("t0");
            fprintf(g_tr, "0 back %d us=%llu\n", i, (unsigned long long)g_clock_us);
        }
        if (rnd(3) == 0) {
            sa2_sched_sleep_ms(1 + rnd(9));
            fprintf(g_tr, "0 slept us=%llu\n", (unsigned long long)g_clock_us);
        }
        close_finished(0);
        sa2_sched_tick_note(0x1000u + (uint32_t)round);
    }
    /* drain: every worker but the two waiters ends; close everything */
    for (int i = 0; i < 400; i++) { sa2_sched_sleep_ms(2); close_finished(1); }
    const uint32_t nt = sa2_sched_tick_count();
    for (uint32_t i = 0; i < nt; i++) {
        const sa2_tick_row *t = sa2_sched_tick_at(i);
        fprintf(g_tr, "tick %u caller=%#x us=%llu calls=%llu sw=%llu adv=%llu fires=%llu "
                "pulses=%llu wgc=%llu\n",
                t->seq, t->caller, (unsigned long long)t->virtual_us,
                (unsigned long long)t->import_calls, (unsigned long long)t->switches,
                (unsigned long long)t->time_advances, (unsigned long long)t->timer_fires,
                (unsigned long long)t->pulses_delivered, (unsigned long long)t->worker_guest_calls);
    }
    fprintf(g_tr, "end threads=%d switches=%llu deadlocks=%llu dirty=%u\n", l_n,
            (unsigned long long)sa2_sched_switches(), (unsigned long long)sa2_sched_deadlocks(),
            g_dirty);
    fclose(g_tr);
    g_tr = NULL;
    unsigned finished = 0;
    for (int n = 1; n <= l_n; n++) if (l_idx[n] >= 0 && l_closed[n]) finished++;
    printf("{\"threads\": %d, \"refused\": %u, \"rows\": %d, \"rows_reused\": %llu, "
           "\"dirty\": %u, ", l_n, l_refused, sa2_sched_count(),
           (unsigned long long)sa2_sched_rows_reused(), g_dirty);
    print_frees((unsigned)l_n - l_refused, finished);
    printf("}\n");
    return 0;
}

static void wait_finished(int idx) {
    while (idx >= 0 && !sa2_sched_finished(idx)) sa2_sched_sleep_ms(1);
}

static int trapkeep(void) {
    /* The four first workers: a trap of its own, ExitThread, ExitThread with an FLS
     * callback that traps, a return. */
    const uint32_t first[4] = {FN_TRAP, FN_EXIT, FN_EXITFLS, FN_SHORT};
    for (int i = 0; i < 4; i++) {
        int idx = spawn(first[i], 1, 0);
        wait_finished(idx);
    }
    for (int n = 1; n <= 4; n++) if (l_idx[n] >= 0) close_one(n);
    /* Six more, one at a time, each closed when it ends: every one needs a reused row. */
    for (int i = 0; i < 6; i++) {
        int idx = spawn(FN_SHORT, 1, 0);
        wait_finished(idx);
        if (idx >= 0) close_one(l_n);
    }
    /* Then workers held open (a waiter nobody wakes) until a creation is refused. */
    for (int i = 0; i < 4 && !l_refused; i++) spawn(FN_WAITER, 1, 0);
    unsigned finished = 0;
    for (int n = 1; n <= l_n; n++) if (l_idx[n] >= 0 && l_closed[n]) finished++;
    printf("{\"rows\": [");
    for (int r = 1; r < sa2_sched_count(); r++) {
        printf("%s{\"row\": %d, \"thread\": %u, \"trap_status\": %u, \"exit_code\": %u, "
               "\"note\": ", r > 1 ? ", " : "", r, cn_of(r), sa2_sched_trap_status(r),
               sa2_sched_exit_code(r));
        jstr(sa2_sched_trap_note(r));
        printf("}");
    }
    printf("], \"placed\": [");
    for (int n = 1; n <= l_n; n++) printf("%s%d", n > 1 ? ", " : "", l_idx[n]);
    printf("], \"rows_reused\": %llu, \"refused\": %u, \"refused_why\": ",
           (unsigned long long)sa2_sched_rows_reused(), l_refused);
    jstr(l_refused_why);
    printf(", ");
    print_frees((unsigned)l_n - l_refused, finished);
    printf("}\n");
    return 0;
}

int main(int argc, char **argv) {
    sa2_threads_config_from_env();
    sa2_sched_init();
    g_base_tid = sa2_sched_tid(0);
    int rc = 2;
    if (argc >= 5 && !strcmp(argv[1], "order"))
        rc = order(argv[2], (uint32_t)strtoul(argv[3], NULL, 0), atoi(argv[4]));
    else if (argc >= 2 && !strcmp(argv[1], "trapkeep"))
        rc = trapkeep();
    else
        fprintf(stderr, "usage: %s order TRACE SEED ROUNDS | trapkeep\n", argv[0]);
    fflush(NULL);
    _exit(rc);   /* the waiters never end; nothing joins them */
}
