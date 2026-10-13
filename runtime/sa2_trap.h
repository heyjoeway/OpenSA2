/* Trap and diagnostic channel for runtime v0.
 *
 * Anything the runtime cannot honour -- an unlifted block, a function-table
 * miss, an out-of-range guest access, an unimplemented intrinsic -- raises a
 * trap rather than crashing. A trap carries a status and the guest address
 * that caused it, plus a free-text note, and unwinds to the innermost
 * `sa2_call` through `siglongjmp`. The status values are the S1 fixture
 * harness's (tools/spike/fixtures/shapes.h), so a row this runtime produces is
 * comparable with one the spike produced.
 */
#ifndef SA2_RUNTIME_TRAP_H
#define SA2_RUNTIME_TRAP_H

#include <setjmp.h>
#include <stdint.h>
#if defined(_WIN32)
#include "sa2_win32.h"     /* sigjmp_buf, sigsetjmp and alarm on a Windows host */
#endif

#ifdef __cplusplus
extern "C" {
#endif

enum {
    SA2_TRAP_NONE         = 0,  /* the function returned through its own ret */
    SA2_TRAP_CONTROL_LEFT = 1,  /* control left the lifted code (exit_pc = target) */
    SA2_TRAP_FAULT        = 2,  /* a guest access the runtime refused (exit_pc = the guest address) */
    SA2_TRAP_NOT_RUNNABLE = 3,  /* no function for this address in this program */
    SA2_TRAP_TIMEOUT      = 4,  /* the call exceeded its wall-clock budget */
    /* A host signal inside compiled lifted code. Distinct from SA2_TRAP_FAULT because its
     * address is not a guest address (G2 finding 7): `si_addr` is the low 32 bits of a
     * *host* pointer, and it used to be written into `exit_pc` -- the field the trap
     * fixpoint (tools/s2/corpus_run/close_traps.py) feeds back to the lifter, which lifts
     * any value landing in the image range. Nothing distinguished the two but luck: the
     * one case that stalled the fixpoint was caught only by falling outside that range.
     * `exit_pc` is 0 here and the host address is carried in `host_fault_addr`. */
    SA2_TRAP_HOST_FAULT   = 5,
    /* The plan declared a live-in it cannot supply (harness/plan_ops.h OP_ST_UNSUPPLIED).
     * The row ran; it must not be counted as a match. */
    SA2_TRAP_UNSUPPLIED   = 6,
};

#define SA2_TRAP_NOTE_MAX 256

/* The process's exit status when a call's timeout cannot unwind its thread: x86-64 Windows
 * only, where a thread in a kernel wait takes the timeout's landing only when the wait
 * returns, and one that has not landed shortly after the deadline ends the process with this
 * status and the trap's note on stderr (sa2_trap.c). A fault or trap outside any sa2_call
 * exits 3 on every host. */
#define SA2_TRAP_EXIT_TIMEOUT_STUCK 4

typedef struct {
    uint32_t status;
    uint32_t exit_pc;          /* a *guest* address, or 0 when the status carries none */
    uint64_t host_fault_addr;  /* SA2_TRAP_HOST_FAULT only: the host `si_addr`, full width */
    char     note[SA2_TRAP_NOTE_MAX];
} sa2_trap_t;

/* Raise: records status/address and longjmps to the innermost sa2_call. When
 * no call is active it prints the trap and aborts, because returning would
 * corrupt the caller's state silently. */
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) __attribute__((noreturn));

/* Attach a diagnostic to the trap that is about to be raised. */
void sa2_trap_note(const char *fmt, ...) __attribute__((format(printf, 1, 2)));

const sa2_trap_t *sa2_trap_last(void);
void sa2_trap_clear(void);

/* Used by sa2_call only: the buffer `sigsetjmp` must be called on, and the
 * window during which a trap may unwind. */
sigjmp_buf *sa2_trap_jmpbuf(void);
void sa2_trap_window(int active);
int  sa2_trap_in_window(void);

/* 1 when the host instruction ending at `ret` is a call, which is how the fault
 * report outside a call tells a return address on the host stack from a pointer
 * into .rodata. Host code, so the host ISA's call forms (sa2_trap.c). `ret` must
 * point at least eight bytes into the binary's text. */
int sa2_trap_host_call_precedes(uintptr_t ret);

/* Install SIGSEGV/SIGBUS/SIGFPE handlers as a backstop for faults the bounds
 * checks cannot see (a fault inside compiled lifted code, for example). */
void sa2_trap_install_handlers(void);
/* The calling thread's own signal stack; every guest host thread calls it. */
void sa2_trap_thread_init(void);
/* Takes the calling thread's signal stack down and frees it; every guest host
 * thread calls it as it ends (runtime/sa2_threads.c thread_body), after its last
 * sa2_call. A no-op on a thread sa2_trap_thread_init gave no stack. */
void sa2_trap_thread_fini(void);

#ifdef __cplusplus
}
#endif
#endif
