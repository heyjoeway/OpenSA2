/* The stall watch. See sa2_stall.h. */
#define _GNU_SOURCE
#include "sa2_stall.h"

#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#if defined(__linux__)
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>

#include "sa2_files.h"
#include "sa2_threads.h"
#endif

/* ----------------------------------------------------------------- the text */

/* printf into out[*len, cap): the line is kept only when it fits whole, so a table cut short
 * ends at a line's end. */
static void put(char *out, size_t cap, size_t *len, int *cut, const char *fmt, ...)
    __attribute__((format(printf, 5, 6)));
static void put(char *out, size_t cap, size_t *len, int *cut, const char *fmt, ...) {
    if (*cut || *len >= cap) { *cut = 1; return; }
    va_list ap;
    va_start(ap, fmt);
    const int w = vsnprintf(out + *len, cap - *len, fmt, ap);
    va_end(ap);
    if (w < 0 || (size_t)w >= cap - *len) {
        out[*len] = 0;
        *cut = 1;
        return;
    }
    *len += (size_t)w;
}

size_t sa2_stall_format(const sa2_stall_snapshot *s, char *out, size_t cap) {
    if (!cap) return 0;
    size_t len = 0;
    int cut = 0;
    out[0] = 0;
    char cur[48];
    if (s->current >= 0) snprintf(cur, sizeof cur, "guest thread %d", s->current);
    else snprintf(cur, sizeof cur, "no guest thread");
    put(out, cap, &len, &cut,
        "sa2 stall: no frame for %.1f s after frame %" PRIu64 " (virtual %" PRIu64 ".%06" PRIu64 " s); the token "
        "names %s; the guest lock is %s; %" PRIu64 " nudges taken\n",
        s->no_frame_s, s->frames, s->virtual_us / 1000000u, s->virtual_us % 1000000u, cur,
        s->lock_held > 0 ? "held" : s->lock_held == 0 ? "free" : "not looked at", s->nudges);
    if (s->player_row >= 0 && s->player_row < s->rows && s->player_row < SA2_STALL_ROWS &&
        s->row[s->player_row].player_wait) {
        const sa2_stall_row *w = &s->row[s->player_row];
        put(out, cap, &len, &cut,
            "sa2 stall: guest thread %d (host tid %u) is in the recorder's %s: the game waits for the player "
            "to take its frames. A player paused or in the background (an Android app out of view, a window "
            "pause-unfocused holds) looks like this, and frames go on when it reads again; a player that "
            "stopped reading looks the same\n",
            w->index, w->host_tid, w->player_wait);
    }
    put(out, cap, &len, &cut,
        "sa2 stall: row host_tid start      state    susp wait             object     deadline_us where"
        "                last_caller calls blocks wakes | host: state wchan call cpus (last)\n");
    int finished = 0;
    for (int i = 0; i < s->rows && i < SA2_STALL_ROWS; i++) {
        const sa2_stall_row *r = &s->row[i];
        if (r->state && !strcmp(r->state, "finished")) {   /* counted below, not listed */
            finished++;
            continue;
        }
        put(out, cap, &len, &cut,
            "sa2 stall: %3d %8u 0x%08x %-8s %4d %-16s 0x%08x %11" PRIu64 " %-20s 0x%08x %" PRIu64 " %" PRIu64
            " %" PRIu64 " | %c %s%s%s on %s (last %d)\n",
            r->index, r->host_tid, r->start_va, r->state ? r->state : "?", r->suspend_count,
            r->wait ? r->wait : "?", r->wait_object, r->deadline_us,
            r->where && r->where[0] ? r->where : "-", r->last_caller, r->guest_calls, r->blocks, r->wakes,
            r->kstate ? r->kstate : '?', r->wchan[0] ? r->wchan : "?", r->call[0] ? " " : "", r->call,
            r->cpus[0] ? r->cpus : "?", r->last_cpu);
    }
    if (finished) put(out, cap, &len, &cut, "sa2 stall: and %d finished threads' rows\n", finished);
    if (s->io_active)
        put(out, cap, &len, &cut,
            "sa2 stall: vfs: a %s of \"%s\" (%" PRIu64 " bytes) in progress for %.1f s on host tid %u\n",
            s->io_write ? "write" : "read", s->io_path ? s->io_path : "?", s->io_bytes, s->io_for_s,
            s->io_tid);
    else
        put(out, cap, &len, &cut, "sa2 stall: vfs: no read or write in progress\n");
    if (cut) {
        /* The rows that did not fit are said to be missing, in the room the last line left. */
        static const char more[] = "sa2 stall: (the table was cut short)\n";
        if (len + sizeof more <= cap) {
            memcpy(out + len, more, sizeof more);
            len += sizeof more - 1;
        }
    }
    return len;
}

#if defined(__linux__)

/* --------------------------------------------------------- the kernel's view */

static double mono_s(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (double)t.tv_sec + (double)t.tv_nsec / 1e9;
}

/* A small /proc file into buf[n] (NUL-terminated, trailing newline removed); 0 when unread. */
static int slurp(const char *path, char *buf, size_t n) {
    const int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return 0;
    ssize_t k;
    do k = read(fd, buf, n - 1); while (k < 0 && errno == EINTR);
    close(fd);
    if (k <= 0) return 0;
    buf[k] = 0;
    while (k > 0 && (buf[k - 1] == '\n' || buf[k - 1] == ' ')) buf[--k] = 0;
    return 1;
}

/* What descriptor `fd` is: a file's last path component, or "pipe:[ino]". */
static void fd_name(long fd, char *out, size_t n) {
    char link[64], target[256];
    snprintf(link, sizeof link, "/proc/self/fd/%ld", fd);
    const ssize_t k = readlink(link, target, sizeof target - 1);
    if (k <= 0) { snprintf(out, n, "?"); return; }
    target[k] = 0;
    const char *slash = strrchr(target, '/');
    const char *name = slash && slash[1] ? slash + 1 : target;
    const size_t m = strlen(name) < n - 1 ? strlen(name) : n - 1;
    memcpy(out, name, m);
    out[m] = 0;
}

#if defined(__x86_64__)
#define NR_READ 0
#define NR_WRITE 1
#define NR_FUTEX 202
#elif defined(__aarch64__)
#define NR_READ 63
#define NR_WRITE 64
#define NR_FUTEX 98
#else
#define NR_READ (-100)
#define NR_WRITE (-101)
#define NR_FUTEX (-102)
#endif

/* The recorder's two waits on the player (sa2_stall.h): its read of the pace pipe, descriptor
 * SA2_REC_PACE_FD, and its write of the stream FIFO, the file SA2_REC_TRACE names (the same file
 * as the descriptor: device and inode). Read at the watch's start; -1 and NULL when unset. */
static int g_pace_fd = -1;
static const char *g_stream_path;

static const char *player_wait(long nr, unsigned long long fd) {
    if (nr == NR_READ && g_pace_fd >= 0 && fd == (unsigned long long)g_pace_fd) return "read of the pace pipe";
    if (nr == NR_WRITE && g_stream_path && fd <= 0x7fffffffull) {
        struct stat a, b;
        if (fstat((int)fd, &a) == 0 && stat(g_stream_path, &b) == 0 && a.st_dev == b.st_dev && a.st_ino == b.st_ino)
            return "write of the stream FIFO";
    }
    return NULL;
}

static void kernel_view(sa2_stall_row *r) {
    r->kstate = '?';
    r->last_cpu = -1;
    if (!r->host_tid) return;
    char path[96], buf[4096];          /* status is about 1.5 KB */
    snprintf(path, sizeof path, "/proc/self/task/%u/stat", r->host_tid);
    if (slurp(path, buf, sizeof buf)) {
        const char *p = strrchr(buf, ')');
        if (p && p[1] == ' ') {
            r->kstate = p[2];
            /* Field 39 is the CPU it last ran on; the state is field 3. */
            const char *q = p + 2;
            for (int field = 3; *q && field < 39; field++) {
                while (*q && *q != ' ') q++;
                while (*q == ' ') q++;
            }
            if (*q) r->last_cpu = (int)strtol(q, NULL, 10);
        }
    }
    snprintf(path, sizeof path, "/proc/self/task/%u/wchan", r->host_tid);
    if (slurp(path, buf, sizeof buf)) snprintf(r->wchan, sizeof r->wchan, "%.39s", buf);
    snprintf(path, sizeof path, "/proc/self/task/%u/status", r->host_tid);
    if (slurp(path, buf, sizeof buf)) {
        const char *c = strstr(buf, "Cpus_allowed_list:");
        if (c) {
            c += 18;
            while (*c == ' ' || *c == '\t') c++;
            size_t m = strcspn(c, "\n");
            if (m >= sizeof r->cpus) m = sizeof r->cpus - 1;
            memcpy(r->cpus, c, m);
            r->cpus[m] = 0;
        }
    }
    /* "nr arg1 arg2 arg3 ...", in hex after the number; "running" or "-1 ..." outside one. */
    snprintf(path, sizeof path, "/proc/self/task/%u/syscall", r->host_tid);
    if (slurp(path, buf, sizeof buf)) {
        char *end = NULL;
        const long nr = strtol(buf, &end, 10);
        if (end != buf && nr >= 0) {
            const unsigned long long a1 = strtoull(end, &end, 16);
            (void)strtoull(end, &end, 16);
            const unsigned long long a3 = strtoull(end, &end, 16);
            if (nr == NR_WRITE || nr == NR_READ) {
                char name[64];
                fd_name((long)a1, name, sizeof name);
                snprintf(r->call, sizeof r->call, "%s(%llu %s, %llu bytes)", nr == NR_WRITE ? "write" : "read", a1,
                         name, a3);
                r->player_wait = player_wait(nr, a1);
            } else if (nr == NR_FUTEX) {
                snprintf(r->call, sizeof r->call, "futex");       /* a guest thread's wait on the token */
            } else {
                snprintf(r->call, sizeof r->call, "syscall %ld", nr);
            }
        }
    }
}

/* ---------------------------------------------------------------- the watch */

static double g_after_s;
static sa2_stall_snapshot g_snap;      /* the watch thread's alone */
static char g_text[64 * 1024];

static void dump(double no_frame_s) {
    sa2_stall_snapshot *s = &g_snap;
    memset(s, 0, sizeof *s);
    s->no_frame_s = no_frame_s;
    s->frames = sa2_sched_frames();
    s->virtual_us = sa2_virtual_us();
    s->rows = sa2_sched_stall_rows(s->row, SA2_STALL_ROWS, &s->current, &s->lock_held, &s->nudges);
    s->player_row = -1;
    for (int i = 0; i < s->rows; i++) {
        kernel_view(&s->row[i]);
        if (s->row[i].player_wait && (s->player_row < 0 || s->row[i].index == s->current)) s->player_row = i;
    }
    int64_t since_ns = 0;
    s->io_active = sa2_vfs_io_now(&s->io_write, &s->io_tid, &s->io_path, &s->io_bytes, &since_ns);
    if (s->io_active) {
        struct timespec t;
        clock_gettime(CLOCK_MONOTONIC, &t);
        s->io_for_s = (double)((int64_t)t.tv_sec * 1000000000 + t.tv_nsec - since_ns) / 1e9;
    }
    const size_t n = sa2_stall_format(s, g_text, sizeof g_text);
    /* One write, so the table is not interleaved with another thread's line. */
    size_t done = 0;
    while (done < n) {
        const ssize_t k = write(2, g_text + done, n - done);
        if (k < 0 && errno == EINTR) continue;
        if (k <= 0) break;
        done += (size_t)k;
    }
}

/* The watch's own descriptors: a byte on the write end ends it at once (sa2_stall_stop). */
static int       g_wake[2] = {-1, -1};
static pthread_t g_thread;
static int       g_running;            /* started and not yet joined; the main thread's */

/* The signals the watch leaves unblocked: the ones a fault of its own raises. Blocked, any of them
 * ends the process at once with no handler run, so a fault here would be a bare "signal 11" with
 * no line from the runtime (sa2_trap.c on_fault) and, on Android, no tombstone. */
static const int k_fault_signals[] = {SIGSEGV, SIGBUS, SIGFPE, SIGILL, SIGTRAP, SIGSYS, SIGABRT};

static void *watch(void *arg) {
    (void)arg;
    /* No other signal lands here: SIGTERM's and SIGURG's handlers belong to the guest's threads. */
    sigset_t mask;
    sigfillset(&mask);
    for (size_t i = 0; i < sizeof k_fault_signals / sizeof k_fault_signals[0]; i++)
        sigdelset(&mask, k_fault_signals[i]);
    pthread_sigmask(SIG_SETMASK, &mask, NULL);     /* whatever its creator blocked */
    uint64_t last = sa2_sched_frames();
    double since = mono_s();
    int dumped = 0;                    /* this stall's table is out: none more until a frame */
    for (;;) {
        struct pollfd stop = {g_wake[0], POLLIN, 0};
        const int r = poll(&stop, 1, 250);
        if (r > 0) break;              /* sa2_stall_stop */
        if (r < 0 && errno != EINTR) break;
        const uint64_t f = sa2_sched_frames();
        const double now = mono_s();
        if (f != last) {
            last = f;
            since = now;
            dumped = 0;
            continue;
        }
        if (!f) continue;              /* armed at the first frame */
        const double quiet = now - since;
        if (!dumped && quiet >= g_after_s) {
            dump(quiet);
            dumped = 1;
        }
    }
    return NULL;
}

void sa2_stall_stop(void) {
    if (!g_running) return;
    g_running = 0;
    const char byte = 1;
    ssize_t k;
    do k = write(g_wake[1], &byte, 1); while (k < 0 && errno == EINTR);
    if (k == 1) pthread_join(g_thread, NULL);
    else fprintf(stderr, "sa2 stall: the stall watch could not be told to stop (%s); it runs to the end\n",
                 strerror(errno));
}

void sa2_stall_start(void) {
    static int started;
    if (started) return;
    started = 1;
    const char *v = getenv("SA2_STALL_DUMP_MS");
    if (!v || !*v) return;
    char *end = NULL;
    const unsigned long ms = strtoul(v, &end, 10);
    if (*end || !ms || ms > 3600000ul) {
        if (strcmp(v, "0"))
            fprintf(stderr, "sa2 stall: SA2_STALL_DUMP_MS=%s is not a count of milliseconds from 1 to 3600000; "
                    "no stall watch\n", v);
        return;
    }
    g_after_s = (double)ms / 1000.0;
    const char *pf = getenv("SA2_REC_PACE_FD");       /* decimal digits only, as the recorder reads it */
    if (pf && *pf) {
        int n = 0;
        const char *p = pf;
        while (*p >= '0' && *p <= '9' && n <= 65535) n = n * 10 + (*p++ - '0');
        if (!*p && n <= 65535) g_pace_fd = n;
    }
    const char *trace = getenv("SA2_REC_TRACE");
    if (trace && *trace) g_stream_path = trace;
    if (pipe2(g_wake, O_CLOEXEC)) {
        fprintf(stderr, "sa2 stall: the stall watch's stop pipe could not be made: %s; no stall watch\n",
                strerror(errno));
        return;
    }
    pthread_attr_t at;
    pthread_attr_init(&at);
    pthread_attr_setstacksize(&at, 256 * 1024);
    const int rc = pthread_create(&g_thread, &at, watch, NULL);
    pthread_attr_destroy(&at);
    if (rc) {
        fprintf(stderr, "sa2 stall: the stall watch's thread could not start: %s\n", strerror(rc));
        return;
    }
    g_running = 1;
    pthread_setname_np(g_thread, "sa2-stall");
    /* Whatever ends the process by exit(), the watch has ended first (sa2_stall.h). */
    if (atexit(sa2_stall_stop))
        fprintf(stderr, "sa2 stall: atexit refused the stall watch's stop; it is stopped only where the game's "
                "run ends\n");
}

#else

void sa2_stall_start(void) {}
void sa2_stall_stop(void) {}

#endif
