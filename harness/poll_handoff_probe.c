/* The guest-poll rule's unit probe (runtime/sa2_threads.h sa2_sched_poll).
 *
 * runtime/sa2_threads.c and runtime/sa2_poll_handoff.c, the production files,
 * linked with stand-ins for the rest of the runtime (flat guest memory, a
 * virtual clock, an import counter, sa2_call running host routines as guest
 * threads) and with a host function standing in for the lifted 0x00427EE0,
 * linked as production links it: `-Wl,-wrap=sub_427ee0`. The stand-in is this
 * file compiled a second time with -DSA2_PROBE_GUEST, so the call below crosses
 * objects and reaches the wrapper through the linker, as lifted code's does.
 *
 *   zig cc -target x86_64-linux-musl -I runtime -DSA2_PROBE_GUEST -c \
 *          harness/poll_handoff_probe.c -o guest.o
 *   zig cc -target x86_64-linux-musl -I runtime harness/poll_handoff_probe.c guest.o \
 *          runtime/sa2_threads.c runtime/sa2_poll_handoff.c -Wl,-wrap=sub_427ee0 -o probe
 *   ./probe SCENARIO        -> one JSON object on stdout
 *
 * Thread 0 is the waiting guest thread: it starts a worker suspended
 * (CreateThread, one import call), resumes it (ResumeThread, another), and
 * polls the operation's done byte, which only the worker sets. The scenarios
 * differ in what thread 0 does between two polls and in what the worker does:
 *
 *   spin      nothing between polls: the NEW FILE wait. The worker sets the
 *             byte at once.
 *   sleeper   nothing between polls; the worker sleeps 50 ms first, so every
 *             hand-off finds nobody ready until the spin rule moves the clock.
 *   draw      a frame boundary between polls (the drawing path: BeginScene's
 *             wait); the worker sleeps 1 ms forty times.
 *   import    one import call between polls, and no hand-off point.
 *   done      the operation is done before the first poll; no worker.
 *   noop      the container holds no operation (0x00427EE0 answers done).
 *   unreadable  the container points at an operation outside guest memory, two
 *             polls with nothing between them, a worker ready: the wrapper
 *             reads -1, and a poll that cannot be read never hands off (the
 *             lifted function is about to fault on the same read). The
 *             stand-in answers done when its read fails, so the probe ends.
 *
 * Documentation/scripts/tests/test_poll_handoff.py builds it, runs every
 * scenario, holds each to literal counts, and shows the counts move under
 * SA2_POLL_HANDOFF=0 and SA2_THREADS_BREAK=pollimports. */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "cpu_state.h"

/* The register file the stand-in "lifted code" and sa2_rt_get_gpr share. */
typedef struct { uint32_t gpr[SA2_GPR_COUNT]; } probe_state;

#define MEM_LO     0x00100000u
#define MEM_HI     0x00900000u
#define TEB_BASE   0x00180000u
#define TEB_STRIDE 0x00000100u
#define STACK_BASE 0x00200000u
#define STACK_SIZE 0x00010000u
#define STACKS     8u
#define CONTAINER  0x00300000u
#define OPERATION  0x00300100u
#define UNMAPPED   0x00F00000u   /* above MEM_HI: sa2_peek refuses it */

#ifdef SA2_PROBE_GUEST
/* ------------------------------------------------ the guest function, alone */
#include "guest_memory.h"

/* 0x00427EE0: mov eax,[eax+4] ; test eax,eax ; je +5 ; mov al,[eax+0Ch] ; ret ;
 * mov al,1 ; ret -- the done byte of the container's operation, or 1 when there
 * is no operation. */
void *sub_427ee0(void *state, uint32_t pc, void *memory) {
    (void)pc;
    probe_state *s = (probe_state *)state;
    uint32_t op = 0;
    uint8_t al = 1;
    sa2_peek(s->gpr[SA2_EAX] + 4u, 4, &op);
    if (op) sa2_peek(op + 0x0Cu, 1, &al);
    s->gpr[SA2_EAX] = (op & 0xFFFFFF00u) | al;
    return memory;
}

#else
/* ------------------------------------------------- the probe and stand-ins */
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_threads.h"
#include "sa2_trap.h"

void *sub_427ee0(void *state, uint32_t pc, void *memory);

static uint8_t g_mem[MEM_HI - MEM_LO];
static int in_range(uint32_t addr, uint32_t len) {
    return addr >= MEM_LO && addr < MEM_HI && len <= MEM_HI - addr;
}
int sa2_peek(uint32_t addr, uint32_t len, void *out) {
    if (!in_range(addr, len)) return SA2_E_RANGE;
    memcpy(out, g_mem + (addr - MEM_LO), len);
    return SA2_OK;
}
int sa2_poke(uint32_t addr, uint32_t len, const void *in) {
    if (!in_range(addr, len)) return SA2_E_RANGE;
    memcpy(g_mem + (addr - MEM_LO), in, len);
    return SA2_OK;
}
int sa2_guest_fill(uint32_t addr, int byte, uint32_t len) {
    if (!in_range(addr, len)) return SA2_E_RANGE;
    memset(g_mem + (addr - MEM_LO), byte, len);
    return SA2_OK;
}
uint32_t sa2_guest_teb(unsigned index) { return TEB_BASE + index * TEB_STRIDE; }
int sa2_guest_teb_init_stack(unsigned index, uint32_t stack_limit, uint32_t stack_base) {
    (void)stack_limit; (void)stack_base;
    const uint32_t end = SA2_SEH_END_OF_CHAIN;
    return sa2_poke(sa2_guest_teb(index) + SA2_TEB_EXCEPTION_LIST, 4, &end);
}
int sa2_guest_tstack(unsigned slot, uint32_t *low, uint32_t *top) {
    if (slot >= STACKS) return SA2_E_RANGE;
    *low = STACK_BASE + slot * STACK_SIZE;
    *top = *low + STACK_SIZE;
    return SA2_OK;
}
void sa2_cpu_state_reset(sa2_cpu_state_t *s) { memset(s, 0, sizeof *s); }

void sa2_trap_note(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "trap note: ");
    vfprintf(stderr, fmt, ap);
    fprintf(stderr, "\n");
    va_end(ap);
}
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    fprintf(stderr, "trap %u at %#010x\n", status, exit_pc);
    _exit(3);
}
void sa2_trap_thread_init(void) {}
void sa2_trap_thread_fini(void) {}
void sa2_call_thread_fini(void) {}

static uint64_t g_now_us;
uint64_t sa2_virtual_us(void) { return g_now_us; }
void sa2_virtual_advance_to_us(uint64_t us) { if (us > g_now_us) g_now_us = us; }
int sa2_event_signal(uint32_t handle, int pulse) { (void)handle; (void)pulse; return 0; }
uint32_t sa2_fls_callback(uint32_t index) { (void)index; return 0; }

/* The import-call count the quiet test reads. Only the probe's own "imports"
 * move it, as only the import dispatcher moves the runtime's. */
static uint32_t g_imports;
uint32_t sa2_import_calls(void) { return g_imports; }
static void import_call(void) { g_imports++; }

uint32_t sa2_rt_get_gpr(const void *state, int idx) {
    return ((const probe_state *)state)->gpr[idx];
}

/* ------------------------------------------------- the worker's routines */

enum { W_SET = 0x00401000u, W_SLEEP50 = 0x00401010u, W_SLEEPS = 0x00401020u };

static void set_done(void) {
    const uint8_t one = 1;
    sa2_poke(OPERATION + 0x0Cu, 1, &one);
}

/* sa2_call for a guest thread's start routine: run the host routine the
 * address names, on the calling host thread (the guest thread's own), and
 * return through its own ret. */
int sa2_call(uint32_t va, sa2_cpu_state_t *state, uint32_t timeout_s, sa2_call_result_t *r) {
    (void)timeout_s;
    memset(r, 0, sizeof *r);
    r->status = SA2_TRAP_NONE;
    switch (va) {
    case W_SET:
        set_done();
        break;
    case W_SLEEP50:
        sa2_sched_sleep_ms(50);
        set_done();
        break;
    case W_SLEEPS:
        for (int i = 0; i < 40; i++) sa2_sched_sleep_ms(1);
        set_done();
        break;
    default:
        r->status = SA2_TRAP_CONTROL_LEFT;
        r->exit_pc = va;
        snprintf(r->note, sizeof r->note, "no probe routine at %#010x", va);
        return 0;
    }
    state->gpr[SA2_EAX] = 0;
    return 0;
}

/* ------------------------------------------------------------- the probe */

static probe_state g_state;

/* One call of the guest's poll, as lifted code makes it: EAX holds the
 * container, and the answer comes back in AL. */
static int poll_once(void) {
    g_state.gpr[SA2_EAX] = CONTAINER;
    sub_427ee0(&g_state, 0x00427EE0u, NULL);
    return (int)(g_state.gpr[SA2_EAX] & 0xFFu);
}

static int start_worker(uint32_t routine) {
    const char *why = NULL;
    const int idx = sa2_sched_spawn(routine, 0, 1, &why);   /* CREATE_SUSPENDED */
    import_call();                                           /* CreateThread */
    if (idx < 0) {
        fprintf(stderr, "spawn refused: %s\n", why ? why : "?");
        _exit(4);
    }
    sa2_sched_resume(idx);
    import_call();                                           /* ResumeThread */
    return idx;
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "usage: %s spin|sleeper|draw|import|done|noop|unreadable\n", argv[0]);
        return 2;
    }
    const char *sc = argv[1];
    sa2_threads_config_from_env();
    for (unsigned i = 0; i < 16; i++) sa2_guest_teb_init_stack(i, 0, 0);
    sa2_sched_init();

    const uint32_t op = OPERATION;
    sa2_poke(CONTAINER + 4u, 4, &op);
    uint8_t done_byte = 0;
    int worker = -1, limit = 1000, polls = 0, done = 0;

    if (!strcmp(sc, "spin")) {
        sa2_poke(OPERATION + 0x0Cu, 1, &done_byte);
        worker = start_worker(W_SET);
        while (polls < limit && !done) { polls++; done = poll_once(); }
    } else if (!strcmp(sc, "sleeper")) {
        sa2_poke(OPERATION + 0x0Cu, 1, &done_byte);
        worker = start_worker(W_SLEEP50);
        while (polls < limit && !done) { polls++; done = poll_once(); }
    } else if (!strcmp(sc, "draw")) {
        sa2_poke(OPERATION + 0x0Cu, 1, &done_byte);
        worker = start_worker(W_SLEEPS);
        while (polls < limit) {
            polls++;
            if ((done = poll_once())) break;
            sa2_sched_frame_boundary();
        }
    } else if (!strcmp(sc, "import")) {
        limit = 64;
        sa2_poke(OPERATION + 0x0Cu, 1, &done_byte);
        worker = start_worker(W_SET);
        while (polls < limit) {
            polls++;
            if ((done = poll_once())) break;
            import_call();
        }
    } else if (!strcmp(sc, "unreadable")) {
        const uint32_t unmapped = UNMAPPED;
        sa2_poke(CONTAINER + 4u, 4, &unmapped);
        worker = start_worker(W_SET);
        for (int i = 0; i < 2; i++) { polls++; done = poll_once(); }
    } else if (!strcmp(sc, "done") || !strcmp(sc, "noop")) {
        if (!strcmp(sc, "done")) {
            done_byte = 1;
            sa2_poke(OPERATION + 0x0Cu, 1, &done_byte);
        } else {
            const uint32_t none = 0;
            sa2_poke(CONTAINER + 4u, 4, &none);
        }
        for (int i = 0; i < 2; i++) { polls++; done = poll_once(); }
    } else {
        fprintf(stderr, "unknown scenario %s\n", sc);
        return 2;
    }

    sa2_poll_stats ps;
    sa2_sched_poll_stats(&ps);
    printf("{\"scenario\": \"%s\", \"done\": %s, \"polls\": %d, "
           "\"worker\": \"%s\", \"virtual_us\": %llu, \"frames\": %llu, \"switches\": %llu, "
           "\"spin_observations\": %llu, \"spin_clock_moves\": %llu, "
           "\"stats\": {\"rule\": %d, \"polls\": %llu, \"not_done\": %llu, \"quiet\": %llu, "
           "\"hand_offs\": %llu, \"switched\": %llu, \"first_poll\": %llu, "
           "\"first_import_call\": %u, \"first_frame\": %llu}}\n",
           sc, done ? "true" : "false", polls,
           worker >= 0 ? sa2_sched_state_name(worker) : "none",
           (unsigned long long)sa2_virtual_us(), (unsigned long long)sa2_sched_frames(),
           (unsigned long long)sa2_sched_switches(),
           (unsigned long long)sa2_sched_spin_observations(),
           (unsigned long long)sa2_sched_spin_clock_moves(),
           ps.rule, (unsigned long long)ps.polls, (unsigned long long)ps.not_done,
           (unsigned long long)ps.quiet, (unsigned long long)ps.hand_offs,
           (unsigned long long)ps.switched, (unsigned long long)ps.first_poll,
           ps.first_import_call, (unsigned long long)ps.first_frame);
    fflush(stdout);
    /* A worker the rule never ran is still waiting for the token; the process
     * ends here rather than joining it. */
    _exit(0);
}
#endif
