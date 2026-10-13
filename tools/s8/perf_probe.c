/* tools/s8/perf_probe.c: the perf build's observer (tools/s8/perf_game.py).
 *
 * Compiled into a measurement build only, through a unit perf_game.py generates: that unit
 * includes harness/replay/wrap_tick_probe.c with its GameLoop wrap renamed to
 * sa2_perf_probe_gameloop, then this file, whose __wrap_sub_433ee0 runs the tick probe (and so
 * the lifted GameLoop) and then stamps the tick. Two outputs, each off unless its variable is set:
 *
 *   SA2_PERF_TICKS=<path>  one row per GameLoop return, in text:
 *       tick mono_ns cpu_ns game_mode current_level ingame_frames
 *     mono_ns is CLOCK_MONOTONIC, cpu_ns the process's CPU clock (every guest thread is a host
 *     thread and one runs at a time, so it is the game's CPU), both at the return. Buffered and
 *     flushed every 600 ticks and at exit.
 *   SA2_PROF_OUT=<dir>     a SIGPROF sampler on a POSIX timer over the process's CPU clock, one
 *     sample per millisecond of CPU: <dir>/prof-<pid>.bin holds (u64 pc, u64 sp[0], u64 sp[1],
 *     u64 fp[1], u32 tick, u32 tid) per sample in a MAP_SHARED file, so a run that is killed
 *     keeps them: the two words at the stack pointer and the one above the frame pointer, among
 *     which a leaf runtime function's return address is (sp[0] at its entry, sp[1] after its
 *     `push rbp`, fp[1] once it has a frame), so the memory intrinsics' callers can be named. The
 *     build is a static, non-PIE executable, so a pc is the symbol table's address.
 *     SA2_PROF_US moves the period.
 *
 * Neither reads or writes anything the guest can see beyond the tick probe's own reads (tp_read
 * of three globals per tick), and the signal handler touches only its buffer and a counter.
 */
#include <fcntl.h>
#include <signal.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/syscall.h>
#include <ucontext.h>

static FILE *perf_out;
static int perf_off;

static uint64_t perf_ns(clockid_t c) {
    struct timespec ts;
    clock_gettime(c, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void perf_close(void) {
    if (perf_out) fflush(perf_out);
}

void *__wrap_sub_433ee0(void *state, uint32_t pc, void *memory);
void *__wrap_sub_433ee0(void *state, uint32_t pc, void *memory) {
    void *r = sa2_perf_probe_gameloop(state, pc, memory);
    if (!perf_out && !perf_off) {
        const char *path = getenv("SA2_PERF_TICKS");
        if (!path || !*path || !(perf_out = fopen(path, "w"))) {
            perf_off = 1;
        } else {
            setvbuf(perf_out, NULL, _IOFBF, 1 << 20);
            fputs("tick mono_ns cpu_ns game_mode current_level ingame_frames\n", perf_out);
            atexit(perf_close);
        }
    }
    if (perf_out) {
        const unsigned long t = sa2_replay_gameloop_returns();
        fprintf(perf_out, "%lu %llu %llu %u %u %u\n", t,
                (unsigned long long)perf_ns(CLOCK_MONOTONIC),
                (unsigned long long)perf_ns(CLOCK_PROCESS_CPUTIME_ID),
                tp_read(GAME_MODE, 4), tp_read(CURRENT_LEVEL, 2), tp_read(FRAMES_INGAME, 4));
        if (t % 600 == 0) fflush(perf_out);
    }
    return r;
}

/* ------------------------------------------------------------------ the sampler */

#define PROF_CAP (1u << 23)
struct perf_rec { uint64_t pc, top, top1, fp1; uint32_t tick, tid; };
static struct perf_rec *prof_buf;
static volatile uint32_t prof_n;

static void perf_on_prof(int sig, siginfo_t *si, void *ctx) {
    (void)sig; (void)si;
    const int saved = errno;
    uint32_t i = __atomic_fetch_add(&prof_n, 1, __ATOMIC_RELAXED);
    if (i < PROF_CAP) {
        ucontext_t *uc = ctx;
#if defined(__x86_64__)
        prof_buf[i].pc = (uint64_t)uc->uc_mcontext.gregs[REG_RIP];
        const uint64_t *sp = (const uint64_t *)uc->uc_mcontext.gregs[REG_RSP];
        const uint64_t *fp = (const uint64_t *)uc->uc_mcontext.gregs[REG_RBP];
        prof_buf[i].top = sp[0];
        prof_buf[i].top1 = sp[1];
        /* rbp is a frame pointer only in the runtime's own C (zig keeps them); in lifted code it
         * is a general register, so it is read only when it points into this thread's stack */
        prof_buf[i].fp1 = ((uint64_t)fp >= (uint64_t)sp && (uint64_t)fp - (uint64_t)sp < (1u << 20))
                          ? fp[1] : 0;
#elif defined(__aarch64__)
        prof_buf[i].pc = (uint64_t)uc->uc_mcontext.pc;
        prof_buf[i].top = (uint64_t)uc->uc_mcontext.regs[30];      /* the link register */
#else
        prof_buf[i].pc = 0;
#endif
        prof_buf[i].tick = (uint32_t)gameloop_returns;
        prof_buf[i].tid = (uint32_t)syscall(SYS_gettid);
    }
    errno = saved;
}

__attribute__((constructor)) static void perf_prof_start(void) {
    const char *dir = getenv("SA2_PROF_OUT");
    if (!dir || !*dir) return;
    char path[512];
    snprintf(path, sizeof path, "%s/prof-%d.bin", dir, (int)getpid());
    int fd = open(path, O_RDWR | O_CREAT | O_TRUNC, 0644);
    if (fd < 0) return;
    if (ftruncate(fd, (off_t)PROF_CAP * sizeof(struct perf_rec))) { close(fd); return; }
    void *m = mmap(NULL, (size_t)PROF_CAP * sizeof(struct perf_rec), PROT_READ | PROT_WRITE,
                   MAP_SHARED, fd, 0);
    close(fd);
    if (m == MAP_FAILED) return;
    prof_buf = m;
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = perf_on_prof;
    sa.sa_flags = SA_SIGINFO | SA_RESTART;
    sigaction(SIGPROF, &sa, NULL);
    timer_t tm;
    struct sigevent se;
    memset(&se, 0, sizeof se);
    se.sigev_notify = SIGEV_SIGNAL;
    se.sigev_signo = SIGPROF;
    if (timer_create(CLOCK_PROCESS_CPUTIME_ID, &se, &tm)) return;
    const char *us = getenv("SA2_PROF_US");
    long period = us && *us ? strtol(us, NULL, 0) : 1000;
    if (period < 50) period = 50;
    struct itimerspec its = {{period / 1000000, (period % 1000000) * 1000},
                             {period / 1000000, (period % 1000000) * 1000}};
    timer_settime(tm, 0, &its, NULL);
}
