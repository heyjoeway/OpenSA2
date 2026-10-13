/* Direct call entry. See sa2_call.h. */
#include "sa2_call.h"

#include <stdio.h>
#include <string.h>
#include <unistd.h>

#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_trap.h"

/* One State buffer is reused across calls: it is fully overwritten by
 * sa2_rt_state_load on every entry, and allocating 3.4 kB per fixture row
 * would dominate the harness's run.
 *
 * **Per guest thread.** G1's thread strategy gives each guest thread its own
 * CPU-state struct, and a worker running lifted code while the main thread is
 * parked inside its own `sa2_call` would otherwise overwrite the state that
 * call will return through. With one guest thread this is the same single
 * buffer it always was, allocated once and reused. */
static _Thread_local void *g_state;

int sa2_runtime_init(const char *pe_path, uint32_t extra_entries) {
    int rc = sa2_guest_init(pe_path);
    if (rc != SA2_OK) return rc;
    /* The generated table knows its own size; ask for it plus the caller's
     * natives, and let sa2_ft_init round up to a power of two. */
    if (sa2_ft_init(sa2_ft_generated_count() + extra_entries + 16u) != 0) return SA2_E_NOMEM;
    if (sa2_ft_register_generated() != 0) return -1;
    if (!g_state) {
        g_state = sa2_rt_state_alloc();
        if (!g_state) return SA2_E_NOMEM;
    }
    sa2_trap_install_handlers();
    return SA2_OK;
}

void sa2_runtime_shutdown(void) {
    sa2_rt_state_free(g_state);
    g_state = NULL;
    sa2_ft_shutdown();
    sa2_guest_shutdown();
}

/* A guest thread's State, allocated by its first sa2_call, freed as its host
 * thread ends (runtime/sa2_threads.c thread_body): rows are reused since
 * 2026-10-02, and 3.4 kB kept per thread for the life of the process had no
 * bound (the 2026-10-02 review, S3). */
void sa2_call_thread_fini(void) {
    sa2_rt_state_free(g_state);
    g_state = NULL;
}

int sa2_call_place_return(sa2_cpu_state_t *state, uint32_t value) {
    return sa2_poke(state->gpr[SA2_ESP], 4, &value);
}

int sa2_call(uint32_t va, sa2_cpu_state_t *state, uint32_t timeout_s, sa2_call_result_t *result) {
    memset(result, 0, sizeof *result);
    sa2_lifted_fn fn = sa2_ft_lookup(va);
    if (!fn) {
        result->status = SA2_TRAP_NOT_RUNNABLE;
        snprintf(result->note, sizeof result->note,
                 "no function registered for %#010x (region %s); %u entries in the table",
                 va, sa2_guest_region_of(va), sa2_ft_count());
        return -1;
    }
    if (!g_state) {
        g_state = sa2_rt_state_alloc();
        if (!g_state) return SA2_E_NOMEM;
    }

    /* Lifted code runs with the *host* rounding mode set from the guest control word
     * (runtime/remill_runtime.cpp), and remill's FLDCW may move it. Restore the host's
     * own mode on every exit path, or one row's `fldcw 0x0F7F` silently follows the
     * process into every later row. */
    const int host_round = sa2_rt_get_host_round();

    state->gpr[SA2_EIP] = va;
    sa2_rt_state_load(g_state, state);
    sa2_trap_clear();

    if (timeout_s) alarm(timeout_s);
    if (sigsetjmp(*sa2_trap_jmpbuf(), 1) == 0) {
        sa2_trap_window(1);
        fn(g_state, va, NULL);
        sa2_trap_window(0);
        sa2_rt_state_store(g_state, state);
        if (state->gpr[SA2_EIP] != SA2_CALL_SENTINEL) {
            result->status = SA2_TRAP_CONTROL_LEFT;
            result->exit_pc = state->gpr[SA2_EIP];
            snprintf(result->note, sizeof result->note,
                     "returned with EIP %#010x, not the sentinel %#010x", state->gpr[SA2_EIP], SA2_CALL_SENTINEL);
        }
    } else {
        const sa2_trap_t *t = sa2_trap_last();
        result->status = t->status;
        result->exit_pc = t->exit_pc;
        result->host_fault_addr = t->host_fault_addr;
        snprintf(result->note, sizeof result->note, "%s", t->note);
        /* The state is whatever the lifted code left behind: still useful for
         * a first-divergence report, so it is handed back rather than zeroed. */
        sa2_rt_state_store(g_state, state);
    }
    if (timeout_s) alarm(0);
    sa2_rt_set_host_round(host_round);
    return result->status == SA2_TRAP_NONE ? 0 : -1;
}
