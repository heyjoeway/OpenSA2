/* Direct call entry for runtime v0 (design 5, S2: "direct call entry").
 *
 * Invoke one lifted function by its *original guest address* with a
 * caller-supplied initial state -- registers, stack pointer, x87 control word
 * -- run it to its return, and hand the resulting state back. This is what the
 * fixture harness drives and what a host will use before the whole-program
 * entry point exists.
 *
 * The call pushes a sentinel return address onto the caller's guest stack so
 * the callee's own `ret` lands somewhere recognisable: when the function
 * returns with EIP equal to the sentinel, the call returned through its own
 * `ret` and the status is 0. Anything else is a trap with the address.
 */
#ifndef SA2_RUNTIME_SA2_CALL_H
#define SA2_RUNTIME_SA2_CALL_H

#include <stdint.h>

#include "cpu_state.h"
#include "sa2_trap.h"

#ifdef __cplusplus
extern "C" {
#endif

#define SA2_CALL_SENTINEL 0xDEAD0000u

typedef struct {
    uint32_t status;           /* SA2_TRAP_* */
    uint32_t exit_pc;          /* the *guest* address that caused a non-zero status, else 0 */
    uint64_t host_fault_addr;  /* SA2_TRAP_HOST_FAULT only; never a guest address */
    char     note[SA2_TRAP_NOTE_MAX];
} sa2_call_result_t;

/* Initialise the runtime: guest memory from `pe_path`, the function table
 * (generated entries plus room for `extra_entries` natives), signal handlers. */
int  sa2_runtime_init(const char *pe_path, uint32_t extra_entries);
void sa2_runtime_shutdown(void);
/* Free the calling thread's State buffer, which its first sa2_call allocated;
 * every guest host thread calls it as it ends (runtime/sa2_threads.c
 * thread_body). A later sa2_call on the same thread would allocate another. */
void sa2_call_thread_fini(void);

/* Run the lifted function registered for `va`. `state` is read for the initial
 * register and x87 values and overwritten with the final ones. `timeout_s`
 * bounds the call in wall-clock seconds (0 disables it). */
int sa2_call(uint32_t va, sa2_cpu_state_t *state, uint32_t timeout_s, sa2_call_result_t *result);

/* Push `value` as the return address at `state->gpr[SA2_ESP]` without moving
 * ESP: the fixture convention where ESP already points at the return slot. */
int sa2_call_place_return(sa2_cpu_state_t *state, uint32_t value);

#ifdef __cplusplus
}
#endif
#endif
