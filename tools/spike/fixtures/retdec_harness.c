/* RetDec fixture harness (tools/spike/fixtures): runs a vector file through
 * one RetDec C function (retdec_glue.c) inside the guest mapping, with a
 * fault guard so a crash is recorded as status 2 at the faulting address. */
#include <setjmp.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "guest.h"
#include "marshal.h"
#include "shapes.h"
#include "vectors.h"

int retdec_call(uint32_t va, const call_t *c);

static sigjmp_buf jb;
static volatile uint32_t fault_addr, trap_pc;

/* Called by retdec_glue.c's stubs for callees RetDec's C names but no TU
 * provides (the other nine and the Ninja helpers): the row leaves the lifted
 * code at that address, status 1. */
void retdec_trap(uint32_t pc) { trap_pc = pc; siglongjmp(jb, 2); }
static void on_fault(int sig, siginfo_t *si, void *ctx) {
    (void)sig; (void)ctx;
    fault_addr = (uint32_t)(uintptr_t)si->si_addr;
    siglongjmp(jb, 1);
}

int main(int argc, char **argv) {
    static uint8_t altstack[1 << 16];
    stack_t ss = {altstack, 0, sizeof altstack};
    struct sigaction sa;
    vectors_t v;
    const shape_t *sh;
    uint32_t words, r;
    call_t c;
    if (argc != 4) { fprintf(stderr, "usage: %s image.bin vectors.bin out.bin\n", argv[0]); return 2; }
    if (guest_map(argv[1]) != 0) return 1;
    if (vectors_read(argv[2], &v) != 0) return 1;
    sh = shape_for(v.va);
    if (!sh || sh->in_words != v.in_words || sh->out_words != v.out_words) { fprintf(stderr, "shape mismatch\n"); return 1; }
    sigaltstack(&ss, NULL);
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_fault; sa.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_NODEFER;
    sigaction(SIGSEGV, &sa, NULL); sigaction(SIGBUS, &sa, NULL); sigaction(SIGFPE, &sa, NULL);
    words = 2 + v.out_words;
    for (r = 0; r < v.rows; r++) {
        uint32_t *out = v.out + (size_t)r * words;
        uint32_t status = 0, exit_pc = 0, eax = 0;
        marshal_in(v.va, v.in + (size_t)r * v.in_words, &c);
        switch (sigsetjmp(jb, 1)) {
        case 0: eax = (uint32_t)retdec_call(v.va, &c); break;
        case 1: status = 2; exit_pc = fault_addr; break;
        default: status = 1; exit_pc = trap_pc; break;
        }
        out[0] = status; out[1] = exit_pc;
        marshal_out(v.va, &c, status, eax, 0, out + 2);
    }
    return vectors_write(argv[3], &v);
}
