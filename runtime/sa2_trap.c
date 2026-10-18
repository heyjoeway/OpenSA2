/* Trap channel. See sa2_trap.h. */
#define _GNU_SOURCE      /* REG_RIP and the aarch64 mcontext, for the fault report outside a call */
/* Signals, sigsetjmp and a Linux signal context: a host without them needs a
 * trap backend of its own, and is told so here rather than by the first header
 * it lacks. x86-64 Windows has one, at the end of this file: a vectored
 * exception handler in place of the signal handlers, a watchdog thread in place
 * of SIGALRM, and its own register save and restore in place of sigsetjmp
 * (sa2_win32.h). */
#if defined(_WIN32)
#if !defined(__x86_64__)
#error "sa2_trap.c: the Windows trap backend is for x86-64 only"
#endif
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <malloc.h>     /* _resetstkoflw */
#elif !defined(__linux__) || !(defined(__x86_64__) || defined(__aarch64__))
#error "sa2_trap.c is the Linux backend for x86-64 and aarch64; this host has no trap backend"
#endif
#include "sa2_trap.h"

#include <signal.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#if !defined(_WIN32)
#include <sys/prctl.h>
#include <sys/syscall.h>
#include <ucontext.h>
#endif
#include <unistd.h>

/* Per guest thread, not per process. G1's thread strategy runs each guest
 * thread on its own host thread, so the buffer `sigsetjmp` was called on, the
 * trap that is being reported and the window during which an unwind is legal
 * all belong to the thread that is running lifted code -- one shared `g_jb`
 * would have unwound a worker's fault onto the main thread's stack. With one
 * guest thread this is the same storage it always was. */
static _Thread_local sigjmp_buf  g_jb;
static _Thread_local sa2_trap_t  g_trap;
static _Thread_local char        g_pending_note[SA2_TRAP_NOTE_MAX];
static _Thread_local volatile int g_window;
#if defined(_WIN32)
static void win_after_unwind(void);
#endif

sigjmp_buf *sa2_trap_jmpbuf(void) { return &g_jb; }
void sa2_trap_window(int active)  { g_window = active; }
int  sa2_trap_in_window(void)     { return g_window; }

const sa2_trap_t *sa2_trap_last(void) { return &g_trap; }

void sa2_trap_clear(void) {
    memset(&g_trap, 0, sizeof g_trap);
    g_pending_note[0] = 0;
#if defined(_WIN32)
    win_after_unwind();
#endif
}

void sa2_trap_note(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_pending_note, sizeof g_pending_note, fmt, ap);
    va_end(ap);
}

void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    g_trap.status = status;
    g_trap.exit_pc = exit_pc;
    g_trap.host_fault_addr = 0;
    snprintf(g_trap.note, sizeof g_trap.note, "%s", g_pending_note);
    g_pending_note[0] = 0;
    if (!g_window) {
        fprintf(stderr, "sa2 runtime: trap status %u at %#010x outside any sa2_call%s%s\n",
                status, exit_pc, g_trap.note[0] ? ": " : "", g_trap.note);
        _exit(3);
    }
    g_window = 0;
    siglongjmp(g_jb, 1);
}

/* Set while the fault report scans the host stack: a second fault there is the
 * scan's, and must not replace the report of the first. */
static _Thread_local volatile int g_reporting;

#if !defined(_WIN32)
/* The faulting host thread's program counter and stack pointer, from the
 * signal's context. Host registers of the host binary, whatever the guest is.
 * The context's layout is the kernel's as well as the ISA's, so a backend is
 * chosen by both, and any other host (Windows among them) stops here. */
#if defined(__linux__) && defined(__x86_64__)
#define SA2_PC_NAME " rip 0x"
static uintptr_t uc_pc(const void *uc) { return (uintptr_t)((const ucontext_t *)uc)->uc_mcontext.gregs[REG_RIP]; }
static uintptr_t uc_sp(const void *uc) { return (uintptr_t)((const ucontext_t *)uc)->uc_mcontext.gregs[REG_RSP]; }
#elif defined(__linux__) && defined(__aarch64__)
#define SA2_PC_NAME " pc 0x"
static uintptr_t uc_pc(const void *uc) { return (uintptr_t)((const ucontext_t *)uc)->uc_mcontext.pc; }
static uintptr_t uc_sp(const void *uc) { return (uintptr_t)((const ucontext_t *)uc)->uc_mcontext.sp; }
#else
#error "sa2_trap.c: no signal-context accessors for this host"
#endif
#endif /* !_WIN32 */

/* The scan below reads words of the host stack, and keeps one as a caller when
 * it points just past a call instruction in the binary's text. That text is
 * the host's own code -- compiled lifted code and the runtime, never guest
 * bytes -- so the call forms are the host ISA's: on x86-64 `call rel32` (E8
 * five bytes back) and the `call r/m` forms (FF two, three, six or seven bytes
 * back), as the scan has always tested; on aarch64 `bl` and `blr`, which are
 * four bytes and aligned. The guest being x86 does not enter into it. */
int sa2_trap_host_call_precedes(uintptr_t ret) {
#if defined(__x86_64__)
    const uint8_t *b = (const uint8_t *)ret;
    return b[-5] == 0xE8 || b[-2] == 0xFF || b[-3] == 0xFF || b[-6] == 0xFF || b[-7] == 0xFF;
#elif defined(__aarch64__)
    if (ret & 3u) return 0;
    uint32_t insn;
    memcpy(&insn, (const void *)(ret - 4u), 4);
    return (insn & 0xFC000000u) == 0x94000000u      /* bl imm26 */
        || (insn & 0xFFFFFC1Fu) == 0xD63F0000u;     /* blr xn */
#endif
}

#if !defined(_WIN32)
static void on_fault(int sig, siginfo_t *si, void *uc) {
    (void)sig;
    if (!g_window && g_reporting) {
        static const char unreadable[] = " callers unreadable\n";
        ssize_t ignored = write(2, unreadable, sizeof unreadable - 1);
        (void)ignored;
        _exit(3);
    }
    if (!g_window) {
        /* The address and the host thread, formatted by hand: nothing here may
         * call stdio. A stage run died this way with neither, and the message
         * alone could not say which thread was outside its call (2026-09-23). */
        char msg[320] = "sa2 runtime: fault outside any sa2_call at host 0x";
        size_t n = strlen(msg);
        uintptr_t v = (uintptr_t)si->si_addr;
        for (int sh = (int)sizeof v * 8 - 4; sh >= 0; sh -= 4)
            msg[n++] = "0123456789abcdef"[(v >> sh) & 15u];
        const char rip[] = SA2_PC_NAME;
        memcpy(msg + n, rip, sizeof rip - 1);
        n += sizeof rip - 1;
        v = uc_pc(uc);
        for (int sh = (int)sizeof v * 8 - 4; sh >= 0; sh -= 4)
            msg[n++] = "0123456789abcdef"[(v >> sh) & 15u];
#if defined(__aarch64__)
        /* A leaf function's caller is in the link register only, never on
         * the stack the scan below reads. */
        const char lr[] = " lr 0x";
        memcpy(msg + n, lr, sizeof lr - 1);
        n += sizeof lr - 1;
        v = (uintptr_t)((ucontext_t *)uc)->uc_mcontext.regs[30];
        for (int sh = (int)sizeof v * 8 - 4; sh >= 0; sh -= 4)
            msg[n++] = "0123456789abcdef"[(v >> sh) & 15u];
#endif
        const char base[] = " image 0x";
        memcpy(msg + n, base, sizeof base - 1);
        n += sizeof base - 1;
        extern char __ehdr_start;       /* the binary's own load address */
        v = (uintptr_t)&__ehdr_start;
        for (int sh = (int)sizeof v * 8 - 4; sh >= 0; sh -= 4)
            msg[n++] = "0123456789abcdef"[(v >> sh) & 15u];
        const char tid[] = " thread ";
        memcpy(msg + n, tid, sizeof tid - 1);
        n += sizeof tid - 1;
        long t = (long)syscall(SYS_gettid);
        char d[24];
        int k = 0;
        do { d[k++] = (char)('0' + t % 10); t /= 10; } while (t && k < 20);
        while (k) msg[n++] = d[--k];
        /* And its name: the runtime's own threads are named (the stall watch is "sa2-stall"), and a
         * guest thread carries the process's. */
        char comm[17] = {0};
        if (prctl(PR_GET_NAME, comm, 0, 0, 0) == 0 && comm[0]) {
            msg[n++] = ' ';
            msg[n++] = '(';
            for (int c = 0; c < 16 && comm[c]; c++) msg[n++] = comm[c];
            msg[n++] = ')';
        }
        ssize_t ignored = write(2, msg, n);     /* before the scan, which can fault */
        (void)ignored;
        /* The words on the host stack that follow a call instruction in the
         * binary's text: the return addresses a debugger would have given,
         * which the build has none of. A fault in the report writer's string
         * routine said where it was and not who called it (2026-09-23). The
         * call test keeps out pointers to .rodata, which the same range holds. */
        extern char etext;
        const uintptr_t *sp = (const uintptr_t *)uc_sp(uc);
        n = 0;
        const char callers[] = " callers";
        memcpy(msg + n, callers, sizeof callers - 1);
        n += sizeof callers - 1;
        g_reporting = 1;
        for (int w = 0, found = 0; w < 96 && found < 6; w++) {
            v = sp[w];
            if (v < (uintptr_t)&__ehdr_start + 8u || v >= (uintptr_t)&etext) continue;
            if (!sa2_trap_host_call_precedes(v))
                continue;
            msg[n++] = ' ';
            for (int sh = 28; sh >= 0; sh -= 4) msg[n++] = "0123456789abcdef"[(v >> sh) & 15u];
            found++;
        }
        msg[n++] = '\n';
        ignored = write(2, msg, n);
        (void)ignored;
        _exit(3);
    }
    /* `si_addr` is a *host* address and must never reach `exit_pc`, which every consumer
     * reads as a guest address -- `close_traps.py` lifts any value of it that lands in the
     * image range, so the low 32 bits of a host pointer could have been handed to the
     * lifter as guest code (G2 finding 7). The status is distinct, `exit_pc` is 0, and the
     * host address is carried in its own full-width field and in the note. The
     * bounds-checked intrinsics report genuine guest addresses under SA2_TRAP_FAULT. */
    g_trap.status = SA2_TRAP_HOST_FAULT;
    g_trap.exit_pc = 0;
    g_trap.host_fault_addr = (uint64_t)(uintptr_t)si->si_addr;
    snprintf(g_trap.note, sizeof g_trap.note,
             "host signal %d at host address %p (not a guest address)", sig, si->si_addr);
    g_window = 0;
    siglongjmp(g_jb, 1);
}

static void on_alarm(int sig) {
    (void)sig;
    if (!g_window) _exit(3);
    g_trap.status = SA2_TRAP_TIMEOUT;
    g_trap.exit_pc = 0;
    snprintf(g_trap.note, sizeof g_trap.note, "call exceeded its wall-clock budget");
    g_window = 0;
    siglongjmp(g_jb, 1);
}

/* A signal stack for the calling thread. sigaltstack is per thread, and the
 * one installed with the handlers is the main thread's alone: a guest thread
 * whose stack overflowed died with no report at all (the rule-5 review,
 * 2026-09-23). Called at the start of every guest host thread, and freed by
 * sa2_trap_thread_fini as the thread ends: rows are reused since 2026-10-02,
 * so a stack kept for the life of the process would grow it by 64 KiB per
 * thread with no bound (the 2026-10-02 review, S3). */
static _Thread_local void *g_altstack;

void sa2_trap_thread_init(void) {
    const size_t size = 1u << 16;
    void *p = malloc(size);
    if (!p) return;
    stack_t ss = {p, 0, size};
    if (sigaltstack(&ss, NULL) != 0) { free(p); return; }
    g_altstack = p;
}

void sa2_trap_thread_fini(void) {
    if (!g_altstack) return;
    /* Disabled first: the kernel must not deliver a signal onto freed memory.
     * Not on it now (this is no handler), so the call cannot fail with EPERM;
     * if it fails anyway, the stack is left installed rather than freed. */
    stack_t off;
    memset(&off, 0, sizeof off);
    off.ss_flags = SS_DISABLE;
    if (sigaltstack(&off, NULL) != 0) return;
    free(g_altstack);
    g_altstack = NULL;
}

void sa2_trap_install_handlers(void) {
    static uint8_t altstack[1 << 16];
    stack_t ss = {altstack, 0, sizeof altstack};
    sigaltstack(&ss, NULL);
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_fault;
    sa.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_NODEFER;
    sigaction(SIGSEGV, &sa, NULL);
    sigaction(SIGBUS, &sa, NULL);
    sigaction(SIGFPE, &sa, NULL);
    signal(SIGALRM, on_alarm);
}

#else /* _WIN32: the Windows backend */

/* The Linux backend's handlers, for an x86-64 Windows host. Same statuses, the same notes
 * where the facts are the same, and the same unwind to the innermost sa2_call.
 *
 * A fault. A vectored exception handler sees it first, and takes the fault the Linux
 * backend's SIGSEGV, SIGBUS or SIGFPE handler would have: an access violation, a page-in
 * error, a stack overflow or misalignment, an integer divide or overflow, and an unmasked
 * floating-point exception. Inside a call window it takes every such fault, wherever the
 * faulting instruction is, as on_fault does: a fault in a DLL the runtime called (the
 * CRT's string routines are ucrtbase's here, not the binary's own as musl's are) cannot be
 * left to the frame-based search, because that search unwinds through lifted code, and the
 * lifted objects carry no unwind data for about a quarter of their functions (5,613 of
 * 24,036 have no .pdata entry, 2026-10-11): it reads a stray word as a return address,
 * stops, and the process ends with no report at all. Outside a window it takes a fault
 * only when the faulting instruction is in this binary (lifted code and the runtime, which
 * install no structured exception handler of their own); a fault in a system DLL is left to
 * that DLL's own handler, and one nobody handles reaches the unhandled-exception filter,
 * which treats it the same way. Inside a window the handler records the trap and resumes
 * the thread at a landing that unwinds with siglongjmp (sa2_win_longjmp): the unwind runs
 * as ordinary code, after the exception dispatch has finished, never from inside it.
 * Outside a window it prints the Linux backend's report (host address, rip, image base,
 * thread, callers) and exits 3.
 *
 * A landing starts from the x87 and SSE state a Linux signal handler starts from (fninit,
 * control word 0x037F, MXCSR 0x1F80, the direction flag clear): a redirect changes only rip
 * and rsp, so without it the unwound call would carry the faulting code's x87 stack,
 * rounding and exception masks, and a set direction flag, into the code after sa2_call.
 *
 * A timeout. alarm() arms a watchdog thread; at the deadline it suspends the thread that
 * armed it and resumes it at the timeout landing, as SIGALRM's handler unwinds the thread
 * it interrupts. Linux delivers SIGALRM to the main thread, which every guest worker
 * blocks it from (sa2_threads.c); here the deadline belongs to the thread that set it,
 * which is the thread sa2_call bounds. A thread suspended inside a kernel wait takes the
 * landing only when the wait returns, where SIGALRM would interrupt the wait: the main
 * thread parked on a condition while a guest worker runs would leave the bound unenforced.
 * So the landing reports that it ran, and a thread that has not landed within
 * SA2_WD_GRACE_MS of the redirect ends the process: the watchdog writes the timeout's note
 * and exits SA2_TRAP_EXIT_TIMEOUT_STUCK (sa2_trap.h). */

/* sigsetjmp and siglongjmp on this host (sa2_win32.h): the Windows x64 nonvolatile
 * registers, the stack pointer past the return address, and the return address. The buffer
 * is sa2_win_jmp_buf: words 0-7 rbx rbp rsi rdi r12 r13 r14 r15, 8 rsp, 9 rip, xmm6-xmm15
 * from byte 96, 16-aligned. setjmp is a leaf that changes no register it must preserve, so it
 * needs no unwind data; longjmp never returns. */
__asm__(
    "    .text\n"
    "    .globl sa2_win_setjmp\n"
    "    .def sa2_win_setjmp; .scl 2; .type 32; .endef\n"
    "    .p2align 4\n"
    "sa2_win_setjmp:\n"
    "    movq %rbx, 0(%rcx)\n"
    "    movq %rbp, 8(%rcx)\n"
    "    movq %rsi, 16(%rcx)\n"
    "    movq %rdi, 24(%rcx)\n"
    "    movq %r12, 32(%rcx)\n"
    "    movq %r13, 40(%rcx)\n"
    "    movq %r14, 48(%rcx)\n"
    "    movq %r15, 56(%rcx)\n"
    "    leaq 8(%rsp), %rax\n"
    "    movq %rax, 64(%rcx)\n"
    "    movq (%rsp), %rax\n"
    "    movq %rax, 72(%rcx)\n"
    "    movdqa %xmm6, 96(%rcx)\n"
    "    movdqa %xmm7, 112(%rcx)\n"
    "    movdqa %xmm8, 128(%rcx)\n"
    "    movdqa %xmm9, 144(%rcx)\n"
    "    movdqa %xmm10, 160(%rcx)\n"
    "    movdqa %xmm11, 176(%rcx)\n"
    "    movdqa %xmm12, 192(%rcx)\n"
    "    movdqa %xmm13, 208(%rcx)\n"
    "    movdqa %xmm14, 224(%rcx)\n"
    "    movdqa %xmm15, 240(%rcx)\n"
    "    xorl %eax, %eax\n"
    "    ret\n"
    "    .globl sa2_win_longjmp\n"
    "    .def sa2_win_longjmp; .scl 2; .type 32; .endef\n"
    "    .p2align 4\n"
    "sa2_win_longjmp:\n"
    "    movl %edx, %eax\n"
    "    testl %eax, %eax\n"
    "    jnz 1f\n"
    "    movl $1, %eax\n"
    "1:\n"
    "    movq 0(%rcx), %rbx\n"
    "    movq 8(%rcx), %rbp\n"
    "    movq 16(%rcx), %rsi\n"
    "    movq 24(%rcx), %rdi\n"
    "    movq 32(%rcx), %r12\n"
    "    movq 40(%rcx), %r13\n"
    "    movq 48(%rcx), %r14\n"
    "    movq 56(%rcx), %r15\n"
    "    movdqa 96(%rcx), %xmm6\n"
    "    movdqa 112(%rcx), %xmm7\n"
    "    movdqa 128(%rcx), %xmm8\n"
    "    movdqa 144(%rcx), %xmm9\n"
    "    movdqa 160(%rcx), %xmm10\n"
    "    movdqa 176(%rcx), %xmm11\n"
    "    movdqa 192(%rcx), %xmm12\n"
    "    movdqa 208(%rcx), %xmm13\n"
    "    movdqa 224(%rcx), %xmm14\n"
    "    movdqa 240(%rcx), %xmm15\n"
    "    movq 64(%rcx), %rsp\n"
    "    jmpq *72(%rcx)\n");

static _Thread_local int g_overflowed;      /* a stack overflow's guard page is to be restored */

/* After an unwind past a stack overflow: the guard page the overflow consumed is put back
 * before the next call, or a second overflow on this thread would end the process with no
 * report. sa2_trap_clear, which every sa2_call runs before its setjmp, is the place: the
 * stack is unwound by then. */
static void win_after_unwind(void) {
    if (!g_overflowed) return;
    g_overflowed = 0;
    _resetstkoflw();
}

/* The binary's own range and the end of its code, from its PE headers: what the Linux
 * backend reads from __ehdr_start and etext. */
static void win_image(uintptr_t *base, uintptr_t *end, uintptr_t *text_end) {
    const uint8_t *b = (const uint8_t *)GetModuleHandleW(NULL);
    const IMAGE_DOS_HEADER *dos = (const IMAGE_DOS_HEADER *)b;
    const IMAGE_NT_HEADERS64 *nt = (const IMAGE_NT_HEADERS64 *)(b + dos->e_lfanew);
    const IMAGE_SECTION_HEADER *sec = IMAGE_FIRST_SECTION(nt);
    uintptr_t hi = 0;
    for (unsigned i = 0; i < nt->FileHeader.NumberOfSections; i++) {
        if (!(sec[i].Characteristics & IMAGE_SCN_CNT_CODE)) continue;
        const uintptr_t z = (uintptr_t)b + sec[i].VirtualAddress + sec[i].Misc.VirtualSize;
        if (z > hi) hi = z;
    }
    *base = (uintptr_t)b;
    *end = (uintptr_t)b + nt->OptionalHeader.SizeOfImage;
    *text_end = hi;
}

/* The POSIX signal the Linux backend would have been sent for this exception, or 0 for an
 * exception it would not have handled (SIGILL's, a breakpoint, a C++ throw...). */
static int win_signal_of(DWORD code) {
    switch (code) {
    case EXCEPTION_ACCESS_VIOLATION:
    case EXCEPTION_IN_PAGE_ERROR:
    case EXCEPTION_STACK_OVERFLOW:
    case EXCEPTION_ARRAY_BOUNDS_EXCEEDED:
        return 11;      /* SIGSEGV */
    case EXCEPTION_DATATYPE_MISALIGNMENT:
        return 7;       /* SIGBUS */
    case EXCEPTION_INT_DIVIDE_BY_ZERO:
    case EXCEPTION_INT_OVERFLOW:
    case EXCEPTION_FLT_DENORMAL_OPERAND:
    case EXCEPTION_FLT_DIVIDE_BY_ZERO:
    case EXCEPTION_FLT_INEXACT_RESULT:
    case EXCEPTION_FLT_INVALID_OPERATION:
    case EXCEPTION_FLT_OVERFLOW:
    case EXCEPTION_FLT_STACK_CHECK:
    case EXCEPTION_FLT_UNDERFLOW:
    case 0xC00002B4u:   /* STATUS_FLOAT_MULTIPLE_FAULTS */
    case 0xC00002B5u:   /* STATUS_FLOAT_MULTIPLE_TRAPS */
        return 8;       /* SIGFPE */
    default:
        return 0;
    }
}

/* Resume the thread at `landing`, on its own stack below what the interrupted code had
 * live, aligned as a call would leave it. */
static void win_redirect(CONTEXT *ctx, void (*landing)(void)) {
    uintptr_t sp = (uintptr_t)ctx->Rsp - 128u;
    sp &= ~(uintptr_t)15u;
    sp -= 8u;
    ctx->Rsp = (DWORD64)sp;
    ctx->Rip = (DWORD64)(uintptr_t)landing;
}

/* The floating-point and string state a Linux signal handler starts with: the x87 reset
 * (empty stack, exceptions masked, control word 0x037F, 64-bit precision), the SSE control
 * at its default (0x1F80: every exception masked, round to nearest, no flush to zero), and
 * the direction flag clear. Each landing runs it first, before any code that relies on it. */
static inline void win_landing_fp_reset(void) {
    static const unsigned short cw = 0x037Fu;
    static const unsigned int mxcsr = 0x1F80u;
    __asm__ volatile("fninit\n\tfldcw %0\n\tldmxcsr %1\n\tcld" : : "m"(cw), "m"(mxcsr) : "memory");
}

__attribute__((noreturn)) static void win_fault_landing(void) {
    win_landing_fp_reset();
    g_window = 0;
    siglongjmp(g_jb, 1);
}

static HANDLE g_wd_landed;              /* set by the timeout landing (the watchdog's grace) */

/* on_alarm's body, run by the timed-out thread itself. */
__attribute__((noreturn)) static void win_timeout_landing(void) {
    win_landing_fp_reset();
    SetEvent(g_wd_landed);
    g_trap.status = SA2_TRAP_TIMEOUT;
    g_trap.exit_pc = 0;
    snprintf(g_trap.note, sizeof g_trap.note, "call exceeded its wall-clock budget");
    g_window = 0;
    siglongjmp(g_jb, 1);
}

static void win_write(const char *s, size_t n) {
    int ignored = _write(2, s, (unsigned)n);
    (void)ignored;
}

/* The Linux backend's report of a fault outside any call: host address, rip, image base
 * and thread, then the callers the stack scan finds. */
__attribute__((noreturn)) static void win_report_outside(uintptr_t addr, const CONTEXT *ctx) {
    uintptr_t base, end, text_end;
    win_image(&base, &end, &text_end);
    char msg[320];
    int n = snprintf(msg, sizeof msg, "sa2 runtime: fault outside any sa2_call at host 0x%016llx"
                     " rip 0x%016llx image 0x%016llx thread %lu\n", (unsigned long long)addr,
                     (unsigned long long)ctx->Rip, (unsigned long long)base,
                     (unsigned long)GetCurrentThreadId());
    win_write(msg, n > 0 ? (size_t)n : 0u);         /* before the scan, which can fault */
    const uintptr_t *sp = (const uintptr_t *)(uintptr_t)ctx->Rsp;
    size_t k = 0;
    const char callers[] = " callers";
    memcpy(msg, callers, sizeof callers - 1);
    k += sizeof callers - 1;
    g_reporting = 1;
    for (int w = 0, found = 0; w < 96 && found < 6; w++) {
        const uintptr_t v = sp[w];
        if (v < base + 8u || v >= text_end) continue;
        if (!sa2_trap_host_call_precedes(v)) continue;
        msg[k++] = ' ';
        /* All sixteen digits: the image sits above 4 GiB here (0x140000000). */
        for (int sh = 60; sh >= 0; sh -= 4) msg[k++] = "0123456789abcdef"[(v >> sh) & 15u];
        found++;
    }
    msg[k++] = '\n';
    win_write(msg, k);
    _exit(3);
}

static LONG win_fault(EXCEPTION_POINTERS *ep, int first_chance) {
    const EXCEPTION_RECORD *er = ep->ExceptionRecord;
    const DWORD code = er->ExceptionCode;
    const int sig = win_signal_of(code);
    if (!sig) return EXCEPTION_CONTINUE_SEARCH;
    CONTEXT *ctx = ep->ContextRecord;
    if (first_chance && !g_window) {
        uintptr_t base, end, text_end;
        win_image(&base, &end, &text_end);
        if ((uintptr_t)ctx->Rip < base || (uintptr_t)ctx->Rip >= end)
            return EXCEPTION_CONTINUE_SEARCH;       /* a DLL's: its own handler first */
    }
    if (!g_window && g_reporting) {
        static const char unreadable[] = " callers unreadable\n";
        win_write(unreadable, sizeof unreadable - 1);
        _exit(3);
    }
    const uintptr_t addr = ((code == EXCEPTION_ACCESS_VIOLATION || code == EXCEPTION_IN_PAGE_ERROR)
                            && er->NumberParameters >= 2)
                               ? (uintptr_t)er->ExceptionInformation[1]
                               : (uintptr_t)er->ExceptionAddress;
    if (!g_window) win_report_outside(addr, ctx);
    /* As the Linux backend: a host address never reaches exit_pc (G2 finding 7). */
    g_trap.status = SA2_TRAP_HOST_FAULT;
    g_trap.exit_pc = 0;
    g_trap.host_fault_addr = (uint64_t)addr;
    /* The Linux note, with the exception code and the faulting rip beside the signal: no
     * debugger reads this host's binary, and the rip names the host function (win_link.py
     * --map). */
    snprintf(g_trap.note, sizeof g_trap.note,
             "host signal %d (exception %#lx, rip %#llx) at host address %p (not a guest "
             "address)", sig, (unsigned long)code, (unsigned long long)ctx->Rip, (void *)addr);
    if (code == EXCEPTION_STACK_OVERFLOW) g_overflowed = 1;
    win_redirect(ctx, win_fault_landing);
    return EXCEPTION_CONTINUE_EXECUTION;
}

static LONG CALLBACK win_vectored(EXCEPTION_POINTERS *ep) { return win_fault(ep, 1); }
static LONG WINAPI win_unhandled(EXCEPTION_POINTERS *ep) { return win_fault(ep, 0); }

/* ------------------------------------------------------------ the watchdog */

static INIT_ONCE         g_wd_once = INIT_ONCE_STATIC_INIT;
static CRITICAL_SECTION  g_wd_lock;
static HANDLE            g_wd_wake;
static HANDLE            g_wd_target;       /* the thread that armed it */
static DWORD             g_wd_target_id;    /* and its id, for the stuck thread's note */
static volatile int     *g_wd_window;       /* that thread's g_window */
static ULONGLONG         g_wd_deadline;     /* GetTickCount64 ms; 0 when disarmed */

/* How long a redirected thread has to reach the timeout landing before the watchdog ends the
 * process (the backend's comment above): a thread running code lands at once, and one in a
 * kernel wait that ends within this lands late but lands. */
#define SA2_WD_GRACE_MS 2000u

/* With g_wd_lock held. The redirected thread's id, or 0 when nothing was redirected. */
static DWORD wd_fire(void) {
    HANDLE t = g_wd_target;
    if (!t || SuspendThread(t) == (DWORD)-1) return 0;
    CONTEXT ctx;
    memset(&ctx, 0, sizeof ctx);
    ctx.ContextFlags = CONTEXT_CONTROL;
    if (!GetThreadContext(t, &ctx)) {
        ResumeThread(t);
        return 0;
    }
    if (!*g_wd_window) _exit(3);        /* on_alarm outside a window */
    win_redirect(&ctx, win_timeout_landing);
    ResetEvent(g_wd_landed);
    if (!SetThreadContext(t, &ctx)) {
        ResumeThread(t);
        return 0;
    }
    ResumeThread(t);
    return g_wd_target_id;
}

/* The timed-out thread did not reach its landing within the grace: it is in a kernel wait,
 * where a context change takes effect only when the wait returns. The trap's note, with the
 * thread and why, and the process ends: the bound holds, as SIGALRM's would, without the
 * unwind that this host cannot make. */
__attribute__((noreturn)) static void wd_stuck(DWORD thread) {
    char msg[320];
    const int n = snprintf(msg, sizeof msg, "sa2 runtime: call exceeded its wall-clock budget; "
                           "thread %lu did not reach the timeout's landing within %u ms (a "
                           "kernel wait that has not returned), so it cannot be unwound: exit "
                           "%d\n", (unsigned long)thread, SA2_WD_GRACE_MS,
                           SA2_TRAP_EXIT_TIMEOUT_STUCK);
    win_write(msg, n > 0 ? (size_t)n : 0u);
    _exit(SA2_TRAP_EXIT_TIMEOUT_STUCK);
}

static DWORD WINAPI wd_main(LPVOID arg) {
    (void)arg;
    for (;;) {
        EnterCriticalSection(&g_wd_lock);
        const ULONGLONG now = GetTickCount64(), dl = g_wd_deadline;
        if (dl && dl <= now) {
            g_wd_deadline = 0;
            const DWORD fired = wd_fire();
            LeaveCriticalSection(&g_wd_lock);
            /* Outside the lock: the landed thread's sa2_call calls alarm(0) next. */
            if (fired && WaitForSingleObject(g_wd_landed, SA2_WD_GRACE_MS) != WAIT_OBJECT_0)
                wd_stuck(fired);
            continue;
        }
        const DWORD wait = !dl ? INFINITE
                         : (dl - now > 0xFFFFFFFEull ? 0xFFFFFFFEu : (DWORD)(dl - now));
        LeaveCriticalSection(&g_wd_lock);
        WaitForSingleObject(g_wd_wake, wait);
    }
    return 0;
}

static BOOL CALLBACK wd_init(PINIT_ONCE once, PVOID param, PVOID *ctx) {
    (void)once; (void)param; (void)ctx;
    InitializeCriticalSection(&g_wd_lock);
    g_wd_wake = CreateEventW(NULL, FALSE, FALSE, NULL);
    g_wd_landed = CreateEventW(NULL, FALSE, FALSE, NULL);
    if (!g_wd_wake || !g_wd_landed) return FALSE;
    HANDLE th = CreateThread(NULL, 1u << 16, wd_main, NULL, 0, NULL);
    if (!th) return FALSE;
    CloseHandle(th);
    return TRUE;
}

unsigned int alarm(unsigned int seconds) {
    if (!InitOnceExecuteOnce(&g_wd_once, wd_init, NULL, NULL)) {
        static const char none[] = "sa2 runtime: no watchdog thread for alarm()\n";
        win_write(none, sizeof none - 1);
        _exit(3);
    }
    EnterCriticalSection(&g_wd_lock);
    const ULONGLONG now = GetTickCount64();
    const unsigned left = g_wd_deadline > now ? (unsigned)((g_wd_deadline - now + 999u) / 1000u) : 0u;
    if (g_wd_target) CloseHandle(g_wd_target);
    g_wd_target = NULL;
    g_wd_deadline = 0;
    if (seconds) {
        g_wd_target = OpenThread(THREAD_SUSPEND_RESUME | THREAD_GET_CONTEXT | THREAD_SET_CONTEXT,
                                 FALSE, GetCurrentThreadId());
        g_wd_window = &g_window;
        g_wd_target_id = GetCurrentThreadId();
        if (g_wd_target) g_wd_deadline = now + (ULONGLONG)seconds * 1000u;
    }
    LeaveCriticalSection(&g_wd_lock);
    SetEvent(g_wd_wake);
    return left;
}

/* ------------------------------------------------------------ installation */

/* The guarantee is the room the handlers have on a thread whose stack overflowed: the
 * 64 KiB the Linux backend's signal stacks have.
 *
 * The x87 precision control too. Remill's x87 semantics compute in the host's long double,
 * on the host's x87, and tools/spike/pc24pass rounds to the guest's precision on top of a
 * host that rounds to 64 bits, as Linux starts every thread (control word 0x037F). Windows
 * starts a thread at 53 bits (0x027F); Wine and mingw's startup were measured at 0x037F
 * (2026-10-11), but a native Windows thread is not promised it, so every thread that runs
 * lifted code sets the precision field to 64 bits here and leaves the rest. */
void sa2_trap_thread_init(void) {
    ULONG guarantee = 1u << 16;
    SetThreadStackGuarantee(&guarantee);
    unsigned short cw;
    __asm__ volatile("fnstcw %0" : "=m"(cw));
    cw = (unsigned short)(cw | 0x0300u);
    __asm__ volatile("fldcw %0" : : "m"(cw));
}

void sa2_trap_thread_fini(void) {}

void sa2_trap_install_handlers(void) {
    static int installed;
    if (!installed) {
        installed = 1;
        AddVectoredExceptionHandler(1, win_vectored);
        SetUnhandledExceptionFilter(win_unhandled);
    }
    sa2_trap_thread_init();
}
#endif /* _WIN32 */
