/* The stall watch's unit probe (runtime/sa2_stall.h) and the runtime's nudge (runtime/sa2_threads.c,
 * SIGURG), after the AYN Thor's stall at the SEGA logo (2026-10-17).
 *
 * runtime/sa2_stall.c, runtime/sa2_threads.c and runtime/sa2_trap.c, the production files, linked
 * with stand-ins for the rest of the runtime (a virtual clock, flat guest memory, no file layer):
 *
 *   zig cc -target x86_64-linux-musl -O2 -I runtime harness/stall_watch_probe.c \
 *          runtime/sa2_stall.c runtime/sa2_threads.c runtime/sa2_trap.c -o probe
 *   ./probe format       -> the table of a fixed snapshot on stdout, then the same cut to 420 bytes,
 *                           then the snapshot with its writer waiting for the player
 *   ./probe watch        -> one JSON object on stdout; the watch's table on stderr
 *   ./probe player fifo|pace WAIT_MS DIR  -> the same, the writer in the recorder's wait on the player
 *   ./probe episodes WAIT_MS              -> the same, over two stalls with a frame between them
 *
 * format  sa2_stall_format over two live rows, a finished one and a file read in progress, whole
 *         and cut short; then with the running row in the recorder's write of the stream FIFO.
 * watch   SA2_STALL_DUMP_MS from the environment. A host thread takes guest thread 0's row
 *         (sa2_sched_init), crosses one frame boundary, and then writes 100 bytes into a pipe
 *         already full, so it sleeps in pipe_write holding the guest token, as the recorder's
 *         Present did on the Thor. The main thread starts the watch, waits WAIT_MS (argv[2],
 *         default 1000), sends itself SIGURG (the relay's nudge, thread-directed so the handler
 *         runs on a thread that is not the token's holder and must pass it on), then drains
 *         the pipe. The JSON: the nudges the runtime took (2: the main thread's and the one it
 *         passed on), the writer's host tid, and what its write returned (100: restarted by
 *         SA_RESTART, not cut short by EINTR).
 * player  The recorder's two waits on the player, as a paused player leaves the game in them: with
 *         `fifo`, the writer's full pipe is a FIFO made at DIR/game.fifo and named by SA2_REC_TRACE;
 *         with `pace`, the writer reads the read end of an empty pipe whose descriptor
 *         SA2_REC_PACE_FD names. The main thread sets the setting, starts the watch, waits WAIT_MS
 *         and lets the writer go (drains the FIFO, or writes the pace byte).
 * episodes Two stalls: the writer sleeps in its write, the main thread drains the pipe after
 *         WAIT_MS and fills it again, the writer crosses a second frame boundary and sleeps in a
 *         second write, drained after WAIT_MS more. The watch's tables: one a stall.
 *
 * Documentation/scripts/tests/test_stall_watch.py builds it and holds the text and the run. */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "sa2_call.h"
#include "sa2_files.h"
#include "sa2_stall.h"
#include "sa2_threads.h"
#include "sa2_trap.h"

/* ------------------------------------------------------------- stand-ins */

static uint64_t g_clock_us;
uint64_t sa2_virtual_us(void) { return g_clock_us; }
void sa2_virtual_advance_to_us(uint64_t us) { if (us > g_clock_us) g_clock_us = us; }
uint32_t sa2_import_calls(void) { return 0; }
int sa2_event_signal(uint32_t h, int pulse) { (void)h; (void)pulse; return 1; }
uint32_t sa2_fls_callback(uint32_t i) { (void)i; return 0; }
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
void sa2_call_thread_fini(void) {}
int sa2_call(uint32_t va, sa2_cpu_state_t *st, uint32_t timeout_s, sa2_call_result_t *r) {
    (void)va; (void)st; (void)timeout_s;
    memset(r, 0, sizeof *r);
    r->status = SA2_TRAP_NONE;
    return 0;
}
/* The file layer: nothing in progress. */
int sa2_vfs_io_now(int *write, uint32_t *tid, const char **path, uint64_t *bytes, int64_t *since_ns) {
    (void)write; (void)tid; (void)path; (void)bytes; (void)since_ns;
    return 0;
}

/* ----------------------------------------------------------------- format */

static int format_case(void) {
    static sa2_stall_snapshot s;
    memset(&s, 0, sizeof s);
    s.no_frame_s = 3.25;
    s.frames = 237;
    s.virtual_us = 3950012;
    s.current = 0;
    s.lock_held = 1;
    s.nudges = 2;
    s.rows = 3;
    sa2_stall_row *a = &s.row[0], *b = &s.row[1], *c = &s.row[2];
    c->index = 9; c->host_tid = 4260; c->start_va = 0x008653d0u; c->state = "finished"; c->wait = "not-blocked";
    a->index = 0; a->host_tid = 4242; a->start_va = 0; a->state = "running"; a->suspend_count = 0;
    a->wait = "not-blocked"; a->where = "frame"; a->last_caller = 0x00425da0u;
    a->guest_calls = 123456; a->blocks = 300; a->wakes = 300; a->kstate = 'S'; a->last_cpu = 7;
    snprintf(a->wchan, sizeof a->wchan, "pipe_write");
    snprintf(a->call, sizeof a->call, "write(5 game.fifo, 1120 bytes)");
    snprintf(a->cpus, sizeof a->cpus, "0-7");
    b->index = 3; b->host_tid = 4250; b->start_va = 0x0085c3a0u; b->state = "blocked"; b->suspend_count = 1;
    b->wait = "event"; b->wait_object = 0x108; b->deadline_us = 3966666; b->where = "WaitForSingleObject";
    b->last_caller = 0x0085c4c1u; b->guest_calls = 99; b->blocks = 12; b->wakes = 11; b->kstate = 'S';
    b->last_cpu = 6;
    snprintf(b->wchan, sizeof b->wchan, "futex_wait_queue");
    snprintf(b->cpus, sizeof b->cpus, "7");
    s.io_active = 1; s.io_write = 0; s.io_tid = 4242; s.io_path = "resource/gd_PC/PRS/stg13.prs";
    s.io_bytes = 65536; s.io_for_s = 2.5;
    static char out[8192];
    sa2_stall_format(&s, out, sizeof out);
    fputs(out, stdout);
    puts("--- cut to 420 bytes");
    const size_t n = sa2_stall_format(&s, out, 420);
    fputs(out, stdout);
    printf("--- %zu bytes\n", n);
    /* The running row in the recorder's write of the stream FIFO: the line after the first. */
    a->player_wait = "write of the stream FIFO";
    s.player_row = 0;
    puts("--- waiting for the player");
    sa2_stall_format(&s, out, sizeof out);
    fputs(out, stdout);
    return 0;
}

/* ------------------------------------------------------------------ watch */

static int g_pipe[2];
static volatile uint32_t g_writer_tid;
static volatile long g_wrote = -2;
static volatile int g_write_errno;
static volatile int g_ready;

static void *writer(void *arg) {
    (void)arg;
    g_writer_tid = (uint32_t)syscall(SYS_gettid);
    sa2_sched_init();                      /* guest thread 0, holding the guest lock */
    sa2_sched_frame_boundary();            /* the first frame: the watch is armed */
    __atomic_store_n(&g_ready, 1, __ATOMIC_RELEASE);
    char buf[100];
    memset(buf, 'w', sizeof buf);
    const ssize_t k = write(g_pipe[1], buf, sizeof buf);   /* the pipe is full: it sleeps */
    g_write_errno = k < 0 ? errno : 0;
    g_wrote = (long)k;
    return NULL;
}

static int watch_case(int wait_ms) {
    if (pipe(g_pipe)) { perror("pipe"); return 2; }
    const int size = fcntl(g_pipe[1], F_SETPIPE_SZ, 4096);
    char fill[65536];
    memset(fill, 'f', sizeof fill);
    fcntl(g_pipe[1], F_SETFL, O_NONBLOCK);
    while (write(g_pipe[1], fill, sizeof fill) > 0) {}
    fcntl(g_pipe[1], F_SETFL, 0);          /* the writer's write blocks */
    sa2_threads_config_from_env();         /* the SIGURG handler */
    sa2_stall_start();
    pthread_t t;
    pthread_create(&t, NULL, writer, NULL);
    while (!__atomic_load_n(&g_ready, __ATOMIC_ACQUIRE)) usleep(1000);
    struct timespec w = {wait_ms / 1000, (long)(wait_ms % 1000) * 1000000L};
    nanosleep(&w, NULL);
    /* The relay's nudge, to a thread that is not the token's holder. */
    syscall(SYS_tgkill, (long)getpid(), syscall(SYS_gettid), (long)SIGURG);
    usleep(200 * 1000);
    const long before_drain = g_wrote;
    char sink[65536];
    fcntl(g_pipe[0], F_SETFL, O_NONBLOCK);
    for (int i = 0; i < 1000 && g_wrote == -2; i++) {
        while (read(g_pipe[0], sink, sizeof sink) > 0) {}
        usleep(1000);
    }
    pthread_join(t, NULL);
    sa2_stall_row rows[4];
    int current = -1, held = -1;
    uint64_t nudges = 0;
    const int n = sa2_sched_stall_rows(rows, 4, &current, &held, &nudges);
    printf("{\"pipe_size\": %d, \"writer_tid\": %u, \"wrote_before_drain\": %ld, \"wrote\": %ld, \"write_errno\": %d, "
           "\"nudges\": %llu, \"rows\": %d, \"row0_tid\": %u, \"current\": %d}\n",
           size, g_writer_tid, before_drain, g_wrote, g_write_errno, (unsigned long long)nudges, n,
           n > 0 ? rows[0].host_tid : 0u, current);
    return 0;
}

/* ----------------------------------------------------------------- player */

static int g_pace[2];
static volatile long g_read = -2;

static void *pace_reader(void *arg) {
    (void)arg;
    g_writer_tid = (uint32_t)syscall(SYS_gettid);
    sa2_sched_init();
    sa2_sched_frame_boundary();
    __atomic_store_n(&g_ready, 1, __ATOMIC_RELEASE);
    char b;
    g_read = (long)read(g_pace[0], &b, 1);     /* the pace pipe is empty: it sleeps */
    return NULL;
}

/* A pipe (or the FIFO at `fifo`, opened both ways) whose write end is full: g_pipe. */
static int full_pipe(const char *fifo) {
    if (fifo) {
        unlink(fifo);
        if (mkfifo(fifo, 0600)) { perror("mkfifo"); return -1; }
        g_pipe[0] = open(fifo, O_RDONLY | O_NONBLOCK);
        g_pipe[1] = open(fifo, O_WRONLY);
        if (g_pipe[0] < 0 || g_pipe[1] < 0) { perror("open fifo"); return -1; }
        fcntl(g_pipe[0], F_SETFL, 0);
    } else if (pipe(g_pipe)) {
        perror("pipe");
        return -1;
    }
    const int size = fcntl(g_pipe[1], F_SETPIPE_SZ, 4096);
    char fill[65536];
    memset(fill, 'f', sizeof fill);
    fcntl(g_pipe[1], F_SETFL, O_NONBLOCK);
    while (write(g_pipe[1], fill, sizeof fill) > 0) {}
    fcntl(g_pipe[1], F_SETFL, 0);
    return size;
}

static void drain(volatile long *until_not) {
    char sink[65536];
    fcntl(g_pipe[0], F_SETFL, O_NONBLOCK);
    for (int i = 0; i < 1000 && *until_not == -2; i++) {
        while (read(g_pipe[0], sink, sizeof sink) > 0) {}
        usleep(1000);
    }
    while (read(g_pipe[0], sink, sizeof sink) > 0) {}
    fcntl(g_pipe[0], F_SETFL, 0);
}

static int player_case(const char *kind, int wait_ms, const char *dir) {
    char path[512], fd[16];
    pthread_t t;
    if (!strcmp(kind, "fifo")) {
        snprintf(path, sizeof path, "%s/game.fifo", dir);
        if (full_pipe(path) < 0) return 2;
        setenv("SA2_REC_TRACE", path, 1);
        sa2_stall_start();
        pthread_create(&t, NULL, writer, NULL);
    } else if (!strcmp(kind, "pace")) {
        if (pipe(g_pace)) { perror("pipe"); return 2; }
        snprintf(fd, sizeof fd, "%d", g_pace[0]);
        setenv("SA2_REC_PACE_FD", fd, 1);
        sa2_stall_start();
        pthread_create(&t, NULL, pace_reader, NULL);
    } else {
        return 2;
    }
    while (!__atomic_load_n(&g_ready, __ATOMIC_ACQUIRE)) usleep(1000);
    struct timespec w = {wait_ms / 1000, (long)(wait_ms % 1000) * 1000000L};
    nanosleep(&w, NULL);
    if (!strcmp(kind, "fifo")) drain(&g_wrote);
    else if (write(g_pace[1], "p", 1) != 1) return 2;
    pthread_join(t, NULL);
    printf("{\"writer_tid\": %u, \"wrote\": %ld, \"read\": %ld}\n", g_writer_tid, g_wrote, g_read);
    return 0;
}

/* --------------------------------------------------------------- episodes */

static volatile int g_again;
static volatile long g_wrote2 = -2;

static void *two_stalls(void *arg) {
    (void)arg;
    g_writer_tid = (uint32_t)syscall(SYS_gettid);
    sa2_sched_init();
    sa2_sched_frame_boundary();
    __atomic_store_n(&g_ready, 1, __ATOMIC_RELEASE);
    char buf[100];
    memset(buf, 'w', sizeof buf);
    g_wrote = (long)write(g_pipe[1], buf, sizeof buf);      /* the first stall */
    while (!__atomic_load_n(&g_again, __ATOMIC_ACQUIRE)) usleep(1000);
    sa2_sched_frame_boundary();                             /* frames go on: the stall ends */
    g_wrote2 = (long)write(g_pipe[1], buf, sizeof buf);     /* the second stall */
    return NULL;
}

static int episodes_case(int wait_ms) {
    if (full_pipe(NULL) < 0) return 2;
    sa2_stall_start();
    pthread_t t;
    pthread_create(&t, NULL, two_stalls, NULL);
    while (!__atomic_load_n(&g_ready, __ATOMIC_ACQUIRE)) usleep(1000);
    struct timespec w = {wait_ms / 1000, (long)(wait_ms % 1000) * 1000000L};
    nanosleep(&w, NULL);
    drain(&g_wrote);
    char fill[65536];                                        /* full again for the second write */
    memset(fill, 'f', sizeof fill);
    fcntl(g_pipe[1], F_SETFL, O_NONBLOCK);
    while (write(g_pipe[1], fill, sizeof fill) > 0) {}
    fcntl(g_pipe[1], F_SETFL, 0);
    __atomic_store_n(&g_again, 1, __ATOMIC_RELEASE);
    nanosleep(&w, NULL);
    drain(&g_wrote2);
    pthread_join(t, NULL);
    printf("{\"writer_tid\": %u, \"wrote\": %ld, \"wrote2\": %ld}\n", g_writer_tid, g_wrote, g_wrote2);
    return 0;
}

int main(int argc, char **argv) {
    if (argc >= 2 && !strcmp(argv[1], "format")) return format_case();
    if (argc >= 2 && !strcmp(argv[1], "watch")) return watch_case(argc >= 3 ? atoi(argv[2]) : 1000);
    if (argc == 5 && !strcmp(argv[1], "player")) return player_case(argv[2], atoi(argv[3]), argv[4]);
    if (argc == 3 && !strcmp(argv[1], "episodes")) return episodes_case(atoi(argv[2]));
    fprintf(stderr, "usage: %s format | watch [WAIT_MS] | player fifo|pace WAIT_MS DIR | episodes WAIT_MS\n", argv[0]);
    return 2;
}
