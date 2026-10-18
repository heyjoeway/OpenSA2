/* Import binding and Win32 stubs. See sa2_imports.h for why the IAT is bound
 * rather than the unbound values registered. */
#include "sa2_imports.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_codepage.h"
#include "sa2_files.h"
#include "sa2_d3d9.h"
#include "sa2_dialog.h"
#include "sa2_dsound.h"
#include "sa2_ddraw.h"
#include "sa2_devprofile.h"
#include "sa2_dinput.h"
#include "sa2_format.h"
#include "sa2_gdi.h"
#include "sa2_guest_call.h"
#include "sa2_iface.h"
#include "sa2_locale.h"
#include "sa2_module.h"
#include "sa2_trap.h"
#include "sa2_wbem.h"
#include "sa2_xaudio2.h"
#include "sa2_threads.h"
#include "sa2_stall.h"
#include "sa2_window.h"

/* --------------------------------------------------------------- state */

typedef uint32_t (*import_impl)(void *state, const uint32_t *argv);

typedef struct {
    sa2_import_info info;
    import_impl     impl;
} import_slot;

static import_slot *g_imports;
static uint32_t     g_import_count;
static int          g_bound;
static int          g_bind_enabled = 1;

/* One arena for every string the info rows point at, so a row stays valid for
 * the life of the process and nothing points into guest memory, which
 * `sa2_guest_shutdown` unmaps. */
#define SA2_STRING_CHUNK 65536u
static char  *g_strings;
static size_t g_strings_used, g_strings_cap;

static sa2_import_call g_log[SA2_IMPORT_LOG_MAX];
static uint32_t        g_log_used;
static uint32_t        g_calls;
static uint32_t        g_call_budget;      /* SA2_IMPORT_BUDGET; 0 = no bound */
static int             g_budget_released;  /* the guest has stopped; the harness may call */
void sa2_import_budget_release(void) { g_budget_released = 1; }
int  sa2_import_budget_released(void) { return g_budget_released; }
uint32_t sa2_import_budget(void) { return g_call_budget; }

static uint32_t g_last_error;
static uint64_t g_virtual_ms;
static uint8_t  g_exited;
static uint32_t g_exit_code;

/* The import currently being dispatched, so a stub can name itself in a trap
 * without the table being threaded through every signature, and the call
 * record currently being filled, so a stub can declare what it wrote through
 * an out-pointer (NULL once the log is full, which every user checks). Set and
 * restored by `import_native`; `sa2_import_refused` also counts its refusals
 * on the row `g_current` points at.
 *
 * Per thread, as sa2_trap.c's jump buffer is: each guest thread is a host
 * thread, and a stub that hands the scheduler token off mid-call (Sleep, a
 * wait) lets another thread enter an import of its own. Process-wide, the
 * second thread's save and restore would leave the first inside its import
 * with the second's name, and the second running guest code with the first's
 * (the P2 integration review, 2026-09-23). */
static _Thread_local sa2_import_info *g_current;
static _Thread_local sa2_import_call *g_current_rec;

/* A deliberate stop. WinMain is entered by a direct call, which does not go
 * through the function table, so a native registered over its address never
 * sees it: the only place a driver can put a breakpoint on directly-called
 * guest code is an import it calls. Naming one turns "the run stopped at the
 * edge of this slice's scope" into an ending a probe can assert on, instead of
 * leaving it indistinguishable from "the run hit a wall". */
static char     g_stop_import[64];
static uint32_t g_stop_import_after;
static uint32_t g_stop_import_seen;
static int      g_stop_import_fired;

void sa2_import_stop_on(const char *name, uint32_t after_n) {
    snprintf(g_stop_import, sizeof g_stop_import, "%s", name ? name : "");
    g_stop_import_after = after_n;
    g_stop_import_seen = 0;
    g_stop_import_fired = 0;
}
int sa2_import_stopped_on_request(void) { return g_stop_import_fired; }

/* Declare what the stub wrote through its out-pointer, for the call record. */
static void note_out(uint32_t lo, uint32_t hi, uint8_t n) {
    if (!g_current_rec) return;
    g_current_rec->out[0] = lo;
    g_current_rec->out[1] = hi;
    g_current_rec->outc = n;
}

/* The virtual install location. Nothing in the project knows the real one yet:
 * the live oracle (design 5, S4) has not run, so there is no captured value to
 * agree with. These are therefore *named placeholders*, overridable from the
 * environment, recorded in every call record, and listed as a residue -- not a
 * guess dressed as a fact. They are unobservable until file I/O exists,
 * because the only thing the image does with them is build a path and then
 * call an import that is not implemented. */
#define SA2_DEFAULT_CWD      "C:\\SA2"
#define SA2_DEFAULT_EXE_PATH "C:\\SA2\\sonic2app.exe"
static char g_cwd[260] = SA2_DEFAULT_CWD;
static char g_exe_path[260] = SA2_DEFAULT_EXE_PATH;

/* TLS. Windows guarantees 64 indices and extends to 1088; the image's own
 * TlsAlloc sites are few, so the array is the full documented size and the
 * runtime never has to explain a failure the real OS would not have given.
 *
 * **The index allocation is per process and the storage is per thread**, which
 * is what the T in TLS means and what the single-threaded version could not
 * express. It is load-bearing rather than tidy: the CRT's `_threadstartex`
 * (0x00828a50) stores each thread's `_ptd` block in a TLS slot and every CRT
 * call on that thread reads it back, so one shared array would have had the
 * sound worker running on the main thread's per-thread data. SA2_TLS_SLOTS and
 * the storage live in runtime/sa2_threads.h. */
#define SA2_TLS_OUT_OF_IDX  0xFFFFFFFFu
static uint8_t  g_tls_taken[SA2_TLS_SLOTS];
#define g_tls (sa2_sched_tls(sa2_sched_current()))

/* ------------------------------------------------------------- helpers */

static const char *intern(const char *s, size_t n) {
    /* A full chunk is left where it is and a new one started: growing it
     * with realloc moved every string already handed out. The Metal Harbor
     * stage run opened enough files to move this arena, and its report then
     * read an opened file's name from freed memory (2026-09-23). */
    if (g_strings_used + n + 1 > g_strings_cap) {
        size_t want = n + 1 > SA2_STRING_CHUNK ? n + 1 : SA2_STRING_CHUNK;
        char *p = (char *)malloc(want);
        if (!p) return "";
        g_strings = p;
        g_strings_used = 0;
        g_strings_cap = want;
    }
    char *out = g_strings + g_strings_used;
    memcpy(out, s, n);
    out[n] = 0;
    g_strings_used += n + 1;
    return out;
}

static uint32_t gpeek32(uint32_t a) {
    uint32_t v = 0;
    if (sa2_peek(a, 4, &v) != SA2_OK) {
        sa2_trap_note("import binding: read of 4 bytes at guest %#010x is outside the guest space", a);
        sa2_trap_raise(SA2_TRAP_FAULT, a);
    }
    return v;
}

/* A guest NUL-terminated byte string into `out`. Returns its length, or -1
 * when the string leaves the guest space before terminating. */
static int gstr(uint32_t a, char *out, size_t cap) {
    for (size_t i = 0; i + 1 < cap; i++) {
        uint8_t *p = sa2_gp(a + (uint32_t)i, 1);
        if (!p) return -1;
        out[i] = (char)*p;
        if (!*p) return (int)i;
    }
    out[cap - 1] = 0;
    return (int)cap - 1;
}

/* A guest UTF-16 string, narrowed. Characters above U+00FF are replaced with
 * '?': every module name this image passes is ASCII, and a silent truncation
 * that changed a comparison would be worse than a visible one. */
static int gwstr(uint32_t a, char *out, size_t cap) {
    for (size_t i = 0; i + 1 < cap; i++) {
        uint8_t *p = sa2_gp(a + (uint32_t)(i * 2), 2);
        if (!p) return -1;
        uint16_t c = (uint16_t)(p[0] | (p[1] << 8));
        out[i] = c ? (c < 0x100 ? (char)c : '?') : 0;
        if (!c) return (int)i;
    }
    out[cap - 1] = 0;
    return (int)cap - 1;
}

static int ieq(const char *a, const char *b) {
    for (; *a && *b; a++, b++) {
        int ca = (*a >= 'A' && *a <= 'Z') ? *a + 32 : *a;
        int cb = (*b >= 'A' && *b <= 'Z') ? *b + 32 : *b;
        if (ca != cb) return 0;
    }
    return *a == *b;
}

static int gpoke32(uint32_t a, uint32_t v) {
    if (sa2_poke(a, 4, &v) != SA2_OK) {
        sa2_trap_note("import stub: write of 4 bytes at guest %#010x is outside the guest space", a);
        sa2_trap_raise(SA2_TRAP_FAULT, a);
    }
    return SA2_OK;
}

/* A guest byte string written from the host, trapping rather than truncating. */
static void gput(uint32_t a, const char *s, uint32_t n) {
    if (sa2_poke(a, n, s) != SA2_OK) {
        sa2_trap_note("import stub: write of %u bytes at guest %#010x is outside the guest space", n, a);
        sa2_trap_raise(SA2_TRAP_FAULT, a);
    }
}

/* ------------------------------------------------------------ virtual time
 *
 * Design 3.5: `QueryPerformanceCounter`, `timeGetTime`, `GetTickCount` and
 * `Sleep` are virtualised so the simulation runs under host control and
 * replays are deterministic. Nothing here reads the host clock, so two runs of
 * the same binary produce the same numbers -- which is what makes the security
 * cookie an oracle the probe can recompute instead of a value it must accept.
 *
 * The epoch is a *named placeholder*: no live-oracle capture exists to agree
 * with, so it is a constant the environment can move rather than a measurement
 * pretending to be one. `SA2_VIRTUAL_EPOCH` moves it and every derived value
 * moves with it, which is how the probe's oracle is shown not to be carrying
 * its own answer. */
/* QueryPerformanceFrequency's answer: 10,000,000, what the native game gets.
 * Wine's NtQueryPerformanceFrequency reports a fixed 10 MHz (its TICKSPERSEC),
 * and every native capture holds it: Sofdec copies the frequency into
 * 0x01c05cd0 during Init (the keyframe of every boot and Metal Harbor word log)
 * and its timer unit 0x0176a608 from tick 380 of every v15 boot, 0x00989680 in
 * all of them (the G4 interrogation, Reviewer B's finding 1; the fold's journal,
 * agents-journals/s4/2026-09-27-g4-round1-fold.md). No capture records the
 * answer itself yet: the device profile does not carry it, and recording it
 * there waits for the next change of the device-profile probe
 * (Documentation/S4_CARRY_FORWARD.md). Until 2026-09-27 the answer was
 * 3,579,545, the ACPI timer's frequency older Windows reports, a placeholder
 * no capture agreed with; SA2_TIME_BREAK=qpf answers it again, so the boot
 * replay's timer word shows the check failing (M5, the boot mask). */
#define SA2_QPC_HZ        10000000u
#define SA2_QPC_HZ_BEFORE 3579545u   /* SA2_TIME_BREAK=qpf: the answer before 2026-09-27 */
static uint32_t g_qpc_hz = SA2_QPC_HZ;
static uint64_t g_epoch_100ns =   /* 2012-10-02 00:00:00 UTC in FILETIME units, the PC release date */
    129939264000000000ull;
static uint32_t g_pid = 0x00000f44u;  /* a named placeholder: one process */
/* Thread 0's id. Every other guest thread's id is derived from it by the
 * scheduler, so `SA2_GUEST_TID` still moves all of them together. */
static uint32_t g_tid = 0x00000f48u;
static uint32_t cur_tid(void) { return sa2_sched_tid(sa2_sched_current()); }

/* The virtual clock in microseconds since process start. `Sleep` advances it in
 * milliseconds; each of the four time stubs advances it by one microsecond, so
 * the clock is strictly monotone across time calls and QueryPerformanceCounter,
 * whose reported frequency is 10 MHz (3.58 MHz under SA2_TIME_BREAK=qpf), moves
 * between two consecutive reads.
 *
 * GetTickCount reports milliseconds, so two consecutive GetTickCount calls with
 * nothing between them return the same value, exactly as on Windows. An earlier
 * version of this comment claimed otherwise; a guest that spins waiting for the
 * tick to change needs Sleep, and Sleep is what moves it. The quantum is
 * arbitrary and named as such: no capture exists to agree with. */
static uint64_t g_virtual_us;
static void clock_tick(void) { g_virtual_us += 1; }
static uint64_t virtual_us(void) { return g_virtual_us + g_virtual_ms * 1000ull; }

/* The strong definition of the clock `remill_runtime.cpp` declares weakly, so
 * RDTSC, GetTickCount, QueryPerformanceCounter and Sleep are all the same
 * clock. Without this they would be two clocks that drift apart, and guest code
 * that cross-checks them -- which frame pacing does -- would see time run
 * backwards. */
uint64_t sa2_virtual_us(void) { return virtual_us(); }

/* The scheduler's only write to the clock: move it to `us`, never backwards.
 * Expressed in the millisecond component so that GetTickCount, timeGetTime and
 * QueryPerformanceCounter stay one clock -- an advance written into the
 * microsecond component would drift `g_virtual_ms` away from the others and
 * the drift would look like a game that ran slow. */
void sa2_virtual_advance_to_us(uint64_t us) {
    uint64_t now = virtual_us();
    if (us <= now) return;
    uint64_t delta = us - now;
    g_virtual_ms += delta / 1000ull;
    g_virtual_us += delta % 1000ull;
}

/* --------------------------------------------------------------- handles
 *
 * Synthetic HANDLEs live *outside* the guest address space on purpose: a guest
 * that dereferences one faults in `sa2_peek` with the address in the trap,
 * rather than reading whatever happens to be at a fabricated guest pointer.
 * The one exception is a module handle for the image itself, which Windows
 * defines to be the image base and which the guest legitimately dereferences
 * (__tmainCRTStartup reads the PE header through it at 0x007a7ec4). */
#define SA2_HANDLE_BASE   0x7f000000u
#define SA2_HANDLE_STRIDE 0x1000u
/* The whole region Documentation/handle-regions.json declares (4096 handles of
 * 0x1000). It was 64, for every event, thread and other kernel object the run
 * ever made, none of them freed: loading Metal Harbor made its fifty-sixth
 * thread at tick 117, `handle_new` had no row, CreateThread answered NULL, and
 * the game's thread wrapper (0x00865380) resumed it unchecked (2026-09-23). */
#define SA2_HANDLE_MAX    4096u

enum { H_FREE = 0, H_MODULE, H_HEAP, H_WINSTA, H_PROCESS, H_THREAD, H_STD, H_EVENT };

typedef struct {
    uint8_t     kind;
    const char *name;
    /* H_EVENT only: the two bits an event object is. Kept beside the handle
     * rather than in guest memory because the guest never sees an event's
     * contents, only its handle. */
    uint8_t     manual_reset;
    uint8_t     signalled;
    /* H_THREAD only: the scheduler's index for this thread, and its place in
     * the creation order (sa2_sched_seq), which says whether the row still
     * holds that thread once rows are reused. */
    uint8_t     thread_index;
    uint32_t    thread_seq;
    /* H_EVENT only: the census slice 10 needed and did not have. "An event
     * nothing signals" is one of the ways a file read can never finish, and it
     * is not distinguishable from "an event signalled on a path nobody took"
     * unless the runtime counts both ends. `create_caller` is the guest address
     * that asked for the event, so a run names the event by the code that owns
     * it rather than by a handle number that means nothing outside this run. */
    uint32_t    create_caller;
    uint32_t    sets, resets, waits, waits_blocked;
} handle_row;

static handle_row g_handles[SA2_HANDLE_MAX];
static uint32_t   g_handle_used;

static uint32_t handle_new(uint8_t kind, const char *name) {
    if (g_handle_used >= SA2_HANDLE_MAX) return 0;
    uint32_t i = g_handle_used++;
    g_handles[i].kind = kind;
    g_handles[i].name = name;
    g_handles[i].manual_reset = 0;
    g_handles[i].signalled = 0;
    g_handles[i].thread_index = 0;
    g_handles[i].thread_seq = 0;
    g_handles[i].create_caller = 0;
    g_handles[i].sets = g_handles[i].resets = 0;
    g_handles[i].waits = g_handles[i].waits_blocked = 0;
    return SA2_HANDLE_BASE + i * SA2_HANDLE_STRIDE;
}

static handle_row *handle_row_of(uint32_t h) {
    if (h < SA2_HANDLE_BASE) return NULL;
    uint32_t i = (h - SA2_HANDLE_BASE) / SA2_HANDLE_STRIDE;
    if (i >= g_handle_used || (h - SA2_HANDLE_BASE) % SA2_HANDLE_STRIDE) return NULL;
    return &g_handles[i];
}

static const handle_row *handle_of(uint32_t h) { return handle_row_of(h); }

/* ------------------------------------------------------------- the heap
 *
 * `_heap_init` at 0x007b5165 calls HeapCreate and stores the handle at
 * 0x01751ff4; `_malloc` at 0x007a84a5 reads it, and when it is 0 the CRT takes
 * its uninitialised-runtime path and exits. So the heap is not optional and it
 * is not a stub: it is an allocator over the guest heap region, with headers in
 * guest memory the way a real heap has them.
 *
 * Boundary tags, implicit free list, next-fit, coalescing both ways. Each block
 * is 8-byte aligned with a 16-byte header:
 *
 *     +0  magic         SA2_HEAP_MAGIC ^ the heap handle that owns it
 *     +4  total | 1     block bytes including the header; bit 0 set = in use
 *     +8  prev_total    0 for the first block in the arena
 *     +12 payload       bytes the guest asked for, which HeapSize must return
 *
 * The magic is per-heap rather than global so that freeing a block on the wrong
 * heap is caught instead of silently working. Every refusal traps with the
 * pointer: a heap that quietly tolerates a bad free turns one defect into a
 * corruption that surfaces somewhere else, which is the failure mode this
 * project is least able to debug.
 *
 * The arena is the guest heap region minus a 64 KiB tail, which holds the
 * strings the runtime itself has to publish into guest memory (the command
 * line, the environment block). Those are not allocations the guest may free,
 * so they are not in the allocator's range at all. */
#define SA2_HEAP_MAGIC     0x48504132u        /* "2APH" */
#define SA2_HEAP_HDR       16u
#define SA2_HEAP_MIN_SPLIT 32u
#define SA2_RT_STRINGS_LEN 0x10000u
#define SA2_HEAP_ARENA_BASE SA2_GUEST_HEAP_BASE
#define SA2_HEAP_ARENA_END  (SA2_GUEST_HEAP_END - SA2_RT_STRINGS_LEN)

#define HEAP_ZERO_MEMORY 0x00000008u

static int      g_heap_ready;
static uint32_t g_heap_rover;
static uint32_t g_heap_live_blocks, g_heap_live_bytes, g_heap_peak_bytes;
static uint32_t g_heap_allocs, g_heap_frees, g_heap_failures;
static uint32_t g_heap_fail_last, g_heap_fail_max;

/* The last SA2_IMPORT_RECENT import calls, newest last. See the store site. */
static sa2_import_recent g_recent[SA2_IMPORT_RECENT];
static uint32_t          g_recent_next;
static sa2_import_recent *g_recent_cur;
static uint32_t           g_recent_seq;
static uint32_t caller_of_current(void);
static uint32_t g_process_heap;

/* The runtime's own guest-side string arena, bump-allocated and never freed. */
static uint32_t g_rt_strings_next = SA2_HEAP_ARENA_END;

static uint32_t rt_string(const char *s, uint32_t n) {
    uint32_t need = (n + 8u) & ~7u;
    if (g_rt_strings_next + need > SA2_GUEST_HEAP_END) return 0;
    uint32_t at = g_rt_strings_next;
    g_rt_strings_next += need;
    if (sa2_guest_fill(at, 0, need) != SA2_OK) return 0;
    gput(at, s, n);
    return at;
}

static uint32_t blk_total(uint32_t b) { return gpeek32(b + 4u) & ~7u; }
static int      blk_inuse(uint32_t b) { return (int)(gpeek32(b + 4u) & 1u); }
static void     blk_set(uint32_t b, uint32_t total, int inuse) {
    gpoke32(b + 4u, (total & ~7u) | (inuse ? 1u : 0u));
}

static void heap_arena_init(void) {
    if (g_heap_ready) return;
    uint32_t total = SA2_HEAP_ARENA_END - SA2_HEAP_ARENA_BASE;
    if (sa2_guest_fill(SA2_HEAP_ARENA_BASE, 0, SA2_HEAP_HDR) != SA2_OK) {
        sa2_trap_note("the guest heap arena %#010x..%#010x is not mappable",
                      SA2_HEAP_ARENA_BASE, SA2_HEAP_ARENA_END);
        sa2_trap_raise(SA2_TRAP_FAULT, SA2_HEAP_ARENA_BASE);
    }
    gpoke32(SA2_HEAP_ARENA_BASE, 0);          /* magic: free blocks own no heap */
    blk_set(SA2_HEAP_ARENA_BASE, total, 0);
    gpoke32(SA2_HEAP_ARENA_BASE + 8u, 0);
    gpoke32(SA2_HEAP_ARENA_BASE + 12u, 0);
    g_heap_rover = SA2_HEAP_ARENA_BASE;
    g_heap_ready = 1;
}

static uint32_t heap_alloc(uint32_t heap, uint32_t bytes, uint32_t flags) {
    heap_arena_init();
    uint32_t need = bytes ? ((bytes + 7u) & ~7u) : 8u;
    uint32_t want = need + SA2_HEAP_HDR;
    /* Both of these overflow in 32 bits and both were live. `harness/heap_probe.c`
     * found the second one: HeapAlloc(0xfffffff0) rounds to 0xfffffff0, adds the
     * 16-byte header, wraps to 0, and then *every* free block satisfies
     * `total >= want`, so the allocator handed back a block with a zero total.
     * The arena walk that follows reports a corrupt header, which is how the
     * probe saw it. Win32 returns NULL for a request this size, so it does. */
    if (need < bytes || want < need || want > SA2_HEAP_ARENA_END - SA2_HEAP_ARENA_BASE) {
        g_heap_failures++;
        g_heap_fail_last = bytes;
        if (bytes > g_heap_fail_max) g_heap_fail_max = bytes;
        return 0;
    }
    uint32_t start = g_heap_rover, b = start;
    int wrapped = 0;
    for (;;) {
        if (b >= SA2_HEAP_ARENA_END) { if (wrapped) break; b = SA2_HEAP_ARENA_BASE; wrapped = 1; }
        if (wrapped && b >= start) break;
        uint32_t total = blk_total(b);
        if (!total) break;                                     /* a corrupt header: stop, do not loop */
        if (!blk_inuse(b) && total >= want) {
            if (total - want >= SA2_HEAP_MIN_SPLIT) {
                uint32_t nb = b + want;
                gpoke32(nb, 0);
                blk_set(nb, total - want, 0);
                gpoke32(nb + 8u, want);
                gpoke32(nb + 12u, 0);
                uint32_t after = nb + (total - want);
                if (after < SA2_HEAP_ARENA_END) gpoke32(after + 8u, total - want);
                total = want;
            } else {
                uint32_t after = b + total;
                if (after < SA2_HEAP_ARENA_END) gpoke32(after + 8u, total);
            }
            gpoke32(b, SA2_HEAP_MAGIC ^ heap);
            blk_set(b, total, 1);
            gpoke32(b + 12u, bytes);
            g_heap_rover = b + total;
            g_heap_allocs++;
            g_heap_live_blocks++;
            g_heap_live_bytes += total;
            if (g_heap_live_bytes > g_heap_peak_bytes) g_heap_peak_bytes = g_heap_live_bytes;
            uint32_t p = b + SA2_HEAP_HDR;
            if (flags & HEAP_ZERO_MEMORY) {
                if (sa2_guest_fill(p, 0, total - SA2_HEAP_HDR) != SA2_OK) {
                    sa2_trap_note("HeapAlloc: cannot zero %#010x..%#010x", p, b + total);
                    sa2_trap_raise(SA2_TRAP_FAULT, p);
                }
            }
            return p;
        }
        b += total;
    }
    g_heap_failures++;
    g_heap_fail_last = bytes;
    if (bytes > g_heap_fail_max) g_heap_fail_max = bytes;
    return 0;   /* Win32: HeapAlloc without HEAP_GENERATE_EXCEPTIONS returns NULL */
}

/* The block behind a guest pointer, or a trap naming what was wrong with it.
 * `what` is the caller, so the diagnostic says which import refused. */
static uint32_t heap_block_of(uint32_t heap, uint32_t p, const char *what) {
    if (p < SA2_HEAP_ARENA_BASE + SA2_HEAP_HDR || p >= SA2_HEAP_ARENA_END
        || ((p - SA2_HEAP_ARENA_BASE) & 7u)) {
        sa2_trap_note("%s: %#010x is not a pointer this heap ever returned "
                      "(arena %#010x..%#010x)", what, p, SA2_HEAP_ARENA_BASE, SA2_HEAP_ARENA_END);
        sa2_trap_raise(SA2_TRAP_FAULT, p);
    }
    uint32_t b = p - SA2_HEAP_HDR;
    if (gpeek32(b) != (SA2_HEAP_MAGIC ^ heap) || !blk_inuse(b)) {
        sa2_trap_note("%s: the block at %#010x has magic %#010x and in-use %d, "
                      "which is not a live block of heap %#010x",
                      what, p, gpeek32(b), blk_inuse(b), heap);
        sa2_trap_raise(SA2_TRAP_FAULT, p);
    }
    return b;
}

static void heap_free(uint32_t heap, uint32_t p) {
    uint32_t b = heap_block_of(heap, p, "HeapFree");
    uint32_t total = blk_total(b);
    g_heap_frees++;
    if (g_heap_live_blocks) g_heap_live_blocks--;
    g_heap_live_bytes -= total < g_heap_live_bytes ? total : g_heap_live_bytes;
    gpoke32(b, 0);
    blk_set(b, total, 0);
    gpoke32(b + 12u, 0);
    /* coalesce forward */
    uint32_t nxt = b + total;
    if (nxt < SA2_HEAP_ARENA_END && !blk_inuse(nxt)) {
        total += blk_total(nxt);
        blk_set(b, total, 0);
    }
    /* coalesce backward */
    uint32_t prev_total = gpeek32(b + 8u);
    if (prev_total && b - prev_total >= SA2_HEAP_ARENA_BASE && !blk_inuse(b - prev_total)) {
        uint32_t pb = b - prev_total;
        total += prev_total;
        blk_set(pb, total, 0);
        b = pb;
    }
    uint32_t after = b + total;
    if (after < SA2_HEAP_ARENA_END) gpoke32(after + 8u, total);
    g_heap_rover = b;
}

/* ----------------------------------------------------- dynamic imports
 *
 * `GetProcAddress` against a *module* handle. 689 of the image's 706 call sites
 * ask the game's own data DLL for an asset symbol and are a separate slice;
 * the 17 that ask an OS module are the CRT's, they are on the startup path, and
 * they are here. A resolved name gets a thunk out of the high half of the
 * import region and one function-table row, so it dispatches through exactly
 * the machinery a static import does. */
typedef struct {
    const char *module;       /* as the runtime names it, uppercase, no suffix */
    const char *name;
    uint8_t     argc;
    uint8_t     conv;
    import_impl impl;         /* NULL: resolvable, but calling it refuses loudly */
} dyn_row;

static const dyn_row g_dyn_table[];
static void *import_native(void *s, uint32_t pc, void *m);
static import_slot g_dyn[SA2_GUEST_IMPORT_SLOTS - SA2_GUEST_IMPORT_DYN_FIRST];
static uint32_t    g_dyn_count;
/* Names GetProcAddress was asked for on a module the runtime admits, and could
 * not answer. This is the next slice's work list, not a silent 0. */
#define SA2_DYN_MISS_MAX 64u
static const char *g_dyn_missing[SA2_DYN_MISS_MAX];
static uint32_t    g_dyn_missing_count;

/* ------------------------------------------------------------- the stubs
 *
 * Every one of these returns a value the receipt can defend. Where the guest
 * can cope with a refusal, they refuse: a Win32-legal failure is loud and
 * cannot become a silent divergence in S5, while a fabricated success can. */

/* KERNEL32!GetCurrentDirectoryA(DWORD nBufferLength, LPSTR lpBuffer)
 *
 * Faithful Win32: on success the return is the length *excluding* the NUL and
 * the buffer holds the path; when the buffer is too small (or absent) the
 * return is the size required *including* the NUL and nothing is written. The
 * one free choice is the path itself, which is a named placeholder -- see
 * g_cwd above. */
static uint32_t imp_GetCurrentDirectoryA(void *s, const uint32_t *a) {
    (void)s;
    uint32_t n = a[0], buf = a[1];
    uint32_t len = (uint32_t)strlen(g_cwd);
    if (!buf || n < len + 1) return len + 1;
    if (sa2_poke(buf, len + 1, g_cwd) != SA2_OK) {
        sa2_trap_note("GetCurrentDirectoryA: buffer %#010x..%#010x is outside the guest space",
                      buf, buf + len + 1);
        sa2_trap_raise(SA2_TRAP_FAULT, buf);
    }
    return len;
}

/* KERNEL32!GetModuleFileNameA(HMODULE hModule, LPSTR lpFilename, DWORD nSize)
 *
 * Only one module exists: the image itself, whose handle is its base, which is
 * what Windows uses too. A handle that is neither NULL nor the image base is a
 * module this runtime has not loaded, and the honest answer is the same one
 * the OS gives: 0 with ERROR_MOD_NOT_FOUND. */
static uint32_t imp_GetModuleFileNameA(void *s, const uint32_t *a) {
    (void)s;
    uint32_t h = a[0], buf = a[1], n = a[2];
    if (h != 0 && h != SA2_GUEST_IMAGE_BASE) {
        g_last_error = SA2_ERROR_MOD_NOT_FOUND;
        return 0;
    }
    if (!buf || !n) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    uint32_t len = (uint32_t)strlen(g_exe_path);
    uint32_t copy = len < n - 1 ? len : n - 1;      /* Win32 truncates to fit */
    char tmp[sizeof g_exe_path];
    memcpy(tmp, g_exe_path, copy);
    tmp[copy] = 0;
    if (sa2_poke(buf, copy + 1, tmp) != SA2_OK) {
        sa2_trap_note("GetModuleFileNameA: buffer %#010x..%#010x is outside the guest space",
                      buf, buf + copy + 1);
        sa2_trap_raise(SA2_TRAP_FAULT, buf);
    }
    if (copy < len) { g_last_error = 122u /* ERROR_INSUFFICIENT_BUFFER */; return n; }
    g_last_error = SA2_ERROR_SUCCESS;
    return copy;
}

/* KERNEL32!GetModuleHandleA/W(LPCSTR lpModuleName)
 *
 * NULL is the calling process's own module, and its handle is its base
 * address: 0x00400000 here, which is both what Windows returns and an address
 * the guest can dereference, since the runtime maps the headers. A named
 * module is one no DLL has been loaded for, so 0 / ERROR_MOD_NOT_FOUND -- the
 * OS answer, not a fabricated handle that would have to be honoured later. */
static uint32_t os_module_handle(const char *key);
static int os_module_switched_off(const char *key);
static void module_key(const char *in, char *out, size_t cap);

static uint32_t module_handle_for(const char *name) {
    if (!name || !*name) return SA2_GUEST_IMAGE_BASE;
    const char *base = g_exe_path, *p;
    for (p = g_exe_path; *p; p++) if (*p == '\\' || *p == '/') base = p + 1;
    if (ieq(name, base)) return SA2_GUEST_IMAGE_BASE;
    /* A module LoadLibrary mapped (runtime/sa2_module.c): its handle is its base. */
    uint32_t mapped = sa2_module_by_name(name);
    if (mapped) { g_last_error = SA2_ERROR_SUCCESS; return mapped; }
    /* An OS module the runtime stands in for. The CRT asks for KERNEL32.DLL by
     * wide name and for "KERNEL32" without a suffix from two different call
     * sites, so the comparison strips path and suffix rather than matching a
     * spelling. Anything else -- mscoree.dll above all -- is a module this
     * process genuinely has not loaded, and 0 is the Win32 answer. */
    char key[64];
    module_key(name, key, sizeof key);
    uint32_t h = os_module_handle(key);
    if (h) { g_last_error = SA2_ERROR_SUCCESS; return h; }
    g_last_error = SA2_ERROR_MOD_NOT_FOUND;
    return 0;
}

static uint32_t imp_GetModuleHandleA(void *s, const uint32_t *a) {
    (void)s;
    char buf[260];
    if (!a[0]) return module_handle_for(NULL);
    if (gstr(a[0], buf, sizeof buf) < 0) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    return module_handle_for(buf);
}

static uint32_t imp_GetModuleHandleW(void *s, const uint32_t *a) {
    (void)s;
    char buf[260];
    if (!a[0]) return module_handle_for(NULL);
    if (gwstr(a[0], buf, sizeof buf) < 0) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    return module_handle_for(buf);
}

/* KERNEL32!LoadLibraryA/W(LPCSTR lpLibFileName)
 *
 * An OS module the runtime stands in for answers with its handle; any other
 * module is handed to runtime/sa2_module.c, which maps it as data -- relocated,
 * never executed -- when every rule there holds, and refuses otherwise. The
 * game resolves 436 of its asset tables through GetProcAddress against the
 * data DLL's handle at 0x01AF0220, and until 2026-09-22 this refused every
 * such module outright: the boot replay's first divergence was the guest
 * reading `advShareDataPvr` through the null that left
 * (agents-journals/s4/2026-09-22-tick-replay-hook.md). A refusal still returns
 * 0, which is the answer Windows gives for a DLL that is not there, and keeps
 * the name and the rule that refused it. */
#define SA2_LOADLIB_MAX 32u
static const char *g_loadlib_refused[SA2_LOADLIB_MAX];
static const char *g_loadlib_refused_why[SA2_LOADLIB_MAX];
static uint32_t    g_loadlib_refused_count;

static void loadlib_refused(const char *name, const char *why) {
    if (g_loadlib_refused_count < SA2_LOADLIB_MAX) {
        g_loadlib_refused[g_loadlib_refused_count] = intern(name, strlen(name));
        g_loadlib_refused_why[g_loadlib_refused_count] = intern(why, strlen(why));
    }
    g_loadlib_refused_count++;
}

static uint32_t load_library(const char *name) {
    char key[64];
    module_key(name, key, sizeof key);
    /* An OS module the runtime stands in for: ___crtMessageBoxA loads USER32
     * this way, and the display query loads ddraw. Those are the same modules
     * GetModuleHandle answers for, so one handle per module and not a second
     * identity. */
    uint32_t h = os_module_handle(key);
    if (h) { g_last_error = SA2_ERROR_SUCCESS; return h; }
    /* An admitted module this run has switched off is absent, and never handed
     * to sa2_module_load: a ddraw.dll in the game's directory is not the one
     * the answers come from. */
    if (os_module_switched_off(key)) {
        loadlib_refused(name, "SA2_DDRAW_DEVICE is not set, and ddraw.dll is answered only "
                              "by runtime/sa2_ddraw.c");
        g_last_error = SA2_ERROR_MOD_NOT_FOUND;
        return 0;
    }
    char why[256] = "";
    uint32_t err = SA2_ERROR_MOD_NOT_FOUND;
    uint32_t base = sa2_module_load(name, why, sizeof why, &err);
    if (base) { g_last_error = SA2_ERROR_SUCCESS; return base; }
    loadlib_refused(name, why);
    g_last_error = err;          /* MOD_NOT_FOUND, BAD_EXE_FORMAT or NOT_ENOUGH_MEMORY */
    return 0;
}

/* A switched-off module answers 0, which natively ddraw.dll does not
 * (runtime/sa2_ddraw.h cites the trace; Documentation/receipts/
 * s4-native-outcomes-v1.json, outcome ddraw-enumeration), so the answer is
 * marked refused against the import rather than passed off as a DLL that is
 * not there, and M4 does not count the import
 * (Documentation/refusal-reasons.json). The marker counts on the import being
 * dispatched, so it is called here, while LoadLibraryA is that import, and not
 * in load_library. */
static uint32_t imp_LoadLibraryA(void *s, const uint32_t *a) {
    (void)s;
    char buf[260], key[64];
    if (!a[0] || gstr(a[0], buf, sizeof buf) < 0) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    const uint32_t h = load_library(buf);
    module_key(buf, key, sizeof key);
    if (!h && os_module_switched_off(key))
        return sa2_import_refused("KERNEL32.dll", "LoadLibraryA", 0);
    return h;
}

/* The image imports LoadLibraryA and not LoadLibraryW, and loads ddraw.dll
 * through the former (00865d4a): the wide form of a switched-off module is a
 * request it never makes, so it stops the run by name instead of answering an
 * unmarked 0. */
static uint32_t imp_LoadLibraryW(void *s, const uint32_t *a) {
    (void)s;
    char buf[260], key[64];
    if (!a[0] || gwstr(a[0], buf, sizeof buf) < 0) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    module_key(buf, key, sizeof key);
    if (os_module_switched_off(key)) {
        sa2_trap_note("LoadLibraryW(\"%s\") from %#010x: the image loads that module only "
                      "through LoadLibraryA", buf, caller_of_current());
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    return load_library(buf);
}

/* KERNEL32!Sleep(DWORD dwMilliseconds)
 *
 * Design 3.5: time is virtualised so the simulation runs under host control
 * and replays are deterministic. A headless build must never block, so the
 * call advances a virtual clock and returns. */
/* KERNEL32!Sleep, which is both a time call and a hand-off point (G1 section 3).
 * The clock still only moves forward by the amount asked for; what changed is
 * that every other runnable guest thread gets to run first, and the move now
 * happens inside the scheduler so a timer tick that falls inside the sleep
 * fires at its own instant rather than being jumped over. */
static uint32_t imp_Sleep(void *s, const uint32_t *a) {
    (void)s;
    sa2_sched_sleep_ms(a[0]);
    if (sa2_sched_budget_hit()) {
        sa2_trap_note("Sleep(%u): the virtual clock reached the %llu ms budget. Nothing but "
                      "this thread's own sleeps was moving the guest forward, which is a "
                      "spin rather than progress; SA2_VIRTUAL_BUDGET_MS moves the bound.",
                      a[0], (unsigned long long)(sa2_sched_budget_us() / 1000ull));
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT,
                       g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    return 0;
}

/* KERNEL32!TlsAlloc / TlsGetValue / TlsSetValue / TlsFree
 *
 * Implemented rather than stubbed, because they have exact semantics and a
 * stub would have to invent one. TlsGetValue returning 0 for an index the
 * guest allocated and set is precisely the "plausible but wrong" failure the
 * brief warns about: the MSVC CRT uses a TLS slot for its per-thread data and
 * would rebuild it on every call. One thread, matching the single TEB. */
static uint32_t imp_TlsAlloc(void *s, const uint32_t *a) {
    (void)s; (void)a;
    for (uint32_t i = 0; i < SA2_TLS_SLOTS; i++) {
        if (!g_tls_taken[i]) { g_tls_taken[i] = 1; g_tls[i] = 0; return i; }
    }
    g_last_error = SA2_ERROR_INVALID_PARAMETER;
    return SA2_TLS_OUT_OF_IDX;
}

static uint32_t imp_TlsGetValue(void *s, const uint32_t *a) {
    (void)s;
    if (a[0] >= SA2_TLS_SLOTS) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    g_last_error = SA2_ERROR_SUCCESS;
    return g_tls[a[0]];
}

static uint32_t imp_TlsSetValue(void *s, const uint32_t *a) {
    (void)s;
    if (a[0] >= SA2_TLS_SLOTS) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    g_tls[a[0]] = a[1];
    return 1;
}

static uint32_t imp_TlsFree(void *s, const uint32_t *a) {
    (void)s;
    if (a[0] >= SA2_TLS_SLOTS || !g_tls_taken[a[0]]) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    g_tls_taken[a[0]] = 0;
    g_tls[a[0]] = 0;
    return 1;
}

/* KERNEL32!OutputDebugStringA/W: no debugger is attached, so natively the
 * string goes nowhere, and here it is kept. The call record holds only the
 * pointer, and the callers that format a message do it into a reused stack
 * buffer -- 0x00851DC0 through wvsprintfA, 0x0040E930 through the C runtime's
 * _snprintf -- so by the end of a run the pointer's text is some later
 * message's. The first SA2_DEBUG_KEPT strings and the last SA2_DEBUG_KEPT are
 * kept, each with its ordinal among all the calls.
 *
 * Reading stops at an address that cannot be read, and the string is kept as
 * far as it went. That follows Windows, which reads the argument under an
 * exception handler; the oracle's kernelbase does not, and faults on a bad
 * pointer other than NULL. No SA2 caller passes one. */
typedef struct {
    uint32_t ordinal;
    uint8_t  truncated;
    char     text[SA2_DEBUG_TEXT_MAX];
} debug_string;

static debug_string g_debug_first[SA2_DEBUG_KEPT];
static debug_string g_debug_last[SA2_DEBUG_KEPT];   /* a ring over the calls after the first ones */
static uint32_t     g_debug_count;

static void keep_debug_string(uint32_t va, int wide) {
    const uint32_t call = g_debug_count++;
    debug_string *d = call < SA2_DEBUG_KEPT ? &g_debug_first[call]
                                            : &g_debug_last[(call - SA2_DEBUG_KEPT) % SA2_DEBUG_KEPT];
    uint32_t n = 0;
    d->ordinal = call;
    d->truncated = 0;
    for (;;) {
        uint16_t ch;
        uint8_t b[2];
        if (sa2_peek(wide ? va + 2u * n : va + n, wide ? 2u : 1u, b) != SA2_OK) {
            d->truncated = 1;
            break;
        }
        ch = wide ? (uint16_t)(b[0] | (b[1] << 8)) : b[0];
        if (!ch) break;
        if (n + 1u >= SA2_DEBUG_TEXT_MAX) { d->truncated = 1; break; }
        if (wide) sa2_cp1252_from_wide(ch, &b[0]);
        d->text[n++] = (char)b[0];
    }
    d->text[n] = 0;
}

static uint32_t imp_OutputDebugStringA(void *s, const uint32_t *a) {
    (void)s;
    keep_debug_string(a[0], 0);
    return 0;
}

static uint32_t imp_OutputDebugStringW(void *s, const uint32_t *a) {
    (void)s;
    keep_debug_string(a[0], 1);
    return 0;
}

uint32_t sa2_debug_string_count(void) { return g_debug_count; }

uint32_t sa2_debug_strings_kept(void) {
    const uint32_t first = g_debug_count < SA2_DEBUG_KEPT ? g_debug_count : SA2_DEBUG_KEPT;
    const uint32_t rest = g_debug_count - first;
    return first + (rest < SA2_DEBUG_KEPT ? rest : SA2_DEBUG_KEPT);
}

const char *sa2_debug_string_at(uint32_t i, uint32_t *ordinal, int *truncated) {
    const uint32_t first = g_debug_count < SA2_DEBUG_KEPT ? g_debug_count : SA2_DEBUG_KEPT;
    const debug_string *d;
    if (i < first) {
        d = &g_debug_first[i];
    } else {
        const uint32_t rest = g_debug_count - first;
        const uint32_t kept = rest < SA2_DEBUG_KEPT ? rest : SA2_DEBUG_KEPT;
        if (i - first >= kept) return NULL;
        const uint32_t call = g_debug_count - kept + (i - first);
        d = &g_debug_last[(call - SA2_DEBUG_KEPT) % SA2_DEBUG_KEPT];
    }
    if (ordinal) *ordinal = d->ordinal;
    if (truncated) *truncated = d->truncated;
    return d->text;
}

/* USER32!wvsprintfA(LPSTR out, LPCSTR fmt, va_list args)
 *
 * runtime/sa2_format.c, which says where its language comes from: the
 * oracle's own user32, case by case (Documentation/receipts/
 * s4-wsprintf-oracle-v1.json). An address it cannot read or write is an access
 * violation inside USER32 natively, so here it is a trap naming the address,
 * not a shortened string. */
static uint32_t imp_wvsprintfA(void *s, const uint32_t *a) {
    (void)s;
    uint32_t ret = 0, fault = 0;
    if (sa2_wvsprintf(a[0], a[1], a[2], &ret, &fault) != SA2_OK) {
        sa2_trap_note("wvsprintfA(%#010x, %#010x, %#010x): guest %#010x cannot be read or "
                      "written -- natively an access violation inside USER32",
                      a[0], a[1], a[2], fault);
        sa2_trap_raise(SA2_TRAP_FAULT, fault);
    }
    return ret;
}

/* KERNEL32!ExitProcess(UINT uExitCode)
 *
 * The one import whose faithful implementation is *not* to return, so it
 * raises the trap itself rather than being left to the loud default. The
 * distinction is worth six lines: "the guest asked to exit with 255" and "the
 * runtime has no stub for this" are different facts, and only the first says
 * the run ended where the guest meant it to. The exit code is kept so a
 * caller can read it without parsing the note. */
static uint32_t imp_ExitProcess(void *s, const uint32_t *a) {
    (void)s;
    g_exited = 1;
    g_exit_code = a[0];
    sa2_trap_note("the guest called ExitProcess(%u) -- it ended the process itself, "
                  "this is not a missing stub", a[0]);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

static uint32_t imp_SetLastError(void *s, const uint32_t *a) {
    (void)s;
    g_last_error = a[0];
    return 0;
}

static uint32_t imp_GetLastError(void *s, const uint32_t *a) {
    (void)s; (void)a;
    return g_last_error;
}

/* --------------------------------------------------------------- time */

/* KERNEL32!GetSystemTimeAsFileTime(LPFILETIME). 100 ns units since 1601. */
static uint32_t imp_GetSystemTimeAsFileTime(void *s, const uint32_t *a) {
    (void)s;
    clock_tick();
    uint64_t ft = g_epoch_100ns + virtual_us() * 10ull;
    gpoke32(a[0], (uint32_t)ft);
    gpoke32(a[0] + 4u, (uint32_t)(ft >> 32));
    note_out((uint32_t)ft, (uint32_t)(ft >> 32), 2);
    return 0;
}

static uint32_t imp_GetCurrentProcessId(void *s, const uint32_t *a) { (void)s; (void)a; return g_pid; }
static uint32_t imp_GetCurrentThreadId(void *s, const uint32_t *a) { (void)s; (void)a; return cur_tid(); }

/* KERNEL32!GetTickCount: milliseconds since process start, which is the
 * virtual clock and not the host's. */
static uint32_t imp_GetTickCount(void *s, const uint32_t *a) {
    (void)s; (void)a;
    clock_tick();
    return (uint32_t)(virtual_us() / 1000ull);
}

/* WINMM!timeGetTime: the same virtual clock GetTickCount reads, named
 * separately by design 3.5. Two clocks that drift apart is the failure mode
 * here, and guest frame pacing cross-checks them, so there is exactly one. */
static uint32_t imp_timeGetTime(void *s, const uint32_t *a) {
    (void)s; (void)a;
    clock_tick();
    return (uint32_t)(virtual_us() / 1000ull);
}

/* KERNEL32!QueryPerformanceCounter/Frequency. The counter is the virtual clock
 * scaled to the reported frequency, so counter/frequency is exactly the
 * virtual elapsed time and the two answers cannot disagree. */
static uint32_t imp_QueryPerformanceCounter(void *s, const uint32_t *a) {
    (void)s;
    clock_tick();
    uint64_t c = virtual_us() * (uint64_t)g_qpc_hz / 1000000ull;
    gpoke32(a[0], (uint32_t)c);
    gpoke32(a[0] + 4u, (uint32_t)(c >> 32));
    note_out((uint32_t)c, (uint32_t)(c >> 32), 2);
    return 1;
}

static uint32_t imp_QueryPerformanceFrequency(void *s, const uint32_t *a) {
    (void)s;
    gpoke32(a[0], g_qpc_hz);
    gpoke32(a[0] + 4u, 0);
    note_out(g_qpc_hz, 0, 2);
    return 1;
}

/* --------------------------------------------------------------- heap */

/* KERNEL32!HeapCreate(DWORD flOptions, SIZE_T dwInitialSize, SIZE_T dwMaximumSize)
 *
 * Every heap this runtime hands out is a view of the one guest arena, tagged by
 * handle, so a block freed on the wrong heap is caught. dwMaximumSize is
 * recorded and not enforced: the arena bound is the real one and enforcing a
 * second, smaller one would make an allocation fail for a reason the guest
 * could not have observed on Windows. */
static uint32_t imp_HeapCreate(void *s, const uint32_t *a) {
    (void)s; (void)a;
    heap_arena_init();
    uint32_t h = handle_new(H_HEAP, "heap");
    if (!h) { g_last_error = 8u /* ERROR_NOT_ENOUGH_MEMORY */; return 0; }
    return h;
}

/* The process heap's handle, made on first use: GetProcessHeap's answer, and
 * the heap OLEAUT32 allocates BSTRs from (sa2_bstr_alloc). */
static uint32_t process_heap(void) {
    if (!g_process_heap) { heap_arena_init(); g_process_heap = handle_new(H_HEAP, "process-heap"); }
    return g_process_heap;
}

static uint32_t imp_GetProcessHeap(void *s, const uint32_t *a) {
    (void)s; (void)a;
    return process_heap();
}

static uint32_t heap_arg(uint32_t h, const char *what) {
    const handle_row *r = handle_of(h);
    if (!r || r->kind != H_HEAP) {
        sa2_trap_note("%s: %#010x is not a heap handle this runtime created", what, h);
        sa2_trap_raise(SA2_TRAP_FAULT, h);
    }
    return h;
}

static uint32_t imp_HeapAlloc(void *s, const uint32_t *a) {
    (void)s;
    uint32_t h = heap_arg(a[0], "HeapAlloc");
    uint32_t p = heap_alloc(h, a[2], a[1]);
    if (!p) g_last_error = 8u;
    return p;
}

static uint32_t imp_HeapFree(void *s, const uint32_t *a) {
    (void)s;
    uint32_t h = heap_arg(a[0], "HeapFree");
    if (!a[2]) return 1;                 /* Win32: freeing NULL succeeds */
    heap_free(h, a[2]);
    return 1;
}

static uint32_t imp_HeapSize(void *s, const uint32_t *a) {
    (void)s;
    uint32_t h = heap_arg(a[0], "HeapSize");
    uint32_t b = heap_block_of(h, a[2], "HeapSize");
    return gpeek32(b + 12u);
}

static uint32_t imp_HeapReAlloc(void *s, const uint32_t *a) {
    (void)s;
    uint32_t h = heap_arg(a[0], "HeapReAlloc");
    uint32_t old = a[2], want = a[3];
    if (!old) return heap_alloc(h, want, a[1]);
    uint32_t b = heap_block_of(h, old, "HeapReAlloc");
    uint32_t had = gpeek32(b + 12u);
    if (want <= blk_total(b) - SA2_HEAP_HDR) {      /* fits where it is */
        gpoke32(b + 12u, want);
        if ((a[1] & HEAP_ZERO_MEMORY) && want > had)
            sa2_guest_fill(old + had, 0, want - had);
        return old;
    }
    uint32_t p = heap_alloc(h, want, a[1] & ~HEAP_ZERO_MEMORY);
    if (!p) { g_last_error = 8u; return 0; }
    uint8_t *dst = sa2_gp(p, want), *src = sa2_gp(old, had);
    if (!dst || !src) {
        sa2_trap_note("HeapReAlloc: %#010x..%#010x or %#010x..%#010x is outside the guest space",
                      p, p + want, old, old + had);
        sa2_trap_raise(SA2_TRAP_FAULT, p);
    }
    memcpy(dst, src, had < want ? had : want);
    if ((a[1] & HEAP_ZERO_MEMORY) && want > had) sa2_guest_fill(p + had, 0, want - had);
    heap_free(h, old);
    return p;
}

/* ----------------------------------------------------------- startup */

/* KERNEL32!GetStartupInfoA(LPSTARTUPINFOA). 68 bytes on 32-bit.
 *
 * Everything is zero except cb. That is not laziness: dwFlags without
 * STARTF_USESHOWWINDOW is what a process launched with no explicit show
 * command gets, and it makes __tmainCRTStartup compute nCmdShow = 10
 * (SW_SHOWDEFAULT) *itself* at 0x007a7fa1 rather than being handed a number by
 * the runtime. A guest-computed value is evidence; a runtime-supplied one is
 * not. The standard-handle fields are 0 for the same reason GetStdHandle
 * returns 0 -- this is a GUI-subsystem image (Subsystem 2) with no console. */
#define SA2_STARTUPINFOA_CB 68u
static uint32_t imp_GetStartupInfoA(void *s, const uint32_t *a) {
    (void)s;
    if (sa2_guest_fill(a[0], 0, SA2_STARTUPINFOA_CB) != SA2_OK) {
        sa2_trap_note("GetStartupInfoA: %#010x..%#010x is outside the guest space",
                      a[0], a[0] + SA2_STARTUPINFOA_CB);
        sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
    }
    gpoke32(a[0], SA2_STARTUPINFOA_CB);
    return 0;
}

/* KERNEL32!GetCommandLineA. The string is published once into the runtime's
 * own guest-side arena, which the guest may read and must never free. Its
 * contents are a named placeholder built from the virtual exe path. */
static uint32_t g_cmdline_va;
static char     g_cmdline_tail[512];     /* what the guest parses into WinMain's lpCmdLine */
static uint32_t imp_GetCommandLineA(void *s, const uint32_t *a) {
    (void)s; (void)a;
    if (!g_cmdline_va) {
        char buf[1024];
        int n = g_cmdline_tail[0]
              ? snprintf(buf, sizeof buf, "\"%s\" %s", g_exe_path, g_cmdline_tail)
              : snprintf(buf, sizeof buf, "\"%s\"", g_exe_path);
        g_cmdline_va = rt_string(buf, (uint32_t)n + 1u);
    }
    return g_cmdline_va;
}
const char *sa2_import_cmdline_tail(void) { return g_cmdline_tail; }

/* SA2_LOCALE_ANSWERS=<file> writes every call of the imports
 * tools/s4/locale_check.py holds to native -- the locale's, the environment
 * block's and the standard handles' -- and what this runtime answered, one line
 * each:
 *
 *     <seq> t<thread> <caller> <import> <argument>... -> <return> out=<hex> err=<error>
 *
 * `out` is what the call wrote: UTF-16 units as four hex digits, bytes as two
 * (GetLocaleInfoA, GetCPInfo's 18), the environment block's units to its final
 * NUL; `err` is the last error when the call answered 0, else 0.
 * EnumSystemLocalesA's line, written when it returns, holds each callback's
 * LCID and answer instead: ` cb=<lcid>:<answer>,...`; its <seq> is its own,
 * not that of the last import its callbacks made. The file is the host's:
 * the guest sees nothing of it, so a run is the same with it and without it.
 * Code page conversions are SA2_CTYPE_ANSWERS' (see "The character data"). */
static FILE *g_locale_answers;

/* `seq` is the call's own number: g_calls when it was dispatched. */
static void locale_answer_begin_at(uint32_t seq, const char *name, const uint32_t *a,
                                   unsigned argc, uint32_t ret) {
    FILE *f = g_locale_answers;
    fprintf(f, "%u t%u %08x %s", seq, (unsigned)sa2_sched_current(), caller_of_current(),
            name);
    for (unsigned i = 0; i < argc; i++) fprintf(f, " %08x", a[i]);
    fprintf(f, " -> %08x", ret);
}

static void locale_answer_begin(const char *name, const uint32_t *a, unsigned argc,
                                uint32_t ret) {
    locale_answer_begin_at(g_calls, name, a, argc, ret);
}

static void locale_answer_end(uint32_t ret) {
    fprintf(g_locale_answers, " err=%u\n", ret ? 0u : g_last_error);
    fflush(g_locale_answers);
}

/* One line; `out`/`out_n` the bytes written, as two hex digits each. */
static void locale_answer_line(const char *name, const uint32_t *a, unsigned argc, uint32_t ret,
                               const uint8_t *out, uint32_t out_n) {
    if (!g_locale_answers) return;
    locale_answer_begin(name, a, argc, ret);
    fputs(" out=", g_locale_answers);
    for (uint32_t i = 0; i < out_n; i++) fprintf(g_locale_answers, "%02x", out[i]);
    locale_answer_end(ret);
}

/* The same, `out` read from guest memory: `n` units of `width` bytes. */
static void locale_answer_guest(const char *name, const uint32_t *a, unsigned argc,
                                uint32_t ret, uint32_t out, uint32_t n, unsigned width) {
    if (!g_locale_answers) return;
    locale_answer_begin(name, a, argc, ret);
    fputs(" out=", g_locale_answers);
    for (uint32_t i = 0; i < n; i++) {
        const uint8_t *p = sa2_gp(out + i * width, width);
        if (!p) { fputs("..", g_locale_answers); break; }
        if (width == 2u) fprintf(g_locale_answers, "%04x", (unsigned)(p[0] | (p[1] << 8)));
        else fprintf(g_locale_answers, "%02x", (unsigned)p[0]);
    }
    locale_answer_end(ret);
}

static void locale_answer_env(uint32_t block) {
    if (!g_locale_answers) return;
    uint32_t n = 0;
    for (;;) {                                   /* to the block's final NUL */
        const uint8_t *p = sa2_gp(block + 2u * n, 4);
        if (!p) break;
        n++;
        if (!(p[0] | p[1]) && !(p[2] | p[3])) { n++; break; }
    }
    const uint32_t none[1] = {0};
    locale_answer_guest("GetEnvironmentStringsW", none, 0, block, block, n, 2u);
}

/* KERNEL32!GetEnvironmentStringsW / GetEnvironmentStrings.
 *
 * The C runtime asks for the wide block once, at start-up
 * (___crtGetEnvironmentStringsA), converts it through the ANSI code page and
 * frees it, and _setenvp builds _environ from the copy. Natively the block is
 * Wine's environment for the game's process -- the variables the oracle's
 * runner passes (each receipt's session.game_environment) and what Proton and
 * Wine add -- so it is a host property, and the capture's device profile
 * records it (runtime/sa2_devprofile.h, "The environment"). With a profile that
 * does, this answers the part of it the C runtime saw at start-up. A run with no
 * capture gets the default, an empty block: a single terminator, not a
 * fabricated PATH, because a variable the guest then acts on would be a value
 * this project invented and cannot defend.
 *
 * SA2_ENV_BREAK=empty answers the empty block whatever the profile holds, the
 * answer before profiles recorded the environment, so tools/s4/locale_check.py
 * can show that its check sees the block.
 *
 * The block is the runtime's, published once in its guest-side string arena,
 * and FreeEnvironmentStrings is a no-op that records, exactly as it would be for
 * a block Windows owns. The ANSI form, which neither side calls (the C runtime
 * takes it only when the wide form fails), answers the empty block with no
 * recorded environment and traps with one, rather than answer a block the
 * capture's process did not have. */
static uint32_t g_env_w, g_env_a;
static int      g_env_break;
static int env_profiled(void) { return sa2_dp_env_recorded() && !g_env_break; }

static uint32_t imp_GetEnvironmentStringsW(void *s, const uint32_t *a) {
    (void)s; (void)a;
    if (!g_env_w) {
        if (!env_profiled()) {
            static const char z[4] = {0, 0, 0, 0};
            g_env_w = rt_string(z, 4);
        } else {
            uint32_t units = 0;
            const uint16_t *w = sa2_dp_env_block(&units);
            const uint32_t bytes = 2u * units + 2u;       /* and the block's final NUL */
            char *buf = calloc(bytes, 1);
            if (buf) {
                for (uint32_t i = 0; i < units; i++) {
                    buf[2u * i] = (char)(w[i] & 0xFFu);
                    buf[2u * i + 1u] = (char)(w[i] >> 8);
                }
                g_env_w = rt_string(buf, bytes);
                free(buf);
            }
        }
        if (!g_env_w) {
            sa2_trap_note("GetEnvironmentStringsW: the runtime's string arena has no room for "
                          "the environment block");
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
        }
    }
    locale_answer_env(g_env_w);
    return g_env_w;
}
static uint32_t imp_GetEnvironmentStringsA(void *s, const uint32_t *a) {
    (void)s; (void)a;
    if (env_profiled()) {
        sa2_trap_note("GetEnvironmentStrings: the capture's profile records the wide block, and "
                      "its ANSI form is not implemented");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    if (!g_env_a) { static const char z[2] = {0, 0}; g_env_a = rt_string(z, 2); }
    return g_env_a;
}
static uint32_t imp_FreeEnvironmentStrings(void *s, const uint32_t *a) { (void)s; (void)a; return 1; }

/* The standard handles.
 *
 * The C runtime's __ioinit asks GetStdHandle for the three at start-up and
 * types each with GetFileType: FILE_TYPE_CHAR makes descriptor 0, 1 or 2 a
 * device, FILE_TYPE_PIPE a pipe, and no handle leaves it a device with no handle
 * (_NO_CONSOLE_FILENO); `_isatty` and stdout's buffering follow.
 *
 * Which the game gets is the host's: how its process was launched. The oracle's
 * runner gives the game Unix pipes (tools/oracle/run_oracle_probe.py: stdin an
 * empty pipe, stdout and stderr captured), and Wine types a Unix pipe it wraps
 * as a character device, FILE_TYPE_CHAR
 * (private/generated/oracle/captures/n0-trace-v1a/sa2-n0-trace.txt, the `host
 * GetStdHandle` lines). So with a capture's profile that records the standard
 * handles (runtime/sa2_devprofile.h, "The standard handles"), each handle the
 * host had is one of three in this runtime's region, typed as recorded, and one
 * it did not have is answered as it was, NULL or INVALID_HANDLE_VALUE. The
 * numbers are this runtime's, compared by what they name
 * (Documentation/decisions/2026-09-24-S4-handles-identity-stage-mask.md).
 *
 * With no such profile the answer is the one before profiles recorded them:
 * no handle, a GUI-subsystem game launched the ordinary way, unless
 * SA2_STD_HANDLES=pipe asks for three pipes. That switch was the replay
 * configuration's parity with the oracle's process from 2026-09-23, before a
 * trace showed Wine types the runner's pipes as character devices; beside a
 * profile that records the handles it traps at configuration, since the handles
 * are then the capture's. SA2_STDIO_BREAK=pipe answers the three pipes whatever
 * the profile holds, the replay configuration's answer before 2026-09-26, so
 * tools/s4/locale_check.py can show that its check sees the type.
 *
 * Writes are accepted and discarded and reads find the end of the stream, in
 * every configuration; nothing a replay runs writes to them (the reach run's
 * table counts no WriteFile), and natively neither does Metal Harbor's. */
#define SA2_STD_HANDLE_BASE   0x7b000000u
#define SA2_STD_HANDLE_STRIDE 16u
#define SA2_FILE_TYPE_PIPE    3u

enum { STD_NONE = 0, STD_PIPES = 1, STD_PROFILE = 2 };
static int g_std_mode;               /* set at configuration */

/* GetFileType's answer for standard handle i, or -1 when the runtime hands out
 * no handle i. */
static int std_type(uint32_t i) {
    if (i >= 3u) return -1;
    if (g_std_mode == STD_PIPES) return (int)SA2_FILE_TYPE_PIPE;
    if (g_std_mode != STD_PROFILE) return -1;
    const uint32_t h = sa2_dp_std_handle(i);
    if (!h || h == 0xFFFFFFFFu) return -1;
    return (int)sa2_dp_std_type(i);
}

/* 0, 1, 2 for stdin, stdout, stderr's handle; -1 for anything else, and for a
 * handle the runtime hands out none of, so the default configuration answers
 * for that range exactly as it did before standard handles existed. */
static int std_index(uint32_t h) {
    if (h < SA2_STD_HANDLE_BASE || h >= SA2_STD_HANDLE_BASE + 3u * SA2_STD_HANDLE_STRIDE ||
        (h - SA2_STD_HANDLE_BASE) % SA2_STD_HANDLE_STRIDE)
        return -1;
    const uint32_t i = (h - SA2_STD_HANDLE_BASE) / SA2_STD_HANDLE_STRIDE;
    return std_type(i) >= 0 ? (int)i : -1;
}

static uint32_t imp_GetStdHandle(void *s, const uint32_t *a) {
    (void)s;
    /* Win32 does not clear the last error on success and the CRT reads it
     * across this call, so neither does this. */
    uint32_t i, r;
    switch (a[0]) {
    case 0xFFFFFFF6u: i = 0; break;             /* STD_INPUT_HANDLE */
    case 0xFFFFFFF5u: i = 1; break;             /* STD_OUTPUT_HANDLE */
    case 0xFFFFFFF4u: i = 2; break;             /* STD_ERROR_HANDLE */
    default:                                    /* Wine's answer in every configuration; the CRT never asks */
        g_last_error = SA2_ERROR_INVALID_HANDLE;
        return 0xFFFFFFFFu;
    }
    if (std_type(i) >= 0) r = SA2_STD_HANDLE_BASE + i * SA2_STD_HANDLE_STRIDE;
    else if (g_std_mode == STD_PROFILE) r = sa2_dp_std_handle(i);   /* NULL or INVALID, as recorded */
    else r = 0u;
    /* A recorded INVALID_HANDLE_VALUE is the PEB's value, which kernelbase
     * returns without touching the last error; only a bad `which` sets it. */
    locale_answer_line("GetStdHandle", a, 1, r, NULL, 0);
    return r;
}
static uint32_t imp_SetStdHandle(void *s, const uint32_t *a) { (void)s; (void)a; return 1; }
/* Win32 documents SetHandleCount as returning its argument on NT. */
static uint32_t imp_SetHandleCount(void *s, const uint32_t *a) { (void)s; return a[0]; }
/* FILE_TYPE_UNKNOWN for a handle nothing opened -- no standard handle on a
 * GUI-subsystem image -- which the CRT reads as "not a character device, not a
 * pipe, not a disk file" and leaves the descriptor closed; a standard handle's
 * type as the configuration says (see "The standard handles").
 *
 * A handle this runtime *did* create is a different question and refuses by
 * name: answering UNKNOWN for a file handle would make the CRT decline to
 * attach a descriptor to a file it had just opened, and that failure would
 * surface as a missing asset rather than as a wrong answer here. */
static uint32_t imp_GetFileType(void *s, const uint32_t *a) {
    (void)s;
    uint32_t r;
    const int std = std_index(a[0]);
    if (!a[0] || a[0] == 0xffffffffu) r = 0;      /* FILE_TYPE_UNKNOWN */
    else if (std >= 0) r = (uint32_t)std_type((uint32_t)std);
    /* A handle the guest file system opened is a disk file, and saying so is
     * what lets the CRT attach a descriptor to it. Before file I/O existed
     * this stub refused every runtime-created handle, which was the right
     * answer while there were no files and the wrong one now. */
    else if (sa2_vfs_is_handle(a[0])) r = 1u;     /* FILE_TYPE_DISK */
    else if (handle_of(a[0])) {
        sa2_trap_note("GetFileType(%#010x): this runtime created that handle but has no file "
                      "type for it; file I/O is not implemented yet", a[0]);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    } else r = 0;
    if (std >= 0) locale_answer_line("GetFileType", a, 1, r, NULL, 0);
    return r;
}

/* ------------------------------------------------- threads and locking
 *
 * G1's strategy, implemented in runtime/sa2_threads.c: guest threads are host
 * threads under one guest lock with deterministic hand-off. A critical section
 * is therefore contended only when its owner was handed off *inside* it, which
 * can happen -- so the recursion counter in the guest's own CRITICAL_SECTION is
 * still the state, and the owner field is now read as well as written.
 *
 * The fields written are the documented RTL_CRITICAL_SECTION layout, because
 * the CRT reads LockCount and RecursionCount back.
 *
 * Enter and Leave are hand-off *points*, not hand-offs: the lock is offered to
 * another thread only when this one would otherwise spin. Handing off on every
 * uncontended Enter would be legal under the decision and would make the
 * interleaving depend on how many times the CRT happened to take a lock, which
 * is a worse thing to depend on than the sync operations that actually block. */
#define CS_DEBUGINFO 0x00u
#define CS_LOCKCOUNT 0x04u
#define CS_RECURSION 0x08u
#define CS_OWNER     0x0cu
#define CS_SEMAPHORE 0x10u
#define CS_SPINCOUNT 0x14u

/* Times an Enter found the section held by another guest thread and had to
 * hand off. Counted after the check that found it, so a run with no contention
 * reports none. */
static uint64_t g_cs_contentions;
uint64_t sa2_import_cs_contentions(void) { return g_cs_contentions; }

static uint32_t imp_InitializeCriticalSection(void *s, const uint32_t *a) {
    (void)s;
    if (sa2_guest_fill(a[0], 0, 24) != SA2_OK) {
        sa2_trap_note("InitializeCriticalSection: %#010x..%#010x is outside the guest space",
                      a[0], a[0] + 24u);
        sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
    }
    gpoke32(a[0] + CS_DEBUGINFO, 0xffffffffu);   /* no debug info, as on a release heap */
    gpoke32(a[0] + CS_LOCKCOUNT, 0xffffffffu);   /* -1 = unlocked */
    return 0;
}

static uint32_t imp_InitializeCriticalSectionAndSpinCount(void *s, const uint32_t *a) {
    imp_InitializeCriticalSection(s, a);
    gpoke32(a[0] + CS_SPINCOUNT, a[1]);
    return 1;
}

static uint32_t imp_EnterCriticalSection(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t me = cur_tid();
    for (;;) {
        uint32_t rec = gpeek32(a[0] + CS_RECURSION);
        uint32_t own = gpeek32(a[0] + CS_OWNER);
        if (rec == 0u || own == me) break;              /* free, or already mine */
        g_cs_contentions++;
        if (sa2_sched_block(SA2_WAIT_CS, a[0], 0, "EnterCriticalSection") == SA2_WAKE_NEVER) {
            /* Say how the holder ended, if it has. On 2026-09-22 this note named
             * a deadlock whose cause was the holder dying on a function-table
             * miss with the lock held; the wait is only where that shows, and
             * the miss is what has to be fixed. */
            char holder[160] = "";
            for (int i = 0; i < sa2_sched_count(); i++) {
                if (sa2_sched_tid(i) != own || !sa2_sched_finished(i)) continue;
                const char *why = sa2_sched_trap_note(i);
                snprintf(holder, sizeof holder, "; that thread has finished with it held (%s)",
                         why && *why ? why : "its routine returned");
                break;
            }
            sa2_trap_note("EnterCriticalSection(%#010x): thread %u holds it with recursion "
                          "%u and no other guest thread can run, so this wait has no end%s",
                          a[0], own, rec, holder);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT,
                           g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
        }
    }
    gpoke32(a[0] + CS_LOCKCOUNT, gpeek32(a[0] + CS_LOCKCOUNT) + 1u);
    gpoke32(a[0] + CS_RECURSION, gpeek32(a[0] + CS_RECURSION) + 1u);
    gpoke32(a[0] + CS_OWNER, me);
    return 0;
}

static uint32_t imp_LeaveCriticalSection(void *s, const uint32_t *a) {
    (void)s;
    uint32_t rec = gpeek32(a[0] + CS_RECURSION);
    if (!rec) {
        sa2_trap_note("LeaveCriticalSection on %#010x whose recursion count is already 0", a[0]);
        sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
    }
    gpoke32(a[0] + CS_RECURSION, rec - 1u);
    gpoke32(a[0] + CS_LOCKCOUNT, gpeek32(a[0] + CS_LOCKCOUNT) - 1u);
    if (rec == 1u) {
        gpoke32(a[0] + CS_OWNER, 0);
        sa2_sched_wake(SA2_WAIT_CS, a[0], 0);
        /* G1: "released only inside the runtime's sync and time entry points
         * (WaitForSingleObject, Sleep, event and critical-section calls, the
         * message pump), where the runtime picks the next runnable thread".
         * This is that, at the one critical-section call where the guest has
         * given something up. The comment above used to argue the other way --
         * that handing off on an uncontended lock makes the interleaving depend
         * on lock traffic -- and that argument lost to a measurement: the
         * guest's ADX streaming poll makes four uncontended critical-section
         * calls per iteration and no other runtime call at all, so without this
         * the CRI server thread sat READY and was never once scheduled. An
         * interleaving that depends on lock traffic is still a function of the
         * program; a worker that never runs is not. */
        if (sa2_sched_cs_handoff()) sa2_sched_yield("LeaveCriticalSection");
    }
    return 0;
}

static uint32_t imp_DeleteCriticalSection(void *s, const uint32_t *a) {
    (void)s;
    sa2_guest_fill(a[0], 0, 24);
    return 0;
}

/* Interlocked*: one thread, so the atomicity is free and the *value* is the
 * whole contract. Returning a plausible constant here is the archetypal silent
 * corruption -- the CRT uses InterlockedDecrement for reference counts. */
static uint32_t imp_InterlockedIncrement(void *s, const uint32_t *a) {
    (void)s;
    uint32_t v = gpeek32(a[0]) + 1u;
    gpoke32(a[0], v);
    return v;
}
static uint32_t imp_InterlockedDecrement(void *s, const uint32_t *a) {
    (void)s;
    uint32_t v = gpeek32(a[0]) - 1u;
    gpoke32(a[0], v);
    return v;
}
static uint32_t imp_InterlockedExchange(void *s, const uint32_t *a) {
    (void)s;
    uint32_t old = gpeek32(a[0]);
    gpoke32(a[0], a[1]);
    return old;
}

/* ---------------------------------------------------- process, version */

/* GetCurrentProcess returns the pseudo-handle (HANDLE)-1 on Windows. */
static uint32_t imp_GetCurrentProcess(void *s, const uint32_t *a) { (void)s; (void)a; return 0xffffffffu; }
static uint32_t imp_IsDebuggerPresent(void *s, const uint32_t *a) { (void)s; (void)a; return 0; }

/* SetUnhandledExceptionFilter: record the guest filter and return the previous
 * one, which is the contract the CRT relies on to chain. */
static uint32_t g_ueh_filter;
static uint32_t imp_SetUnhandledExceptionFilter(void *s, const uint32_t *a) {
    (void)s;
    uint32_t prev = g_ueh_filter;
    g_ueh_filter = a[0];
    return prev;
}

/* KERNEL32!GetVersionExW(LPOSVERSIONINFOW). The values are a named placeholder
 * -- Windows 7 SP1 x86, the platform the 2012 PC build shipped for -- because
 * no live-oracle capture exists to agree with and the guest branches on them.
 * SA2_GUEST_WINVER moves major.minor.build so a check that depends on them can
 * be moved without a rebuild. */
static uint32_t g_ver_major = 6, g_ver_minor = 1, g_ver_build = 7601;
static uint32_t imp_GetVersionExW(void *s, const uint32_t *a) {
    (void)s;
    uint32_t cb = gpeek32(a[0]);
    if (cb < 276u) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    sa2_guest_fill(a[0] + 4u, 0, cb - 4u);
    gpoke32(a[0] + 4u, g_ver_major);
    gpoke32(a[0] + 8u, g_ver_minor);
    gpoke32(a[0] + 12u, g_ver_build);
    gpoke32(a[0] + 16u, 2u);                 /* VER_PLATFORM_WIN32_NT */
    if (cb >= 284u) {
        /* OSVERSIONINFOEXW, little-endian: wServicePackMajor at 276, wServicePackMinor
         * at 278, wSuiteMask at 280, wProductType at 282, wReserved at 283. The first
         * version of this wrote two packed dwords whose halves landed one WORD to the
         * left, so it reported service pack 1.1 on a domain controller. Each field is
         * written on its own now, because a packed constant that is one WORD out reads
         * exactly like a packed constant that is right. */
        uint8_t sp[4] = {1, 0, 0, 0};                 /* SP major 1, SP minor 0 */
        uint8_t tail[4] = {0x00, 0x01, 1, 0};         /* wSuiteMask 0x0100, VER_NT_WORKSTATION */
        gput(a[0] + 276u, (const char *)sp, 4);
        gput(a[0] + 280u, (const char *)tail, 4);
    }
    return 1;
}

static uint32_t imp_TerminateProcess(void *s, const uint32_t *a) {
    (void)s;
    g_exited = 1;
    g_exit_code = a[1];
    sa2_trap_note("the guest called TerminateProcess(%#010x, %u) -- it ended the process itself",
                  a[0], a[1]);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

/* ------------------------------------------------ named kernel objects
 *
 * WinMain's first act is CreateMutexW on a name in the image's own .rdata: the
 * single-instance guard. One process, so the name is always new and
 * GetLastError is 0 rather than ERROR_ALREADY_EXISTS -- which is the answer
 * that lets the game run, and also the true one, because nothing else in this
 * runtime has created it. The name is recorded, because a second process model
 * is a later decision and this is its input. */
#define SA2_NAMED_OBJ_MAX 32u
static const char *g_named_obj[SA2_NAMED_OBJ_MAX];
static uint32_t    g_named_obj_count;

static uint32_t named_object(const char *name, const char *kind) {
    if (g_named_obj_count < SA2_NAMED_OBJ_MAX)
        g_named_obj[g_named_obj_count] = intern(name, strlen(name));
    g_named_obj_count++;
    g_last_error = SA2_ERROR_SUCCESS;     /* not ERROR_ALREADY_EXISTS: one process */
    return handle_new(H_PROCESS, kind);
}

static uint32_t imp_CreateMutexW(void *s, const uint32_t *a) {
    (void)s;
    char buf[260];
    buf[0] = 0;
    if (a[2]) gwstr(a[2], buf, sizeof buf);
    return named_object(buf, "mutex");
}

/* ------------------------------------------------------- event objects
 *
 * The CRI middleware wrapper at 0x0085342c creates an event per worker and the
 * routine two instructions above it waits on one:
 *
 *     0085344d  call [87310Ch]        ; CreateEventA(NULL, TRUE, FALSE, NULL)
 *     00853453  cmp eax,edi
 *     00853459  push 89A6CCh          ; "E2008070324:Can not create event object."
 *
 * so a null handle is a named failure the guest reports and not a path worth
 * taking. The object itself is two bits, and this runtime holds both beside
 * the handle: the guest never dereferences an event.
 *
 * **`WaitForSingleObject` refuses a wait that could not end.** This process has
 * one guest thread. Nothing can signal an event while the caller blocks, so a
 * wait on an unsignalled event with a non-zero timeout is a hang with no exit,
 * and a runtime that slept would produce a run that stops for no recorded
 * reason. The same rule `sa2_window.c` applies to `GetMessageW` on an empty
 * queue, for the same reason. A zero timeout is a poll and answers
 * WAIT_TIMEOUT, which is a true answer. */
/* Waits that actually blocked and handed the guest lock on. Incremented at the
 * point the hand-off is entered, which is after the fast paths above it have
 * already declined, so a run whose every wait returned immediately reports 0. */
static uint64_t g_waits_blocked;
uint64_t sa2_import_waits_blocked(void) { return g_waits_blocked; }

#define SA2_WAIT_OBJECT_0  0x00000000u
#define SA2_WAIT_TIMEOUT   0x00000102u
#define SA2_WAIT_FAILED    0xFFFFFFFFu

static uint32_t caller_of_current(void);

static uint32_t make_event(uint32_t manual, uint32_t initial, const char *name) {
    const uint32_t h = handle_new(H_EVENT, "event");
    handle_row *r = handle_row_of(h);
    if (!r) { g_last_error = SA2_ERROR_NOT_ENOUGH_MEMORY; return 0; }
    r->manual_reset = manual ? 1u : 0u;
    r->signalled = initial ? 1u : 0u;
    r->create_caller = caller_of_current();
    if (name && *name) named_object(name, "event");
    g_last_error = SA2_ERROR_SUCCESS;
    return h;
}

static uint32_t imp_CreateEventA(void *s, const uint32_t *a) {
    (void)s;
    char buf[260];
    buf[0] = 0;
    if (a[3]) gstr(a[3], buf, sizeof buf);
    return make_event(a[1], a[2], buf);
}

static uint32_t imp_CreateEventW(void *s, const uint32_t *a) {
    (void)s;
    char buf[260];
    buf[0] = 0;
    if (a[3]) gwstr(a[3], buf, sizeof buf);
    return make_event(a[1], a[2], buf);
}

static handle_row *event_arg(uint32_t h, const char *what) {
    handle_row *r = handle_row_of(h);
    if (!r || r->kind != H_EVENT) {
        sa2_trap_note("%s(%#010x): not an event handle this runtime created", what, h);
        sa2_trap_raise(SA2_TRAP_FAULT, h);
    }
    return r;
}

static uint32_t imp_SetEvent(void *s, const uint32_t *a) {
    (void)s;
    handle_row *r = event_arg(a[0], "SetEvent");
    r->signalled = 1;
    r->sets++;
    sa2_sched_wake(SA2_WAIT_EVENT, a[0], 0);
    return 1;
}

/* The event census: one row per event this runtime created, with the guest
 * address that created it and both ends of its traffic. Returns 0 past the
 * last event, so a reader cannot mistake "no more rows" for "row of zeroes". */
int sa2_import_event_at(unsigned i, sa2_event_row *out) {
    unsigned seen = 0;
    for (uint32_t k = 0; k < g_handle_used; k++) {
        if (g_handles[k].kind != H_EVENT) continue;
        if (seen++ != i) continue;
        out->handle        = SA2_HANDLE_BASE + k * SA2_HANDLE_STRIDE;
        out->create_caller = g_handles[k].create_caller;
        out->sets          = g_handles[k].sets;
        out->resets        = g_handles[k].resets;
        out->waits         = g_handles[k].waits;
        out->waits_blocked = g_handles[k].waits_blocked;
        out->manual_reset  = g_handles[k].manual_reset;
        out->signalled     = g_handles[k].signalled;
        return 1;
    }
    return 0;
}

unsigned sa2_import_event_count(void) {
    unsigned n = 0;
    for (uint32_t k = 0; k < g_handle_used; k++) if (g_handles[k].kind == H_EVENT) n++;
    return n;
}

/* What runtime/sa2_threads.c calls when a multimedia timer's event callback
 * fires. `pulse` is TIME_CALLBACK_EVENT_PULSE, which does not leave the event
 * signalled -- the scheduler has already released the one waiter, and setting
 * the flag as well would hand a second, phantom tick to the next waiter. */
int sa2_event_signal(uint32_t handle, int pulse) {
    handle_row *r = handle_row_of(handle);
    if (!r || r->kind != H_EVENT) return 0;
    if (!pulse) r->signalled = 1;
    return 1;
}

static uint32_t imp_ResetEvent(void *s, const uint32_t *a) {
    (void)s;
    handle_row *r = event_arg(a[0], "ResetEvent");
    r->signalled = 0;
    r->resets++;
    return 1;
}

/* KERNEL32!WaitForSingleObject over events and threads.
 *
 * The single-threaded runtime refused every wait that could not return
 * immediately, because nothing could signal an event while the caller blocked.
 * With G1's scheduler something can: the runtime hands the guest lock to the
 * next runnable thread, and when nothing is runnable it advances virtual time
 * to the nearest timer tick or timeout. The refusal survives for the case that
 * is still true -- nothing runnable, no deadline -- and now names it.
 *
 * INFINITE is 0xFFFFFFFF and is a wait with no deadline, not a wait of four
 * billion milliseconds: the difference decides whether the clock may be
 * advanced on this thread's account. */
#define SA2_INFINITE 0xFFFFFFFFu

static uint32_t imp_WaitForSingleObject(void *s, const uint32_t *a) {
    (void)s;
    if (sa2_thread_index_of_handle(a[0]) >= 0) {
        const int idx = sa2_thread_index_of_handle(a[0]);
        for (;;) {
            /* The second test holds only if the row went to a later thread
             * while this wait was pending, which needs the thread waited for to
             * have ended and its handle to have been closed under the waiter
             * (undefined on Windows): the thread did end. */
            if (sa2_sched_finished(idx) || sa2_thread_index_of_handle(a[0]) != idx)
                return SA2_WAIT_OBJECT_0;
            if (a[1] == 0) return SA2_WAIT_TIMEOUT;
            uint64_t dl = (a[1] == SA2_INFINITE) ? 0ull
                                                 : sa2_virtual_us() + (uint64_t)a[1] * 1000ull;
            int w = sa2_sched_block(SA2_WAIT_THREAD, (uint32_t)idx, dl,
                                    "WaitForSingleObject(thread)");
            if (w == SA2_WAKE_TIMEOUT) return SA2_WAIT_TIMEOUT;
            if (w == SA2_WAKE_NEVER) {
                sa2_trap_note("WaitForSingleObject(%#010x, %u) on guest thread %d, which is "
                              "%s: no other guest thread can run and no timer is pending, so "
                              "this wait has no end", a[0], a[1], idx,
                              sa2_sched_state_name(idx));
                sa2_trap_raise(SA2_TRAP_CONTROL_LEFT,
                               g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
            }
        }
    }
    handle_row *r = handle_row_of(a[0]);
    /* No handle, or a thread handle whose thread ended and was closed and whose
     * row went to a later thread: a closed handle, which Windows refuses. */
    if (!r || r->kind == H_THREAD) {
        g_last_error = SA2_ERROR_INVALID_HANDLE;
        return SA2_WAIT_FAILED;
    }
    if (r->kind != H_EVENT) {
        sa2_trap_note("WaitForSingleObject(%#010x, %u): this runtime models the wait state of "
                      "events and threads only, and that handle is a %s", a[0], a[1],
                      r->name ? r->name : "kernel object of another kind");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    r->waits++;
    for (;;) {
        if (r->signalled) {
            if (!r->manual_reset) r->signalled = 0;
            return SA2_WAIT_OBJECT_0;
        }
        if (a[1] == 0) return SA2_WAIT_TIMEOUT;
        uint64_t dl = (a[1] == SA2_INFINITE) ? 0ull
                                             : sa2_virtual_us() + (uint64_t)a[1] * 1000ull;
        g_waits_blocked++;
        r->waits_blocked++;
        int w = sa2_sched_block(SA2_WAIT_EVENT, a[0], dl, "WaitForSingleObject(event)");
        /* A pulse released exactly this thread and left no flag behind, so
         * re-reading `signalled` here would block again on a tick that did
         * arrive. That is the whole reason SA2_WAKE_PULSE is a distinct code. */
        if (w == SA2_WAKE_PULSE) return SA2_WAIT_OBJECT_0;
        if (w == SA2_WAKE_TIMEOUT) return SA2_WAIT_TIMEOUT;
        if (w == SA2_WAKE_NEVER) {
            if (sa2_sched_budget_hit())
                sa2_trap_note("WaitForSingleObject(%#010x, %u): the virtual clock reached the "
                              "%llu ms budget with this thread still waiting. Nothing but "
                              "timers was advancing the guest, which is a livelock rather "
                              "than a hang; SA2_VIRTUAL_BUDGET_MS moves the bound.",
                              a[0], a[1], (unsigned long long)(sa2_sched_budget_us() / 1000ull));
            else
                sa2_trap_note("WaitForSingleObject(%#010x, %u) on an event nothing has signalled: "
                              "no other guest thread is runnable and no timer is pending, so "
                              "nothing can signal it and the wait has no end. A timeout of 0 "
                              "polls and is answered.", a[0], a[1]);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT,
                           g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
        }
        /* SA2_WAKE_SIGNAL: loop and read the flag the signaller set. */
    }
}

/* KERNEL32!CreateThread and the rest of the thread surface, over
 * runtime/sa2_threads.c.
 *
 * The single-threaded runtime refused here on purpose, so that the CRT's
 * `_beginthreadex` at 0x00828b54 took its own documented failure path rather
 * than being handed a handle to a thread that would never run. That refusal
 * was right while there was nothing to run the thread on; it is now the thing
 * standing between the guest and its main loop, and G1's decision says what
 * replaces it.
 *
 * A refusal is still possible and still honest -- no stack slot, no TEB slot,
 * the host declining a thread -- and it lands on exactly the same guest path it
 * did before, with the reason recorded instead of implied.
 *
 * The handle is a real handle from the table above, so `CloseHandle` and
 * `WaitForSingleObject` see an object of a kind they know, and the thread index
 * is carried in the handle row: a guest that keeps a handle around after the
 * thread ends still gets a true answer from `GetExitCodeThread`. */
static uint32_t g_threads_refused;
static uint32_t g_threads_created;
/* Why the last CreateThread was refused. The trap note said so, and the next
 * note overwrote it: a stage run refused one of fifty-seven threads and the
 * reason was nowhere in its record (2026-09-23). */
static const char *g_thread_refused_why = "";
static uint32_t g_thread_refused_start;
uint32_t sa2_import_threads_refused(void) { return g_threads_refused; }
const char *sa2_import_thread_refused_why(void) { return g_thread_refused_why; }
uint32_t sa2_import_thread_refused_start(void) { return g_thread_refused_start; }
uint32_t sa2_import_threads_created(void) { return g_threads_created; }

/* A thread handle names the thread it was created for and no other: once that
 * thread has ended and this handle has been closed, the scheduler may give the
 * row to a later thread (SA2_MAX_GUEST_THREADS in sa2_threads.h), and then this
 * handle names nothing, as a closed handle names nothing on Windows. */
int sa2_thread_index_of_handle(uint32_t h) {
    const handle_row *r = handle_of(h);
    if (!r || r->kind != H_THREAD) return -1;
    if (sa2_sched_seq(r->thread_index) != r->thread_seq) return -1;
    return (int)r->thread_index;
}

/* The per-thread state this file keeps by row, cleared for a new thread: a
 * reused row's last thread's affinity and name are not the new thread's. */
static void thread_row_reset(int idx);

/* CreateThread(lpAttr, cbStack, lpStart, lpParam, dwFlags, lpThreadId) */
#define SA2_CREATE_SUSPENDED 0x00000004u

static uint32_t imp_CreateThread(void *s, const uint32_t *a) {
    (void)s;
    /* The handle first, then the thread: a thread spawned before its handle
     * was refused kept running with nobody holding it, and the refusal went
     * unrecorded (the rule-5 review, 2026-09-23). A row taken for a thread
     * that is then refused is never handed out and never reused. */
    const uint32_t h = handle_new(H_THREAD, "thread");
    handle_row *r = handle_row_of(h);
    const char *why = NULL;
    int idx = -1;
    if (!h || !r)
        why = "the runtime has no kernel-object handle left (SA2_HANDLE_MAX)";
    else
        idx = sa2_sched_spawn(a[2], a[3], (a[4] & SA2_CREATE_SUSPENDED) != 0, &why);
    if (idx < 0) {
        if (r) r->kind = H_FREE;
        g_threads_refused++;
        g_thread_refused_why = why ? why : "refused";
        g_thread_refused_start = a[2];
        sa2_trap_note("CreateThread(start %#010x): %s", a[2], why ? why : "refused");
        g_last_error = SA2_ERROR_NOT_ENOUGH_MEMORY;
        if (a[5]) gpoke32(a[5], 0);
        return 0;
    }
    r->thread_index = (uint8_t)idx;
    r->thread_seq = (uint32_t)sa2_sched_seq(idx);
    thread_row_reset(idx);
    g_threads_created++;
    g_last_error = SA2_ERROR_SUCCESS;
    if (a[5]) gpoke32(a[5], sa2_sched_tid(idx));
    return h;
}

static int thread_arg(uint32_t h, const char *what) {
    const int idx = sa2_thread_index_of_handle(h);
    if (idx < 0) {
        sa2_trap_note("%s(%#010x): not a thread handle this runtime created, or the handle "
                      "of a thread that ended and was closed", what, h);
        sa2_trap_raise(SA2_TRAP_FAULT, h);
    }
    return idx;
}

/* ResumeThread/SuspendThread return the *previous* suspend count, or -1. The
 * guest at 0x007852cc tests nothing, but the CRT does, and a plausible constant
 * here is the archetypal silent corruption this runtime keeps refusing. */
static uint32_t imp_ResumeThread(void *s, const uint32_t *a) {
    (void)s;
    const int prev = sa2_sched_resume(thread_arg(a[0], "ResumeThread"));
    return (uint32_t)prev;
}

static uint32_t imp_SuspendThread(void *s, const uint32_t *a) {
    (void)s;
    const int prev = sa2_sched_suspend(thread_arg(a[0], "SuspendThread"));
    return (uint32_t)prev;
}

/* SetThreadPriority is recorded, not honoured: see the hand-off rule in
 * runtime/sa2_threads.c. Returning failure would send the guest down a path it
 * does not need; returning success while recording the value is the truthful
 * answer, because the call did do the one observable thing Windows guarantees,
 * which is that GetThreadPriority reads it back. */
static uint32_t imp_SetThreadPriority(void *s, const uint32_t *a) {
    (void)s;
    sa2_sched_set_priority(thread_arg(a[0], "SetThreadPriority"), (int32_t)a[1]);
    return 1;
}

static uint32_t imp_GetThreadPriority(void *s, const uint32_t *a) {
    (void)s;
    return (uint32_t)sa2_sched_priority(thread_arg(a[0], "GetThreadPriority"));
}

/* SetThreadAffinityMask answers as Windows does for the machine the capture's
 * profile describes. It returns the thread's previous mask when every
 * processor the new one names is in the process's mask. It returns 0, with
 * ERROR_INVALID_PARAMETER, for an empty mask or one that names a processor
 * outside it. A thread starts with the process's mask. The answer is tested:
 * CRI's affinity routine (007dadd0), which Sofdec's worker set-up reaches
 * only when GetSystemInfo reports more than one processor, keeps the mask it
 * asked for only when the answer is not 0 (007dae41). So a constant would
 * pass a request native refuses. Whatever the masks say, the scheduler runs
 * one guest thread at a time (G1's thread strategy).
 *
 * SetThreadIdealProcessor's answer the image drops (00865293, whose method's
 * callers overwrite eax), so 0, an ideal processor every host has, is enough. */
static uint32_t g_process_mask;          /* defined with the processor count, below */
static uint32_t g_thread_affinity[SA2_MAX_GUEST_THREADS];
static char     g_thread_names[SA2_MAX_GUEST_THREADS][32];   /* RaiseException's, below */
static void thread_row_reset(int idx) {
    if (idx < 0 || idx >= SA2_MAX_GUEST_THREADS) return;
    g_thread_affinity[idx] = 0;
    g_thread_names[idx][0] = '\0';
}
static uint32_t imp_SetThreadAffinityMask(void *s, const uint32_t *a) {
    (void)s;
    const int idx = thread_arg(a[0], "SetThreadAffinityMask");
    const uint32_t mask = a[1];
    if (!mask || (mask & ~g_process_mask)) {
        g_last_error = SA2_ERROR_INVALID_PARAMETER;
        return 0;
    }
    const uint32_t prev = g_thread_affinity[idx] ? g_thread_affinity[idx] : g_process_mask;
    g_thread_affinity[idx] = mask;
    return prev;
}
static uint32_t imp_SetThreadIdealProcessor(void *s, const uint32_t *a) {
    (void)s; (void)thread_arg(a[0], "SetThreadIdealProcessor");
    return 0u;
}

#define SA2_STILL_ACTIVE 0x00000103u
static uint32_t imp_GetExitCodeThread(void *s, const uint32_t *a) {
    (void)s;
    const int idx = thread_arg(a[0], "GetExitCodeThread");
    if (a[1]) gpoke32(a[1], sa2_sched_finished(idx) ? sa2_sched_exit_code(idx)
                                                    : SA2_STILL_ACTIVE);
    return 1;
}

/* ExitThread leaves the thread's own lifted code without returning through it.
 * The runtime cannot resume a host call stack it has abandoned, so this ends
 * the guest thread by trapping out of its `sa2_call` -- which is what
 * `thread_body` in sa2_threads.c already handles, because a worker that traps
 * and a worker that returns both end the same way. The exit code is recorded
 * first so `GetExitCodeThread` answers with it. */
static uint32_t imp_ExitThread(void *s, const uint32_t *a) {
    (void)s;
    sa2_sched_exit_current(a[0]);
    sa2_trap_note("the guest called ExitThread(%u) on guest thread %d", a[0],
                  sa2_sched_current());
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

/* ole32!CoInitializeEx / CoInitialize / CoUninitialize.
 *
 * These initialise COM *for the calling thread* and nothing more. This runtime
 * enters no apartment and keeps one depth counter for the whole process, not
 * one per guest thread: the first call succeeds, and every later one answers
 * S_FALSE until CoUninitialize has brought the depth back to 0. That is
 * Windows' answer only while the calls come from one thread: a thread's first
 * call made while another thread holds a count would get S_FALSE where Windows
 * gives S_OK. The image's
 * COM set-up and teardown sites are CoInitialize at 008646BC (IsXInputDevice),
 * CoInitializeEx at 007841D4 (the XAudio2 set-up), and CoUninitialize at
 * 0077EE39, 00784275 (a jmp) and 0086496C; natively the set-up calls ran on the
 * main thread, 0024 (native-outcomes-v1a/native-run.log:15240 is
 * IsXInputDevice's CoUninitialize, :15506 the XAudio2 set-up's
 * CoInitializeEx). The requested apartment model is recorded rather than
 * checked, because nothing here behaves differently for one.
 *
 * It is deliberately not a licence for the rest of COM. `CoCreateInstance`
 * answers only the two classes the image asks for, each from a module of its
 * own, and refuses every other class by name with its CLSID recorded (the
 * comment above imp_CoCreateInstance) -- which is the useful outcome, and the
 * one a blanket "COM works" would hide.
 *
 * The answer comes from this depth counter, never from the device profile.
 * IsXInputDevice's CoInitialize (008646BC) meets it once for each game
 * controller the game asks IsXInputDevice about, and native's call succeeded:
 * its CoUninitialize, native-outcomes-v1a/native-run.log:15240, is reachable
 * only past the `setge` at 008646D6 that keeps the sign of the answer. The
 * profile probe's own CoInitialize at the Mod Loader's Init meets a different
 * apartment, the multithreaded one the game's XAudio2 set-up entered at
 * 007841D4, so the profile's `wmi_coinit` record is information only
 * (runtime/sa2_devprofile.h). */
static uint32_t g_co_init_depth;
static uint32_t g_co_init_model;

static uint32_t imp_CoInitializeEx(void *s, const uint32_t *a) {
    (void)s;
    g_co_init_model = a[1];
    return g_co_init_depth++ ? 0x00000001u /* S_FALSE */ : 0u /* S_OK */;
}

static uint32_t imp_CoInitialize(void *s, const uint32_t *a) {
    (void)s; (void)a;
    return g_co_init_depth++ ? 0x00000001u : 0u;
}

static uint32_t imp_CoUninitialize(void *s, const uint32_t *a) {
    (void)s; (void)a;
    if (g_co_init_depth) g_co_init_depth--;
    return 0;
}

/* ole32!CoCreateInstance(rclsid, pUnkOuter, dwClsContext, riid, ppv)
 *
 * The image asks for two classes, one site each, and the native game gets
 * both (agents-journals/s4/2026-09-23-native-outcomes.md). Each is answered by
 * the module that implements it, which reads the outer object and the context
 * off this import's frame (import_native leaves ESP on the return address
 * until the stub returns), refuses by name anything its one site never
 * pushes, and says it handled the call:
 *
 *   - CLSID_WbemLocator {4590F811-1D3A-11D0-891F-00AA004B2E24}, from
 *     IsXInputDevice (00864680, the call at 008646DB), each time DirectInput's
 *     enumeration callback asks it about a game controller:
 *     runtime/sa2_wbem.h. It has no switch of its own: with SA2_DINPUT_DEVICE
 *     off nothing is enumerated and nothing asks.
 *   - XAudio2 2.6's engine {3EDA9B49-2085-498B-9BB2-39A6778493DE} against
 *     IID_IXAudio2 {8BCF1F58-9FE7-4583-8AC6-E2ADC465C8BB}, from the sound
 *     system's set-up: runtime/sa2_xaudio2.h. With SA2_XAUDIO2_DEVICE off it
 *     refuses the class as this stub always did, REGDB_E_CLASSNOTREG and a
 *     null *ppv through the refusal marker, and only under the default device
 *     profile: a capture's game got the engine, so configuration refuses a
 *     capture's profile with the device off. The site, and the branch the
 *     guest takes when the call fails:
 *
 *     00784103  call [8733ECh]        ; CoCreateInstance(CLSID, NULL, 1, IID, &p)
 *     00784109  test eax,eax
 *     0078410b  jl 0078413Ch          ; failure: return the HRESULT, touch nothing
 *
 * Any other class answers REGDB_E_CLASSNOTREG through the marker at the end of
 * imp_CoCreateInstance, which is Windows' answer for a class nobody
 * registered; tools/s4/dinput_surface.py's `one-site-each` claim holds the
 * import's slot to the two sites above. A call meets the marker at most once:
 * a class a module handled returns that module's answer, its refusal
 * included, and never reaches the one at the end. The CLSID and IID of every
 * call are recorded, answered or not, so a class this runtime does not
 * provide is a fact in the JSON and not a guess in prose. */
#define SA2_COCREATE_MAX 8u
static struct { uint8_t clsid[16], iid[16]; uint32_t caller; } g_cocreate[SA2_COCREATE_MAX];
static uint32_t g_cocreate_count;

uint32_t sa2_import_cocreate_count(void) { return g_cocreate_count; }
const uint8_t *sa2_import_cocreate_clsid(uint32_t i) {
    return i < g_cocreate_count && i < SA2_COCREATE_MAX ? g_cocreate[i].clsid : NULL;
}
const uint8_t *sa2_import_cocreate_iid(uint32_t i) {
    return i < g_cocreate_count && i < SA2_COCREATE_MAX ? g_cocreate[i].iid : NULL;
}
uint32_t sa2_import_cocreate_caller(uint32_t i) {
    return i < g_cocreate_count && i < SA2_COCREATE_MAX ? g_cocreate[i].caller : 0u;
}

static uint32_t imp_CoCreateInstance(void *s, const uint32_t *a) {
    uint8_t clsid[16], iid[16];
    memset(clsid, 0, sizeof clsid);
    memset(iid, 0, sizeof iid);
    if (a[0]) sa2_peek(a[0], 16, clsid);
    if (a[3]) sa2_peek(a[3], 16, iid);
    if (g_cocreate_count < SA2_COCREATE_MAX) {
        memcpy(g_cocreate[g_cocreate_count].clsid, clsid, 16);
        memcpy(g_cocreate[g_cocreate_count].iid, iid, 16);
        g_cocreate[g_cocreate_count].caller = caller_of_current();
    }
    g_cocreate_count++;
    uint32_t handled = 0;
    uint32_t hr = sa2_wbem_cocreate(s, clsid, iid, a[4], &handled);
    if (handled) return hr;
    hr = sa2_xaudio2_cocreate(s, clsid, iid, a[4], &handled);
    if (handled) return hr;
    if (a[4]) gpoke32(a[4], 0);
    return sa2_import_refused("ole32.dll", "CoCreateInstance", 0x80040154u);  /* REGDB_E_CLASSNOTREG */
}

/* ole32!CoSetProxyBlanket(pProxy, dwAuthnSvc, dwAuthzSvc, pServerPrincName,
 * dwAuthnLevel, dwImpLevel, pAuthInfo, dwCapabilities). Its one caller is
 * IsXInputDevice, on the WMI services object ConnectServer returned; what
 * native ole32 does with that object, and the HRESULT, are
 * runtime/sa2_wbem.h's (sa2_wbem_proxy_blanket). */
static uint32_t imp_CoSetProxyBlanket(void *s, const uint32_t *a) {
    (void)s;
    return sa2_wbem_proxy_blanket(a, caller_of_current());
}

/* OLEAUT32 ordinals 2 and 6: SysAllocString(psz) and SysFreeString(bstr).
 *
 * IsXInputDevice allocates three names with SysAllocString and frees them with
 * SysFreeString, once for every game controller the capture's device profile
 * lists (native-outcomes-v1a:14844-14846 are the three allocations; Wine
 * traces no free). The WMI objects' Get allocates one more BSTR per entity
 * through sa2_bstr_alloc, which the game never frees: it imports no
 * VariantClear (runtime/sa2_wbem.h).
 *
 * A BSTR is what OLEAUT32 makes: a 4-byte byte count, the UTF-16 units and a
 * NUL unit, with the pointer at the first unit. It comes from the process heap,
 * the heap GetProcessHeap answers and OLEAUT32's allocator uses on Windows, and
 * never from the game's CRT heap, which the CRT makes for itself with
 * HeapCreate. So SysFreeString of anything but a live BSTR traps, naming it,
 * instead of freeing a block of another heap.
 *
 * The guest string is read unit by unit and bounds-checked: a unit outside the
 * guest space traps, and so does a string with no NUL within
 * SA2_BSTR_MAX_UNITS. The image's three strings are 8 to 15 units. */
#define SA2_BSTR_MAX_UNITS 4096u
static uint32_t g_bstr_allocs, g_bstr_frees;

uint32_t sa2_bstr_alloc(const uint16_t *units, uint32_t n) {
    if (n > SA2_BSTR_MAX_UNITS) return 0;
    const uint32_t b = heap_alloc(process_heap(), 4u + 2u * n + 2u, 0);
    if (!b) return 0;
    gpoke32(b, 2u * n);
    if (n) gput(b + 4u, (const char *)units, 2u * n);
    gput(b + 4u + 2u * n, "\0\0", 2u);
    g_bstr_allocs++;
    return b + 4u;
}

void sa2_bstr_free(uint32_t bstr) {
    if (!bstr) return;
    /* A live process-heap block is not enough: the game allocates from that
     * heap itself (HeapAlloc at 007bf148, among others). A BSTR's block was
     * asked for its prefix, its units and a NUL, so its requested size is the
     * byte count it holds plus 6. */
    const uint32_t b = heap_block_of(process_heap(), bstr - 4u, "SysFreeString");
    if ((uint64_t)gpeek32(b + 12u) != (uint64_t)gpeek32(bstr - 4u) + 6u) {   /* no wrap */
        sa2_trap_note("SysFreeString: %#010x is a process-heap block of %u bytes whose "
                      "prefix says %u, so it is no BSTR SysAllocString returned",
                      bstr, gpeek32(b + 12u), gpeek32(bstr - 4u));
        sa2_trap_raise(SA2_TRAP_FAULT, bstr);
    }
    heap_free(process_heap(), bstr - 4u);
    g_bstr_frees++;
}

uint32_t sa2_bstr_allocs(void) { return g_bstr_allocs; }
uint32_t sa2_bstr_frees(void) { return g_bstr_frees; }

static uint32_t imp_SysAllocString(void *s, const uint32_t *a) {
    (void)s;
    if (!a[0]) return 0;                 /* SysAllocString(NULL) is NULL */
    static uint16_t units[SA2_BSTR_MAX_UNITS];
    uint32_t n = 0;
    for (;; n++) {
        if (n == SA2_BSTR_MAX_UNITS) {
            sa2_trap_note("SysAllocString from %#010x: the string at %#010x has no NUL within "
                          "%u UTF-16 units", caller_of_current(), a[0], n);
            sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
        }
        uint16_t u = 0;
        if (sa2_peek(a[0] + 2u * n, 2, &u) != SA2_OK) {
            sa2_trap_note("SysAllocString from %#010x: unit %u of the string at %#010x is "
                          "outside the guest space", caller_of_current(), n, a[0]);
            sa2_trap_raise(SA2_TRAP_FAULT, a[0] + 2u * n);
        }
        if (!u) break;
        units[n] = u;
    }
    return sa2_bstr_alloc(units, n);
}

static uint32_t imp_SysFreeString(void *s, const uint32_t *a) {
    (void)s;
    sa2_bstr_free(a[0]);
    return 0;                            /* void: the game reads no EAX here */
}

/* WINMM!timeSetEvent(uDelay, uResolution, lpTimeProc, dwUser, fuEvent) and
 * timeKillEvent(uTimerID), over the timer table in runtime/sa2_threads.c.
 *
 * The one site in this image is the sound subsystem's:
 *
 *     00785283  call [87338Ch]        ; timeSetEvent(10, 0, hEvent, 0, 0x21)
 *     00785291  je 0078526Eh          ; 0: tear the object down and return 0
 *     007852a1  call 00828AD3h        ; _beginthreadex
 *
 * fuEvent 0x21 is TIME_PERIODIC | TIME_CALLBACK_EVENT_PULSE, so lpTimeProc is
 * an *event handle* and not a procedure: every 10 ms the timer pulses that
 * event, and the worker thread created on the next line is what waits on it.
 * Reading 0x21 as a callback pointer is the shape of mistake that would run
 * guest code at a handle value, so the flag is decoded rather than assumed.
 *
 * The clock is virtual, so "every 10 ms" means every 10 ms of the guest's own
 * time -- and the scheduler advances that clock only when no guest thread can
 * run. The tick is therefore a function of the program, which is what makes
 * two runs identical.
 *
 * A refusal is still possible: no timer slot, or an event handle this runtime
 * did not create. Both land on the guest's own failure path at 0x0078526E. */
static uint32_t g_timers_refused;
static uint32_t g_timers_created;
uint32_t sa2_import_timers_refused(void) { return g_timers_refused; }
uint32_t sa2_import_timers_created(void) { return g_timers_created; }

#define SA2_TIME_CALLBACK_MASK        0x0030u
#define SA2_TIME_CALLBACK_EVENT_SET   0x0010u
#define SA2_TIME_CALLBACK_EVENT_PULSE 0x0020u

static uint32_t imp_timeSetEvent(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t kind = a[4] & SA2_TIME_CALLBACK_MASK;
    if (kind == SA2_TIME_CALLBACK_EVENT_SET || kind == SA2_TIME_CALLBACK_EVENT_PULSE) {
        const handle_row *r = handle_of(a[2]);
        if (!r || r->kind != H_EVENT) {
            g_timers_refused++;
            sa2_trap_note("timeSetEvent(%u, %u, %#010x, %u, %#x): fuEvent asks for an event "
                          "callback and that handle is not an event this runtime created",
                          a[0], a[1], a[2], a[3], a[4]);
            return 0;
        }
    }
    const uint32_t id = sa2_timer_set(a[0], a[1], a[2], a[3], a[4]);
    if (!id) { g_timers_refused++; return 0; }
    g_timers_created++;
    return id;
}

static uint32_t imp_timeKillEvent(void *s, const uint32_t *a) {
    (void)s;
    return sa2_timer_kill(a[0]) ? 0u /* TIMERR_NOERROR */ : 97u /* MMSYSERR_INVALPARAM */;
}

/* KERNEL32!RaiseException(dwCode, dwFlags, nArgs, lpArgs), exactly as the G1
 * decision's section 2 step 1 specifies it:
 *
 *   "The runtime's `RaiseException` returns normally for the SetThreadName
 *    code 0x406D1388 (that is what the `__except` filter does on a machine
 *    with no debugger) and **traps** for every other code with the faulting
 *    address, classified as a named residue "EH raised" in M5."
 *
 * It is on the thread-creation path and nowhere else in the game region: G1
 * section 2 measured one game-region function using C SEH, 0x008651e0, whose
 * only caller is the thread-class wrapper at 0x00865380. Its frame is
 *
 *     00865225  push 406D1388h
 *     0086522a  call [8731B0h]        ; RaiseException
 *     00865230  jmp 00865239h         ; returns here, unwinds FS:[0], returns
 *
 * so returning normally is not a shortcut: it is what Windows does when no
 * debugger is attached and the registered filter says EXCEPTION_CONTINUE_EXECUTION.
 *
 * Every other code traps by name. Dispatch through the FS:[0] chain is G1's
 * step 2 and is not implemented, so a throw that reaches here is a work item
 * and must look like one. */
#define SA2_MS_VC_EXCEPTION 0x406D1388u
static uint32_t g_setthreadname_calls;
static uint32_t g_raise_other_calls;
uint32_t sa2_import_setthreadname_calls(void) { return g_setthreadname_calls; }
const char *sa2_import_thread_name(int idx) {
    return (idx >= 0 && idx < SA2_MAX_GUEST_THREADS) ? g_thread_names[idx] : "";
}

/* KERNEL32!GetProcessAffinityMask(hProcess, lpProcessMask, lpSystemMask).
 *
 * The thread-class wrapper at 0x00865260 asks for it and then tests bit
 * `ideal` of the *system* mask before calling SetThreadIdealProcessor. Its
 * local A is pushed first, so it is the third argument:
 *
 *     00865264  lea eax,[esp+4]       ; A
 *     0086526a  push eax              ; lpSystemAffinityMask
 *     00865277  call [873060h]        ; GetProcessAffinityMask
 *     00865286  shl edx,cl            ; cl = the ideal processor it wants
 *     00865288  test [esp+4],edx      ; A, the system mask
 *
 * so the value decides a branch and cannot be a plausible constant. CRI's
 * thread set-up asks too (007dae19) and uses only the call's success.
 *
 * The processor count and both masks are the capture's. The device profile
 * (runtime/sa2_devprofile.h) records what GetSystemInfo and
 * GetProcessAffinityMask answered natively. The game's answers depend on
 * them: Sofdec builds three decoding workers only when GetSystemInfo reports
 * more than one processor (the test at 007fbca9, in 007fbc90), and the boot
 * replay diverged at tick 379 for want of them
 * (agents-journals/s4/2026-09-23-native-outcomes.md). A replay therefore depends on the capture it replays, never on the machine it
 * runs on. The default profile, for a run that stands for no capture, answers
 * one processor. SA2_GUEST_CPUS moves the count, and only on the default
 * profile: beside a capture's profile it is refused at configuration. */
static uint32_t g_cpus = 1u, g_process_mask = 1u, g_system_mask = 1u;
static uint32_t cpu_mask(uint32_t n) {
    return (n >= 32u) ? 0xffffffffu : ((1u << n) - 1u);
}

static uint32_t imp_GetProcessAffinityMask(void *s, const uint32_t *a) {
    (void)s;
    if (!a[1] || !a[2]) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    gpoke32(a[1], g_process_mask);
    gpoke32(a[2], g_system_mask);
    return 1;
}

static uint32_t imp_RaiseException(void *s, const uint32_t *a) {
    (void)s;
    if (a[0] == SA2_MS_VC_EXCEPTION) {
        g_setthreadname_calls++;
        /* THREADNAME_INFO { DWORD dwType; LPCSTR szName; DWORD dwThreadID; DWORD dwFlags; }
         * The name is the guest's own string and the thread id is the guest's
         * own, so recording them turns a no-op into an observable: a run in
         * which the guest never named a thread and one in which it named two
         * are otherwise the same number of import calls. */
        if (a[2] >= 3u && a[3]) {
            uint32_t name_p = gpeek32(a[3] + 4u);
            uint32_t tid = gpeek32(a[3] + 8u);
            int slot = -1;
            for (int i = 0; i < sa2_sched_count() && i < SA2_MAX_GUEST_THREADS; i++)
                if (sa2_sched_tid(i) == tid || tid == 0xFFFFFFFFu) { slot = i; break; }
            if (slot < 0) slot = sa2_sched_current();
            if (name_p && slot >= 0 && slot < SA2_MAX_GUEST_THREADS)
                gstr(name_p, g_thread_names[slot], sizeof g_thread_names[0]);
        }
        return 0;
    }
    g_raise_other_calls++;
    sa2_trap_note("RaiseException(%#010x, %#x, %u, %#010x): this runtime returns normally "
                  "only for the SetThreadName code %#010x (G1 decision section 2 step 1); "
                  "dispatching any other exception through the FS:[0] chain is step 2 and "
                  "is not implemented", a[0], a[1], a[2], a[3], SA2_MS_VC_EXCEPTION);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

static uint32_t imp_CloseHandle(void *s, const uint32_t *a) {
    (void)s;
    if (std_index(a[0]) >= 0) return 1;           /* a standard pipe handle */
    if (sa2_vfs_is_handle(a[0]) || sa2_vfs_is_find_handle(a[0]))
        return sa2_vfs_close(a[0]) == SA2_VFS_OK;
    /* A thread handle is closed while the thread may still be running, which
     * Windows allows: the handle goes, the thread does not. The handle row is
     * kept, and the scheduler is told: a thread that has ended and whose one
     * handle is closed is a thread object Windows has freed, and its row may go
     * to a later thread once every row has been used. The handle then names
     * nothing (sa2_thread_index_of_handle), so a late GetExitCodeThread refuses
     * by name rather than reading the later thread. */
    if (!handle_of(a[0])) {
        sa2_trap_note("CloseHandle(%#010x): not a handle this runtime created", a[0]);
        sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
    }
    const int thread = sa2_thread_index_of_handle(a[0]);
    if (thread >= 0) sa2_sched_handle_closed(thread);
    return 1;
}

/* KERNEL32!lstrcmpA. Windows documents this as a locale word comparison; for
 * the ASCII option strings the image compares (WinMain tests its command tail
 * against "-r") a byte comparison has the same sign, and inventing a collation
 * order would be a value with no oracle. A byte outside ASCII refuses rather
 * than pretending to know where it sorts. */
static uint32_t imp_lstrcmpA(void *s, const uint32_t *a) {
    (void)s;
    char x[512], y[512];
    if (gstr(a[0], x, sizeof x) < 0 || gstr(a[1], y, sizeof y) < 0) {
        g_last_error = SA2_ERROR_INVALID_PARAMETER;
        return 0;
    }
    /* Both sides, not just the left one: the guest compares its own .rdata
     * strings against each other as often as it compares an option flag. */
    for (int side = 0; side < 2; side++) {
        const char *str = side ? y : x;
        for (const char *p = str; *p; p++) if ((unsigned char)*p >= 0x80u) {
            sa2_trap_note("lstrcmpA: \"%s\" has a byte outside ASCII and this runtime has no "
                          "collation order to compare it with", str);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT,
                           g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
        }
    }
    int r = strcmp(x, y);
    return (uint32_t)(int32_t)(r < 0 ? -1 : (r > 0 ? 1 : 0));
}

uint32_t    sa2_named_object_count(void) { return g_named_obj_count; }
const char *sa2_named_object_at(uint32_t i) {
    return i < g_named_obj_count && i < SA2_NAMED_OBJ_MAX ? g_named_obj[i] : NULL;
}

/* ------------------------------------------------- code pages and locale
 *
 * The CRT converts its wide environment block to ANSI during startup and
 * builds its ctype table from the ANSI code page, so these are on the path and
 * none of them can be a no-op.
 *
 * The *code page* is 1252, Western European, which is what the 2012 PC
 * build's target platform used, moved by SA2_GUEST_ACP. The oracle agrees:
 * its user32 writes U+20AC as 0x80 when wvsprintfA converts a wide argument
 * (Documentation/receipts/s4-wsprintf-oracle-v1.json). The *mapping* is a
 * published fixed table, and runtime/sa2_codepage.c implements it exactly,
 * including the 27 assigned positions in 0x80..0x9F that plain Latin-1 does
 * not have. Getting those wrong is the quiet kind of wrong: every ASCII string
 * would convert correctly and a quotation mark in a filename would not.
 * WideCharToMultiByte also carries the best fit Windows' default (no
 * WC_NO_BEST_FIT_CHARS) uses for a character the table lacks, U+0100 becoming
 * 'A', as the oracle's Wine has it (sa2_chardata.h, since 2026-09-26: see "The
 * character data" below). The wsprintf formatter (runtime/sa2_format.c)
 * writes '?' for such a character; whether the oracle's user32 picks the best
 * fit there is not measured (Documentation/receipts/s4-wsprintf-oracle-v1.json
 * has no case with one).
 *
 * Code page 936, Chinese Simplified's, is the other one the game's C runtime
 * converts through: in each archive loader's "Chinese-simplified" interval
 * (see "The locale" below), and natively nowhere else. It is Wine's table
 * whole, both directions and the best fit, from the oracle's c_936.nls
 * (runtime/sa2_locale.h), and conversions through it follow kernelbase's
 * mbstowcs_dbcs and wcstombs_dbcs_slow.
 *
 * Any other code page traps by name rather than falling back to Latin-1,
 * because a fallback that is right 95 % of the time is how a text bug survives
 * to S6; natively the replays convert through no other. */
static uint32_t g_acp = 1252;
static uint32_t g_oemcp = 437;

/* SA2_LOCALE_BREAK (see "The locale" below): `refuse` answers every locale
 * import as before 2026-09-26, code pages included; `noenum` only enumerates no
 * locale. Declared here for IsValidCodePage and GetCPInfo. */
enum { LOCALE_NATIVE = 0, LOCALE_BREAK_REFUSE = 1, LOCALE_BREAK_NOENUM = 2 };
static int g_locale_break;

static uint32_t resolve_cp(uint32_t cp) {
    if (cp == 0u) return g_acp;        /* CP_ACP */
    if (cp == 1u) return g_oemcp;      /* CP_OEMCP */
    if (cp == 2u || cp == 3u) return g_acp;  /* CP_MACCP / CP_THREAD_ACP: one thread, one locale */
    return cp;
}

/* A code page the runtime does not implement traps: natively the replays convert
 * through 1252 and, in the Chinese-simplified interval, 936, and no other. */
__attribute__((noreturn))
static void refuse_codepage(const char *what, uint32_t cp) {
    sa2_trap_note("%s: code page %u is not implemented; this runtime implements %u "
                  "(SA2_GUEST_ACP) and 936 exactly and refuses the rest rather than guessing",
                  what, cp, g_acp);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

static uint32_t imp_GetACP(void *s, const uint32_t *a) { (void)s; (void)a; return g_acp; }
static uint32_t imp_GetOEMCP(void *s, const uint32_t *a) { (void)s; (void)a; return g_oemcp; }

/* As kernelbase answers: FALSE for CP_ACP, CP_OEMCP, CP_MACCP and
 * CP_THREAD_ACP, which name no table; TRUE for the code pages this runtime has
 * the oracle's tables for (the ANSI and OEM code pages, 1252 and 437, and 936),
 * each of which Wine has; a trap for any other, whose answer is whether the
 * oracle's Proton ships its table, which nothing here records. The C runtime asks
 * about the locale's ANSI code page when it qualifies one: 1252 for the user's,
 * 936 for "Chinese-simplified". Under SA2_LOCALE_BREAK=refuse the answer before
 * 2026-09-26: TRUE for the ANSI and OEM code pages, CP_ACP and CP_OEMCP, and
 * FALSE for every other, 936 among them. */
static uint32_t imp_IsValidCodePage(void *s, const uint32_t *a) {
    (void)s;
    uint32_t r = 0u;
    if (g_locale_break == LOCALE_BREAK_REFUSE)
        r = a[0] == g_acp || a[0] == g_oemcp || a[0] == 0u || a[0] == 1u;
    else if (a[0] <= 3u)
        r = 0u;
    else if (a[0] == g_acp || a[0] == g_oemcp || a[0] == SA2_CP936)
        r = 1u;
    else
        refuse_codepage("IsValidCodePage", a[0]);
    locale_answer_line("IsValidCodePage", a, 1, r, NULL, 0);
    return r;
}

/* CPINFO { UINT MaxCharSize; BYTE DefaultChar[2]; BYTE LeadByte[12]; } -- 18
 * bytes, as kernelbase fills them from the code page's table. A single-byte code
 * page has MaxCharSize 1 and no lead-byte ranges; 936 has MaxCharSize 2 and the
 * one range 0x81..0xFE. Its default character is '?' too. */
static uint32_t imp_GetCPInfo(void *s, const uint32_t *a) {
    (void)s;
    uint32_t cp = resolve_cp(a[0]);
    const int dbcs = cp == SA2_CP936 && g_locale_break != LOCALE_BREAK_REFUSE;
    if (cp != g_acp && !dbcs) refuse_codepage("GetCPInfo", cp);
    if (!a[1]) {                               /* kernelbase's NULL CPINFO */
        g_last_error = SA2_ERROR_INVALID_PARAMETER;
        locale_answer_line("GetCPInfo", a, 1, 0u, NULL, 0);
        return 0;
    }
    if (sa2_guest_fill(a[1], 0, 18) != SA2_OK) {
        sa2_trap_note("GetCPInfo: %#010x..%#010x is outside the guest space", a[1], a[1] + 18u);
        sa2_trap_raise(SA2_TRAP_FAULT, a[1]);
    }
    gpoke32(a[1], dbcs ? 2u : 1u);
    uint8_t dflt[2] = {(uint8_t)'?', 0};
    gput(a[1] + 4u, (const char *)dflt, 2);
    if (dbcs) {
        uint8_t lead[12];
        sa2_cp936_lead_bytes(lead);
        gput(a[1] + 6u, (const char *)lead, 12);
    }
    locale_answer_guest("GetCPInfo", a, 1, 1u, a[1], 18u, 1u);
    return 1;
}

/* The character data.
 *
 * The C runtime builds its character tables from the six imports below: at
 * start-up _setmbcp's setSBUpLow fills _mbctype and _mbcasemap (their
 * addresses are in agents-journals/s4/2026-09-26-ctype.md, "The path", and
 * tools/s4/ctype_check.py's docstring and MB_TABLES, not here: every guest
 * address in this file counts toward Documentation/scripts/check_boundary.py's
 * limit of 100), and each setlocale that qualifies a locale fills the heap
 * tables isalpha, isspace, tolower and the C++ ctype facet read
 * (__init_ctype). Both convert the code page's 256 bytes with
 * MultiByteToWideChar, classify them with GetStringTypeW, case them with
 * LCMapStringW and convert the case back with WideCharToMultiByte. The answers
 * are Wine's (runtime/sa2_codepage.h, sa2_chardata.h), which the native
 * locale trace shows the game getting; until 2026-09-26 they were rules that
 * differed from Wine for twelve characters and one case
 * (agents-journals/s4/2026-09-26-ctype.md).
 *
 * SA2_CTYPE_BREAK=rules answers as before: the rules below, U+0192 its own
 * upper case, and no best fit. SA2_CTYPE_BREAK=nobestfit answers with Wine's
 * data but converts back without the best fit, so U+0192's upper case,
 * U+0191, which code page 1252 lacks, becomes '?' where native's _mbcasemap
 * holds 0x83. tools/s4/ctype_check.py runs both and needs each to move what
 * it checks.
 *
 * SA2_CTYPE_ANSWERS=<file> writes every call of the six, and what this runtime
 * answered, one line each:
 *
 *     <seq> t<thread> <caller> <import> <argument>... -> <return> in=<hex> out=<hex>
 *
 * `in` is what the call read and `out` what it wrote, in the import's code
 * units (two hex digits a byte, four a UTF-16 unit or a CT_CTYPE1 word). The
 * file is the host's: the guest sees nothing of it, so a run is the same with
 * it and without it. */
enum { CTYPE_WINE = 0, CTYPE_RULES = 1, CTYPE_NOBESTFIT = 2 };
static int   g_ctype_break;
static FILE *g_ctype_answers;
static uint32_t caller_of_current(void);

static void ctype_units(FILE *f, uint32_t va, uint32_t n, unsigned width) {
    for (uint32_t i = 0; i < n; i++) {
        const uint8_t *p = sa2_gp(va + i * width, width);
        if (!p) { fputs("..", f); return; }
        if (width == 2u) fprintf(f, "%04x", (unsigned)(p[0] | (p[1] << 8)));
        else fprintf(f, "%02x", (unsigned)p[0]);
    }
}

static void ctype_answer(const char *name, const uint32_t *a, unsigned argc, uint32_t ret,
                         uint32_t in, uint32_t in_n, unsigned in_w,
                         uint32_t out, uint32_t out_n, unsigned out_w) {
    FILE *f = g_ctype_answers;
    if (!f) return;
    fprintf(f, "%u t%u %08x %s", g_calls, (unsigned)sa2_sched_current(), caller_of_current(),
            name);
    for (unsigned i = 0; i < argc; i++) fprintf(f, " %08x", a[i]);
    fprintf(f, " -> %08x in=", ret);
    ctype_units(f, in, in_n, in_w);
    fputs(" out=", f);
    ctype_units(f, out, out_n, out_w);
    fputc('\n', f);
    fflush(f);
}

/* A UTF-16 unit as a 1252 byte, the way the break asks: 1 when the code page
 * has it, and otherwise the best fit's answer unless `best` is off. */
static int ctype_to_byte(uint16_t w, int best, uint8_t *b) {
    if (best && g_ctype_break == CTYPE_WINE) return sa2_cp1252_best_fit(w, b);
    return sa2_cp1252_from_wide(w, b);
}

#define WC_NO_BEST_FIT_CHARS 0x00000400u

/* Code page 936's conversions, as kernelbase's MultiByteToWideChar and
 * WideCharToMultiByte check their arguments and convert (runtime/sa2_locale.c
 * holds the conversions, tools/s4/locale_check.py holds them to Wine's).
 * What they do not implement traps by name: MB_COMPOSITE's decomposition and
 * WC_COMPOSITECHECK's composition, which no caller in the image passes with
 * code page 936. Each call is recorded under SA2_CTYPE_ANSWERS as the 1252
 * path's are. */
#define MB_PRECOMPOSED       0x00000001u
#define MB_COMPOSITE         0x00000002u
#define MB_USEGLYPHCHARS     0x00000004u
#define MB_ERR_INVALID_CHARS 0x00000008u
#define WC_COMPOSITECHECK    0x00000200u
#define WC_KNOWN_FLAGS       0x000006F0u   /* DISCARDNS SEPCHARS DEFAULTCHAR ERR_INVALID COMPOSITECHECK NO_BEST_FIT */
#define CP936_MAX_LEN        0x00100000u   /* a -1 length's longest walk */

__attribute__((noreturn))
static void cp936_refuse_flags(const char *what, uint32_t flags) {
    sa2_trap_note("%s(936, flags %#x): the flag asks for a composition this runtime does not "
                  "implement", what, flags);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

/* A source `n` units of `width` long, or to its NUL and past it when `len` is
 * negative, copied from guest memory; NULL when it leaves the guest space. */
static void *cp936_source(uint32_t va, int32_t len, unsigned width, uint32_t *n) {
    uint32_t count = (uint32_t)len;
    if (len < 0) {
        for (count = 0; count < CP936_MAX_LEN; count++) {
            const uint8_t *p = sa2_gp(va + count * width, width);
            if (!p) return NULL;
            if (!p[0] && (width == 1u || !p[1])) { count++; break; }
        }
    }
    uint8_t *buf = malloc((size_t)count * width + 2u);
    if (!buf) return NULL;
    for (uint32_t i = 0; i < count * width; i++) {
        const uint8_t *p = sa2_gp(va + i, 1);
        if (!p) { free(buf); return NULL; }
        buf[i] = *p;
    }
    *n = count;
    return buf;
}

static uint32_t mbtowc_936(const uint32_t *a) {
    const uint32_t flags = a[1], src = a[2], dst = a[4];
    const int32_t srclen = (int32_t)a[3], dstlen = (int32_t)a[5];
    uint32_t ret = 0, n = 0, written = 0;
    if (!src || !srclen || (!dst && dstlen) || dstlen < 0) {
        g_last_error = SA2_ERROR_INVALID_PARAMETER;
        goto answered;
    }
    if (flags & ~(MB_PRECOMPOSED | MB_COMPOSITE | MB_USEGLYPHCHARS | MB_ERR_INVALID_CHARS)) {
        g_last_error = 1004u;                                   /* ERROR_INVALID_FLAGS */
        goto answered;
    }
    if (flags & MB_COMPOSITE) cp936_refuse_flags("MultiByteToWideChar", flags);
    uint8_t *in = cp936_source(src, srclen, 1u, &n);
    if (!in) {
        sa2_trap_note("MultiByteToWideChar: the source at %#010x leaves the guest space", src);
        sa2_trap_raise(SA2_TRAP_FAULT, src);
    }
    if ((flags & MB_ERR_INVALID_CHARS) && sa2_cp936_invalid_chars(in, n)) {
        g_last_error = SA2_ERROR_NO_UNICODE_XLATE;
        free(in);
        goto answered;
    }
    uint16_t *out = malloc(((size_t)dstlen + 1u) * 2u);
    if (!out) {
        sa2_trap_note("MultiByteToWideChar(936): no host memory for %d units", dstlen);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    int overflow = 0;
    written = sa2_cp936_mbtowc(in, n, out, (uint32_t)dstlen, &overflow);
    if (out && dstlen) {
        for (uint32_t i = 0; i < written; i++) {
            const uint8_t wb[2] = {(uint8_t)(out[i] & 0xFFu), (uint8_t)(out[i] >> 8)};
            gput(dst + 2u * i, (const char *)wb, 2);
        }
    }
    free(out);
    free(in);
    if (overflow) g_last_error = SA2_ERROR_INSUFFICIENT_BUFFER;
    else ret = written;
answered:
    ctype_answer("MultiByteToWideChar", a, 6, ret, src, n, 1u, dst, dstlen ? written : 0u, 2u);
    return ret;
}

static uint32_t wctomb_936(const uint32_t *a) {
    const uint32_t flags = a[1], src = a[2], dst = a[4];
    const int32_t srclen = (int32_t)a[3], dstlen = (int32_t)a[5];
    uint32_t ret = 0, n = 0, written = 0;
    if (!src || !srclen || (!dst && dstlen) || dstlen < 0) {
        g_last_error = SA2_ERROR_INVALID_PARAMETER;
        goto answered;
    }
    if (flags & ~WC_KNOWN_FLAGS) {
        g_last_error = 1004u;                                   /* ERROR_INVALID_FLAGS */
        goto answered;
    }
    if (flags & WC_COMPOSITECHECK) cp936_refuse_flags("WideCharToMultiByte", flags);
    uint8_t *raw = cp936_source(src, srclen, 2u, &n);
    if (!raw) {
        sa2_trap_note("WideCharToMultiByte: the source at %#010x leaves the guest space", src);
        sa2_trap_raise(SA2_TRAP_FAULT, src);
    }
    uint16_t *in = malloc((size_t)n * 2u + 2u);
    for (uint32_t i = 0; in && i < n; i++)
        in[i] = (uint16_t)(raw[2u * i] | (raw[2u * i + 1u] << 8));
    free(raw);
    const uint16_t table_default = sa2_cp936_default();          /* the table's '?' */
    uint8_t defchar[2] = {(uint8_t)(table_default & 0xFFu), (uint8_t)(table_default >> 8)};
    if (a[6]) {                   /* kernelbase reads the caller's default as two bytes */
        const uint8_t *d = sa2_gp(a[6], 2);
        if (!d) {
            sa2_trap_note("WideCharToMultiByte: the default character at %#010x is outside "
                          "the guest space", a[6]);
            sa2_trap_raise(SA2_TRAP_FAULT, a[6]);
        }
        defchar[0] = d[0];
        defchar[1] = d[1];
    }
    uint8_t *out = malloc((size_t)dstlen + 1u);
    if (!in || !out) {
        sa2_trap_note("WideCharToMultiByte(936): no host memory for %u units", n);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    int used = 0, overflow = 0;
    written = in && out ? sa2_cp936_wctomb(in, n, out, (uint32_t)dstlen, defchar,
                                           (flags & WC_NO_BEST_FIT_CHARS) != 0, &used, &overflow)
                        : 0u;
    if (out && dstlen && written) gput(dst, (const char *)out, written);
    free(out);
    free(in);
    if (a[7]) gpoke32(a[7], (uint32_t)used);
    if (overflow) g_last_error = SA2_ERROR_INSUFFICIENT_BUFFER;
    else ret = written;
answered:
    ctype_answer("WideCharToMultiByte", a, 8, ret, src, n, 2u, dst, dstlen ? written : 0u, 1u);
    return ret;
}

/* KERNEL32!WideCharToMultiByte(cp, flags, wstr, cch, str, cb, defchar, usedflag)
 *
 * As Wine converts to a single-byte code page (kernelbase's
 * wcstombs_sbcs_slow): a character the code page has is its byte; one it lacks
 * is its best-fit byte unless WC_NO_BEST_FIT_CHARS asks for none; one with
 * neither is the caller's default character, or '?', and sets *usedflag. The
 * other flags are not read. Under SA2_CTYPE_BREAK=rules the answer is the one
 * before: '?' for every character the code page lacks, whatever the flags and
 * the default character. */
static uint32_t imp_WideCharToMultiByte(void *s, const uint32_t *a) {
    (void)s;
    uint32_t cp = resolve_cp(a[0]);
    if (cp == SA2_CP936 && g_locale_break != LOCALE_BREAK_REFUSE) return wctomb_936(a);
    if (cp != g_acp) refuse_codepage("WideCharToMultiByte", cp);
    int32_t cch = (int32_t)a[3];
    uint32_t src = a[2], dst = a[4], cb = a[5];
    const int rules = g_ctype_break == CTYPE_RULES;
    uint8_t dflt = (uint8_t)'?';
    if (a[6] && !rules) {
        const uint8_t *d = sa2_gp(a[6], 1);
        if (!d) {
            sa2_trap_note("WideCharToMultiByte: the default character at %#010x is outside "
                          "the guest space", a[6]);
            sa2_trap_raise(SA2_TRAP_FAULT, a[6]);
        }
        dflt = *d;
    }
    uint32_t n = 0, read = 0, used = 0, ret;
    for (uint32_t i = 0; ; i++) {
        if (cch >= 0 && (int32_t)i >= cch) break;
        uint8_t *p = sa2_gp(src + i * 2u, 2);
        if (!p) {
            sa2_trap_note("WideCharToMultiByte: the source leaves the guest space at %#010x",
                          src + i * 2u);
            sa2_trap_raise(SA2_TRAP_FAULT, src + i * 2u);
        }
        uint16_t w = (uint16_t)(p[0] | (p[1] << 8));
        read++;
        uint8_t b;
        if (!ctype_to_byte(w, !(a[1] & WC_NO_BEST_FIT_CHARS) && !rules, &b)) {
            used = 1;
            b = dflt;
        }
        if (cb) {
            if (n >= cb) {
                g_last_error = 122u /* ERROR_INSUFFICIENT_BUFFER */;
                ret = 0;
                goto answered;
            }
            gput(dst + n, (const char *)&b, 1);
        }
        n++;
        if (cch < 0 && !w) break;      /* -1: the terminator is converted and counted */
    }
    if (a[7]) gpoke32(a[7], used);
    ret = n;
answered:
    ctype_answer("WideCharToMultiByte", a, 8, ret, src, read, 2u, dst, cb ? n : 0u, 1u);
    return ret;
}

/* KERNEL32!MultiByteToWideChar(cp, flags, str, cb, wstr, cch) */
static uint32_t imp_MultiByteToWideChar(void *s, const uint32_t *a) {
    (void)s;
    uint32_t cp = resolve_cp(a[0]);
    if (cp == SA2_CP936 && g_locale_break != LOCALE_BREAK_REFUSE) return mbtowc_936(a);
    if (cp != g_acp) refuse_codepage("MultiByteToWideChar", cp);
    int32_t cb = (int32_t)a[3];
    uint32_t src = a[2], dst = a[4], cch = a[5];
    uint32_t n = 0, read = 0, ret;
    for (uint32_t i = 0; ; i++) {
        if (cb >= 0 && (int32_t)i >= cb) break;
        uint8_t *p = sa2_gp(src + i, 1);
        if (!p) {
            sa2_trap_note("MultiByteToWideChar: the source leaves the guest space at %#010x",
                          src + i);
            sa2_trap_raise(SA2_TRAP_FAULT, src + i);
        }
        read++;
        uint16_t w = sa2_cp1252_to_wide(*p);
        if (cch) {
            if (n >= cch) { g_last_error = 122u; ret = 0; goto answered; }
            uint8_t wb[2] = {(uint8_t)(w & 0xffu), (uint8_t)(w >> 8)};
            gput(dst + n * 2u, (const char *)wb, 2);
        }
        n++;
        if (cb < 0 && !*p) break;
    }
    ret = n;
answered:
    ctype_answer("MultiByteToWideChar", a, 6, ret, src, read, 1u, dst, cch ? n : 0u, 2u);
    return ret;
}

/* KERNEL32!GetStringTypeW(dwInfoType, lpSrcStr, cchSrc, lpCharType)
 *
 * CT_CTYPE1 only, Wine's flags for the characters the runtime has data for
 * (sa2_char_ctype1): U+0000..U+00FF and the rest of code page 1252, which is
 * what building a single-byte code page's ctype table asks for, and U+0191
 * and U+F8F5. A character outside them, or a type other than CT_CTYPE1,
 * refuses by name. */
#define C1_UPPER 0x0001u
#define C1_LOWER 0x0002u
#define C1_DIGIT 0x0004u
#define C1_SPACE 0x0008u
#define C1_PUNCT 0x0010u
#define C1_CNTRL 0x0020u
#define C1_BLANK 0x0040u
#define C1_XDIGIT 0x0080u
#define C1_ALPHA 0x0100u
#define C1_DEFINED 0x0200u

/* The rules the runtime answered with before 2026-09-26, which
 * SA2_CTYPE_BREAK=rules restores. They are the Unicode categories as the
 * runtime read them, and Wine's table differs for twelve characters (U+0085,
 * U+00AA, U+00AD, U+00B2, U+00B3, U+00B5, U+00B9, U+00BA, U+02C6, U+02DC,
 * U+20AC, U+2122) and has U+0191 and U+F8F5, which these refuse.
 * 0 means "no defensible answer", and every caller refuses on it rather than
 * returning C1_DEFINED for a character it cannot classify. */
static uint16_t ctype1_rules(uint16_t w) {
    if (w >= 0x100u) {
        /* The 27 assigned positions in CP1252's 0x80..0x9F are the only code
         * points above U+00FF the ANSI code page can produce, so they are the
         * only ones a ctype table built from it can ask about. Eight are
         * letters (OE oe S-caron s-caron Z-caron z-caron Y-diaeresis f-hook);
         * the rest are punctuation and symbols, which CT_CTYPE1 reports as
         * C1_PUNCT. */
        uint8_t b;
        if (!sa2_cp1252_from_wide(w, &b)) return 0;
        if (w == 0x0152u || w == 0x0160u || w == 0x017Du || w == 0x0178u)
            return (uint16_t)(C1_DEFINED | C1_UPPER | C1_ALPHA);
        if (w == 0x0153u || w == 0x0161u || w == 0x017Eu || w == 0x0192u)
            return (uint16_t)(C1_DEFINED | C1_LOWER | C1_ALPHA);
        return (uint16_t)(C1_DEFINED | C1_PUNCT);
    }
    uint16_t t = C1_DEFINED;
    if (w < 0x20u || w == 0x7Fu || (w >= 0x80u && w < 0xA0u)) {
        t |= C1_CNTRL;
        if (w >= 0x09u && w <= 0x0Du) t |= C1_SPACE;
        if (w == 0x09u) t |= C1_BLANK;
        return t;
    }
    if (w == 0x20u || w == 0xA0u) return (uint16_t)(t | C1_SPACE | C1_BLANK);
    if (w >= '0' && w <= '9') return (uint16_t)(t | C1_DIGIT | C1_XDIGIT);
    if (w >= 'A' && w <= 'Z') return (uint16_t)(t | C1_UPPER | C1_ALPHA
                                                | (w <= 'F' ? C1_XDIGIT : 0u));
    if (w >= 'a' && w <= 'z') return (uint16_t)(t | C1_LOWER | C1_ALPHA
                                                | (w <= 'f' ? C1_XDIGIT : 0u));
    if (w < 0x80u) return (uint16_t)(t | C1_PUNCT);
    /* Latin-1 supplement: letters are C0..FF except the two multiplication and
     * division signs, and 0xAA/0xB5/0xBA are letters too. */
    if (w == 0xAAu || w == 0xB5u || w == 0xBAu) return (uint16_t)(t | C1_LOWER | C1_ALPHA);
    if (w >= 0xC0u && w <= 0xDEu && w != 0xD7u) return (uint16_t)(t | C1_UPPER | C1_ALPHA);
    if ((w >= 0xDFu && w <= 0xFFu) && w != 0xF7u) return (uint16_t)(t | C1_LOWER | C1_ALPHA);
    return (uint16_t)(t | C1_PUNCT);
}

static uint32_t imp_GetStringTypeW(void *s, const uint32_t *a) {
    (void)s;
    if (a[0] != 1u) {
        sa2_trap_note("GetStringTypeW: dwInfoType %#x is not CT_CTYPE1; the runtime has no "
                      "defensible table for it", a[0]);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    int32_t cch = (int32_t)a[2];
    uint32_t n = 0, ret = 1;
    for (uint32_t i = 0; ; i++) {
        if (cch >= 0 && (int32_t)i >= cch) break;
        uint8_t *p = sa2_gp(a[1] + i * 2u, 2);
        if (!p) { g_last_error = SA2_ERROR_INVALID_PARAMETER; ret = 0; break; }
        uint16_t w = (uint16_t)(p[0] | (p[1] << 8));
        uint16_t t = g_ctype_break == CTYPE_RULES ? ctype1_rules(w) : sa2_char_ctype1(w);
        if (!t) {
            if (g_ctype_break == CTYPE_RULES)
                sa2_trap_note("GetStringTypeW: U+%04X is outside the characters the rules "
                              "SA2_CTYPE_BREAK=rules restores classify (U+0000..U+00FF and "
                              "the rest of code page 1252; ctype1_rules)", w);
            else
                sa2_trap_note("GetStringTypeW: U+%04X is outside the characters this runtime "
                              "has Wine's character types for (U+0000..U+00FF, the rest of "
                              "code page 1252, U+0191 and U+F8F5; runtime/sa2_codepage.h)", w);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT,
                           g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
        }
        uint8_t tb[2] = {(uint8_t)(t & 0xffu), (uint8_t)(t >> 8)};
        gput(a[3] + i * 2u, (const char *)tb, 2);
        n++;
        if (cch < 0 && !w) break;
    }
    ctype_answer("GetStringTypeW", a, 4, ret, a[1], n, 2u, a[3], n, 2u);
    return ret;
}

/* KERNEL32!GetStringTypeA(Locale, dwInfoType, lpSrcStr, cchSrc, lpCharType): each
 * byte through code page 1252, then GetStringTypeW's answer. Every byte of the
 * code page is in the data, so none is refused. */
static uint32_t imp_GetStringTypeA(void *s, const uint32_t *a) {
    (void)s;
    if (a[1] != 1u) {
        sa2_trap_note("GetStringTypeA: dwInfoType %#x is not CT_CTYPE1", a[1]);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    int32_t cch = (int32_t)a[3];
    uint32_t n = 0, ret = 1;
    for (uint32_t i = 0; ; i++) {
        if (cch >= 0 && (int32_t)i >= cch) break;
        uint8_t *p = sa2_gp(a[2] + i, 1);
        if (!p) { g_last_error = SA2_ERROR_INVALID_PARAMETER; ret = 0; break; }
        const uint16_t w = sa2_cp1252_to_wide(*p);
        uint16_t t = g_ctype_break == CTYPE_RULES ? ctype1_rules(w) : sa2_char_ctype1(w);
        uint8_t tb[2] = {(uint8_t)(t & 0xffu), (uint8_t)(t >> 8)};
        gput(a[4] + i * 2u, (const char *)tb, 2);
        n++;
        if (cch < 0 && !*p) break;
    }
    ctype_answer("GetStringTypeA", a, 5, ret, a[2], n, 1u, a[4], n, 2u);
    return ret;
}

/* KERNEL32!LCMapStringW(Locale, dwMapFlags, src, cchSrc, dst, cchDst).
 * LCMAP_LOWERCASE / LCMAP_UPPERCASE, Wine's case for the characters the runtime
 * has data for (sa2_char_case); any other map refuses, because a sort key or a
 * normalisation form invented here would be a value the guest compares against
 * and this project could never defend. A character outside the data is answered
 * unchanged, as before: right for most of them, and wrong for every cased
 * letter beyond code page 1252 (U+0100's lower case is U+0101), which no call
 * the game makes has carried. LCMapStringA converts through code page 1252 both
 * ways, back with the best fit, as Wine's does (WideCharToMultiByte with no
 * flags). */
#define LCMAP_LOWERCASE 0x00000100u
#define LCMAP_UPPERCASE 0x00000200u

/* The case the runtime answered with before 2026-09-26, which
 * SA2_CTYPE_BREAK=rules restores; Wine's differs in one place, U+0192's upper
 * case, which is U+0191. */
static uint16_t case_rules(uint16_t w, int up) {
    /* The eight letters CP1252 has above U+00FF. Leaving them alone made this
     * table contradict `ctype1_rules`, which calls four of them upper case and four
     * lower: the CRT builds `_pctype` from GetStringTypeW and its case tables
     * from LCMapString, so the two tables it ends up holding disagreed about the
     * same byte. 0x00FF/0x0178 is in the same pair and was already handled in
     * one direction only. */
    switch (w) {
    case 0x0152u: case 0x0153u: return up ? 0x0152u : 0x0153u;   /* OE / oe */
    case 0x0160u: case 0x0161u: return up ? 0x0160u : 0x0161u;   /* S-caron */
    case 0x017Du: case 0x017Eu: return up ? 0x017Du : 0x017Eu;   /* Z-caron */
    case 0x0178u:               return up ? 0x0178u : 0x00FFu;   /* Y-diaeresis */
    default: break;
    }
    if (w >= 0x100u) return w;                     /* f-hook U+0192 has no case pair */
    if (up) {
        if (w >= 'a' && w <= 'z') return (uint16_t)(w - 32);
        if (w >= 0xE0u && w <= 0xFEu && w != 0xF7u) return (uint16_t)(w - 32);
        if (w == 0xFFu) return 0x0178u;
        return w;
    }
    if (w >= 'A' && w <= 'Z') return (uint16_t)(w + 32);
    if (w >= 0xC0u && w <= 0xDEu && w != 0xD7u) return (uint16_t)(w + 32);
    return w;
}

static uint32_t lcmap(const uint32_t *a, int wide) {
    uint32_t flags = a[1], src = a[2], dst = a[4], cchdst = a[5];
    int32_t cchsrc = (int32_t)a[3];
    int up = (flags & LCMAP_UPPERCASE) != 0, low = (flags & LCMAP_LOWERCASE) != 0;
    if (!up && !low) {
        sa2_trap_note("LCMapString%s: map flags %#x are not LCMAP_UPPERCASE or "
                      "LCMAP_LOWERCASE; the runtime has no defensible answer for them",
                      wide ? "W" : "A", flags);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    uint32_t n = 0, read = 0, ret;
    for (uint32_t i = 0; ; i++) {
        if (cchsrc >= 0 && (int32_t)i >= cchsrc) break;
        uint8_t *p = sa2_gp(src + (wide ? i * 2u : i), wide ? 2 : 1);
        if (!p) { g_last_error = SA2_ERROR_INVALID_PARAMETER; ret = 0; goto answered; }
        read++;
        uint16_t w = wide ? (uint16_t)(p[0] | (p[1] << 8)) : sa2_cp1252_to_wide(*p);
        uint16_t m;
        if (g_ctype_break == CTYPE_RULES) m = case_rules(w, up);
        else sa2_char_case(w, up, &m);
        if (cchdst) {
            if (n >= cchdst) { g_last_error = 122u; ret = 0; goto answered; }
            if (wide) {
                uint8_t wb[2] = {(uint8_t)(m & 0xffu), (uint8_t)(m >> 8)};
                gput(dst + n * 2u, (const char *)wb, 2);
            } else {
                uint8_t b;
                if (!ctype_to_byte(m, 1, &b)) b = (uint8_t)'?';
                gput(dst + n, (const char *)&b, 1);
            }
        }
        n++;
        if (cchsrc < 0 && !w) break;
    }
    ret = n;
answered:
    ctype_answer(wide ? "LCMapStringW" : "LCMapStringA", a, 6, ret, src, read, wide ? 2u : 1u,
                 dst, cchdst ? n : 0u, wide ? 2u : 1u);
    return ret;
}

static uint32_t imp_LCMapStringW(void *s, const uint32_t *a) { (void)s; return lcmap(a, 1); }
static uint32_t imp_LCMapStringA(void *s, const uint32_t *a) { (void)s; return lcmap(a, 0); }

/* ------------------------------------------------------------- the locale
 *
 * GetUserDefaultLCID, IsValidLocale, GetLocaleInfoA and W and
 * EnumSystemLocalesA, as kernelbase answers them. The answers have two
 * sources, split where Wine splits them, between the user's locale and every
 * other:
 *
 *  - The user's locale -- its LCID, the code pages, and GetLocaleInfo of it,
 *    registry overrides included -- is the host's, so the capture's device
 *    profile carries it (runtime/sa2_devprofile.h, "The locale"). A run with no
 *    capture gets the default: 0x0409 (SA2_GUEST_LCID moves it), code pages
 *    1252 and 437, and Wine's data for 0x0409 with no override
 *    (runtime/sa2_locale.h).
 *  - Every other locale is Wine's platform data (runtime/sa2_locale.h): the
 *    installed locales EnumSystemLocalesA enumerates, the LCIDs IsValidLocale
 *    accepts, the two names each enumeration callback reads, and GetLocaleInfo
 *    of 0x0804, the locale the game's C runtime selects for
 *    "Chinese-simplified".
 *
 * Natively each archive loader's setlocale(LC_ALL, "Chinese-simplified")
 * enumerates the installed locales twice, matches the C runtime's alias "chs"
 * against 0x0804's LOCALE_SABBREVLANGNAME on the second pass, and initialises
 * every category in 0x0804 and code page 936, until the loader's
 * setlocale(LC_ALL, "") restores the user's locale
 * (agents-journals/s4/2026-09-26-locale.md names the call sites; they are not
 * repeated here, since every guest address in this file counts toward
 * Documentation/scripts/check_boundary.py's limit). Until 2026-09-26 the enumeration
 * here was empty, so the first call failed, and the user's locale's formatting
 * data was refused, leaving LC_MONETARY, LC_NUMERIC and LC_TIME in "C": nine
 * imports whose answers did not reproduce native. tools/s4/locale_check.py
 * holds every answer below to the native traces.
 *
 * What lies outside the data traps by name, since Wine answers it and a guess
 * would be a value no oracle checked: GetLocaleInfo of an LCTYPE the data does
 * not hold for its locale, of a valid locale it holds nothing of, or with a
 * flag kernelbase reads and this does not implement.
 *
 * SA2_LOCALE_BREAK restores an answer from before 2026-09-26, so that check can
 * show it sees the difference. `refuse` answers every locale import as then:
 * GetUserDefaultLCID and GetACP the placeholders; IsValidLocale TRUE for the
 * placeholder LCID, LOCALE_USER_DEFAULT and LOCALE_SYSTEM_DEFAULT alone;
 * GetLocaleInfo only the LCID, code page and names of the placeholder locale
 * and ERROR_INVALID_FLAGS for the rest; an enumeration that calls no callback;
 * and code pages as "code pages" above says. `noenum` keeps today's answers and
 * only enumerates no locale, so "Chinese-simplified" fails and code page 936 is
 * never reached. Neither is a configuration any replay runs
 * (tools/s4/reach_receipt.py DEVICE_BREAKS strips them from a shell), and
 * neither answer is marked as a refusal: a break is a falsifier's run. */
#define LOCALE_NOUSEROVERRIDE       0x80000000u
#define LOCALE_USE_CP_ACP           0x40000000u
#define LOCALE_RETURN_NUMBER        0x20000000u
#define LOCALE_RETURN_GENITIVE      0x10000000u
#define LOCALE_SSHORTTIME           0x00000079u
#define LCID_INSTALLED              0x00000001u
#define LCID_SUPPORTED              0x00000002u
#define LCID_ALTERNATE_SORTS        0x00000004u
#define ERROR_INVALID_FLAGS_        1004u

static uint32_t g_lcid = 0x0409u;      /* the default user locale; SA2_GUEST_LCID */

/* The user's locale is the capture's when its profile records one, unless the
 * break asks for the answers before profiles did. */
static int locale_profiled(void) {
    return sa2_dp_locale_recorded() && g_locale_break != LOCALE_BREAK_REFUSE;
}
static uint32_t user_lcid(void) { return locale_profiled() ? sa2_dp_locale_lcid() : g_lcid; }
static uint32_t system_lcid(void) { return locale_profiled() ? sa2_dp_locale_system() : g_lcid; }

static uint32_t imp_GetUserDefaultLCID(void *s, const uint32_t *a) {
    (void)s; (void)a;
    return user_lcid();
}

static uint32_t imp_IsValidLocale(void *s, const uint32_t *a) {
    (void)s;
    uint32_t r;
    if (g_locale_break == LOCALE_BREAK_REFUSE)
        r = a[0] == g_lcid || a[0] == 0x0400u || a[0] == 0x0800u;
    else
        r = (uint32_t)sa2_locale_valid(a[0]);
    locale_answer_line("IsValidLocale", a, 2, r, NULL, 0);
    return r;
}

/* The answer before 2026-09-26 (SA2_LOCALE_BREAK=refuse): the LCTYPEs the
 * placeholder locale's table answered -- its LCID, names and code page -- and
 * only for it, LOCALE_NEUTRAL, LOCALE_USER_DEFAULT and LOCALE_SYSTEM_DEFAULT. */
static int locale_answered_before(uint32_t lcid, uint32_t lctype) {
    if (lcid != g_lcid && lcid != 0u && lcid != 0x0400u && lcid != 0x0800u) return 0;
    switch (lctype & 0xFFFFu) {
    case 0x0001u: case 0x0003u: case 0x0004u: case 0x0005u: case 0x0007u: case 0x0008u:
    case 0x0009u: case 0x000Au: case 0x1001u: case 0x1002u: case 0x1004u:
        return 1;
    default:
        return 0;
    }
}

/* The number a recorded answer spells, as kernelbase's LOCALE_RETURN_NUMBER
 * reads it: hexadecimal for the two LANGIDs, LOCALE_ILANGUAGE and
 * LOCALE_IDEFAULTLANGUAGE, decimal for the rest; 0 when it is none. */
static int locale_parse_number(uint32_t lctype, const uint16_t *w, uint32_t n, uint32_t *out) {
    const unsigned base = (lctype == 0x0001u || lctype == 0x0009u) ? 16u : 10u;
    uint64_t v = 0;
    if (!n) return 0;
    for (uint32_t i = 0; i < n; i++) {
        const uint16_t c = w[i];
        unsigned d;
        if (c >= '0' && c <= '9') d = c - '0';
        else if (base == 16u && c >= 'a' && c <= 'f') d = c - 'a' + 10u;
        else if (base == 16u && c >= 'A' && c <= 'F') d = c - 'A' + 10u;
        else return 0;
        v = v * base + d;
        if (v > 0xFFFFFFFFull) return 0;
    }
    *out = (uint32_t)v;
    return 1;
}

__attribute__((noreturn))
static void locale_trap(const uint32_t *a, int wide, const char *why) {
    sa2_trap_note("GetLocaleInfo%s(%#x, LCTYPE %#x): %s", wide ? "W" : "A", a[0], a[1], why);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

/* A UTF-16 unit in code page `cp` as WideCharToMultiByte with no flag writes
 * it: 1 or 2 bytes into out, the count returned. The locale's own code page is
 * 1252 or 936 for every locale the data answers but a few, and those few answer
 * names in ASCII; anything else traps. */
static unsigned locale_wide_to_cp(const uint32_t *a, uint32_t cp, uint16_t w, uint8_t out[2]) {
    if (cp == 1252u) {
        sa2_cp1252_best_fit(w, &out[0]);      /* the table's byte, the best fit, or '?' */
        return 1;
    }
    if (cp == SA2_CP936) {
        const uint16_t mb = sa2_cp936_table(w);
        if (mb >> 8) {
            out[0] = (uint8_t)(mb >> 8);
            out[1] = (uint8_t)(mb & 0xFFu);
            return 2;
        }
        out[0] = (uint8_t)mb;
        return 1;
    }
    if (w < 0x80u) {                          /* every ANSI code page's ASCII */
        out[0] = (uint8_t)w;
        return 1;
    }
    char why[160];
    snprintf(why, sizeof why, "U+%04X through code page %u, which this runtime does not "
             "implement", (unsigned)w, (unsigned)cp);
    locale_trap(a, 0, why);
    return 0;
}

/* GetLocaleInfoA/W(LCID, LCTYPE, lpLCData, cchData), as kernelbase's
 * GetLocaleInfoA and GetLocaleInfoW check their arguments, find the locale
 * (NlsValidateLocale) and answer (get_locale_info, then for the A form
 * WideCharToMultiByte through the locale's ANSI code page, or the process's with
 * LOCALE_USE_CP_ACP or for a UTF-8 locale). */
static uint32_t locale_info(const uint32_t *a, int wide) {
    const uint32_t lctype = a[1], type = lctype & 0xFFFFu, dst = a[2];
    const int32_t len = (int32_t)a[3];
    uint32_t ret = 0;
    sa2_locale_answer ans;
    uint32_t lcid = a[0], acp = 0, oemcp = 0, units = 0;
    if (len < 0 || (len && !dst)) { g_last_error = SA2_ERROR_INVALID_PARAMETER; goto answered; }
    if (!wide && (type == LOCALE_SSHORTTIME || (lctype & LOCALE_RETURN_GENITIVE))) {
        g_last_error = ERROR_INVALID_FLAGS_;
        goto answered;
    }
    if (g_locale_break == LOCALE_BREAK_REFUSE && !locale_answered_before(lcid, lctype)) {
        g_last_error = ERROR_INVALID_FLAGS_;
        goto answered;
    }
    if (lctype & 0x0FFF0000u || (lctype & LOCALE_RETURN_GENITIVE))
        locale_trap(a, wide, "a flag this runtime does not implement");
    /* NlsValidateLocale */
    switch (lcid) {
    case 0x0800u: lcid = system_lcid(); break;
    case 0x0000u: case 0x0400u: case 0x0C00u: case 0x1000u: lcid = user_lcid(); break;
    case 0x1400u: locale_trap(a, wide, "the user's UI locale, which no profile records"); break;
    default:
        if (!sa2_locale_valid(lcid)) {
            g_last_error = SA2_ERROR_INVALID_PARAMETER;
            goto answered;
        }
    }
    const int users = lcid == user_lcid() && !(lctype & LOCALE_NOUSEROVERRIDE);
    if (users && locale_profiled()) {
        const uint16_t *w = NULL;
        uint32_t n = 0, error = 0;
        const int r = sa2_dp_locale_info(type, &w, &n, &error);
        if (r < 0) locale_trap(a, wide, "the capture's profile records no answer for it");
        if (r == 0) { g_last_error = error; goto answered; }   /* native's own failure */
        ans.w = w;
        ans.n = n;
        ans.kind = sa2_locale_numeric(type) ? SA2_LOCALE_NUMBER : SA2_LOCALE_STRING;
        ans.value = 0;
        if (ans.kind == SA2_LOCALE_NUMBER && (lctype & LOCALE_RETURN_NUMBER) &&
            !locale_parse_number(type, w, n, &ans.value)) {
            g_last_error = ERROR_INVALID_FLAGS_;
            goto answered;
        }
        acp = sa2_dp_locale_acp();
    } else {
        if (!sa2_locale_data(lcid, type, &ans))
            locale_trap(a, wide, "the runtime has Wine's data for no such LCID and LCTYPE "
                                 "(runtime/sa2_locale.h)");
        if (!sa2_locale_codepages(lcid, &acp, &oemcp)) locale_trap(a, wide, "no code page");
    }
    if (acp == 65001u || (lctype & LOCALE_USE_CP_ACP)) acp = g_acp;   /* get_locale_codepage */

    if (lctype & LOCALE_RETURN_NUMBER) {
        if (ans.kind != SA2_LOCALE_NUMBER) { g_last_error = ERROR_INVALID_FLAGS_; goto answered; }
        /* in UTF-16 units; the A form asks for len / 2 of them and counts bytes */
        const uint32_t room = wide ? (uint32_t)len : (uint32_t)len / 2u;
        if (!room) { ret = wide ? 2u : 4u; goto answered; }
        if (room < 2u) { g_last_error = SA2_ERROR_INSUFFICIENT_BUFFER; goto answered; }
        gpoke32(dst, ans.value);
        ret = wide ? 2u : 4u;
        units = ret;
        goto answered;
    }
    if (wide) {
        const uint32_t need = ans.n + 1u;
        if (!len) { ret = need; goto answered; }
        if ((uint32_t)len < need) { g_last_error = SA2_ERROR_INSUFFICIENT_BUFFER; goto answered; }
        for (uint32_t i = 0; i < need; i++) {
            const uint16_t c = i < ans.n ? ans.w[i] : 0u;
            const uint8_t wb[2] = {(uint8_t)(c & 0xFFu), (uint8_t)(c >> 8)};
            gput(dst + 2u * i, (const char *)wb, 2);
        }
        ret = units = need;
        goto answered;
    }
    {
        /* The answer and its NUL through the code page, into the buffer as far
         * as whole characters fit, as kernelbase's wcstombs does. */
        uint32_t need = 0, at = 0, i = 0;
        for (i = 0; i <= ans.n; i++) {
            uint8_t b[2];
            need += locale_wide_to_cp(a, acp, i < ans.n ? ans.w[i] : 0u, b);
        }
        if (!len) { ret = need; goto answered; }
        for (i = 0; i <= ans.n; i++) {
            uint8_t b[2];
            const unsigned k = locale_wide_to_cp(a, acp, i < ans.n ? ans.w[i] : 0u, b);
            if (at + k > (uint32_t)len) break;
            gput(dst + at, (const char *)b, k);
            at += k;
        }
        units = at;
        if (i <= ans.n) { g_last_error = SA2_ERROR_INSUFFICIENT_BUFFER; goto answered; }
        ret = need;
    }
answered:
    locale_answer_guest(wide ? "GetLocaleInfoW" : "GetLocaleInfoA", a, 4, ret, dst,
                        len ? units : 0u, wide ? 2u : 1u);
    return ret;
}

static uint32_t imp_GetLocaleInfoA(void *s, const uint32_t *a) { (void)s; return locale_info(a, 0); }
static uint32_t imp_GetLocaleInfoW(void *s, const uint32_t *a) { (void)s; return locale_info(a, 1); }

/* KERNEL32!EnumSystemLocalesA(LOCALE_ENUMPROCA, DWORD dwFlags)
 *
 * The C runtime's __get_qualified_locale walks the installed locales with it
 * to find one matching the language and country it parsed:
 *
 *     007b7ea0  push 7B7D55h                  ; the enumeration callback
 *     007b7ea8  call [873114h]                ; EnumSystemLocalesA(.., LCID_INSTALLED)
 *
 * As kernelbase does, this hands the callback each installed locale's LCID as
 * eight lower-case hex digits, in Wine's order, until the callback answers
 * FALSE, and answers TRUE. The string is written on the guest's own stack below
 * the call, where kernelbase's `char name[10]` lies natively, so each thread's
 * enumeration has its own. Alternate sorts are not in the data, so a call that
 * asks for them (LCID_ALTERNATE_SORTS, alone or beside the default sorts)
 * traps; with neither LCID_INSTALLED nor LCID_SUPPORTED no default sort is
 * enumerated, as in kernelbase. */
static uint32_t imp_EnumSystemLocalesA(void *s, const uint32_t *a) {
    const uint32_t proc = a[0], flags = a[1] ? a[1] : LCID_SUPPORTED;
    const uint32_t args[2] = {a[0], a[1]};
    const uint32_t seq = g_calls;        /* the callbacks' imports advance g_calls */
    FILE *f = g_locale_answers;
    char cb[4096];
    size_t at = 0;
    cb[0] = 0;
    /* Before the default-sort test: kernelbase enumerates the alternate sorts
     * for LCID_ALTERNATE_SORTS alone too, so a TRUE with no callback there
     * would be a silent wrong answer, not the trap this promises. */
    if (g_locale_break == LOCALE_NATIVE && (flags & LCID_ALTERNATE_SORTS)) {
        sa2_trap_note("EnumSystemLocalesA(flags %#x): alternate sorts are not in this "
                      "runtime's data (runtime/sa2_locale.h)", flags);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    if (g_locale_break == LOCALE_NATIVE && (flags & (LCID_INSTALLED | LCID_SUPPORTED))) {
        const uint32_t esp0 = sa2_rt_get_gpr(s, SA2_ESP);
        const uint32_t name = esp0 - 16u;
        sa2_rt_set_gpr(s, SA2_ESP, name);
        for (uint32_t i = 0; i < sa2_locale_installed_count(); i++) {
            char text[16];
            snprintf(text, sizeof text, "%08x", sa2_locale_installed_at(i));
            gput(name, text, 9);
            const uint32_t r = sa2_guest_stdcall(s, proc, 1u, &name);
            if (f && at + 16u < sizeof cb)
                at += (size_t)snprintf(cb + at, sizeof cb - at, "%s%s:%u", at ? "," : "", text,
                                       r ? 1u : 0u);
            if (!r) break;
        }
        sa2_rt_set_gpr(s, SA2_ESP, esp0);
    }
    if (f) {
        locale_answer_begin_at(seq, "EnumSystemLocalesA", args, 2, 1u);
        fprintf(f, " cb=%s", cb);
        locale_answer_end(1u);
    }
    return 1u;
}

/* ------------------------------------------- the dynamic-import stubs */

/* Encode/DecodePointer. The only contract the guest can observe is that Decode
 * inverts Encode; the cookie Windows uses is per-process, unreadable, and there
 * is no capture to agree with, so it is a *named placeholder*. Identity would
 * also be legal -- the image's own __encode_pointer at 0x007ac1cf returns its
 * argument unchanged when the OS has no EncodePointer -- but a non-trivial
 * value is what catches a path that encodes and never decodes. */
static uint32_t g_ptr_cookie = 0x5ec0decau;
static uint32_t imp_EncodePointer(void *s, const uint32_t *a) { (void)s; return a[0] ^ g_ptr_cookie; }
static uint32_t imp_DecodePointer(void *s, const uint32_t *a) { (void)s; return a[0] ^ g_ptr_cookie; }

/* Fls*: fiber-local storage. One fiber, so the semantics are TLS's, but the
 * index spaces are separate on Windows and are separate here, because a guest
 * that mixed them would be finding a runtime bug, not a Windows one.
 *
 * **The values are per thread.** "One fiber" is true and was read as "one set
 * of values", which is the opposite of what it means: a thread that has never
 * converted to a fiber still has *its own* implicit fiber, so FLS on a
 * fiberless process behaves exactly like TLS. Holding these in one array cost
 * S4 slice 12 a starved main thread -- the MSVC CRT's `_ptiddata` lives in an
 * FLS slot, so every guest thread shared one, and three threads created
 * suspended together all ran the routine the last of them registered. */
/* The values are per thread (sa2_sched_fls); the index allocation is not,
 * exactly as with Tls* above. SA2_FLS_SLOTS lives in sa2_threads.h because the
 * storage does. */
#define g_fls (sa2_sched_fls(sa2_sched_current()))
static uint8_t  g_fls_taken[SA2_FLS_SLOTS];
static uint32_t g_fls_callback[SA2_FLS_SLOTS];

static uint32_t imp_FlsAlloc(void *s, const uint32_t *a) {
    (void)s;
    for (uint32_t i = 0; i < SA2_FLS_SLOTS; i++) {
        if (!g_fls_taken[i]) {
            g_fls_taken[i] = 1; g_fls[i] = 0; g_fls_callback[i] = a[0];
            return i;
        }
    }
    g_last_error = SA2_ERROR_INVALID_PARAMETER;
    return 0xffffffffu;   /* FLS_OUT_OF_INDEXES */
}
static uint32_t imp_FlsGetValue(void *s, const uint32_t *a) {
    (void)s;
    if (a[0] >= SA2_FLS_SLOTS) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    g_last_error = SA2_ERROR_SUCCESS;
    return g_fls[a[0]];
}
static uint32_t imp_FlsSetValue(void *s, const uint32_t *a) {
    (void)s;
    if (a[0] >= SA2_FLS_SLOTS) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    g_fls[a[0]] = a[1];
    return 1;
}
/* FlsFree drops the index's value on every thread, and Windows first runs the
 * index's callback for each thread still holding one (Wine's RtlFlsFree), on
 * the calling thread. The image's one call is `_mtterm` (0x007ac336), which
 * only `_mtinit`'s failure path reaches (0x007ac80d), so no run has a callback
 * due here; if one ever is, it is refused by name rather than run untested. */
static uint32_t imp_FlsFree(void *s, const uint32_t *a) {
    (void)s;
    if (a[0] >= SA2_FLS_SLOTS || !g_fls_taken[a[0]]) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    /* Before the scheduler starts there are no rows, and sa2_sched_fls(0) is
     * thread 0's storage from before it: still one thread to clear. */
    const int threads = sa2_sched_count() ? sa2_sched_count() : 1;
    for (int i = 0; i < threads; i++) {
        if (sa2_sched_finished(i) || !sa2_sched_fls(i)[a[0]]) continue;
        if (g_fls_callback[a[0]]) {
            sa2_trap_note("FlsFree(%u): guest thread %d holds %#010x there, so Windows would run "
                          "the index's callback %#010x first; this runtime runs FLS callbacks "
                          "only as a thread ends, and no call site in this image reaches "
                          "FlsFree outside _mtinit's failure path", a[0], i,
                          sa2_sched_fls(i)[a[0]], g_fls_callback[a[0]]);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
        }
        sa2_sched_fls(i)[a[0]] = 0;
    }
    g_fls_taken[a[0]] = 0; g_fls_callback[a[0]] = 0;
    return 1;
}

uint32_t sa2_fls_callback(uint32_t index) {
    return index < SA2_FLS_SLOTS && g_fls_taken[index] ? g_fls_callback[index] : 0u;
}

/* KERNEL32!IsProcessorFeaturePresent(DWORD).
 *
 * The one site is __ms_p5_mp_test_fdiv at 0x007ba6aa asking for feature 0,
 * PF_FLOATING_POINT_PRECISION_ERRATA -- the Pentium FDIV bug. 0 is the exact
 * answer for every processor this port will ever run on, not a placeholder.
 * Any other feature is refused rather than guessed, because a wrong "yes"
 * would send the guest down a code path for hardware that is not there. */
static uint32_t imp_IsProcessorFeaturePresent(void *s, const uint32_t *a) {
    (void)s;
    if (a[0] == 0u) return 0;
    sa2_trap_note("IsProcessorFeaturePresent(%u): the runtime answers only feature 0 "
                  "(PF_FLOATING_POINT_PRECISION_ERRATA); this one has no defensible answer yet", a[0]);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

/* USER32!GetProcessWindowStation / GetUserObjectInformationA.
 *
 * ___crtMessageBoxA asks whether the window station is visible, and uses
 * MB_SERVICE_NOTIFICATION when it is not. A headless host has no visible
 * window station, so the flags are 0 -- which is the true answer here rather
 * than a convenient one. */
static uint32_t g_winsta;
static uint32_t imp_GetProcessWindowStation(void *s, const uint32_t *a) {
    (void)s; (void)a;
    if (!g_winsta) g_winsta = handle_new(H_WINSTA, "window-station");
    return g_winsta;
}

static uint32_t imp_GetUserObjectInformationA(void *s, const uint32_t *a) {
    (void)s;
    /* UOI_FLAGS = 1: USEROBJECTFLAGS { BOOL fInherit; BOOL fReserved; DWORD dwFlags; } */
    if (a[1] != 1u || a[3] < 12u) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    gpoke32(a[2], 0);
    gpoke32(a[2] + 4u, 0);
    gpoke32(a[2] + 8u, 0);        /* not WSF_VISIBLE */
    if (a[4]) gpoke32(a[4], 12u);
    return 1;
}

/* USER32!MessageBoxA. Recorded, never displayed, and the run is *not* stopped:
 * the CRT's fatal-error path calls this and then exits, and stopping here would
 * hide the exit code the guest chose. What a probe wants is the text, which is
 * the guest's own diagnostic about itself. */
#define SA2_MSGBOX_MAX 8u
static char     g_msgbox_text[SA2_MSGBOX_MAX][512];
static char     g_msgbox_caption[SA2_MSGBOX_MAX][128];
static uint32_t g_msgbox_count;
static uint32_t imp_MessageBoxA(void *s, const uint32_t *a) {
    (void)s;
    if (g_msgbox_count < SA2_MSGBOX_MAX) {
        gstr(a[1], g_msgbox_text[g_msgbox_count], sizeof g_msgbox_text[0]);
        gstr(a[2], g_msgbox_caption[g_msgbox_count], sizeof g_msgbox_caption[0]);
    }
    g_msgbox_count++;
    return 1;   /* IDOK */
}
static uint32_t imp_GetActiveWindow(void *s, const uint32_t *a) {
    (void)s; (void)a;
    return sa2_window_main();
}
static uint32_t imp_GetLastActivePopup(void *s, const uint32_t *a) { (void)s; return a[0]; }

/* ------------------------------------------------- window and message pump
 *
 * Design 5 S4: "window creation stubbed, message pump virtualised, main loop
 * ticks under host control". `runtime/sa2_window.c` owns the model -- the
 * handle tokens, the class and window registry, the queue and the host's
 * iteration budget -- and everything here is the Win32 shape over it: read the
 * guest's structure, hand the fields to the model, write back what Windows
 * would have written.
 *
 * The guest's path, for orientation. `FUN_004019D0` fills a WNDCLASSW at
 * [esp+8] with style 3 (CS_VREDRAW|CS_HREDRAW), lpfnWndProc 0x00401810 and the
 * class name at 0x0089dd14, registers it, computes a window rectangle from
 * five GetSystemMetrics values, creates the window, shows it, focuses it and
 * sets the cursor. WinMain then runs the pump at 0x00401da4. */

/* The guest address that called the import now running.
 *
 * It used to read `g_current_rec->caller`, and `g_current_rec` is null once the
 * 4096-entry call log is full -- so from call 4097 onwards every stub that
 * named its caller named 0x00000000 instead, and 0 reads like an address
 * rather than like "not known". The recording d3d9 device's log shows it:
 * `D3DXCreateTextureFromFileInMemoryEx` at call 14 000 was recorded with
 * caller 0. The caller is now kept in its own saved/restored variable, which
 * has no capacity. */
static _Thread_local uint32_t g_current_caller;

static uint32_t caller_of_current(void) { return g_current_caller; }

uint32_t sa2_import_refused(const char *dll, const char *name, uint32_t code) {
    sa2_import_info *cur = g_current;
    char want[64], have[64];
    module_key(dll, want, sizeof want);
    if (cur) module_key(cur->dll, have, sizeof have);
    if (!cur || strcmp(want, have) || strcmp(name, cur->name)) {
        sa2_trap_note("the refusal marker for %s!%s ran outside that import's call (the "
                      "current import is %s!%s)", dll, name, cur ? cur->dll : "none",
                      cur ? cur->name : "none");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, cur ? cur->thunk : SA2_GUEST_IMPORT_BASE);
    }
    if (!cur->refused_calls++) {
        cur->first_refused_ret = code;
        cur->first_refused_caller = g_current_caller;
    }
    return code;
}

/* A resource name is either a pointer to a wide string or MAKEINTRESOURCE(id),
 * which Windows distinguishes by the high 16 bits being zero. Both are kept as
 * they arrive: a resource id is what this image passes and it is evidence. */
static void resource_text(uint32_t v, char *out, size_t cap) {
    if (v && (v >> 16) == 0u) snprintf(out, cap, "#%u", v);
    else if (!v) snprintf(out, cap, "NULL");
    else if (gwstr(v, out, cap) < 0) snprintf(out, cap, "?");
}

/* USER32!LoadIconW(hInstance, lpIconName) and LoadCursorW.
 *
 * There is no icon and no cursor bitmap to load: a headless host has no
 * resource manager and S5 owns anything that draws. What the guest needs is a
 * non-zero handle it can store in the WNDCLASSW and hand back to the window
 * system, so it gets a typed token and the request is recorded. */
static uint32_t imp_LoadIconW(void *s, const uint32_t *a) {
    (void)s;
    char name[SA2_WND_NAME_MAX];
    resource_text(a[1], name, sizeof name);
    uint32_t h = sa2_window_token(SA2_WND_HICON_BASE, a[0], a[1]);
    sa2_window_log("LoadIconW", caller_of_current(), a[0], a[1], h, 0);
    if (!h) {
        sa2_trap_note("LoadIconW(%#010x, %s): the token table is full", a[0], name);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    return h;
}

static uint32_t imp_LoadCursorW(void *s, const uint32_t *a) {
    (void)s;
    uint32_t h = sa2_window_token(SA2_WND_HCURSOR_BASE, a[0], a[1]);
    sa2_window_log("LoadCursorW", caller_of_current(), a[0], a[1], h, 0);
    if (!h) {
        sa2_trap_note("LoadCursorW(%#010x, %#010x): the token table is full", a[0], a[1]);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    return h;
}

/* USER32!RegisterClassW(const WNDCLASSW *).
 *
 *   +00 style  +04 lpfnWndProc  +08 cbClsExtra  +0C cbWndExtra  +10 hInstance
 *   +14 hIcon  +18 hCursor      +1C hbrBackground +20 lpszMenuName +24 lpszClassName
 *
 * Returns the ATOM. Zero is failure and the guest shows "Failed To Register
 * The Window Class." on it, so a refusal here is visible in the guest's own
 * words rather than only in a trap. */
static uint32_t imp_RegisterClassW(void *s, const uint32_t *a) {
    (void)s;
    sa2_wnd_class c;
    memset(&c, 0, sizeof c);
    const uint32_t p = a[0];
    c.style     = gpeek32(p + 0x00u);
    c.wndproc   = gpeek32(p + 0x04u);
    c.cls_extra = gpeek32(p + 0x08u);
    c.wnd_extra = gpeek32(p + 0x0cu);
    c.hinstance = gpeek32(p + 0x10u);
    c.hicon     = gpeek32(p + 0x14u);
    c.hcursor   = gpeek32(p + 0x18u);
    c.hbrush    = gpeek32(p + 0x1cu);
    c.menu_name_va  = gpeek32(p + 0x20u);
    c.class_name_va = gpeek32(p + 0x24u);
    resource_text(c.class_name_va, c.name, sizeof c.name);
    /* A class with no window procedure is not a class: the pump would have
     * nothing to dispatch to and DispatchMessageW would silently do nothing,
     * which is the shape of bug that looks like a working message loop. */
    if (!c.wndproc) {
        sa2_trap_note("RegisterClassW(\"%s\"): lpfnWndProc is null", c.name);
        sa2_trap_raise(SA2_TRAP_FAULT, p + 4u);
    }
    uint32_t atom = sa2_window_register_class(&c);
    sa2_window_log("RegisterClassW", caller_of_current(), p, c.style, c.wndproc, atom);
    return atom;
}

/* USER32!CreateWindowExW -- twelve arguments, in the order the guest pushes
 * them at 0x00401ae1:
 *   0 dwExStyle 1 lpClassName 2 lpWindowName 3 dwStyle 4 x 5 y 6 nWidth
 *   7 nHeight 8 hWndParent 9 hMenu 10 hInstance 11 lpParam */
static uint32_t imp_CreateWindowExW(void *s, const uint32_t *a) {
    (void)s;
    sa2_wnd_window w;
    memset(&w, 0, sizeof w);
    w.exstyle = a[0];
    resource_text(a[1], w.class_name, sizeof w.class_name);
    resource_text(a[2], w.title, sizeof w.title);
    w.style = a[3];
    w.x = (int32_t)a[4];
    w.y = (int32_t)a[5];
    w.w = (int32_t)a[6];
    w.h = (int32_t)a[7];
    w.parent = a[8];
    w.menu = a[9];
    w.hinstance = a[10];
    w.param = a[11];
    uint32_t h = sa2_window_create(&w);
    sa2_window_log("CreateWindowExW", caller_of_current(), a[3], a[6], a[7], h);
    /* Windows sends WM_CREATE/WM_SIZE here. Nothing is sent: the guest's own
     * window procedure handles neither (0x00401810 dispatches 0x10..0x105 and
     * sends everything else to DefWindowProcW), so a fabricated WM_CREATE
     * would be the runtime inventing traffic that changes nothing and hides
     * what the queue really carries. */
    return h;
}

/* USER32!ShowWindow(hWnd, nCmdShow). Returns whether the window was previously
 * visible, which is the value Windows returns and which is false the first
 * time -- so the guest can distinguish its own first show. */
static uint32_t imp_ShowWindow(void *s, const uint32_t *a) {
    (void)s;
    sa2_wnd_window *w = sa2_window_mutable(a[0]);
    sa2_window_log("ShowWindow", caller_of_current(), a[0], a[1], w ? w->visible : 0u, 0);
    if (!w) { g_last_error = 1400u; return 0; }   /* ERROR_INVALID_WINDOW_HANDLE */
    const uint32_t was = w->visible;
    w->visible = a[1];
    w->shows++;
    return was ? 1u : 0u;
}

static uint32_t imp_SetForegroundWindow(void *s, const uint32_t *a) {
    (void)s;
    sa2_wnd_window *w = sa2_window_mutable(a[0]);
    sa2_window_log("SetForegroundWindow", caller_of_current(), a[0], 0, 0, 0);
    if (!w) return 0;
    w->foregrounds++;
    return 1;
}

/* USER32!SetFocus(hWnd) returns the window that had the focus, or NULL. */
static uint32_t g_focus;
static uint32_t imp_SetFocus(void *s, const uint32_t *a) {
    (void)s;
    sa2_wnd_window *w = sa2_window_mutable(a[0]);
    const uint32_t prev = g_focus;
    sa2_window_log("SetFocus", caller_of_current(), a[0], prev, 0, 0);
    if (!w && a[0]) { g_last_error = 1400u; return 0; }
    g_focus = a[0];
    if (w) w->focuses++;
    return prev;
}

/* USER32!ShowCursor(bShow). A real display counter, not a no-op: Windows
 * starts it at 0 when a mouse is present and the guest calls this with its own
 * full-screen flag, so the returned count is the guest's argument plus the
 * runtime's starting point and moves when the argument moves. */
static int32_t g_cursor_show;
static uint32_t imp_ShowCursor(void *s, const uint32_t *a) {
    (void)s;
    g_cursor_show += a[0] ? 1 : -1;
    sa2_window_log("ShowCursor", caller_of_current(), a[0], (uint32_t)g_cursor_show, 0, 0);
    return (uint32_t)g_cursor_show;
}

/* SPI_GETNONCLIENTMETRICS, the third action this stub answers.
 *
 * It is here because it is a **wall three instructions wide**. The dialog
 * sizer at 0x00402870 calls it at 0x00402ce3 and calls
 * GDI32!CreateFontIndirectW at 0x00402cf1, so refusing it put the whole GDI
 * group behind a USER32 parameter -- a refusal that stopped nine imports that
 * were not themselves refusing. That is the same shape as the DirectSound
 * refusal that disabled CRI's file system five call sites away, and it is the
 * reason `Documentation/scripts/check_refused_presentation.py` exists.
 *
 * **The numbers below are invented and none of them is a measurement.** They
 * are the values a 96-DPI Windows desktop reports, written so the guest's
 * arithmetic has something consistent to work on, and `g_spi_invented` counts
 * every call that fabricated them. SA2_WND_STRICT=1 refuses instead, which is
 * how a run shows whether the guest depends on any of them.
 *
 * `cbSize` is honoured rather than assumed: the caller declares the size it
 * compiled against, this image declares 500 (the pre-Vista NONCLIENTMETRICSW,
 * without `iPaddedBorderWidth`), and writing 504 into a 500-byte buffer would
 * be a four-byte stack overwrite in the guest. Exactly `cbSize` bytes are
 * written, the struct is zeroed first so no field is left holding whatever the
 * caller's stack had, and a size this runtime does not recognise is refused by
 * name rather than rounded to one it does. */
#define SA2_NCM_SIZE_NOPAD 500u     /* through lfMessageFont */
#define SA2_NCM_SIZE_PAD   504u     /* plus iPaddedBorderWidth */

static uint32_t g_spi_invented;

uint32_t sa2_spi_invented(void) { return g_spi_invented; }

/* A zeroed LOGFONTW with lfHeight set, at `off` within the staging buffer.
 * 92 bytes: five LONGs, eight BYTEs, then 32 WCHARs of face name, which is
 * left empty because this runtime has no font to name and an invented face
 * name would be the one field a guest could compare against a real one. */
static void ncm_logfont(uint8_t *buf, uint32_t off, int32_t height) {
    buf[off + 0] = (uint8_t)(uint32_t)height;
    buf[off + 1] = (uint8_t)((uint32_t)height >> 8);
    buf[off + 2] = (uint8_t)((uint32_t)height >> 16);
    buf[off + 3] = (uint8_t)((uint32_t)height >> 24);
    buf[off + 23] = 1u;             /* lfCharSet = DEFAULT_CHARSET is 1 */
}

static void ncm_i32(uint8_t *buf, uint32_t off, int32_t v) {
    for (uint32_t i = 0; i < 4; i++) buf[off + i] = (uint8_t)((uint32_t)v >> (i * 8));
}

static uint32_t spi_nonclientmetrics(uint32_t cbsize, uint32_t pv) {
    if (!pv) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    if (cbsize != SA2_NCM_SIZE_NOPAD && cbsize != SA2_NCM_SIZE_PAD) {
        sa2_trap_note("SystemParametersInfoW(SPI_GETNONCLIENTMETRICS): cbSize is %u, "
                      "which is neither the 500-byte NONCLIENTMETRICSW nor the "
                      "504-byte one with iPaddedBorderWidth. Writing either into a "
                      "buffer of that size would be a guest stack overwrite.", cbsize);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk
                                                        : SA2_GUEST_IMPORT_BASE);
    }
    if (sa2_window_strict()) {
        sa2_trap_note("SystemParametersInfoW(SPI_GETNONCLIENTMETRICS) refused because "
                      "SA2_WND_STRICT=1: every number it would write is invented.");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk
                                                        : SA2_GUEST_IMPORT_BASE);
    }

    uint8_t buf[SA2_NCM_SIZE_PAD];
    memset(buf, 0, sizeof buf);
    ncm_i32(buf,   0, (int32_t)cbsize);   /* cbSize, echoed as Windows does */
    ncm_i32(buf,   4,  1);                /* iBorderWidth      */
    ncm_i32(buf,   8, 17);                /* iScrollWidth      */
    ncm_i32(buf,  12, 17);                /* iScrollHeight     */
    ncm_i32(buf,  16, 22);                /* iCaptionWidth     */
    ncm_i32(buf,  20, 22);                /* iCaptionHeight    */
    ncm_logfont(buf, 24, -12);            /* lfCaptionFont     */
    ncm_i32(buf, 116, 17);                /* iSmCaptionWidth   */
    ncm_i32(buf, 120, 17);                /* iSmCaptionHeight  */
    ncm_logfont(buf, 124, -12);           /* lfSmCaptionFont   */
    ncm_i32(buf, 216, 19);                /* iMenuWidth        */
    ncm_i32(buf, 220, 19);                /* iMenuHeight       */
    ncm_logfont(buf, 224, -12);           /* lfMenuFont        */
    ncm_logfont(buf, 316, -12);           /* lfStatusFont      */
    ncm_logfont(buf, 408, -12);           /* lfMessageFont     */
    if (cbsize == SA2_NCM_SIZE_PAD) ncm_i32(buf, 500, 0);   /* iPaddedBorderWidth */

    gput(pv, (const char *)buf, cbsize);
    g_spi_invented++;
    return 1;
}

/* USER32!SystemParametersInfoW(uiAction, uiParam, pvParam, fWinIni).
 *
 * Two actions are reached: SPI_GETSCREENSAVEACTIVE (0x10) at 0x00401d77 and,
 * only if that reported an active screen saver, SPI_SETSCREENSAVEACTIVE (0x11)
 * at 0x00401d96. A headless host has no screen saver, so the GET writes 0 --
 * the true answer here, not a convenient one -- and the guest skips the SET by
 * its own arithmetic at 0x00401d7d. Any other action is refused. */
static uint32_t imp_SystemParametersInfoW(void *s, const uint32_t *a) {
    (void)s;
    sa2_window_log("SystemParametersInfoW", caller_of_current(), a[0], a[1], a[2], a[3]);
    if (a[0] == 0x10u) {                 /* SPI_GETSCREENSAVEACTIVE */
        if (!a[2]) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
        gpoke32(a[2], 0u);
        return 1;
    }
    if (a[0] == 0x11u) return 1;         /* SPI_SETSCREENSAVEACTIVE: recorded, no effect */
    if (a[0] == 0x29u) return spi_nonclientmetrics(a[1], a[2]);  /* SPI_GETNONCLIENTMETRICS */
    sa2_trap_note("SystemParametersInfoW(action %#x): the runtime answers only "
                  "SPI_GET/SETSCREENSAVEACTIVE and SPI_GETNONCLIENTMETRICS; this one "
                  "has no defensible answer yet", a[0]);
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

/* USER32!GetSystemMetrics(nIndex). sa2_window.c holds the values and refuses
 * the indices it has no defensible answer for. */
static uint32_t imp_GetSystemMetrics(void *s, const uint32_t *a) {
    (void)s;
    uint32_t v = 0;
    if (!sa2_window_metric(a[0], &v)) {
        sa2_trap_note("GetSystemMetrics(%u): no defensible value for this index; the five "
                      "the window code reads are 0, 1, 4, 32 and 33", a[0]);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    sa2_window_log("GetSystemMetrics", caller_of_current(), a[0], v, 0, 0);
    return v;
}

/* KERNEL32!SetThreadExecutionState(esFlags): the guest asks Windows not to
 * blank the display while it runs. Recorded; the previous state is returned,
 * which for a process that has never called it is ES_CONTINUOUS. */
static uint32_t g_exec_state = 0x80000000u;
static uint32_t imp_SetThreadExecutionState(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t prev = g_exec_state;
    g_exec_state = a[0];
    sa2_window_log("SetThreadExecutionState", caller_of_current(), a[0], prev, 0, 0);
    return prev;
}

/* ------------------------------------------------------------- Steam
 *
 * Design 3.5 (c). The twelve `steam_api` entry points are all cdecl and all
 * land in one place: `runtime/sa2_steam.c` decides what each answers and owns
 * the fabricated interfaces the accessors return. The stub here is one
 * function because the only thing that varies is the name, and the name is
 * already in the import record. */
/* SteamMetricsSDK!MetricsAgent. All four go to one adapter, which passes the
 * decorated name through: the import's own `name` field is the decoration, and
 * matching on it in `sa2_iface.c` keeps the four-way choice in one place.
 *
 * Two of the four are `__thiscall` (`QAE` in the decoration). That needs no new
 * convention here -- a thiscall callee pops its stack arguments exactly as a
 * stdcall one does, and takes `this` in ECX, which these stubs never read. So
 * the rows below are SA2_IMP_STDCALL and `argc` counts the stack arguments
 * only: three for Initialize(const char*, int, const char*) and three for
 * LogEvent(const char*, const char*, const char*), with `this` in neither. */
static uint32_t imp_steam_metrics(void *s, const uint32_t *a) {
    (void)s;
    return sa2_steam_metrics(g_current ? g_current->name : "", a, caller_of_current());
}

/* XINPUT1_3 ordinals 2, 3 and 4. See `sa2_iface.h` for why the default answer
 * is ERROR_DEVICE_NOT_CONNECTED and what SA2_XINPUT_PAD=1 is for.
 *
 * The out-parameter is written on **both** paths, which is the whole reason
 * this adapter exists rather than the module returning a status alone: XInput
 * fills the caller's XINPUT_STATE or XINPUT_CAPABILITIES, and a caller that
 * reads a buffer this runtime never wrote reads its own stack. XInputSetState
 * has no out-parameter -- its second argument is an XINPUT_VIBRATION the guest
 * supplies -- so `bytes` is zero for it and nothing is written. */
static uint32_t imp_xinput(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t caller = caller_of_current();
    const sa2_import_info *info = g_current ? sa2_import_for_thunk(g_current->thunk) : NULL;
    const uint32_t ordinal = info ? info->ordinal : 0u;
    uint32_t buf[32], len = 0;
    const uint32_t st = sa2_xinput(ordinal, a, caller, buf, &len);
    if (len) {
        /* The out-parameter is the last argument, and the argument count is
         * not the same for the two that have one: GetState(dwUserIndex,
         * pState) puts it at a[1] and GetCapabilities(dwUserIndex, dwFlags,
         * pCapabilities) at a[2]. The index comes from the ordinal rather
         * than from an assumption that they agree. */
        const uint32_t out = (ordinal == 4u) ? a[2] : a[1];
        if (!out) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 87u; }
        for (uint32_t i = 0; i < len; i++) {
            uint8_t *q = sa2_gp(out + i, 1);
            if (!q) {
                sa2_trap_note("XINPUT1_3!Ordinal#%u: the %u-byte out-parameter at "
                              "guest %#010x leaves the guest space", ordinal, len, out);
                sa2_trap_raise(SA2_TRAP_FAULT, out + i);
            }
            *q = (uint8_t)buf[i];
        }
    }
    /* ERROR_DEVICE_NOT_CONNECTED is this stub's refusal by policy: the answer
     * the native game gets on a host with no pad (Documentation/receipts/
     * s4-native-outcomes-v1.json, outcome xinput-not-connected), marked so
     * tools/s4/refusal_audit.py derives it from this source. One marker per
     * ordinal, because a marker names its import with literals. */
    if (st == 1167u) {
        switch (ordinal) {
        case 2u: return sa2_import_refused("XINPUT1_3.dll", "Ordinal#2", st);
        case 3u: return sa2_import_refused("XINPUT1_3.dll", "Ordinal#3", st);
        case 4u: return sa2_import_refused("XINPUT1_3.dll", "Ordinal#4", st);
        default: break;
        }
    }
    return st;
}

static uint32_t imp_steam(void *s, const uint32_t *a) {
    return sa2_steam_api(g_current ? g_current->name : "", s, a, caller_of_current());
}

/* KERNEL32!GetSystemInfo(LPSYSTEM_INFO)
 *
 * The page size and the application address range are facts about the
 * machine this runtime is: a 4 KiB page, because that is `SA2_PAGE_SHIFT`, and
 * the guest space it actually maps, so a guest that clamps an allocation to it
 * cannot be handed an address outside it. The processor count and the active
 * processor mask are the capture's, from its device profile (see
 * GetProcessAffinityMask above). A replay depends on the capture it replays,
 * not on the host it runs on, and G1's scheduler runs one guest thread at a
 * time whatever the count.
 *
 * The one field this image reads is the processor count:
 *
 *     007fb9c7  call [8731E4h]        ; GetSystemInfo(&si)
 *     007fb9cd  mov eax,[esp+14h]     ; si.dwNumberOfProcessors
 *
 * Only a count above one makes Sofdec build its three decoding workers
 * (007fbc90). */
static uint32_t imp_GetSystemInfo(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t p = a[0];
    if (!p) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    sa2_guest_fill(p, 0, 36u);
    uint16_t arch = 0;                         /* PROCESSOR_ARCHITECTURE_INTEL */
    sa2_poke(p + 0u, 2, &arch);
    gpoke32(p + 4u, 1u << SA2_PAGE_SHIFT);
    gpoke32(p + 8u, SA2_GUEST_NULL_GUARD);
    gpoke32(p + 12u, SA2_GUEST_END - 1u);
    gpoke32(p + 16u, g_system_mask);
    gpoke32(p + 20u, g_cpus);
    gpoke32(p + 24u, 586u);                    /* PROCESSOR_INTEL_PENTIUM */
    gpoke32(p + 28u, 0x10000u);                /* the documented granularity */
    uint16_t level = 6u, rev = 0x0f01u;        /* named placeholders, like the CPUID model */
    sa2_poke(p + 32u, 2, &level);
    sa2_poke(p + 34u, 2, &rev);
    return 0;
}

/* DSOUND.dll ordinal 11, DirectSoundCreate8(pcGuidDevice, ppDS8, pUnkOuter)
 *
 * With `SA2_DSOUND_DEVICE=1` this hands back the recording `IDirectSound8` of
 * `runtime/sa2_dsound.c`: design 3.5 puts dsound in group (b), presentation,
 * and design 5 S4's work list asks for "presentation stubs as no-ops that
 * record calls". Nothing audible is involved and no host audio device is
 * opened; S5 owns "dsound stubs to PCM streams", which is the audio.
 *
 * Refusing was not neutral. S4 slice 10 traced the CRI streaming livelock to
 * this call: the guest's sound init at 0x00861260 returns at 0x008612AD on the
 * failure branch, and CRI's file-system setup (0x007C5270, called at
 * 0x008613BF) is **past that return**. With no file system there is no CRIFS
 * worker, a queued partition read is never started, and the poll at 0x004425D0
 * can never start the thing it waits for.
 *
 * Without the device the old answer stands, and the paragraph below is why it
 * was safe as an answer even though it was not safe as a consequence: the
 * guest treats the failure as a *warning*, not as a reason to stop.
 *
 *     0086126c  call 007C0CC8h                ; DirectSoundCreate8(0, &g, 0)
 *     00861271  mov eax,[1A51638h]            ; the global it was to fill
 *     00861278  jne 008612AEh                 ; non-null: set it up
 *     0086127b  call [873238h]                ; Sleep(0) and try once more
 *     0086129e  push 89E8F0h                  ; "DirectSound Create Failed!"
 *     008612a4  call [873348h]                ; MessageBoxA
 *     008612ad  ret                           ; and carry on without sound
 *
 * so it retries once, tells the user, and returns. It does not exit. The
 * message box is therefore an *expected* artefact of a run with no audio, and
 * it is recorded by `sa2_msgbox_count` rather than displayed -- which is why a
 * run of this shape reports `no_message_box` false and says why, instead of the
 * probe being taught to ignore it.
 *
 * The global is zeroed explicitly. The guest reads it rather than the return
 * value, and leaving it holding whatever it held would make the answer depend
 * on the previous contents of a guest global. */
static uint32_t imp_DirectSoundCreate8(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t dev = sa2_dsound_make_device(caller_of_current());
    if (!dev) {
        if (a[1]) gpoke32(a[1], 0);
        return 0x80004005u;      /* E_FAIL */
    }
    /* The guest reads the out-pointer rather than the return value (0x00861271
     * reads [0x01A51638] straight after the call), so the pointer is what
     * matters; the S_OK is for the callers that do look. */
    if (a[1]) gpoke32(a[1], dev);
    return 0u;
}

/* d3dx9_42.dll!D3DXCreateTexture(pDevice, W, H, MipLevels, Usage, Format,
 *                                 Pool, ppTexture)
 *
 * The second of the eleven `d3dx9_42` imports a run has reached, and it is a
 * different *kind* of entry point from the first, which is the distinction
 * `Documentation/S5_INPUTS.md`'s third question turns on. This one is a thin
 * wrapper over `IDirect3DDevice9::CreateTexture` -- it rounds the size and
 * format to what the device supports and then calls it -- so anything that
 * implements d3d9 can carry it, and here it is answered by the same fabricated
 * object pool the device method uses. `D3DXGetShaderConstantTable` is not:
 * that one is shader reflection and no d3d9 implementation provides it. */
static uint32_t imp_D3DXCreateTexture(void *s, const uint32_t *a) {
    (void)s;
    return sa2_d3d9_d3dx_create_texture(a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7],
                                        caller_of_current());
}

/* d3dx9_42.dll!D3DXCreateTextureFromFileInMemoryEx(pDevice, pSrcData, SrcDataSize,
 *   Width, Height, MipLevels, Usage, Format, Pool, Filter, MipFilter, ColorKey,
 *   pSrcInfo, pPalette, ppTexture)
 *
 * Fifteen arguments, and the engine passes D3DX_DEFAULT for the four that
 * describe the image, so the answer has to come out of the blob the guest
 * read from its own installation. `runtime/sa2_d3d9.c` parses the DDS header
 * there and refuses anything else by name. */
static uint32_t imp_D3DXCreateTextureFromFileInMemoryEx(void *s, const uint32_t *a) {
    (void)s;
    return sa2_d3d9_d3dx_texture_from_memory(a, caller_of_current());
}

/* The other eight `d3dx9_42` imports, and the reason they are eight one-line
 * adapters rather than eight bodies: every one of them is answered inside
 * `runtime/sa2_d3d9.c`, by the same fabricated object pool, the same texture
 * record and the same refusal path the three above already use. The argument
 * *count* lives in the `g_stubs` row below and nowhere else -- each adapter
 * takes `a` whole -- so a count cannot drift between a C signature here and
 * the number of dwords the stub pops.
 *
 * Every count was read off the call site's own pushes as well as the
 * published signature. Three of them are not what a reading of the header
 * alone suggests, and each one would have drifted the caller's frame:
 *
 *   D3DXSaveSurfaceToFileA                     5, not 4   (0x00869580)
 *   D3DXLoadSurfaceFromSurface                 8, not 7   (0x008676ad, ...)
 *   D3DXCreateCubeTextureFromFileInMemoryEx   14, not 13  (0x008668e2)
 *   D3DXCreateVolumeTextureFromFileInMemoryEx 16, not 15  (0x00866848)
 *
 * The cross-check that settles the family: the 2D
 * `D3DXCreateTextureFromFileInMemoryEx` above is 15 and 0x008667f4 pushes
 * fifteen. A cube map replaces its Width and Height with one Size, so it is
 * 14; a volume adds Depth, so it is 16. */
static uint32_t imp_D3DXCreateCubeTexture(void *s, const uint32_t *a) {
    (void)s;
    return sa2_d3d9_d3dx_create_cube_texture(a, caller_of_current());
}

static uint32_t imp_D3DXCreateCubeTextureFromFileInMemoryEx(void *s, const uint32_t *a) {
    (void)s;
    return sa2_d3d9_d3dx_cube_from_memory(a, caller_of_current());
}

static uint32_t imp_D3DXCreateVolumeTexture(void *s, const uint32_t *a) {
    (void)s;
    return sa2_d3d9_d3dx_create_volume_texture(a, caller_of_current());
}

static uint32_t imp_D3DXCreateVolumeTextureFromFileInMemoryEx(void *s, const uint32_t *a) {
    (void)s;
    return sa2_d3d9_d3dx_volume_from_memory(a, caller_of_current());
}

/* The four whose whole effect is bytes -- a filtered mip chain, a converted
 * surface, a file on the host's disk -- and which therefore have no
 * out-parameter to write. Each is a recorded no-op success and each is
 * counted as one in `sa2_d3d9_stats`; `SA2_D3DX_STRICT=1` refuses all four by
 * name. Neither `D3DXSave...ToFileA` opens a host file: the filename the
 * guest asked for is recorded (`sa2_d3d9_savefile_at`) because that path is
 * the only evidence the call carries. */
static uint32_t imp_D3DXFilterTexture(void *s, const uint32_t *a) {
    (void)s;
    return sa2_d3d9_d3dx_filter_texture(a, caller_of_current());
}

static uint32_t imp_D3DXLoadSurfaceFromSurface(void *s, const uint32_t *a) {
    (void)s;
    return sa2_d3d9_d3dx_load_surface_from_surface(a, caller_of_current());
}

static uint32_t imp_D3DXSaveSurfaceToFileA(void *s, const uint32_t *a) {
    (void)s;
    return sa2_d3d9_d3dx_save_surface_to_file(a, caller_of_current());
}

static uint32_t imp_D3DXSaveTextureToFileA(void *s, const uint32_t *a) {
    (void)s;
    return sa2_d3d9_d3dx_save_texture_to_file(a, caller_of_current());
}

/* DINPUT8.dll!DirectInput8Create(hinst, dwVersion, riidltf, ppvOut, punkOuter)
 *
 * The native game gets an IDirectInput8A here (dinput-trace-v1a:108; it calls
 * through the result at :114), so with SA2_DINPUT_DEVICE=1 this answers the
 * recording device of runtime/sa2_dinput.h, which answers every host question
 * from the capture's device profile. With the device off it answers E_FAIL and
 * a null pointer through the refusal marker, which is the answer this runtime
 * gave before the device existed and not the one native gets. That is legal
 * only under the default profile, as the before measurement:
 * sa2_dinput_config_from_env refuses a capture's profile with the device off.
 * The guest's own code takes the failure:
 *
 *     00864d33  call 007C0CE0h            ; DirectInput8Create(..., &pDI, NULL)
 *     00864d38  test eax,eax
 *     00864d3a  jge 00864D42h
 *     00864d3c  mov dword [ebx],0         ; failure: forget the interface
 *     00864d42  cmp dword [ebx],0
 *     00864d45  je  00864EF4h             ; and skip the whole device setup
 *
 * **The pointer, not the HRESULT, decides whether the setup runs.** The input
 * manager's constructor zeroes the pointer before the call, 00864d42 tests it
 * after, and a failure only makes sure it is zero. So a bare DI_OK that wrote
 * nothing would skip the setup exactly as E_FAIL does and never reach
 * 0x00864d4b; this comment said otherwise until the DirectInput design decoded
 * the guard, and SA2_DINPUT_BREAK=bare-ok is the run that shows it. What
 * reproduces native is a real interface, which is what the device is. */
static uint32_t imp_DirectInput8Create(void *s, const uint32_t *a) {
    (void)s;
    if (sa2_dinput_device_enabled()) return sa2_dinput_create8(a, caller_of_current());
    if (a[3]) gpoke32(a[3], 0);
    return sa2_import_refused("DINPUT8.dll", "DirectInput8Create", 0x80004005u);  /* E_FAIL */
}

/* d3dx9_42.dll!D3DXGetShaderConstantTable(const DWORD *pFunction,
 *                                           LPD3DXCONSTANTTABLE *ppConstantTable)
 *
 * **The first of the eleven `d3dx9_42` imports any run has reached.** The
 * previous slice recorded all eleven as unreached and said so as a floor; this
 * one is reached by the renderer's shader setup as soon as a device exists.
 * Its one call in the image is in the routine at 008671b0, which reads the
 * shader's bytecode back and hands the import its own caller's out-pointer:
 *
 *     00867200  call eax                 ; the shader's GetFunction(NULL, &size)
 *     00867207  call 007A4B64h           ; allocate that many bytes
 *     0086721d  call ecx                 ; GetFunction(buffer, &size)
 *     00867225  call 007A4ACAh           ; D3DXGetShaderConstantTable(buffer, out)
 *     0086722a  test edi,edi             ; the buffer, not the table or the HRESULT
 *     0086722f  call 007A613Fh           ; free the buffer
 *
 * Its two callers zero the slot they pass (00866324, 00866384) and store what
 * the slot then holds at [object+50h] (00866338, 00866398) with no test, so a
 * null table is stored as a table would be. Eight virtual methods use the
 * stored table, each referenced in the image only by its slot. Two release it
 * after a null test: 00866060 and 00866020 (slots 008b7100 and 008b7108; tests
 * at 0086607e and 0086603e). The other six -- 008673d0, 00867240, 00867290,
 * 008672e0, 00867330 and 00867380, slots 008b7074 through 008b7088 -- read it
 * from [object+50h] and call through it with no null test: GetConstantByName
 * while its cached constant handle is null, then GetConstantDesc, SetMatrix,
 * SetVector, SetFloat, SetInt or SetBool. The test at 00867252 and the jne at
 * 00867254, and their twins in the other five, are of that cached handle (in
 * 008673d0 also of a cached register index), not of the table. **So a null
 * table is not a path the guest's lookup and set methods have**: a method that
 * has to look a constant up or set one reads through the table, and with this
 * stub's refusal faults there.
 *
 * Native answers success with a table every time and calls no method on any
 * table through boot, the title and the City Escape attract demo's gameplay
 * (Documentation/receipts/s4-native-outcomes-v1.json, outcome
 * d3dx-constant-table), so in that span none of the eight calls through a
 * table. The owned-reflection candidate in sa2_d3d9.c snapshots the entire
 * CTAB before this caller frees its buffer, resolves numeric/sampler names and
 * writes x86 descriptions. Unsupported layouts and setter methods remain named
 * refusals; held-native reflection/setter conformance remains separate work.
 *
 * With SA2_D3DX_CONSTANT_TABLE set, runtime/sa2_d3d9.c builds a table with COM
 * lifetime and the two reflection methods. Without the switch the call is
 * recorded, the out-pointer is NULL and E_FAIL returns through the refusal
 * marker. This import remains the presentation reflection entry point;
 * here, it is shader reflection, and nothing that implements d3d9 provides it. */
static uint32_t imp_D3DXGetShaderConstantTable(void *s, const uint32_t *a) {
    (void)s;
    if (sa2_d3d9_constant_table_enabled())
        return sa2_d3d9_d3dx_get_shader_constant_table(a, caller_of_current());
    if (a[1]) gpoke32(a[1], 0);
    /* E_FAIL with no table is this stub's refusal by policy, marked so
     * tools/s4/refusal_audit.py derives it from this source. Native answers
     * success with a table (Documentation/receipts/s4-native-outcomes-v1.json,
     * outcome d3dx-constant-table), so the refusal does not reproduce it, and
     * M4 does not count the import (Documentation/refusal-reasons.json). */
    return sa2_import_refused("d3dx9_42.dll", "D3DXGetShaderConstantTable", 0x80004005u);  /* E_FAIL */
}

/* d3d9.dll!Direct3DCreate9(UINT SDKVersion). The one d3d9 import this image
 * has; everything else it asks of Direct3D is a vtable call on what this
 * returns.
 *
 * Three answers, in order of preference and each switched on explicitly:
 * SA2_D3D9_DEVICE gives the recording no-op device (runtime/sa2_d3d9.c),
 * SA2_D3D9_SURVEY gives the older object that answers nothing and logs the
 * first n methods, and with neither set the import refuses by name. The
 * default is the refusal, so no run changes its behaviour by linking this in. */
static uint32_t imp_Direct3DCreate9(void *s, const uint32_t *a) {
    const uint32_t caller = caller_of_current();
    const uint32_t dev = sa2_d3d9_make_d3d9(caller);
    if (dev) return dev;
    return sa2_d3d9_create(s, a[0], caller);
}

/* ---- the MSG structure, in guest memory ----
 *   +00 hwnd +04 message +08 wParam +0C lParam +10 time +14 pt.x +18 pt.y */
static void msg_put(uint32_t p, const sa2_wnd_msg *m) {
    gpoke32(p + 0x00u, m->hwnd);
    gpoke32(p + 0x04u, m->msg);
    gpoke32(p + 0x08u, m->wparam);
    gpoke32(p + 0x0cu, m->lparam);
    gpoke32(p + 0x10u, m->time);
    gpoke32(p + 0x14u, (uint32_t)m->pt_x);
    gpoke32(p + 0x18u, (uint32_t)m->pt_y);
}

static void msg_get(uint32_t p, sa2_wnd_msg *m) {
    memset(m, 0, sizeof *m);
    m->hwnd   = gpeek32(p + 0x00u);
    m->msg    = gpeek32(p + 0x04u);
    m->wparam = gpeek32(p + 0x08u);
    m->lparam = gpeek32(p + 0x0cu);
    m->time   = gpeek32(p + 0x10u);
    m->pt_x   = (int32_t)gpeek32(p + 0x14u);
    m->pt_y   = (int32_t)gpeek32(p + 0x18u);
}

/* USER32!PeekMessageW(lpMsg, hWnd, min, max, wRemoveMsg).
 *
 * This is the top of the guest's main loop and therefore the place the host's
 * control over the loop lives: `sa2_window_poll` advances the iteration count,
 * runs any scheduled injection and posts WM_QUIT when the host's budget is
 * spent. The filters are refused rather than ignored -- the one call site
 * passes 0, 0, 0, and a runtime that accepted a filter it does not implement
 * would answer "no messages" for a queue that had one. */
static uint32_t imp_PeekMessageW(void *s, const uint32_t *a) {
    (void)s;
    if (a[1] || a[2] || a[3]) {
        sa2_trap_note("PeekMessageW(hWnd %#010x, filter %#x..%#x): the pump implements no "
                      "window or message filter; this call would be answered wrongly",
                      a[1], a[2], a[3]);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    /* The return address this poll came back to, counted exactly. It is what
     * distinguishes WinMain's own loop from any other pump. */
    sa2_window_note_poll_caller(caller_of_current());
    /* One tick row per poll, taken *before* the hand-off, so the row is the
     * state this thread saw and not the state the worker left behind. */
    sa2_sched_tick_note(caller_of_current());
    /* The message pump is a hand-off point (G1 section 3): a main loop that
     * polls and never sleeps would otherwise hold the guest lock forever and
     * the worker threads it started would never run a single instruction. */
    sa2_sched_yield("PeekMessageW");
    sa2_wnd_msg m;
    const int have = sa2_window_poll(&m);
    if (have) {
        msg_put(a[0], &m);
        if (a[4] & SA2_PM_REMOVE) {
            sa2_wnd_msg drop;
            sa2_window_get(&drop);
        }
    }
    return have ? 1u : 0u;
}

/* USER32!GetMessageW(lpMsg, hWnd, min, max). Returns 0 on WM_QUIT, which is
 * the guest's own way out of its loop, and never waits -- see sa2_window.h. */
static uint32_t imp_GetMessageW(void *s, const uint32_t *a) {
    (void)s;
    if (a[1] || a[2] || a[3]) {
        sa2_trap_note("GetMessageW(hWnd %#010x, filter %#x..%#x): the pump implements no "
                      "window or message filter", a[1], a[2], a[3]);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    sa2_wnd_msg m;
    const int rc = sa2_window_get(&m);
    if (rc < 0) {
        sa2_trap_note("GetMessageW with an empty queue would block forever: nothing in this "
                      "process can post a message while it waits. Call it only after a "
                      "PeekMessageW that reported one.");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    msg_put(a[0], &m);
    return rc ? 1u : 0u;
}

/* USER32!TranslateMessage(lpMsg). There is no keyboard layout and no key
 * state, so no WM_CHAR is synthesised; the call is counted. Windows returns
 * non-zero only when it translated something, so 0 is the honest answer. */
static uint32_t imp_TranslateMessage(void *s, const uint32_t *a) {
    (void)s;
    sa2_wnd_msg m;
    msg_get(a[0], &m);
    sa2_window_note_translate();
    sa2_window_log("TranslateMessage", caller_of_current(), m.hwnd, m.msg, m.wparam, m.lparam);
    return 0;
}

/* Run a guest window procedure on the live CPU state.
 *
 * This is a call into lifted code from inside a native, which `sa2_call`
 * cannot do -- it owns one shared remill State buffer and re-entering it would
 * overwrite the state the guest is running on. So the call is made the way
 * lifted code makes one: build the frame on the guest's own stack below the
 * import's, point EIP at the procedure, dispatch through the function table,
 * and check afterwards that the callee cleaned up the way a stdcall
 * WNDPROC must.
 *
 * The check is ESP rather than EIP on purpose. The guest's procedure at
 * 0x00401810 ends either in its own `ret 10h` -- which leaves EIP at the
 * sentinel -- or in `jmp dword [873368h]`, a tail jump into DefWindowProcW,
 * after which EIP is the import thunk and no `ret` ran in lifted code at all.
 * Both are correct returns and only the stack pointer says so in both cases. */
static uint32_t call_wndproc(void *s, uint32_t proc, uint32_t hwnd, uint32_t msg,
                             uint32_t wparam, uint32_t lparam) {
    sa2_lifted_fn fn = sa2_ft_lookup(proc);
    if (!fn) {
        sa2_trap_note("the window procedure at %#010x has no lifted function", proc);
        sa2_trap_raise(SA2_TRAP_NOT_RUNNABLE, proc);
    }
    const uint32_t esp0 = sa2_rt_get_gpr(s, SA2_ESP);
    const uint32_t eip0 = sa2_rt_get_gpr(s, SA2_EIP);
    /* 0x40 of slack below the import frame, so a callee that reads a little
     * past its own arguments cannot see this native's scratch. */
    const uint32_t base = esp0 - 0x40u;
    gpoke32(base + 0x00u, SA2_CALL_SENTINEL);
    gpoke32(base + 0x04u, hwnd);
    gpoke32(base + 0x08u, msg);
    gpoke32(base + 0x0cu, wparam);
    gpoke32(base + 0x10u, lparam);
    sa2_rt_set_gpr(s, SA2_ESP, base);
    sa2_rt_set_gpr(s, SA2_EIP, proc);
    fn(s, proc, NULL);
    const uint32_t esp1 = sa2_rt_get_gpr(s, SA2_ESP);
    const uint32_t ret = sa2_rt_get_gpr(s, SA2_EAX);
    if (esp1 != base + 0x14u) {
        sa2_trap_note("window procedure %#010x(msg %#06x) left ESP at %#010x, not %#010x: a "
                      "stdcall WNDPROC must pop its own four arguments", proc, msg, esp1,
                      base + 0x14u);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, proc);
    }
    sa2_rt_set_gpr(s, SA2_ESP, esp0);
    sa2_rt_set_gpr(s, SA2_EIP, eip0);
    return ret;
}

/* USER32!DispatchMessageW(lpMsg): the guest's own window procedure runs. */
static uint32_t imp_DispatchMessageW(void *s, const uint32_t *a) {
    sa2_wnd_msg m;
    msg_get(a[0], &m);
    sa2_window_log("DispatchMessageW", caller_of_current(), m.hwnd, m.msg, m.wparam, m.lparam);
    const sa2_wnd_window *w = sa2_window_of(m.hwnd);
    if (!w || !w->wndproc) {
        /* Windows discards a message for a window it does not know, except for
         * WM_TIMER with a callback, which this image never posts. */
        sa2_window_note_dispatch(1);
        return 0;
    }
    sa2_window_note_dispatch(0);
    const uint32_t r = call_wndproc(s, w->wndproc, m.hwnd, m.msg, m.wparam, m.lparam);
    sa2_window_note_wndproc_returned();   /* after, so the count needs the guest */
    return r;
}

/* USER32!DefWindowProcW(hWnd, Msg, wParam, lParam). Reached as a *tail jump*
 * from the guest's own procedure at 0x004018b1, so its caller is whoever
 * called the procedure. Nothing here has default behaviour worth inventing:
 * there is no non-client area, no menu and no system commands. */
static uint32_t imp_DefWindowProcW(void *s, const uint32_t *a) {
    (void)s;
    sa2_window_log("DefWindowProcW", caller_of_current(), a[0], a[1], a[2], a[3]);
    if (a[1] == SA2_WM_CLOSE) {
        /* DefWindowProc's documented answer to WM_CLOSE is DestroyWindow, which
         * posts WM_DESTROY. The guest handles WM_CLOSE itself, so this path is
         * not reached today; it is here because a silent 0 would be a lie about
         * what a window system does with a close. */
        sa2_window_post(a[0], SA2_WM_DESTROY, 0, 0);
    }
    return 0;
}

/* USER32!PostMessageW / PostQuitMessage: the guest's own way to end its loop.
 * PostQuitMessage from the guest is the observable that says its window
 * procedure ran -- nothing in the runtime calls it. */
static uint32_t imp_PostMessageW(void *s, const uint32_t *a) {
    (void)s;
    sa2_window_log("PostMessageW", caller_of_current(), a[0], a[1], a[2], a[3]);
    if (a[1] == SA2_WM_QUIT) { sa2_window_post_quit(a[2], 0); return 1; }
    return (uint32_t)sa2_window_post(a[0], a[1], a[2], a[3]);
}

static uint32_t imp_PostQuitMessage(void *s, const uint32_t *a) {
    (void)s;
    sa2_window_log("PostQuitMessage", caller_of_current(), a[0], 0, 0, 0);
    sa2_window_post_quit(a[0], 0);
    return 0;
}

static uint32_t imp_DestroyWindow(void *s, const uint32_t *a) {
    (void)s;
    sa2_wnd_window *w = sa2_window_mutable(a[0]);
    sa2_window_log("DestroyWindow", caller_of_current(), a[0], 0, 0, 0);
    if (!w) { g_last_error = 1400u; return 0; }
    w->visible = 0;
    sa2_window_post(a[0], SA2_WM_DESTROY, 0, 0);
    return 1;
}

static uint32_t imp_IsWindow(void *s, const uint32_t *a) {
    (void)s;
    return sa2_window_of(a[0]) ? 1u : 0u;
}

/* USER32!GetClientRect / GetWindowRect(hWnd, RECT*). The client rectangle is
 * the size CreateWindowExW was given, origin at zero, which is what a window
 * with no non-client area has. The window rectangle is the same size at the
 * position the guest asked for. Both are the guest's own numbers handed back,
 * not the runtime's invention. */
static uint32_t rect_out(uint32_t p, int32_t l, int32_t t, int32_t r, int32_t b) {
    gpoke32(p + 0x0u, (uint32_t)l);
    gpoke32(p + 0x4u, (uint32_t)t);
    gpoke32(p + 0x8u, (uint32_t)r);
    gpoke32(p + 0xcu, (uint32_t)b);
    return 1;
}
static uint32_t imp_GetClientRect(void *s, const uint32_t *a) {
    (void)s;
    const sa2_wnd_window *w = sa2_window_of(a[0]);
    sa2_window_log("GetClientRect", caller_of_current(), a[0], a[1], 0, 0);
    if (!w) { g_last_error = 1400u; return 0; }
    return rect_out(a[1], 0, 0, w->w, w->h);
}
static uint32_t imp_GetWindowRect(void *s, const uint32_t *a) {
    (void)s;
    const sa2_wnd_window *w = sa2_window_of(a[0]);
    sa2_window_log("GetWindowRect", caller_of_current(), a[0], a[1], 0, 0);
    if (!w) { g_last_error = 1400u; return 0; }
    return rect_out(a[1], w->x, w->y, w->x + w->w, w->y + w->h);
}

/* ------------------------------------------- the rest of USER32 (design 3.5 b)
 *
 * Thirty names the image imports and nothing answered, so every one of them
 * raised a trap and stopped the guest. Design 3.5 puts USER32's window and
 * message surface in group (b), presentation; S4's work list says
 * "presentation stubs as no-ops that record calls". So the default here is
 * `sa2_window_note_noop`, and the exceptions are the calls for which a no-op
 * success is a statement the guest acts on and the statement is false.
 *
 * `runtime/sa2_window.h`'s header comment holds the reasoning for each of the
 * five exceptions (SendMessageW, Get/SetWindowLongW, SetTimer/KillTimer,
 * WaitMessage, the key-state pair), for the handle-token regions, and for the
 * four fabricated values that `SA2_WND_STRICT=1` turns back into refusals.
 * What is here is only the Win32 shape over that model: read the guest's
 * structure, hand plain host values to sa2_window.c, write back what Windows
 * would have written.
 */

/* A USER32 call this runtime has no defensible answer for. Factored out
 * because the alternative is the same two lines twelve times, and a refusal
 * that is cheap to write is a refusal that gets written instead of a guess. */
static void user32_refuse(void) __attribute__((noreturn));
static void user32_refuse(void) {
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

/* A host string into a guest UTF-16 buffer, NUL terminated. Returns the
 * characters written not counting the NUL, which is what GetWindowTextW and
 * GetClassNameW return. The NUL is always written when the buffer has room
 * for one, because a caller that reads an unwritten buffer reads its own
 * stack -- the defect class sa2_dsound.h rule 2 exists for. */
static int gwput(uint32_t a, const char *s, int cap) {
    if (cap <= 0) return 0;
    char buf[2 * (SA2_WND_NAME_MAX + 1)];
    int max = cap - 1;
    if (max > (int)SA2_WND_NAME_MAX) max = (int)SA2_WND_NAME_MAX;
    int n = 0;
    for (; n < max && s[n]; n++) { buf[2 * n] = s[n]; buf[2 * n + 1] = 0; }
    buf[2 * n] = 0;
    buf[2 * n + 1] = 0;
    gput(a, buf, (uint32_t)(2 * n + 2));
    return n;
}

/* SendMessageW's body, shared with the one other USER32 call in this image
 * that is synchronous: RedrawWindow with RDW_UPDATENOW.
 *
 * This is the one place a sa2_window.c decision is carried out here rather
 * than there, because running a guest window procedure needs the live CPU
 * state and `call_wndproc` already crosses that line for DispatchMessageW.
 * The window procedure address is copied out of the record *before* the call:
 * the procedure may subclass its own window, and re-reading the field
 * afterwards would attribute the call to whatever it installed. */
static uint32_t user32_send(void *s, uint32_t hwnd, uint32_t msg,
                            uint32_t wparam, uint32_t lparam) {
    const sa2_wnd_window *w = sa2_window_of(hwnd);
    if (!w || !w->wndproc) {
        /* A handle this runtime does not own has no procedure to run, so the
         * answer is the one DispatchMessageW gives for the same handle. It is
         * counted apart from a real send, because a run in which every send
         * went nowhere and a run in which every send ran guest code must not
         * report the same number. */
        sa2_window_note_send_default(hwnd, msg);
        return 0;
    }
    const uint32_t proc = w->wndproc;
    if (!sa2_window_send_enter(hwnd, msg)) {
        sa2_trap_note("SendMessageW(%#010x, msg %#06x): %u sends are already on the stack "
                      "and the bound is %u. A window procedure that sends to its own "
                      "window recurses until the guest stack runs out, and the "
                      "stack-guard refusal in sa2_threads.c would then name a site "
                      "thousands of frames from this one. SA2_WND_SEND_DEPTH moves the "
                      "bound.", hwnd, msg, sa2_window_send_depth(),
                      sa2_window_send_depth_limit());
        user32_refuse();
    }
    const uint32_t r = call_wndproc(s, proc, hwnd, msg, wparam, lparam);
    sa2_window_send_leave();
    /* After the call, so the count cannot be reached without the guest having
     * run: the rule DispatchMessageW keeps and the reason it keeps it. */
    sa2_window_note_wndproc_returned();
    return r;
}

/* USER32!SendMessageW(hWnd, Msg, wParam, lParam).
 *
 * Synchronous control flow: Windows runs the target window's procedure and
 * returns what the procedure returned. A recording no-op would drop a call
 * the guest believes has completed and hand back a fabricated 0. */
static uint32_t imp_SendMessageW(void *s, const uint32_t *a) {
    sa2_window_log("SendMessageW", caller_of_current(), a[0], a[1], a[2], a[3]);
    return user32_send(s, a[0], a[1], a[2], a[3]);
}

/* USER32!GetWindowLongW(hWnd, nIndex) / SetWindowLongW(hWnd, nIndex, dwNewLong).
 *
 * Real per-window state; sa2_window.c holds it. Two indices in this image are
 * load-bearing: 0x00401679 reads GWL_STYLE and branches on WS_POPUP, and
 * 0x00403e4a writes GWL_WNDPROC to subclass the control GetDlgItem(hDlg,
 * 0x3E8) returned. GWL_WNDPROC is implemented rather than refused -- it
 * writes the field DispatchMessageW and SendMessageW dispatch through -- so a
 * subclass takes effect on the next message. A positive index past the
 * window's own cbWndExtra is refused by name for the reason
 * `sa2_window_metric` refuses an index it has no value for. */
static uint32_t imp_GetWindowLongW(void *s, const uint32_t *a) {
    (void)s;
    uint32_t v = 0;
    const int rc = sa2_window_get_long(a[0], (int32_t)a[1], &v);
    sa2_window_log("GetWindowLongW", caller_of_current(), a[0], a[1], v, (uint32_t)rc);
    if (rc == SA2_WNDLONG_BADWINDOW) { g_last_error = 1400u; return 0; }
    if (rc == SA2_WNDLONG_BADINDEX) {
        const sa2_wnd_window *w = sa2_window_of(a[0]);
        sa2_trap_note("GetWindowLongW(%#010x, %d): this runtime holds GWL_WNDPROC, "
                      "HINSTANCE, HWNDPARENT, ID, STYLE, EXSTYLE and USERDATA, and %u "
                      "bytes of window extra. A 0 for an index it does not model reads "
                      "exactly like a real 0.",
                      a[0], (int)(int32_t)a[1], w ? w->extra_bytes : 0u);
        user32_refuse();
    }
    return v;
}

static uint32_t imp_SetWindowLongW(void *s, const uint32_t *a) {
    (void)s;
    uint32_t prev = 0;
    const int rc = sa2_window_set_long(a[0], (int32_t)a[1], a[2], &prev);
    sa2_window_log("SetWindowLongW", caller_of_current(), a[0], a[1], a[2], prev);
    if (rc == SA2_WNDLONG_BADWINDOW) { g_last_error = 1400u; return 0; }
    if (rc == SA2_WNDLONG_BADINDEX) {
        const sa2_wnd_window *w = sa2_window_of(a[0]);
        sa2_trap_note("SetWindowLongW(%#010x, %d, %#010x): this runtime holds the seven "
                      "well-known negative indices and %u bytes of window extra. "
                      "Answering and storing nothing is how a subclass silently never "
                      "takes effect.", a[0], (int)(int32_t)a[1], a[2],
                      w ? w->extra_bytes : 0u);
        user32_refuse();
    }
    return prev;
}

/* USER32!SetTimer(hWnd, nIDEvent, uElapse, lpTimerFunc) / KillTimer(hWnd, id).
 *
 * Driven from the virtual clock, so WM_TIMER is posted at the same virtual
 * instants on every replay. Accept-and-forget is the failure this avoids: a
 * guest waiting for the WM_TIMER it was promised waits for ever. Two shapes
 * are refused by name rather than accepted, because each would be exactly
 * that accept-and-forget wearing a success code. */
static uint32_t imp_SetTimer(void *s, const uint32_t *a) {
    (void)s;
    if (a[3]) {
        sa2_trap_note("SetTimer(%#010x, %u, %u, TIMERPROC %#010x): WM_TIMER is delivered "
                      "through the queue here and no TIMERPROC is ever called; the one "
                      "call site in this image, 0x00403f59, passes NULL. Accepting this "
                      "would be a callback that never fires.", a[0], a[1], a[2], a[3]);
        user32_refuse();
    }
    if (!a[1]) {
        sa2_trap_note("SetTimer(%#010x, 0, %u, NULL): a window timer's return value is its "
                      "own id, so id 0 cannot be told from failure; 0x00403f60 passes 1.",
                      a[0], a[2]);
        user32_refuse();
    }
    if (!sa2_window_of(a[0])) {
        sa2_trap_note("SetTimer(%#010x, %u, %u, NULL): WM_TIMER is posted to a window, and "
                      "that handle is not one this runtime owns, so the timer would fire "
                      "into nothing. That is the accept-and-never-deliver a guest waiting "
                      "on WM_TIMER hangs on.", a[0], a[1], a[2]);
        user32_refuse();
    }
    const uint32_t id = sa2_window_set_timer(a[0], a[1], a[2], a[3]);
    sa2_window_log("SetTimer", caller_of_current(), a[0], a[1], a[2], id);
    if (!id) { g_last_error = 8u; return 0; }   /* ERROR_NOT_ENOUGH_MEMORY: the table is full */
    return id;
}

static uint32_t imp_KillTimer(void *s, const uint32_t *a) {
    (void)s;
    const int ok = sa2_window_kill_timer(a[0], a[1]);
    sa2_window_log("KillTimer", caller_of_current(), a[0], a[1], (uint32_t)ok, 0);
    /* Windows returns FALSE for a timer that is not there, and the guest's
     * four cleanup sites call this unconditionally, so a miss is an answer
     * the guest is entitled to rather than a refusal. */
    if (!ok) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    return 1;
}

/* USER32!WaitMessage().
 *
 * Blocks until a message arrives. Returning at once would turn the guest's
 * modal loop at 0x00404d91 into a spin -- and WaitMessage does not poll, so
 * the pump budget that stops every other runaway loop in this file cannot see
 * it. `sa2_window_wait` hands off to the scheduler and to the timer
 * deadlines, and answers NEVER only when nothing left in the process could
 * ever post; see the terminating argument in sa2_window.c. */
static uint32_t imp_WaitMessage(void *s, const uint32_t *a) {
    (void)s; (void)a;
    const int rc = sa2_window_wait();
    sa2_window_log("WaitMessage", caller_of_current(), (uint32_t)rc, 0, 0, 0);
    if (rc == SA2_WNDWAIT_NEVER) {
        sa2_trap_note("WaitMessage with an empty queue: this runtime handed off to every "
                      "other runnable guest thread up to the SA2_WND_WAIT_YIELDS bound, "
                      "no timer is pending, and nothing produced a message -- so nothing "
                      "in this process can post one and the wait has no end.");
        user32_refuse();
    }
    return 1;
}

/* USER32!GetAsyncKeyState(vKey) / GetKeyState(nVirtKey).
 *
 * Input, which design 3.5 gives to S5 ("xinput/dinput stubs fed from the
 * host"). For S4 the answer is that no key is down, which is 0 -- and 0 is
 * what a real keyboard reports for a key nobody is pressing, so nothing is
 * fabricated and neither of these goes through `sa2_window_invent`. The
 * virtual-key code is recorded per key, because the set of keys this game
 * polls is the S5 hand-off and is not recoverable after the run. 0x00863447
 * and 0x0086349e already show VK_MENU (0x12) and VK_CONTROL (0x11), and
 * 0x00404d49 shows VK_MENU again in the dialog loop. */
static uint32_t imp_GetAsyncKeyState(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t v = sa2_window_key_state(a[0], 1);
    sa2_window_log("GetAsyncKeyState", caller_of_current(), a[0], v, 0, 0);
    return v;
}

static uint32_t imp_GetKeyState(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t v = sa2_window_key_state(a[0], 0);
    sa2_window_log("GetKeyState", caller_of_current(), a[0], v, 0, 0);
    return v;
}

/* ---- window state the guest reads back, answered from the record ---- */

/* USER32!EnableWindow(hWnd, bEnable). Returns non-zero when the window was
 * previously *disabled*, which is the opposite of what it reads like. */
static uint32_t imp_EnableWindow(void *s, const uint32_t *a) {
    (void)s;
    int known = 0;
    const uint32_t was_disabled = sa2_window_enable(a[0], a[1], &known);
    sa2_window_log("EnableWindow", caller_of_current(), a[0], a[1], was_disabled, 0);
    if (!known) { g_last_error = 1400u; return 0; }
    return was_disabled;
}

/* USER32!MoveWindow(hWnd, X, Y, nWidth, nHeight, bRepaint). Real, because
 * GetClientRect and GetWindowRect report these same fields: a MoveWindow that
 * recorded and did not move would make those two disagree with the guest's
 * own last instruction to the window system. Two log rows, because the event
 * record carries four arguments and this call has six. */
static uint32_t imp_MoveWindow(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t caller = caller_of_current();
    sa2_window_log("MoveWindow", caller, a[0], a[1], a[2], a[5]);
    sa2_window_log("MoveWindow.size", caller, a[0], a[3], a[4], 0);
    if (!sa2_window_move(a[0], (int32_t)a[1], (int32_t)a[2],
                         (int32_t)a[3], (int32_t)a[4])) {
        g_last_error = 1400u;
        return 0;
    }
    /* bRepaint is recorded and not acted on: nothing here paints, and
     * inventing a WM_PAINT is the traffic CreateWindowExW declines to invent. */
    return 1;
}

/* USER32!SetWindowPos(hWnd, hWndInsertAfter, X, Y, cx, cy, uFlags).
 * SWP_NOMOVE and SWP_NOSIZE are honoured because they are the two flags that
 * decide whether the position this runtime reports changes. Z-order is
 * recorded only: there is one window and nothing composites it. 0x00404cd7
 * passes flags 3, both of them, i.e. a pure Z-order call. */
static uint32_t imp_SetWindowPos(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t caller = caller_of_current();
    sa2_window_log("SetWindowPos", caller, a[0], a[1], a[6], 0);
    sa2_window_log("SetWindowPos.rect", caller, a[2], a[3], a[4], a[5]);
    if (!sa2_window_set_pos(a[0], (int32_t)a[2], (int32_t)a[3],
                            (int32_t)a[4], (int32_t)a[5], a[6])) {
        g_last_error = 1400u;
        return 0;
    }
    return 1;
}

/* USER32!SetWindowTextW(hWnd, lpString). Real: GetWindowTextW reads it back. */
static uint32_t imp_SetWindowTextW(void *s, const uint32_t *a) {
    (void)s;
    char text[SA2_WND_NAME_MAX];
    text[0] = 0;
    if (a[1] && gwstr(a[1], text, sizeof text) < 0) text[0] = 0;
    sa2_window_log("SetWindowTextW", caller_of_current(), a[0], a[1], 0, 0);
    if (!sa2_window_set_title(a[0], text)) { g_last_error = 1400u; return 0; }
    return 1;
}

/* USER32!GetWindowTextW(hWnd, lpString, nMaxCount). The buffer is written on
 * every path that returns, including the failing one: Windows writes a NUL
 * even when it copies nothing, and a caller reading an unwritten buffer reads
 * its own stack. */
static uint32_t imp_GetWindowTextW(void *s, const uint32_t *a) {
    (void)s;
    const char *t = sa2_window_title_of(a[0]);
    sa2_window_log("GetWindowTextW", caller_of_current(), a[0], a[1], a[2], 0);
    if (!a[1] || (int32_t)a[2] <= 0) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    const int n = gwput(a[1], t ? t : "", (int)a[2]);
    if (!t) { g_last_error = 1400u; return 0; }
    return (uint32_t)n;
}

/* USER32!GetClassNameW(hWnd, lpClassName, nMaxCount). */
static uint32_t imp_GetClassNameW(void *s, const uint32_t *a) {
    (void)s;
    const char *name = sa2_window_class_name_of(a[0]);
    sa2_window_log("GetClassNameW", caller_of_current(), a[0], a[1], a[2], 0);
    if (!a[1] || (int32_t)a[2] <= 0) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    const int n = gwput(a[1], name ? name : "", (int)a[2]);
    if (!name) { g_last_error = 1400u; return 0; }
    return (uint32_t)n;
}

/* USER32!GetParent(hWnd). */
static uint32_t imp_GetParent(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t p = sa2_window_parent_of(a[0]);
    sa2_window_log("GetParent", caller_of_current(), a[0], p, 0, 0);
    if (!sa2_window_of(a[0])) { g_last_error = 1400u; return 0; }
    return p;
}

/* USER32!GetWindow(hWnd, uCmd). Answered over this file's own window table.
 * A 0 from a command the runtime models is a real answer -- there is no such
 * relative -- and a command it does not model is refused rather than answered
 * with the same 0, because the guest cannot tell the two apart. */
static uint32_t imp_GetWindow(void *s, const uint32_t *a) {
    (void)s;
    int known = 0;
    const uint32_t h = sa2_window_related(a[0], a[1], &known);
    sa2_window_log("GetWindow", caller_of_current(), a[0], a[1], h, 0);
    if (!known) {
        sa2_trap_note("GetWindow(%#010x, uCmd %u): this runtime answers GW_HWNDFIRST, "
                      "GW_HWNDLAST, GW_HWNDNEXT, GW_HWNDPREV, GW_OWNER and GW_CHILD from "
                      "its own window table; there is no popup set to answer this one "
                      "from, and 0 would read like \"no such window\".", a[0], a[1]);
        user32_refuse();
    }
    if (!sa2_window_of(a[0])) { g_last_error = 1400u; return 0; }
    return h;
}

/* ---- painting: recorded, never drawn, but the handles and the ---- */
/* ---- out-parameters are real                                  ---- */

/* USER32!BeginPaint(hWnd, lpPaint). PAINTSTRUCT on 32-bit Windows:
 *   +00 hdc  +04 fErase  +08 rcPaint[16]  +18 fRestore  +1C fIncUpdate
 *   +20 rgbReserved[32]                                    -- 0x40 bytes
 * The whole structure is zeroed first, on the failing path too. Windows
 * leaves it undefined when BeginPaint fails; undefined here means the
 * caller's own stack bytes, and a guest reading one cannot tell that from a
 * value this runtime wrote. */
static uint32_t imp_BeginPaint(void *s, const uint32_t *a) {
    (void)s;
    int32_t l = 0, t = 0, r = 0, b = 0;
    const uint32_t hdc = sa2_window_begin_paint(a[0], &l, &t, &r, &b);
    sa2_window_log("BeginPaint", caller_of_current(), a[0], a[1], hdc, 0);
    if (!a[1]) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    for (uint32_t off = 0; off < 0x40u; off += 4u) gpoke32(a[1] + off, 0);
    if (!hdc) { g_last_error = 1400u; return 0; }
    gpoke32(a[1] + 0x00u, hdc);
    gpoke32(a[1] + 0x04u, 1u);          /* fErase: nothing erased the background */
    rect_out(a[1] + 0x08u, l, t, r, b); /* rcPaint: the whole client area */
    return hdc;
}

/* USER32!EndPaint(hWnd, lpPaint). Always non-zero on Windows. */
static uint32_t imp_EndPaint(void *s, const uint32_t *a) {
    (void)s;
    sa2_window_log("EndPaint", caller_of_current(), a[0], a[1], 0, 0);
    sa2_window_end_paint(a[0]);
    return 1;
}

/* USER32!DrawIcon(hDC, X, Y, hIcon). Nothing is drawn -- S5 owns drawing --
 * but the two handles are checked, because a TRUE for a handle of the wrong
 * kind would hide a guest that had mixed two up, and the token's high half
 * already says which kind it is. */
static uint32_t imp_DrawIcon(void *s, const uint32_t *a) {
    (void)s;
    sa2_window_note_noop("DrawIcon", caller_of_current(), a[0], a[1], a[2], a[3]);
    const char *dc = sa2_window_token_kind(a[0]);
    const char *ic = sa2_window_token_kind(a[3]);
    if (!dc || strcmp(dc, "HDC") || !ic || strcmp(ic, "HICON")) {
        g_last_error = SA2_ERROR_INVALID_HANDLE;
        return 0;
    }
    return 1;
}

/* USER32!DestroyIcon(hIcon). There is no icon to free and no token is ever
 * reused, so this records and succeeds -- for something that is an icon. */
static uint32_t imp_DestroyIcon(void *s, const uint32_t *a) {
    (void)s;
    const char *kind = sa2_window_token_kind(a[0]);
    sa2_window_note_noop("DestroyIcon", caller_of_current(), a[0], 0, 0, 0);
    if (!kind || strcmp(kind, "HICON")) { g_last_error = SA2_ERROR_INVALID_HANDLE; return 0; }
    return 1;
}

/* USER32!FillRect(hDC, lprc, hbr). The rectangle is read for the record, not
 * to fill anything: nothing in this runtime has a pixel. */
static uint32_t imp_FillRect(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t l = a[1] ? gpeek32(a[1] + 0x0u) : 0u;
    const uint32_t r = a[1] ? gpeek32(a[1] + 0x8u) : 0u;
    sa2_window_note_noop("FillRect", caller_of_current(), a[0], l, r, a[2]);
    if (!a[1]) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    return 1;
}

/* USER32!DrawTextW(hdc, lpchText, cchText, lprc, format).
 *
 * **Not a no-op**, because 0x00402dde passes format 0xC50 -- DT_CALCRECT is
 * set -- and 0x00402de4 reads `rect.bottom - rect.top` straight back out. The
 * rectangle is an out-parameter the guest consumes. There is no font, so the
 * extent is derived from the same base units GetDialogBaseUnits reports,
 * which at least keeps the two fabrications consistent with each other, and
 * SA2_WND_STRICT=1 refuses both. */
static uint32_t imp_DrawTextW(void *s, const uint32_t *a) {
    (void)s;
    char text[256];
    text[0] = 0;
    int n = a[1] ? gwstr(a[1], text, sizeof text) : 0;
    if (n < 0) n = 0;
    /* cchText is a character count, or -1 for "NUL terminated". */
    uint32_t chars = (a[2] == 0xffffffffu) ? (uint32_t)n : a[2];
    if (chars > (uint32_t)n) chars = (uint32_t)n;
    uint32_t lines = 1u;
    if (!(a[4] & SA2_DT_SINGLELINE))
        for (uint32_t i = 0; i < chars; i++) if (text[i] == '\n') lines++;
    int32_t cx = 0, cy = 0;
    if (!sa2_window_text_extent(chars, lines, &cx, &cy)) {
        sa2_trap_note("DrawTextW(format %#x): SA2_WND_STRICT=1 refuses the fabricated text "
                      "extent. There is no font here; the extent is derived from the same "
                      "base units GetDialogBaseUnits reports, and 0x00402de4 reads "
                      "rect.bottom - rect.top back out of the rectangle.", a[4]);
        user32_refuse();
    }
    sa2_window_log("DrawTextW", caller_of_current(), a[0], chars, a[3], a[4]);
    if (a[4] & SA2_DT_CALCRECT) {
        if (!a[3]) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
        const int32_t l = (int32_t)gpeek32(a[3] + 0x0u);
        const int32_t t = (int32_t)gpeek32(a[3] + 0x4u);
        rect_out(a[3], l, t, l + cx, t + cy);
    }
    /* Nothing is drawn. Windows returns the height of the drawn text. */
    return (uint32_t)cy;
}

/* USER32!RedrawWindow(hWnd, lprcUpdate, hrgnUpdate, flags).
 *
 * RDW_UPDATENOW and RDW_ERASENOW make RedrawWindow *synchronous*: the window
 * procedure runs before it returns. 0x004045c0 passes 0x101, which is
 * RDW_INVALIDATE|RDW_UPDATENOW, so this is not hypothetical -- a no-op
 * success there would drop a paint the guest believes has happened. Nothing
 * else is modelled: there is no update region to accumulate, so a plain
 * RDW_INVALIDATE records and returns, in the same spirit as CreateWindowExW
 * declining to fabricate a WM_CREATE. */
static uint32_t imp_RedrawWindow(void *s, const uint32_t *a) {
    sa2_window_log("RedrawWindow", caller_of_current(), a[0], a[1], a[2], a[3]);
    if (!sa2_window_of(a[0])) { g_last_error = 1400u; return 0; }
    if (a[3] & (SA2_RDW_UPDATENOW | SA2_RDW_ERASENOW))
        user32_send(s, a[0], SA2_WM_PAINT, 0, 0);
    return 1;
}

/* ---- rectangles, menus, metrics, and the fabricated values ---- */

/* USER32!SetRect(lprc, xLeft, yTop, xRight, yBottom). Pure arithmetic on the
 * caller's own rectangle: no window system is involved, so this is the real
 * function and not a stub of one. */
static uint32_t imp_SetRect(void *s, const uint32_t *a) {
    (void)s;
    sa2_window_log("SetRect", caller_of_current(), a[1], a[2], a[3], a[4]);
    if (!a[0]) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    return rect_out(a[0], (int32_t)a[1], (int32_t)a[2], (int32_t)a[3], (int32_t)a[4]);
}

/* USER32!GetSystemMenu(hWnd, bRevert). NULL for bRevert, which is what
 * Windows returns. The menu handed back otherwise has no items, which is what
 * makes EnableMenuItem's -1 the true answer below. */
static uint32_t imp_GetSystemMenu(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t m = sa2_window_system_menu(a[0], a[1]);
    sa2_window_log("GetSystemMenu", caller_of_current(), a[0], a[1], m, 0);
    return m;
}

/* USER32!EnableMenuItem(hMenu, uIDEnableItem, uEnable). Windows returns the
 * item's previous state, or -1 when there is no such item. The menu
 * GetSystemMenu hands out has no items, so -1 is the true statement about the
 * object this runtime made -- and it invents nothing, which a fabricated
 * previous state would. */
static uint32_t imp_EnableMenuItem(void *s, const uint32_t *a) {
    (void)s;
    sa2_window_note_noop("EnableMenuItem", caller_of_current(), a[0], a[1], a[2], 0);
    return 0xffffffffu;
}

/* USER32!IsDialogMessageW(hDlg, lpMsg). FALSE: there is no dialog manager
 * here, so the message was not processed and the caller must go on to
 * TranslateMessage and DispatchMessageW -- which is the path the pump already
 * implements and the path 0x00404d66 takes on a FALSE. TRUE would swallow
 * every message the guest's own loop handed it. Saying "not mine" invents
 * nothing; what it costs is dialog keyboard navigation, and there is no
 * keyboard. */
static uint32_t imp_IsDialogMessageW(void *s, const uint32_t *a) {
    (void)s;
    sa2_wnd_msg m;
    msg_get(a[1], &m);
    sa2_window_note_noop("IsDialogMessageW", caller_of_current(), a[0], m.msg, m.wparam,
                         m.lparam);
    return 0;
}

/* USER32!GetSysColor(nIndex). Fabricated: there is no theme. The classic
 * 96-dpi palette is a documented scheme rather than a guess, but it is still
 * this runtime's invention here, so it is counted as one and
 * SA2_WND_STRICT=1 refuses it. 0x004029b3 reads index 5 (COLOR_WINDOW) and
 * hands it to GDI32!CreateSolidBrush. */
static uint32_t imp_GetSysColor(void *s, const uint32_t *a) {
    (void)s;
    uint32_t v = 0;
    if (!sa2_window_sys_color(a[0], &v)) {
        if (sa2_window_strict())
            sa2_trap_note("GetSysColor(%u): SA2_WND_STRICT=1 refuses the fabricated system "
                          "colour, so a run can show whether the guest depends on it.", a[0]);
        else
            sa2_trap_note("GetSysColor(%u): outside the classic 0..30 palette this runtime "
                          "holds, and there is no theme to read a real one from.", a[0]);
        user32_refuse();
    }
    sa2_window_log("GetSysColor", caller_of_current(), a[0], v, 0, 0);
    return v;
}

/* USER32!GetDialogBaseUnits(). LOWORD is the average character width and
 * HIWORD the height of the system font: 8 x 16, MS Sans Serif 8 pt at 96 dpi.
 * Fabricated -- there is no font -- and the dialog layout at 0x00402870
 * multiplies these, so SA2_WND_STRICT=1 says at once whether the layout is
 * load-bearing or merely computed. */
static uint32_t imp_GetDialogBaseUnits(void *s, const uint32_t *a) {
    (void)s; (void)a;
    uint32_t v = 0;
    if (!sa2_window_dialog_base_units(&v)) {
        sa2_trap_note("GetDialogBaseUnits: SA2_WND_STRICT=1 refuses the fabricated system "
                      "font metrics. There is no font here.");
        user32_refuse();
    }
    sa2_window_log("GetDialogBaseUnits", caller_of_current(), v, 0, 0, 0);
    return v;
}

/* USER32!GetCursorPos(lpPoint). Fabricated: there is no mouse. The default is
 * the centre of the main window's client area in screen coordinates, because
 * 0x00863038 subtracts the window rectangle from it to get a client position
 * and a screen origin would put the cursor outside the window. SA2_WND_CURSOR
 * moves it and SA2_WND_STRICT=1 refuses it. */
static uint32_t imp_GetCursorPos(void *s, const uint32_t *a) {
    (void)s;
    int32_t x = 0, y = 0;
    if (!sa2_window_cursor_pos(&x, &y)) {
        sa2_trap_note("GetCursorPos: SA2_WND_STRICT=1 refuses the fabricated cursor "
                      "position. There is no mouse; SA2_WND_CURSOR moves the placeholder "
                      "and 0x00863038 converts it to client coordinates.");
        user32_refuse();
    }
    sa2_window_log("GetCursorPos", caller_of_current(), a[0], (uint32_t)x, (uint32_t)y, 0);
    if (!a[0]) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    gpoke32(a[0] + 0x0u, (uint32_t)x);
    gpoke32(a[0] + 0x4u, (uint32_t)y);
    return 1;
}

/* ------------------------------------------------------------- GDI32
 *
 * Design 3.5 puts `gdi32` in group (b), presentation, and design 5's S4 work
 * list asks for "presentation stubs as no-ops that record calls". These nine
 * are that sentence. `runtime/sa2_gdi.c` owns the model -- the HDC and HFONT
 * tokens, the object and device-context tables, what is selected into what,
 * the colours the guest set, the call log and the one invented answer -- and
 * everything here is the Win32 shape over it: read the guest's structure,
 * hand plain host values to the model, write back what Windows would write.
 *
 * All nine call sites sit under one guest path and it is a dialog, not the
 * renderer: the window procedure at 0x00401810 reaches 0x004026e0, which
 * measures a dialog in 0x00402870 and runs its DialogFunc at 0x00403bd0. See
 * runtime/sa2_gdi.h for the full walk and for why the HDC region is
 * 0x7e500000 rather than the next slot after HBRUSH.
 *
 * **Only GetTextExtentPoint32W invents anything**, and it is the only one of
 * the nine with an out-parameter. SA2_GDI_STRICT=1 turns that answer back
 * into a refusal, and a refusal traps here rather than being flattened into
 * FALSE -- a guest that carried on after one would be carrying on without the
 * number it asked for, which is the failure this project keeps finding.
 *
 * Note that adding these rows takes GDI32 out of the presentation survey's
 * reach: `presentation_survey_takes` is only consulted when `!it->impl`, so a
 * surveyed run's `presentation_surveyed` count no longer includes them. */

/* A guest UTF-16 string, narrowed, empty for a null pointer, "?" when it
 * leaves the guest space. `gwstr` already narrows; this is only the null and
 * failure handling, which all four GDI string arguments need. */
static void gdi_wtext(uint32_t va, char *out, size_t cap) {
    if (!cap) return;
    if (!va) { out[0] = 0; return; }
    if (gwstr(va, out, cap) < 0) snprintf(out, cap, "?");
}

/* The model refused. It has written the reason; this turns it into the same
 * trap shape every other refusing stub in this file uses. */
static void gdi_refuse(void) {
    sa2_trap_note("%s", sa2_gdi_refusal());
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

/* GDI32!CreateDCW(pwszDriver, pwszDevice, pszPort, pdm) -- 4 dwords, as
 * 0x00402cbb and 0x00403c4f push them. */
static uint32_t imp_CreateDCW(void *s, const uint32_t *a) {
    (void)s;
    char driver[SA2_GDI_NAME_MAX], device[SA2_GDI_NAME_MAX], port[SA2_GDI_NAME_MAX];
    gdi_wtext(a[0], driver, sizeof driver);
    gdi_wtext(a[1], device, sizeof device);
    gdi_wtext(a[2], port, sizeof port);
    const uint32_t args[4] = { a[0], a[1], a[2], a[3] };
    const uint32_t h = sa2_gdi_create_dc(caller_of_current(), args, driver, device, port);
    if (!h) gdi_refuse();
    return h;
}

/* GDI32!CreateFontIndirectW(lplf) -- 1 dword, as 0x00402cf1 pushes it.
 *
 * LOGFONTW: +00 lfHeight, +04 lfWidth, +08 lfEscapement, +0C lfOrientation,
 * +10 lfWeight, +14..+1B the eight bytes, +1C lfFaceName[32] (64 bytes, 92
 * total). The face name is a fixed array that need not be terminated when it
 * is exactly 32 characters long, so it is read into a 33-byte buffer and
 * never further: a `gwstr` with a larger cap would read the bytes after the
 * structure whenever the guest fills the array. */
static uint32_t imp_CreateFontIndirectW(void *s, const uint32_t *a) {
    (void)s;
    char face[33];
    int32_t height = 0, weight = 0;
    face[0] = 0;
    if (a[0]) {
        height = (int32_t)gpeek32(a[0] + 0x00u);
        weight = (int32_t)gpeek32(a[0] + 0x10u);
        gdi_wtext(a[0] + 0x1cu, face, sizeof face);
    }
    const uint32_t h = sa2_gdi_create_font(caller_of_current(), a[0], height, weight, face);
    if (!h) gdi_refuse();
    return h;
}

/* GDI32!CreateSolidBrush(COLORREF) -- 1 dword, as 0x004029ca and 0x00403fa5
 * push it (a GetSysColor result in both cases). */
static uint32_t imp_CreateSolidBrush(void *s, const uint32_t *a) {
    (void)s;
    const uint32_t h = sa2_gdi_create_solid_brush(caller_of_current(), a[0]);
    if (!h) gdi_refuse();
    return h;
}

/* GDI32!DeleteDC(hdc) -- 1 dword, as 0x0040359c and 0x00403c66 push it. */
static uint32_t imp_DeleteDC(void *s, const uint32_t *a) {
    (void)s;
    return (uint32_t)sa2_gdi_delete_dc(caller_of_current(), a[0]);
}

/* GDI32!DeleteObject(ho) -- 1 dword, as 0x00403fbf pushes it and as the
 * `call edi` reached from 0x00403605 does. */
static uint32_t imp_DeleteObject(void *s, const uint32_t *a) {
    (void)s;
    return (uint32_t)sa2_gdi_delete_object(caller_of_current(), a[0]);
}

/* GDI32!SelectObject(hdc, h) -- 2 dwords, as 0x00402cff, 0x00403595 and
 * 0x00403c5f push them. The return value is load-bearing: the guest stores it
 * at 0x00402d05 and hands it back at 0x00403595 to restore the DC. */
static uint32_t imp_SelectObject(void *s, const uint32_t *a) {
    (void)s;
    return sa2_gdi_select_object(caller_of_current(), a[0], a[1]);
}

/* GDI32!SetBkColor(hdc, color) and SetTextColor(hdc, color) -- 2 dwords each,
 * as 0x00404402 and 0x00404429 push them. Both answer the previous colour. */
static uint32_t imp_SetBkColor(void *s, const uint32_t *a) {
    (void)s;
    return sa2_gdi_set_bk_color(caller_of_current(), a[0], a[1]);
}

static uint32_t imp_SetTextColor(void *s, const uint32_t *a) {
    (void)s;
    return sa2_gdi_set_text_color(caller_of_current(), a[0], a[1]);
}

/* GDI32!GetTextExtentPoint32W(hdc, lpString, c, psizl) -- 4 dwords, as
 * 0x00402d3d, 0x00402dfb, 0x0040328d and 0x0040344b push them.
 *
 * The one stub here that writes through an out-pointer, and the one whose
 * answer this runtime made up. SIZE is { LONG cx; LONG cy; }. The guest reads
 * cx straight back at 0x00402d43, adds 20 and keeps the larger of that and a
 * running maximum, so the invented width reaches the dialog geometry the
 * guest computes -- which is exactly why SA2_GDI_STRICT exists. */
static uint32_t imp_GetTextExtentPoint32W(void *s, const uint32_t *a) {
    (void)s;
    char text[SA2_GDI_NAME_MAX];
    gdi_wtext(a[1], text, sizeof text);
    int32_t cx = 0, cy = 0;
    const int r = sa2_gdi_text_extent(caller_of_current(), a[0], a[1], (int32_t)a[2],
                                      a[3], text, &cx, &cy);
    if (r < 0) gdi_refuse();          /* strict, or a question with no answer */
    if (r == 0) {                     /* the documented FALSE; nothing is written */
        g_last_error = SA2_ERROR_INVALID_HANDLE;
        return 0;
    }
    gpoke32(a[3] + 0x0u, (uint32_t)cx);
    gpoke32(a[3] + 0x4u, (uint32_t)cy);
    note_out((uint32_t)cx, (uint32_t)cy, 2);
    return 1;
}

/* ------------------------------------------------- dialogs and the clipboard
 *
 * Design 3.5 puts USER32 in the presentation group and S4's work list asks for
 * "presentation stubs as no-ops that record calls". `runtime/sa2_dialog.c`
 * owns the model -- what each of these ten answers, why, and whether the
 * answer is a measured fact or a fabricated success -- and everything here is
 * the Win32 shape over it, exactly as the window stubs above are the shape over
 * `runtime/sa2_window.c`.
 *
 * The cluster is the quit-confirmation dialog raised from WndProc's WM_CLOSE
 * arm (0x0040184a -> 0x004015f0 -> 0x004026e0 -> 0x00404a00). Its head is
 * CreateDialogIndirectParamW at 0x00404cba, which answers NULL: the guest has
 * the branch at 0x00404cc2 and takes its own clean exit, and every clipboard
 * call and thirteen of the fourteen GetDlgItem calls are inside the branch that
 * only runs when it succeeds. See sa2_dialog.h for the whole argument.
 *
 * `SA2_DIALOG_STRICT=1` turns every fabricated success into a refusal, which is
 * what this helper raises. */
static void dialog_refuse(const char *name) {
    sa2_trap_note("%s: %s", name, sa2_dialog_refusal());
    sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

/* USER32!OpenClipboard(hWndNewOwner). */
static uint32_t imp_OpenClipboard(void *s, const uint32_t *a) {
    (void)s;
    uint32_t r = 0;
    if (!sa2_dialog_open_clipboard(caller_of_current(), a[0], &r)) dialog_refuse("OpenClipboard");
    return r;
}

/* USER32!EmptyClipboard(void). */
static uint32_t imp_EmptyClipboard(void *s, const uint32_t *a) {
    (void)s; (void)a;
    uint32_t r = 0;
    if (!sa2_dialog_empty_clipboard(caller_of_current(), &r)) dialog_refuse("EmptyClipboard");
    return r;
}

/* USER32!SetClipboardData(uFormat, hMem). The handle is the guest's own
 * GlobalAlloc block from 0x004046b0; Windows takes ownership of it and so does
 * this runtime, in the sense that nothing ever frees it. That is safe only
 * because GetClipboardData is not one of the image's 251 imports, so nothing
 * can read the data back -- see sa2_dialog.h. */
static uint32_t imp_SetClipboardData(void *s, const uint32_t *a) {
    (void)s;
    uint32_t r = 0;
    if (!sa2_dialog_set_clipboard_data(caller_of_current(), a[0], a[1], &r))
        dialog_refuse("SetClipboardData");
    return r;
}

/* USER32!CloseClipboard(void). */
static uint32_t imp_CloseClipboard(void *s, const uint32_t *a) {
    (void)s; (void)a;
    uint32_t r = 0;
    if (!sa2_dialog_close_clipboard(caller_of_current(), &r)) dialog_refuse("CloseClipboard");
    return r;
}

/* USER32!CreateDialogIndirectParamW(hInstance, lpTemplate, hWndParent,
 * lpDialogFunc, dwInitParam). Five arguments, confirmed against the five
 * pushes at 0x00404ca3..0x00404cb9. Answers NULL and never runs the dialog
 * procedure at 0x00403bd0. */
static uint32_t imp_CreateDialogIndirectParamW(void *s, const uint32_t *a) {
    (void)s;
    uint32_t r = 0;
    if (!sa2_dialog_create_dialog_indirect_param(caller_of_current(), a[0], a[1], a[2],
                                                 a[3], a[4], &r))
        dialog_refuse("CreateDialogIndirectParamW");
    return r;
}

/* USER32!GetDlgItem(hDlg, nIDDlgItem). No dialog exists, so NULL is a fact;
 * ERROR_CONTROL_ID_NOT_FOUND is the code Windows sets for it. */
static uint32_t imp_GetDlgItem(void *s, const uint32_t *a) {
    (void)s;
    uint32_t r = 0;
    if (!sa2_dialog_get_dlg_item(caller_of_current(), a[0], a[1], &r))
        dialog_refuse("GetDlgItem");
    if (!r) g_last_error = SA2_DLG_ERROR_CONTROL_ID_NOT_FOUND;
    return r;
}

/* USER32!CheckDlgButton(hDlg, nIDButton, uCheck). */
static uint32_t imp_CheckDlgButton(void *s, const uint32_t *a) {
    (void)s;
    uint32_t r = 0;
    if (!sa2_dialog_check_dlg_button(caller_of_current(), a[0], a[1], a[2], &r))
        dialog_refuse("CheckDlgButton");
    if (!r) g_last_error = SA2_DLG_ERROR_CONTROL_ID_NOT_FOUND;
    return r;
}

/* USER32!MessageBeep(uType). */
static uint32_t imp_MessageBeep(void *s, const uint32_t *a) {
    (void)s;
    uint32_t r = 0;
    if (!sa2_dialog_message_beep(caller_of_current(), a[0], &r)) dialog_refuse("MessageBeep");
    return r;
}

/* USER32!WinHelpW(hWndMain, lpszHelp, uCommand, dwData). */
static uint32_t imp_WinHelpW(void *s, const uint32_t *a) {
    (void)s;
    uint32_t r = 0;
    if (!sa2_dialog_win_help(caller_of_current(), a[0], a[1], a[2], a[3], &r))
        dialog_refuse("WinHelpW");
    return r;
}

/* USER32!LoadStringW(hInstance, uID, lpBuffer, cchBufferMax).
 *
 * The one stub in this group whose true answer is available rather than
 * invented: the PE is mapped, so `sa2_dialog_load_string` walks the image's own
 * resource directory for the RT_STRING bundle. This image has none, so the
 * answer is 0 for every id -- measured, not assumed -- and the guest's own
 * fallback at 0x00402bf8 and 0x00402c57 runs.
 *
 * **The terminator is written on every path, including the miss.** Windows
 * terminates the buffer even when the string is absent, and the guest reads
 * `[edi]` at 0x00402bff immediately afterwards: leaving it unwritten would
 * hand the guest whatever was on its own stack. The characters go out two
 * bytes at a time so nothing here depends on the host's byte order. */
static uint32_t imp_LoadStringW(void *s, const uint32_t *a) {
    (void)s;
    static uint16_t staging[SA2_DLG_STRING_MAX];
    static const char terminator[2] = {0, 0};
    uint32_t n = 0;
    if (!sa2_dialog_load_string(caller_of_current(), a[0], a[1], a[3],
                                staging, SA2_DLG_STRING_MAX, &n))
        dialog_refuse("LoadStringW");
    for (uint32_t i = 0; i < n; i++) {
        const char le[2] = { (char)(staging[i] & 0xffu), (char)(staging[i] >> 8) };
        gput(a[2] + i * 2u, le, 2u);
    }
    gput(a[2] + n * 2u, terminator, 2u);
    return n;
}

/* ------------------------------------------------------------ files
 *
 * Slice 3 stopped at `SHLWAPI!PathFileExistsA` from 0x004064fb: the game
 * asking whether `C:\SA2\Config\` is there. Everything below answers that
 * question and the ones after it out of the user's own installation, through
 * `runtime/sa2_files.c`, which owns the path mapping, the escape refusal and
 * the read/write split. No stub here builds a host path; each one hands a
 * guest path to that module and gets a result back.
 *
 * Three rules hold across all of them:
 *
 *  1. **A refusal traps; a miss returns.** SA2_VFS_NOT_FOUND is a Win32
 *     answer the guest is entitled to (ERROR_FILE_NOT_FOUND, and the game
 *     routes around it by creating the directory). SA2_VFS_ESCAPE and friends
 *     are not: serving a byte from outside the installation, or quietly
 *     answering "not there" when the truth is "this runtime refused", are the
 *     two ways this slice could go wrong without anyone noticing.
 *  2. **Guest buffers are written through `sa2_gp`,** which returns NULL for
 *     an address outside the guest space, so a ReadFile into a bad pointer
 *     faults with the address rather than scribbling on the host.
 *  3. **File times come from the virtual epoch,** never the host clock, so two
 *     runs of the same binary over the same installation agree.
 */
#define SA2_ERROR_FILE_NOT_FOUND     2u
#define SA2_ERROR_PATH_NOT_FOUND     3u
#define SA2_ERROR_ACCESS_DENIED      5u
#define SA2_ERROR_NO_MORE_FILES     18u
#define SA2_ERROR_HANDLE_EOF        38u
#define SA2_ERROR_ALREADY_EXISTS   183u
#define SA2_INVALID_HANDLE_VALUE  0xFFFFFFFFu

#define GENERIC_READ   0x80000000u
#define GENERIC_WRITE  0x40000000u

/* A path a stub refuses is a trap naming the path and the reason, because the
 * alternative -- a Win32 error code -- is indistinguishable to the guest from
 * a file that simply is not installed. */
static void vfs_refuse(int status, const char *api, const char *path) {
    sa2_trap_note("%s(\"%s\"): the guest file system refuses this path (%s). "
                  "%s maps to the installation root and nothing outside it is served.",
                  api, path, sa2_vfs_status_name(status), sa2_vfs_guest_root());
    sa2_trap_raise(SA2_TRAP_FAULT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
}

/* OK and NOT_FOUND come back; everything else leaves through the trap. */
static int vfs_check(int status, const char *api, const char *path) {
    if (status == SA2_VFS_OK || status == SA2_VFS_NOT_FOUND) return status;
    vfs_refuse(status, api, path);
    return status;
}

/* The guest's path argument, or a Win32 parameter error. */
static int vfs_arg(uint32_t addr, char *out, size_t cap) {
    if (!addr || gstr(addr, out, cap) < 0) {
        g_last_error = SA2_ERROR_INVALID_PARAMETER;
        out[0] = 0;
        return 0;
    }
    return 1;
}

static int vfs_argw(uint32_t addr, char *out, size_t cap) {
    if (!addr || gwstr(addr, out, cap) < 0) {
        g_last_error = SA2_ERROR_INVALID_PARAMETER;
        out[0] = 0;
        return 0;
    }
    return 1;
}

/* SHLWAPI!PathFileExistsA(LPCSTR pszPath) -- the call this slice starts at. */
static uint32_t imp_PathFileExistsA(void *s, const uint32_t *a) {
    (void)s;
    char path[SA2_VFS_PATH_MAX];
    if (!vfs_arg(a[0], path, sizeof path)) return 0;
    int is_dir = 0, layer = 0;
    int rc = vfs_check(sa2_vfs_exists(path, &is_dir, &layer), "PathFileExistsA", path);
    if (rc == SA2_VFS_OK) { g_last_error = SA2_ERROR_SUCCESS; return 1; }
    g_last_error = SA2_ERROR_FILE_NOT_FOUND;
    return 0;
}

/* KERNEL32!GetFileAttributesA(LPCSTR). */
static uint32_t imp_GetFileAttributesA(void *s, const uint32_t *a) {
    (void)s;
    char path[SA2_VFS_PATH_MAX];
    if (!vfs_arg(a[0], path, sizeof path)) return SA2_INVALID_HANDLE_VALUE;
    int status = 0;
    uint32_t attr = sa2_vfs_attributes(path, &status);
    vfs_check(status, "GetFileAttributesA", path);
    if (status != SA2_VFS_OK) { g_last_error = SA2_ERROR_FILE_NOT_FOUND; return 0xFFFFFFFFu; }
    g_last_error = SA2_ERROR_SUCCESS;
    return attr;
}

/* KERNEL32!CreateDirectoryA(LPCSTR, LPSECURITY_ATTRIBUTES). The game calls
 * this at 0x00406502 when its Config directory is missing. It lands in the
 * overlay: see the module comment in sa2_files.h for why the installation is
 * never written. */
static uint32_t imp_CreateDirectoryA(void *s, const uint32_t *a) {
    (void)s;
    char path[SA2_VFS_PATH_MAX];
    if (!vfs_arg(a[0], path, sizeof path)) return 0;
    int already = 0;
    int rc = sa2_vfs_create_directory(path, &already);
    if (rc != SA2_VFS_OK) { vfs_refuse(rc, "CreateDirectoryA", path); return 0; }
    if (already) { g_last_error = SA2_ERROR_ALREADY_EXISTS; return 0; }
    g_last_error = SA2_ERROR_SUCCESS;
    return 1;
}

static uint32_t vfs_create_file(const char *path, const uint32_t *a, const char *api) {
    uint32_t access = 0;
    if (a[1] & GENERIC_READ) access |= SA2_VFS_ACCESS_READ;
    if (a[1] & GENERIC_WRITE) access |= SA2_VFS_ACCESS_WRITE;
    if (!access) access = SA2_VFS_ACCESS_READ;      /* a query-only open */
    int status = 0, existed = 0;
    uint32_t h = sa2_vfs_open(path, access, a[4], &status, &existed);
    if (status != SA2_VFS_OK && status != SA2_VFS_NOT_FOUND && status != SA2_VFS_HOST_ERROR)
        vfs_refuse(status, api, path);
    if (!h) {
        g_last_error = status == SA2_VFS_NOT_FOUND ? SA2_ERROR_FILE_NOT_FOUND
                                                   : SA2_ERROR_ACCESS_DENIED;
        return SA2_INVALID_HANDLE_VALUE;
    }
    /* Win32 sets ERROR_ALREADY_EXISTS on a successful CREATE_ALWAYS/OPEN_ALWAYS
     * over an existing file, and the CRT reads it. */
    g_last_error = (existed && (a[4] == SA2_VFS_CREATE_ALWAYS || a[4] == SA2_VFS_OPEN_ALWAYS))
                   ? SA2_ERROR_ALREADY_EXISTS : SA2_ERROR_SUCCESS;
    return h;
}

static uint32_t imp_CreateFileA(void *s, const uint32_t *a) {
    (void)s;
    char path[SA2_VFS_PATH_MAX];
    if (!vfs_arg(a[0], path, sizeof path)) return SA2_INVALID_HANDLE_VALUE;
    return vfs_create_file(path, a, "CreateFileA");
}

static uint32_t imp_CreateFileW(void *s, const uint32_t *a) {
    (void)s;
    char path[SA2_VFS_PATH_MAX];
    if (!vfs_argw(a[0], path, sizeof path)) return SA2_INVALID_HANDLE_VALUE;
    return vfs_create_file(path, a, "CreateFileW");
}

/* KERNEL32!ReadFile(HANDLE, LPVOID, DWORD, LPDWORD, LPOVERLAPPED). */
static uint32_t imp_ReadFile(void *s, const uint32_t *a) {
    if (std_index(a[0]) >= 0) {                   /* a standard pipe with no writer */
        if (a[3]) gpoke32(a[3], 0);
        g_last_error = 109u;                      /* ERROR_BROKEN_PIPE */
        return 0;
    }
    (void)s;
    if (!sa2_vfs_is_handle(a[0])) {
        sa2_trap_note("ReadFile(%#010x): not a file handle this runtime opened", a[0]);
        sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
    }
    if (a[4]) {
        sa2_trap_note("ReadFile: overlapped I/O is not implemented and this runtime will not "
                      "pretend a synchronous read is one");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, g_current ? g_current->thunk : SA2_GUEST_IMPORT_BASE);
    }
    uint32_t want = a[2], got = 0;
    if (want) {
        uint8_t *p = sa2_gp(a[1], want);
        if (!p) {
            sa2_trap_note("ReadFile: the buffer %#010x..%#010x is outside the guest space",
                          a[1], a[1] + want);
            sa2_trap_raise(SA2_TRAP_FAULT, a[1]);
        }
        if (sa2_vfs_read(a[0], p, want, &got) != SA2_VFS_OK) {
            g_last_error = SA2_ERROR_ACCESS_DENIED;
            return 0;
        }
    }
    if (a[3]) gpoke32(a[3], got);
    note_out(got, 0, 1);
    g_last_error = SA2_ERROR_SUCCESS;
    return 1;
}

/* KERNEL32!WriteFile(HANDLE, LPCVOID, DWORD, LPDWORD, LPOVERLAPPED). */
static uint32_t imp_WriteFile(void *s, const uint32_t *a) {
    (void)s;
    if (std_index(a[0]) >= 0) {                   /* a standard pipe: accepted, discarded */
        if (a[3]) gpoke32(a[3], a[2]);
        return 1;
    }
    if (!sa2_vfs_is_handle(a[0])) {
        sa2_trap_note("WriteFile(%#010x): not a file handle this runtime opened", a[0]);
        sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
    }
    uint32_t want = a[2], put = 0;
    if (want) {
        uint8_t *p = sa2_gp(a[1], want);
        if (!p) {
            sa2_trap_note("WriteFile: the buffer %#010x..%#010x is outside the guest space",
                          a[1], a[1] + want);
            sa2_trap_raise(SA2_TRAP_FAULT, a[1]);
        }
        if (sa2_vfs_write(a[0], p, want, &put) != SA2_VFS_OK) {
            g_last_error = SA2_ERROR_ACCESS_DENIED;
            return 0;
        }
    }
    if (a[3]) gpoke32(a[3], put);
    note_out(put, 0, 1);
    g_last_error = SA2_ERROR_SUCCESS;
    return 1;
}

/* KERNEL32!SetFilePointer(HANDLE, LONG, PLONG, DWORD). */
static uint32_t imp_SetFilePointer(void *s, const uint32_t *a) {
    (void)s;
    if (!sa2_vfs_is_handle(a[0])) {
        sa2_trap_note("SetFilePointer(%#010x): not a file handle this runtime opened", a[0]);
        sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
    }
    int64_t dist = (int32_t)a[1];
    if (a[2]) dist = ((int64_t)(int32_t)gpeek32(a[2]) << 32) | (uint32_t)a[1];
    uint64_t pos = 0;
    if (sa2_vfs_seek(a[0], dist, a[3], &pos) != SA2_VFS_OK) {
        g_last_error = SA2_ERROR_ACCESS_DENIED;
        return SA2_INVALID_HANDLE_VALUE;    /* INVALID_SET_FILE_POINTER */
    }
    if (a[2]) gpoke32(a[2], (uint32_t)(pos >> 32));
    note_out((uint32_t)pos, (uint32_t)(pos >> 32), 2);
    g_last_error = SA2_ERROR_SUCCESS;
    return (uint32_t)pos;
}

/* KERNEL32!GetFileSize(HANDLE, LPDWORD). */
static uint32_t imp_GetFileSize(void *s, const uint32_t *a) {
    (void)s;
    if (!sa2_vfs_is_handle(a[0])) {
        sa2_trap_note("GetFileSize(%#010x): not a file handle this runtime opened", a[0]);
        sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
    }
    uint64_t size = 0;
    if (sa2_vfs_size(a[0], &size) != SA2_VFS_OK) {
        g_last_error = SA2_ERROR_ACCESS_DENIED;
        return SA2_INVALID_HANDLE_VALUE;
    }
    if (a[1]) gpoke32(a[1], (uint32_t)(size >> 32));
    note_out((uint32_t)size, (uint32_t)(size >> 32), 2);
    g_last_error = SA2_ERROR_SUCCESS;
    return (uint32_t)size;
}

static uint32_t imp_FlushFileBuffers(void *s, const uint32_t *a) {
    if (std_index(a[0]) >= 0) return 1;           /* a standard pipe: nothing held */
    (void)s;
    if (!sa2_vfs_is_handle(a[0])) return 1;      /* a console handle: nothing buffered */
    return sa2_vfs_flush(a[0]) == SA2_VFS_OK;
}

static uint32_t imp_SetEndOfFile(void *s, const uint32_t *a) {
    (void)s;
    if (!sa2_vfs_is_handle(a[0])) {
        sa2_trap_note("SetEndOfFile(%#010x): not a file handle this runtime opened", a[0]);
        sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
    }
    return sa2_vfs_set_end_of_file(a[0]) == SA2_VFS_OK;
}

static uint32_t imp_DeleteFileA(void *s, const uint32_t *a) {
    (void)s;
    char path[SA2_VFS_PATH_MAX];
    if (!vfs_arg(a[0], path, sizeof path)) return 0;
    int rc = sa2_vfs_delete(path);
    if (rc == SA2_VFS_OK) { g_last_error = SA2_ERROR_SUCCESS; return 1; }
    if (rc == SA2_VFS_NOT_FOUND) { g_last_error = SA2_ERROR_FILE_NOT_FOUND; return 0; }
    /* SA2_VFS_READ_ONLY: the file exists only in the installation. Deleting it
     * would mutate the user's own copy of the game, and answering "deleted"
     * without deleting would be a lie the guest acts on. */
    vfs_refuse(rc, "DeleteFileA", path);
    return 0;
}

/* KERNEL32!GetFullPathNameA(LPCSTR, DWORD, LPSTR, LPSTR *). The answer is a
 * guest path, built from the normalised relative path: the host path never
 * reaches the guest. */
static uint32_t imp_GetFullPathNameA(void *s, const uint32_t *a) {
    (void)s;
    char path[SA2_VFS_PATH_MAX], rel[SA2_VFS_PATH_MAX];
    if (!vfs_arg(a[0], path, sizeof path)) return 0;
    int rc = sa2_vfs_normalise(path, rel, sizeof rel);
    if (rc != SA2_VFS_OK) { vfs_refuse(rc, "GetFullPathNameA", path); return 0; }
    char full[SA2_VFS_PATH_MAX];
    int n = snprintf(full, sizeof full, "%s%s%s", sa2_vfs_guest_root(),
                     rel[0] ? "\\" : "", rel);
    for (int i = 0; i < n; i++) if (full[i] == '/') full[i] = '\\';
    uint32_t len = (uint32_t)strlen(full);
    if (!a[2] || a[1] < len + 1) return len + 1;
    gput(a[2], full, len + 1);
    if (a[3]) {
        const char *slash = strrchr(full, '\\');
        gpoke32(a[3], slash ? a[2] + (uint32_t)(slash + 1 - full) : 0u);
    }
    g_last_error = SA2_ERROR_SUCCESS;
    return len;
}

/* WIN32_FIND_DATAA, written field by field at the documented offsets. */
static void put_find_data(uint32_t p, const sa2_vfs_dirent *e) {
    uint64_t ft = g_epoch_100ns;
    gpoke32(p + 0u, e->attributes);
    gpoke32(p + 4u, (uint32_t)ft);         gpoke32(p + 8u,  (uint32_t)(ft >> 32));
    gpoke32(p + 12u, (uint32_t)ft);        gpoke32(p + 16u, (uint32_t)(ft >> 32));
    gpoke32(p + 20u, (uint32_t)ft);        gpoke32(p + 24u, (uint32_t)(ft >> 32));
    gpoke32(p + 28u, (uint32_t)(e->size >> 32));
    gpoke32(p + 32u, (uint32_t)e->size);
    gpoke32(p + 36u, 0);
    gpoke32(p + 40u, 0);
    char name[260];
    memset(name, 0, sizeof name);
    snprintf(name, sizeof name, "%s", e->name);
    gput(p + 44u, name, sizeof name);
    char alt[14];
    memset(alt, 0, sizeof alt);
    gput(p + 304u, alt, sizeof alt);
}

static uint32_t imp_FindFirstFileA(void *s, const uint32_t *a) {
    (void)s;
    char path[SA2_VFS_PATH_MAX];
    if (!vfs_arg(a[0], path, sizeof path)) return SA2_INVALID_HANDLE_VALUE;
    sa2_vfs_dirent e;
    int status = 0;
    uint32_t h = sa2_vfs_find_first(path, &e, &status);
    if (status != SA2_VFS_OK && status != SA2_VFS_NOT_FOUND)
        vfs_refuse(status, "FindFirstFileA", path);
    if (!h) { g_last_error = SA2_ERROR_FILE_NOT_FOUND; return SA2_INVALID_HANDLE_VALUE; }
    put_find_data(a[1], &e);
    g_last_error = SA2_ERROR_SUCCESS;
    return h;
}

static uint32_t imp_FindNextFileA(void *s, const uint32_t *a) {
    (void)s;
    if (!sa2_vfs_is_find_handle(a[0])) {
        sa2_trap_note("FindNextFileA(%#010x): not a find handle this runtime opened", a[0]);
        sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
    }
    sa2_vfs_dirent e;
    if (!sa2_vfs_find_next(a[0], &e)) { g_last_error = SA2_ERROR_NO_MORE_FILES; return 0; }
    put_find_data(a[1], &e);
    g_last_error = SA2_ERROR_SUCCESS;
    return 1;
}

static uint32_t imp_FindClose(void *s, const uint32_t *a) {
    (void)s;
    if (!sa2_vfs_is_find_handle(a[0])) {
        sa2_trap_note("FindClose(%#010x): not a find handle this runtime opened", a[0]);
        sa2_trap_raise(SA2_TRAP_FAULT, a[0]);
    }
    return sa2_vfs_close(a[0]) == SA2_VFS_OK;
}

/* --------------------------------------------- modules and GetProcAddress */

/* ddraw.dll's two entry points. runtime/sa2_ddraw.c answers both, from the
 * capture's device profile, and says what the image asks of them; the
 * argument counts are these rows' and are what import_native pops with, and
 * `tools/s4/ddraw_surface.py --check` holds them to the dwords the image
 * pushes. */
static uint32_t imp_DirectDrawEnumerateExA(void *s, const uint32_t *a) {
    return sa2_ddraw_enumerate_ex_a(s, a, caller_of_current());
}
static uint32_t imp_DirectDrawCreate(void *s, const uint32_t *a) {
    (void)s;
    return sa2_ddraw_create(a, caller_of_current());
}

/* The OS modules this runtime admits it has, and the entry points each one
 * answers for. A module that is absent is absent honestly -- mscoree is the
 * .NET runtime and this process never loaded it, so GetModuleHandleW returns 0
 * and ___crtCorExitProcess falls through to ExitProcess, exactly as on Windows.
 *
 * A row with a NULL `impl` is resolvable but not callable: GetProcAddress
 * returns a real thunk and calling it refuses by name. The two DirectDraw
 * entry points were such rows until the native traces showed the game gets
 * both and keeps what they answer (runtime/sa2_ddraw.h); they answer from the
 * capture's device profile, and GetProcAddress can reach them only when
 * SA2_DDRAW_DEVICE admits the module (g_os_modules below). */
static const dyn_row g_dyn_table[] = {
    {"KERNEL32", "EncodePointer",             1, SA2_IMP_STDCALL, imp_EncodePointer},
    {"KERNEL32", "DecodePointer",             1, SA2_IMP_STDCALL, imp_DecodePointer},
    {"KERNEL32", "FlsAlloc",                  1, SA2_IMP_STDCALL, imp_FlsAlloc},
    {"KERNEL32", "FlsGetValue",               1, SA2_IMP_STDCALL, imp_FlsGetValue},
    {"KERNEL32", "FlsSetValue",               2, SA2_IMP_STDCALL, imp_FlsSetValue},
    {"KERNEL32", "FlsFree",                   1, SA2_IMP_STDCALL, imp_FlsFree},
    {"KERNEL32", "IsProcessorFeaturePresent", 1, SA2_IMP_STDCALL, imp_IsProcessorFeaturePresent},
    {"USER32",   "MessageBoxA",               4, SA2_IMP_STDCALL, imp_MessageBoxA},
    {"USER32",   "GetActiveWindow",           0, SA2_IMP_STDCALL, imp_GetActiveWindow},
    {"USER32",   "GetLastActivePopup",        1, SA2_IMP_STDCALL, imp_GetLastActivePopup},
    {"USER32",   "GetProcessWindowStation",   0, SA2_IMP_STDCALL, imp_GetProcessWindowStation},
    {"USER32",   "GetUserObjectInformationA", 5, SA2_IMP_STDCALL, imp_GetUserObjectInformationA},
    {"DDRAW",    "DirectDrawCreate",          3, SA2_IMP_STDCALL, imp_DirectDrawCreate},
    {"DDRAW",    "DirectDrawEnumerateExA",    3, SA2_IMP_STDCALL, imp_DirectDrawEnumerateExA},
    {NULL, NULL, 0, 0, NULL},
};

/* "KERNEL32.DLL", "kernel32", "C:\\windows\\system32\\USER32.dll" all name the
 * same module. Strip a path and a ".dll" suffix, then compare case-blind. */
static void module_key(const char *in, char *out, size_t cap) {
    const char *base = in;
    for (const char *p = in; *p; p++) if (*p == '\\' || *p == '/') base = p + 1;
    size_t n = strlen(base);
    if (n > 4 && ieq(base + n - 4, ".dll")) n -= 4;
    if (n >= cap) n = cap - 1;
    for (size_t i = 0; i < n; i++)
        out[i] = (base[i] >= 'a' && base[i] <= 'z') ? (char)(base[i] - 32) : base[i];
    out[n] = 0;
}

/* Modules this runtime stands in for, and the rule for being on the list: it
 * implements at least one entry point of that module, so the handle is a
 * promise it can keep.
 *
 * **DDRAW is back on the list, behind SA2_DDRAW_DEVICE (2026-09-23).** It came
 * off on 2026-09-21, when this runtime implemented nothing of it and a handle
 * led the display query into traps, while an absent module sent it down the
 * branch the guest keeps for one. The native traces showed that branch is not
 * native's: ddraw.dll loads, the enumeration runs, and the game keeps the
 * video memory DirectDraw reports (runtime/sa2_ddraw.h cites the lines). With
 * the switch, runtime/sa2_ddraw.c answers from the capture's device profile;
 * without it the absence stands as the default, and LoadLibraryA marks it
 * refused, because it is not what native gets. */
static uint32_t g_os_module_handle[4];      /* by index into the admitted list */
static const char *const g_os_modules[] = {"KERNEL32", "USER32", "DDRAW", NULL};
_Static_assert(sizeof g_os_modules / sizeof g_os_modules[0] - 1u
               <= sizeof g_os_module_handle / sizeof g_os_module_handle[0],
               "every admitted module needs a handle slot");

/* An admitted module this run has switched off: DDRAW without SA2_DDRAW_DEVICE. */
static int os_module_switched_off(const char *key) {
    return !strcmp(key, "DDRAW") && !sa2_ddraw_device_enabled();
}

static uint32_t os_module_handle(const char *key) {
    for (uint32_t i = 0; g_os_modules[i]; i++) {
        if (strcmp(g_os_modules[i], key)) continue;
        if (os_module_switched_off(key)) return 0;
        if (!g_os_module_handle[i]) g_os_module_handle[i] = handle_new(H_MODULE, g_os_modules[i]);
        return g_os_module_handle[i];
    }
    return 0;
}

static const char *module_name_of(uint32_t h) {
    const handle_row *r = handle_of(h);
    return (r && r->kind == H_MODULE) ? r->name : NULL;
}

/* KERNEL32!GetProcAddress(HMODULE, LPCSTR). */
static uint32_t imp_GetProcAddress(void *s, const uint32_t *a) {
    (void)s;
    char name[128];
    if (a[1] & 0xffff0000u) {
        if (gstr(a[1], name, sizeof name) < 0) { g_last_error = SA2_ERROR_INVALID_PARAMETER; return 0; }
    } else {
        snprintf(name, sizeof name, "Ordinal#%u", a[1] & 0xffffu);
    }
    if (sa2_module_by_base(a[0])) {
        uint32_t at = (a[1] & 0xffff0000u) ? sa2_module_export(a[0], name, 0)
                                            : sa2_module_export(a[0], NULL, a[1] & 0xffffu);
        if (at) { g_last_error = SA2_ERROR_SUCCESS; return at; }
        if (g_dyn_missing_count < SA2_DYN_MISS_MAX)
            g_dyn_missing[g_dyn_missing_count] = intern(name, strlen(name));
        g_dyn_missing_count++;
        g_last_error = 127u;   /* ERROR_PROC_NOT_FOUND */
        return 0;
    }
    const char *mod = module_name_of(a[0]);
    if (!mod) {
        /* Either the image itself (which exports nothing) or a handle no
         * LoadLibrary in this runtime ever returned. ERROR_PROC_NOT_FOUND is
         * the Win32 answer and the guest checks for it at every one of the 706
         * sites. */
        g_last_error = 127u;
        return 0;
    }
    for (uint32_t i = 0; i < g_dyn_count; i++) {
        if (!strcmp(g_dyn[i].info.name, name) && ieq(g_dyn[i].info.dll, mod))
            return g_dyn[i].info.thunk;
    }
    for (const dyn_row *r = g_dyn_table; r->module; r++) {
        if (strcmp(r->module, mod) || strcmp(r->name, name)) continue;
        if (g_dyn_count >= sizeof g_dyn / sizeof g_dyn[0]) break;
        import_slot *it = &g_dyn[g_dyn_count];
        it->info.dll = r->module;
        it->info.name = r->name;
        it->info.slot = 0;                      /* no IAT slot: this one is dynamic */
        it->info.thunk = SA2_GUEST_IMPORT_BASE
                       + (SA2_GUEST_IMPORT_DYN_FIRST + g_dyn_count) * SA2_GUEST_IMPORT_STRIDE;
        it->info.argc = r->argc;
        it->info.conv = r->conv;
        it->info.implemented = r->impl ? 1u : 0u;
        it->impl = r->impl;
        if (sa2_ft_add(it->info.thunk, import_native, it->info.name, SA2_FN_NATIVE) < 0) {
            sa2_trap_note("GetProcAddress: the function table refused a row for %s!%s", mod, name);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, it->info.thunk);
        }
        g_dyn_count++;
        g_last_error = SA2_ERROR_SUCCESS;
        return it->info.thunk;
    }
    if (g_dyn_missing_count < SA2_DYN_MISS_MAX)
        g_dyn_missing[g_dyn_missing_count] = intern(name, strlen(name));
    g_dyn_missing_count++;
    g_last_error = 127u;   /* ERROR_PROC_NOT_FOUND */
    return 0;
}

/* KERNEL32!FreeLibrary(HMODULE). A mapped module's count goes down and its image
 * stays, because pointers into it outlive the handle in the game's own tables;
 * an OS module is always loaded; anything else is ERROR_INVALID_HANDLE. The game
 * frees the data DLL when it reloads it (0x0077DDAA) and at shutdown. */
static uint32_t imp_FreeLibrary(void *s, const uint32_t *a) {
    (void)s;
    /* The executable's own handle is always loaded, and freeing it is TRUE and
     * nothing on Windows; so is an OS module this runtime stands in for. */
    if (sa2_module_free(a[0]) || module_name_of(a[0]) || a[0] == SA2_GUEST_IMAGE_BASE) {
        g_last_error = SA2_ERROR_SUCCESS;
        return 1;
    }
    g_last_error = SA2_ERROR_MOD_NOT_FOUND;   /* a handle no load returned, or one freed to zero */
    return 0;
}

/* The table. `argc` is what the stub reads and, for stdcall, what it pops off
 * the guest stack; getting it wrong corrupts the caller's frame, so it is
 * written beside the implementation rather than inferred anywhere. */
typedef struct {
    const char *dll;
    const char *name;
    uint8_t     argc;
    uint8_t     conv;
    import_impl impl;
} stub_row;

static const stub_row g_stubs[] = {
    {"KERNEL32.dll", "GetCurrentDirectoryA", 2, SA2_IMP_STDCALL, imp_GetCurrentDirectoryA},
    {"KERNEL32.dll", "GetModuleFileNameA",   3, SA2_IMP_STDCALL, imp_GetModuleFileNameA},
    {"KERNEL32.dll", "GetModuleHandleA",     1, SA2_IMP_STDCALL, imp_GetModuleHandleA},
    {"KERNEL32.dll", "GetModuleHandleW",     1, SA2_IMP_STDCALL, imp_GetModuleHandleW},
    {"KERNEL32.dll", "LoadLibraryA",         1, SA2_IMP_STDCALL, imp_LoadLibraryA},
    {"KERNEL32.dll", "LoadLibraryW",         1, SA2_IMP_STDCALL, imp_LoadLibraryW},
    {"KERNEL32.dll", "GetProcAddress",       2, SA2_IMP_STDCALL, imp_GetProcAddress},
    {"KERNEL32.dll", "FreeLibrary",          1, SA2_IMP_STDCALL, imp_FreeLibrary},
    {"KERNEL32.dll", "Sleep",                1, SA2_IMP_STDCALL, imp_Sleep},
    /* time (design 3.5) */
    {"KERNEL32.dll", "GetSystemTimeAsFileTime", 1, SA2_IMP_STDCALL, imp_GetSystemTimeAsFileTime},
    {"KERNEL32.dll", "GetCurrentProcessId",  0, SA2_IMP_STDCALL, imp_GetCurrentProcessId},
    {"KERNEL32.dll", "GetCurrentThreadId",   0, SA2_IMP_STDCALL, imp_GetCurrentThreadId},
    {"KERNEL32.dll", "GetTickCount",         0, SA2_IMP_STDCALL, imp_GetTickCount},
    {"KERNEL32.dll", "QueryPerformanceCounter",   1, SA2_IMP_STDCALL, imp_QueryPerformanceCounter},
    {"KERNEL32.dll", "QueryPerformanceFrequency", 1, SA2_IMP_STDCALL, imp_QueryPerformanceFrequency},
    /* the heap */
    {"KERNEL32.dll", "HeapCreate",           3, SA2_IMP_STDCALL, imp_HeapCreate},
    {"KERNEL32.dll", "HeapAlloc",            3, SA2_IMP_STDCALL, imp_HeapAlloc},
    {"KERNEL32.dll", "HeapFree",             3, SA2_IMP_STDCALL, imp_HeapFree},
    {"KERNEL32.dll", "HeapReAlloc",          4, SA2_IMP_STDCALL, imp_HeapReAlloc},
    {"KERNEL32.dll", "HeapSize",             3, SA2_IMP_STDCALL, imp_HeapSize},
    {"KERNEL32.dll", "GetProcessHeap",       0, SA2_IMP_STDCALL, imp_GetProcessHeap},
    /* startup state */
    {"KERNEL32.dll", "GetStartupInfoA",      1, SA2_IMP_STDCALL, imp_GetStartupInfoA},
    {"KERNEL32.dll", "GetCommandLineA",      0, SA2_IMP_STDCALL, imp_GetCommandLineA},
    {"KERNEL32.dll", "GetEnvironmentStringsW", 0, SA2_IMP_STDCALL, imp_GetEnvironmentStringsW},
    {"KERNEL32.dll", "GetEnvironmentStrings",  0, SA2_IMP_STDCALL, imp_GetEnvironmentStringsA},
    {"KERNEL32.dll", "FreeEnvironmentStringsA", 1, SA2_IMP_STDCALL, imp_FreeEnvironmentStrings},
    {"KERNEL32.dll", "FreeEnvironmentStringsW", 1, SA2_IMP_STDCALL, imp_FreeEnvironmentStrings},
    {"KERNEL32.dll", "GetStdHandle",         1, SA2_IMP_STDCALL, imp_GetStdHandle},
    {"KERNEL32.dll", "SetStdHandle",         2, SA2_IMP_STDCALL, imp_SetStdHandle},
    {"KERNEL32.dll", "SetHandleCount",       1, SA2_IMP_STDCALL, imp_SetHandleCount},
    {"KERNEL32.dll", "GetFileType",          1, SA2_IMP_STDCALL, imp_GetFileType},

    /* Files: the guest's own installation, through runtime/sa2_files.c. */
    {"SHLWAPI.dll",  "PathFileExistsA",      1, SA2_IMP_STDCALL, imp_PathFileExistsA},
    {"KERNEL32.dll", "GetFileAttributesA",   1, SA2_IMP_STDCALL, imp_GetFileAttributesA},
    {"KERNEL32.dll", "CreateDirectoryA",     2, SA2_IMP_STDCALL, imp_CreateDirectoryA},
    {"KERNEL32.dll", "CreateFileA",          7, SA2_IMP_STDCALL, imp_CreateFileA},
    {"KERNEL32.dll", "CreateFileW",          7, SA2_IMP_STDCALL, imp_CreateFileW},
    {"KERNEL32.dll", "ReadFile",             5, SA2_IMP_STDCALL, imp_ReadFile},
    {"KERNEL32.dll", "WriteFile",            5, SA2_IMP_STDCALL, imp_WriteFile},
    {"KERNEL32.dll", "SetFilePointer",       4, SA2_IMP_STDCALL, imp_SetFilePointer},
    {"KERNEL32.dll", "GetFileSize",          2, SA2_IMP_STDCALL, imp_GetFileSize},
    {"KERNEL32.dll", "FlushFileBuffers",     1, SA2_IMP_STDCALL, imp_FlushFileBuffers},
    {"KERNEL32.dll", "SetEndOfFile",         1, SA2_IMP_STDCALL, imp_SetEndOfFile},
    {"KERNEL32.dll", "DeleteFileA",          1, SA2_IMP_STDCALL, imp_DeleteFileA},
    {"KERNEL32.dll", "GetFullPathNameA",     4, SA2_IMP_STDCALL, imp_GetFullPathNameA},
    {"KERNEL32.dll", "FindFirstFileA",       2, SA2_IMP_STDCALL, imp_FindFirstFileA},
    {"KERNEL32.dll", "FindNextFileA",        2, SA2_IMP_STDCALL, imp_FindNextFileA},
    {"KERNEL32.dll", "FindClose",            1, SA2_IMP_STDCALL, imp_FindClose},
    /* locking, one thread */
    {"KERNEL32.dll", "InitializeCriticalSection", 1, SA2_IMP_STDCALL, imp_InitializeCriticalSection},
    {"KERNEL32.dll", "InitializeCriticalSectionAndSpinCount", 2, SA2_IMP_STDCALL,
                                                      imp_InitializeCriticalSectionAndSpinCount},
    {"KERNEL32.dll", "EnterCriticalSection", 1, SA2_IMP_STDCALL, imp_EnterCriticalSection},
    {"KERNEL32.dll", "LeaveCriticalSection", 1, SA2_IMP_STDCALL, imp_LeaveCriticalSection},
    {"KERNEL32.dll", "DeleteCriticalSection",1, SA2_IMP_STDCALL, imp_DeleteCriticalSection},
    {"KERNEL32.dll", "InterlockedIncrement", 1, SA2_IMP_STDCALL, imp_InterlockedIncrement},
    {"KERNEL32.dll", "InterlockedDecrement", 1, SA2_IMP_STDCALL, imp_InterlockedDecrement},
    {"KERNEL32.dll", "InterlockedExchange",  2, SA2_IMP_STDCALL, imp_InterlockedExchange},
    /* process and version */
    {"KERNEL32.dll", "GetCurrentProcess",    0, SA2_IMP_STDCALL, imp_GetCurrentProcess},
    {"KERNEL32.dll", "IsDebuggerPresent",    0, SA2_IMP_STDCALL, imp_IsDebuggerPresent},
    {"KERNEL32.dll", "SetUnhandledExceptionFilter", 1, SA2_IMP_STDCALL, imp_SetUnhandledExceptionFilter},
    {"KERNEL32.dll", "GetVersionExW",        1, SA2_IMP_STDCALL, imp_GetVersionExW},
    {"KERNEL32.dll", "TerminateProcess",     2, SA2_IMP_STDCALL, imp_TerminateProcess},
    /* code pages and locale */
    {"KERNEL32.dll", "GetACP",               0, SA2_IMP_STDCALL, imp_GetACP},
    {"KERNEL32.dll", "GetOEMCP",             0, SA2_IMP_STDCALL, imp_GetOEMCP},
    {"KERNEL32.dll", "GetCPInfo",            2, SA2_IMP_STDCALL, imp_GetCPInfo},
    {"KERNEL32.dll", "IsValidCodePage",      1, SA2_IMP_STDCALL, imp_IsValidCodePage},
    {"KERNEL32.dll", "WideCharToMultiByte",  8, SA2_IMP_STDCALL, imp_WideCharToMultiByte},
    {"KERNEL32.dll", "MultiByteToWideChar",  6, SA2_IMP_STDCALL, imp_MultiByteToWideChar},
    {"KERNEL32.dll", "GetStringTypeW",       4, SA2_IMP_STDCALL, imp_GetStringTypeW},
    {"KERNEL32.dll", "GetStringTypeA",       5, SA2_IMP_STDCALL, imp_GetStringTypeA},
    {"KERNEL32.dll", "LCMapStringW",         6, SA2_IMP_STDCALL, imp_LCMapStringW},
    {"KERNEL32.dll", "LCMapStringA",         6, SA2_IMP_STDCALL, imp_LCMapStringA},
    {"KERNEL32.dll", "GetUserDefaultLCID",   0, SA2_IMP_STDCALL, imp_GetUserDefaultLCID},
    {"KERNEL32.dll", "IsValidLocale",        2, SA2_IMP_STDCALL, imp_IsValidLocale},
    {"KERNEL32.dll", "EnumSystemLocalesA",   2, SA2_IMP_STDCALL, imp_EnumSystemLocalesA},
    {"KERNEL32.dll", "GetLocaleInfoA",       4, SA2_IMP_STDCALL, imp_GetLocaleInfoA},
    {"KERNEL32.dll", "GetLocaleInfoW",       4, SA2_IMP_STDCALL, imp_GetLocaleInfoW},
    /* named kernel objects and the string helper WinMain uses */
    {"KERNEL32.dll", "CreateMutexW",         3, SA2_IMP_STDCALL, imp_CreateMutexW},
    {"KERNEL32.dll", "CloseHandle",          1, SA2_IMP_STDCALL, imp_CloseHandle},
    {"WINMM.dll",    "timeSetEvent",         5, SA2_IMP_STDCALL, imp_timeSetEvent},
    {"WINMM.dll",    "timeKillEvent",        1, SA2_IMP_STDCALL, imp_timeKillEvent},
    {"ole32.dll",    "CoCreateInstance",     5, SA2_IMP_STDCALL, imp_CoCreateInstance},
    {"ole32.dll",    "CoInitializeEx",       2, SA2_IMP_STDCALL, imp_CoInitializeEx},
    {"ole32.dll",    "CoInitialize",         1, SA2_IMP_STDCALL, imp_CoInitialize},
    {"ole32.dll",    "CoUninitialize",       0, SA2_IMP_STDCALL, imp_CoUninitialize},
    {"ole32.dll",    "CoSetProxyBlanket",    8, SA2_IMP_STDCALL, imp_CoSetProxyBlanket},
    {"OLEAUT32.dll", "Ordinal#2",            1, SA2_IMP_STDCALL, imp_SysAllocString},
    {"OLEAUT32.dll", "Ordinal#6",            1, SA2_IMP_STDCALL, imp_SysFreeString},
    /* Threads (G1 section 3, runtime/sa2_threads.c). */
    {"KERNEL32.dll", "CreateThread",         6, SA2_IMP_STDCALL, imp_CreateThread},
    {"KERNEL32.dll", "ResumeThread",         1, SA2_IMP_STDCALL, imp_ResumeThread},
    {"KERNEL32.dll", "SuspendThread",        1, SA2_IMP_STDCALL, imp_SuspendThread},
    {"KERNEL32.dll", "SetThreadPriority",    2, SA2_IMP_STDCALL, imp_SetThreadPriority},
    {"KERNEL32.dll", "GetThreadPriority",    1, SA2_IMP_STDCALL, imp_GetThreadPriority},
    {"KERNEL32.dll", "SetThreadAffinityMask",2, SA2_IMP_STDCALL, imp_SetThreadAffinityMask},
    {"KERNEL32.dll", "SetThreadIdealProcessor", 2, SA2_IMP_STDCALL, imp_SetThreadIdealProcessor},
    {"KERNEL32.dll", "GetExitCodeThread",    2, SA2_IMP_STDCALL, imp_GetExitCodeThread},
    {"KERNEL32.dll", "ExitThread",           1, SA2_IMP_STDCALL, imp_ExitThread},
    {"KERNEL32.dll", "RaiseException",        4, SA2_IMP_STDCALL, imp_RaiseException},
    {"KERNEL32.dll", "GetProcessAffinityMask",3, SA2_IMP_STDCALL, imp_GetProcessAffinityMask},
    {"KERNEL32.dll", "CreateEventA",         4, SA2_IMP_STDCALL, imp_CreateEventA},
    {"KERNEL32.dll", "CreateEventW",         4, SA2_IMP_STDCALL, imp_CreateEventW},
    {"KERNEL32.dll", "SetEvent",             1, SA2_IMP_STDCALL, imp_SetEvent},
    {"KERNEL32.dll", "ResetEvent",           1, SA2_IMP_STDCALL, imp_ResetEvent},
    {"KERNEL32.dll", "WaitForSingleObject",  2, SA2_IMP_STDCALL, imp_WaitForSingleObject},
    {"KERNEL32.dll", "lstrcmpA",             2, SA2_IMP_STDCALL, imp_lstrcmpA},
    {"KERNEL32.dll", "TlsAlloc",             0, SA2_IMP_STDCALL, imp_TlsAlloc},
    {"KERNEL32.dll", "TlsGetValue",          1, SA2_IMP_STDCALL, imp_TlsGetValue},
    {"KERNEL32.dll", "TlsSetValue",          2, SA2_IMP_STDCALL, imp_TlsSetValue},
    {"KERNEL32.dll", "TlsFree",              1, SA2_IMP_STDCALL, imp_TlsFree},
    {"KERNEL32.dll", "OutputDebugStringA",   1, SA2_IMP_STDCALL, imp_OutputDebugStringA},
    {"KERNEL32.dll", "OutputDebugStringW",   1, SA2_IMP_STDCALL, imp_OutputDebugStringW},
    {"USER32.dll",   "wvsprintfA",           3, SA2_IMP_STDCALL, imp_wvsprintfA},
    {"KERNEL32.dll", "SetLastError",         1, SA2_IMP_STDCALL, imp_SetLastError},
    {"KERNEL32.dll", "GetLastError",         0, SA2_IMP_STDCALL, imp_GetLastError},
    {"KERNEL32.dll", "ExitProcess",          1, SA2_IMP_STDCALL, imp_ExitProcess},
    {"KERNEL32.dll", "SetThreadExecutionState", 1, SA2_IMP_STDCALL, imp_SetThreadExecutionState},

    /* The window and the message pump (design 5, S4). runtime/sa2_window.c
     * owns the model; these are the Win32 shape over it. */
    {"USER32.dll",   "LoadIconW",            2, SA2_IMP_STDCALL, imp_LoadIconW},
    {"USER32.dll",   "LoadCursorW",          2, SA2_IMP_STDCALL, imp_LoadCursorW},
    {"USER32.dll",   "RegisterClassW",       1, SA2_IMP_STDCALL, imp_RegisterClassW},
    {"USER32.dll",   "CreateWindowExW",     12, SA2_IMP_STDCALL, imp_CreateWindowExW},
    {"USER32.dll",   "ShowWindow",           2, SA2_IMP_STDCALL, imp_ShowWindow},
    {"USER32.dll",   "SetForegroundWindow",  1, SA2_IMP_STDCALL, imp_SetForegroundWindow},
    {"USER32.dll",   "SetFocus",             1, SA2_IMP_STDCALL, imp_SetFocus},
    {"USER32.dll",   "ShowCursor",           1, SA2_IMP_STDCALL, imp_ShowCursor},
    {"USER32.dll",   "SystemParametersInfoW", 4, SA2_IMP_STDCALL, imp_SystemParametersInfoW},
    {"USER32.dll",   "GetSystemMetrics",     1, SA2_IMP_STDCALL, imp_GetSystemMetrics},
    {"USER32.dll",   "PeekMessageW",         5, SA2_IMP_STDCALL, imp_PeekMessageW},
    {"USER32.dll",   "GetMessageW",          4, SA2_IMP_STDCALL, imp_GetMessageW},
    {"USER32.dll",   "TranslateMessage",     1, SA2_IMP_STDCALL, imp_TranslateMessage},
    {"USER32.dll",   "DispatchMessageW",     1, SA2_IMP_STDCALL, imp_DispatchMessageW},
    {"USER32.dll",   "DefWindowProcW",       4, SA2_IMP_STDCALL, imp_DefWindowProcW},
    {"USER32.dll",   "PostMessageW",         4, SA2_IMP_STDCALL, imp_PostMessageW},
    {"USER32.dll",   "PostQuitMessage",      1, SA2_IMP_STDCALL, imp_PostQuitMessage},
    {"USER32.dll",   "DestroyWindow",        1, SA2_IMP_STDCALL, imp_DestroyWindow},
    {"USER32.dll",   "IsWindow",             1, SA2_IMP_STDCALL, imp_IsWindow},
    {"USER32.dll",   "GetClientRect",        2, SA2_IMP_STDCALL, imp_GetClientRect},
    {"USER32.dll",   "GetWindowRect",        2, SA2_IMP_STDCALL, imp_GetWindowRect},
    {"USER32.dll",   "MessageBoxA",          4, SA2_IMP_STDCALL, imp_MessageBoxA},
    {"USER32.dll",   "GetActiveWindow",      0, SA2_IMP_STDCALL, imp_GetActiveWindow},
    {"USER32.dll",   "GetLastActivePopup",   1, SA2_IMP_STDCALL, imp_GetLastActivePopup},
    /* The rest of the USER32 surface (design 3.5 b, S4). Every argc here was
     * checked against mingw-w64's winuser.h, the copy zig ships; a wrong one
     * pops the wrong number of dwords and corrupts the caller's frame rather
     * than answering wrongly, which is the defect class this project has been
     * bitten by three times. */
    {"USER32.dll",   "BeginPaint",           2, SA2_IMP_STDCALL, imp_BeginPaint},
    {"USER32.dll",   "DestroyIcon",          1, SA2_IMP_STDCALL, imp_DestroyIcon},
    {"USER32.dll",   "DrawIcon",             4, SA2_IMP_STDCALL, imp_DrawIcon},
    {"USER32.dll",   "DrawTextW",            5, SA2_IMP_STDCALL, imp_DrawTextW},
    {"USER32.dll",   "EnableMenuItem",       3, SA2_IMP_STDCALL, imp_EnableMenuItem},
    {"USER32.dll",   "EnableWindow",         2, SA2_IMP_STDCALL, imp_EnableWindow},
    {"USER32.dll",   "EndPaint",             2, SA2_IMP_STDCALL, imp_EndPaint},
    {"USER32.dll",   "FillRect",             3, SA2_IMP_STDCALL, imp_FillRect},
    {"USER32.dll",   "GetAsyncKeyState",     1, SA2_IMP_STDCALL, imp_GetAsyncKeyState},
    {"USER32.dll",   "GetClassNameW",        3, SA2_IMP_STDCALL, imp_GetClassNameW},
    {"USER32.dll",   "GetCursorPos",         1, SA2_IMP_STDCALL, imp_GetCursorPos},
    {"USER32.dll",   "GetDialogBaseUnits",   0, SA2_IMP_STDCALL, imp_GetDialogBaseUnits},
    {"USER32.dll",   "GetKeyState",          1, SA2_IMP_STDCALL, imp_GetKeyState},
    {"USER32.dll",   "GetParent",            1, SA2_IMP_STDCALL, imp_GetParent},
    {"USER32.dll",   "GetSysColor",          1, SA2_IMP_STDCALL, imp_GetSysColor},
    {"USER32.dll",   "GetSystemMenu",        2, SA2_IMP_STDCALL, imp_GetSystemMenu},
    {"USER32.dll",   "GetWindow",            2, SA2_IMP_STDCALL, imp_GetWindow},
    {"USER32.dll",   "GetWindowLongW",       2, SA2_IMP_STDCALL, imp_GetWindowLongW},
    {"USER32.dll",   "GetWindowTextW",       3, SA2_IMP_STDCALL, imp_GetWindowTextW},
    {"USER32.dll",   "IsDialogMessageW",     2, SA2_IMP_STDCALL, imp_IsDialogMessageW},
    {"USER32.dll",   "KillTimer",            2, SA2_IMP_STDCALL, imp_KillTimer},
    {"USER32.dll",   "MoveWindow",           6, SA2_IMP_STDCALL, imp_MoveWindow},
    {"USER32.dll",   "RedrawWindow",         4, SA2_IMP_STDCALL, imp_RedrawWindow},
    {"USER32.dll",   "SendMessageW",         4, SA2_IMP_STDCALL, imp_SendMessageW},
    {"USER32.dll",   "SetRect",              5, SA2_IMP_STDCALL, imp_SetRect},
    {"USER32.dll",   "SetTimer",             4, SA2_IMP_STDCALL, imp_SetTimer},
    {"USER32.dll",   "SetWindowLongW",       3, SA2_IMP_STDCALL, imp_SetWindowLongW},
    {"USER32.dll",   "SetWindowPos",         7, SA2_IMP_STDCALL, imp_SetWindowPos},
    {"USER32.dll",   "SetWindowTextW",       2, SA2_IMP_STDCALL, imp_SetWindowTextW},
    {"USER32.dll",   "WaitMessage",          0, SA2_IMP_STDCALL, imp_WaitMessage},
    /* GDI32 (design 3.5 group b, presentation). runtime/sa2_gdi.c owns the
     * model; these are the Win32 shape over it. Recording no-op successes:
     * only GetTextExtentPoint32W invents a number, and SA2_GDI_STRICT=1 turns
     * that one back into a refusal. */
    {"GDI32.dll",    "CreateDCW",             4, SA2_IMP_STDCALL, imp_CreateDCW},
    {"GDI32.dll",    "CreateFontIndirectW",   1, SA2_IMP_STDCALL, imp_CreateFontIndirectW},
    {"GDI32.dll",    "CreateSolidBrush",      1, SA2_IMP_STDCALL, imp_CreateSolidBrush},
    {"GDI32.dll",    "DeleteDC",              1, SA2_IMP_STDCALL, imp_DeleteDC},
    {"GDI32.dll",    "DeleteObject",          1, SA2_IMP_STDCALL, imp_DeleteObject},
    {"GDI32.dll",    "GetTextExtentPoint32W", 4, SA2_IMP_STDCALL, imp_GetTextExtentPoint32W},
    {"GDI32.dll",    "SelectObject",          2, SA2_IMP_STDCALL, imp_SelectObject},
    {"GDI32.dll",    "SetBkColor",            2, SA2_IMP_STDCALL, imp_SetBkColor},
    {"GDI32.dll",    "SetTextColor",          2, SA2_IMP_STDCALL, imp_SetTextColor},
    /* Dialogs and the clipboard (design 3.5, presentation). runtime/sa2_dialog.c
     * owns the model. The quit-confirmation dialog at 0x00404a00 is the only
     * caller of any of them. */
    {"USER32.dll",   "OpenClipboard",        1, SA2_IMP_STDCALL, imp_OpenClipboard},
    {"USER32.dll",   "CloseClipboard",       0, SA2_IMP_STDCALL, imp_CloseClipboard},
    {"USER32.dll",   "EmptyClipboard",       0, SA2_IMP_STDCALL, imp_EmptyClipboard},
    {"USER32.dll",   "SetClipboardData",     2, SA2_IMP_STDCALL, imp_SetClipboardData},
    {"USER32.dll",   "CheckDlgButton",       3, SA2_IMP_STDCALL, imp_CheckDlgButton},
    {"USER32.dll",   "CreateDialogIndirectParamW", 5, SA2_IMP_STDCALL, imp_CreateDialogIndirectParamW},
    {"USER32.dll",   "GetDlgItem",           2, SA2_IMP_STDCALL, imp_GetDlgItem},
    {"USER32.dll",   "LoadStringW",          4, SA2_IMP_STDCALL, imp_LoadStringW},
    {"USER32.dll",   "MessageBeep",          1, SA2_IMP_STDCALL, imp_MessageBeep},
    {"USER32.dll",   "WinHelpW",             4, SA2_IMP_STDCALL, imp_WinHelpW},

    /* Steam (design 3.5 c). All cdecl: the guest cleans up, so `argc` here is
     * only what gets recorded. */
    {"steam_api.dll", "SteamAPI_Init",                0, SA2_IMP_CDECL, imp_steam},
    {"steam_api.dll", "SteamAPI_Shutdown",            0, SA2_IMP_CDECL, imp_steam},
    {"steam_api.dll", "SteamAPI_RunCallbacks",        0, SA2_IMP_CDECL, imp_steam},
    {"steam_api.dll", "SteamAPI_RegisterCallback",    2, SA2_IMP_CDECL, imp_steam},
    {"steam_api.dll", "SteamAPI_UnregisterCallback",  1, SA2_IMP_CDECL, imp_steam},
    {"steam_api.dll", "SteamAPI_RegisterCallResult",  2, SA2_IMP_CDECL, imp_steam},
    {"steam_api.dll", "SteamAPI_UnregisterCallResult", 2, SA2_IMP_CDECL, imp_steam},
    {"steam_api.dll", "SteamApps",                    0, SA2_IMP_CDECL, imp_steam},
    {"steam_api.dll", "SteamUser",                    0, SA2_IMP_CDECL, imp_steam},
    {"steam_api.dll", "SteamUtils",                   0, SA2_IMP_CDECL, imp_steam},
    {"steam_api.dll", "SteamFriends",                 0, SA2_IMP_CDECL, imp_steam},
    {"steam_api.dll", "SteamUserStats",               0, SA2_IMP_CDECL, imp_steam},

    /* The presentation boundary. Direct3DCreate9 is stdcall with one argument
     * and refuses by name; SA2_D3D9_SURVEY turns it into a recording object
     * that answers nothing. See runtime/sa2_iface.h. */
    {"d3d9.dll",      "Direct3DCreate9",              1, SA2_IMP_STDCALL, imp_Direct3DCreate9},
    {"d3dx9_42.dll",  "D3DXGetShaderConstantTable",   2, SA2_IMP_STDCALL,
     imp_D3DXGetShaderConstantTable},
    {"d3dx9_42.dll",  "D3DXCreateTexture",            8, SA2_IMP_STDCALL,
     imp_D3DXCreateTexture},
    {"d3dx9_42.dll",  "D3DXCreateTextureFromFileInMemoryEx", 15, SA2_IMP_STDCALL,
     imp_D3DXCreateTextureFromFileInMemoryEx},
    {"d3dx9_42.dll",  "D3DXCreateCubeTexture",         7, SA2_IMP_STDCALL,
     imp_D3DXCreateCubeTexture},
    {"d3dx9_42.dll",  "D3DXCreateCubeTextureFromFileInMemoryEx", 14, SA2_IMP_STDCALL,
     imp_D3DXCreateCubeTextureFromFileInMemoryEx},
    {"d3dx9_42.dll",  "D3DXCreateVolumeTexture",       9, SA2_IMP_STDCALL,
     imp_D3DXCreateVolumeTexture},
    {"d3dx9_42.dll",  "D3DXCreateVolumeTextureFromFileInMemoryEx", 16, SA2_IMP_STDCALL,
     imp_D3DXCreateVolumeTextureFromFileInMemoryEx},
    {"d3dx9_42.dll",  "D3DXFilterTexture",             4, SA2_IMP_STDCALL,
     imp_D3DXFilterTexture},
    {"d3dx9_42.dll",  "D3DXLoadSurfaceFromSurface",    8, SA2_IMP_STDCALL,
     imp_D3DXLoadSurfaceFromSurface},
    {"d3dx9_42.dll",  "D3DXSaveSurfaceToFileA",        5, SA2_IMP_STDCALL,
     imp_D3DXSaveSurfaceToFileA},
    {"d3dx9_42.dll",  "D3DXSaveTextureToFileA",        4, SA2_IMP_STDCALL,
     imp_D3DXSaveTextureToFileA},
    {"KERNEL32.dll",  "GetSystemInfo",                1, SA2_IMP_STDCALL, imp_GetSystemInfo},
    {"DINPUT8.dll",   "DirectInput8Create",           5, SA2_IMP_STDCALL,
     imp_DirectInput8Create},
    {"DSOUND.dll",    "Ordinal#11",                   3, SA2_IMP_STDCALL,
     imp_DirectSoundCreate8},
    {"WINMM.dll",     "timeGetTime",                  0, SA2_IMP_STDCALL, imp_timeGetTime},
    {"SteamMetricsSDK.dll", "?GetInstance@MetricsAgent@SteamMetricsSDK@@SAPAV12@XZ",
                                                      0, SA2_IMP_CDECL,    imp_steam_metrics},
    {"SteamMetricsSDK.dll", "?Initialize@MetricsAgent@SteamMetricsSDK@@QAEXPBDH0@Z",
                                                      3, SA2_IMP_STDCALL,  imp_steam_metrics},
    {"SteamMetricsSDK.dll", "?LogEvent@MetricsAgent@SteamMetricsSDK@@QAEXPBD00@Z",
                                                      3, SA2_IMP_STDCALL,  imp_steam_metrics},
    {"SteamMetricsSDK.dll", "?CleanInstance@MetricsAgent@SteamMetricsSDK@@SAXXZ",
                                                      0, SA2_IMP_CDECL,    imp_steam_metrics},
    {"XINPUT1_3.dll", "Ordinal#2",                    2, SA2_IMP_STDCALL, imp_xinput},
    {"XINPUT1_3.dll", "Ordinal#3",                    2, SA2_IMP_STDCALL, imp_xinput},
    {"XINPUT1_3.dll", "Ordinal#4",                    3, SA2_IMP_STDCALL, imp_xinput},
};

static const stub_row *stub_for(const char *dll, const char *name) {
    for (unsigned i = 0; i < sizeof g_stubs / sizeof g_stubs[0]; i++)
        if (ieq(g_stubs[i].dll, dll) && !strcmp(g_stubs[i].name, name)) return &g_stubs[i];
    return NULL;
}

/* ------------------------------------------------- the presentation survey
 *
 * Design 3.5 classifies the imports, and group (b) is presentation: d3d9,
 * d3dx9_42, dsound, dinput8, xinput1_3, gdi32 and the DirectDraw display
 * enumeration. Every one of them belongs to S5. This runtime therefore
 * refuses them by name -- and, when a driver sets SA2_PRESENT_SURVEY, answers
 * a bounded number of them with 0 so the *ordered list with arguments* can be
 * collected in one run. Nothing else changes: an OS-service import that is
 * missing still stops the run, because a missing OS service is this stage's
 * bug and a missing presentation entry point is the next stage's work. */
static const char *const g_presentation_dlls[] = {
    "d3d9.dll", "d3dx9_42.dll", "DSOUND.dll", "DINPUT8.dll", "XINPUT1_3.dll",
    "GDI32.dll", "DDRAW", NULL,
};
static int32_t  g_present_survey_budget = -1;   /* -1: the survey is off */
static uint32_t g_present_surveyed;

static int presentation_dll(const char *dll) {
    for (uint32_t i = 0; g_presentation_dlls[i]; i++)
        if (ieq(g_presentation_dlls[i], dll)) return 1;
    return 0;
}
static int presentation_survey_takes(const char *dll) {
    return g_present_survey_budget > 0 && presentation_dll(dll);
}
uint32_t sa2_presentation_surveyed(void) { return g_present_surveyed; }
int      sa2_presentation_survey_on(void) { return g_present_survey_budget >= 0; }

/* ------------------------------------------------------- the one native */

static void *import_native(void *s, uint32_t pc, void *m) {
    if (pc < SA2_GUEST_IMPORT_BASE || pc >= SA2_GUEST_IMPORT_END) {
        sa2_trap_note("import dispatch at %#010x is outside the thunk region", pc);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }
    uint32_t idx = (pc - SA2_GUEST_IMPORT_BASE) / SA2_GUEST_IMPORT_STRIDE;
    import_slot *it;
    if (idx >= SA2_GUEST_IMPORT_DYN_FIRST) {
        uint32_t d = idx - SA2_GUEST_IMPORT_DYN_FIRST;
        if (d >= g_dyn_count) {
            sa2_trap_note("import thunk %#010x is dynamic index %u, past the %u resolved by "
                          "GetProcAddress", pc, d, g_dyn_count);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
        }
        it = &g_dyn[d];
    } else if (idx >= g_import_count) {
        sa2_trap_note("import thunk %#010x is index %u, past the %u bound imports", pc, idx, g_import_count);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    } else {
        it = &g_imports[idx];
    }

    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    /* The one place the runtime sees the guest stack pointer on every call, so
     * it is where each guest thread's low-water mark is kept and where a worker
     * that has run out of its own 1 MiB stack slot is caught -- before it can
     * write into the slot below, rather than eleven thousand calls afterwards. */
    sa2_sched_note_esp(esp);
    const uint32_t caller = gpeek32(esp);
    uint32_t argv[SA2_IMPORT_MAX_ARGS];
    memset(argv, 0, sizeof argv);
    for (uint32_t i = 0; i < it->info.argc && i < SA2_IMPORT_MAX_ARGS; i++)
        argv[i] = gpeek32(esp + 4u + 4u * i);

    /* A deterministic bound on a run.
     *
     * The wall-clock `alarm` in `sa2_call` bounds a run too, and it bounds it
     * with a number that depends on how fast this machine is: two runs of the
     * same binary stop at *different* points under it, which is the one thing a
     * determinism comparison cannot tolerate. This stops at the same import
     * call in every run. Off unless SA2_IMPORT_BUDGET is set. */
    /* ...and it bounds the *guest*. The harness's own control probes run after
     * the guest has stopped, and a budget that is already spent refuses them
     * too: `escape_refused_and_control_served` was false in every budgeted run
     * of this slice, with `control_esp_delta` 0, because `PathFileExistsA`
     * never reached its stub. That reads as a regression and is a bound whose
     * range no longer contains the thing it was bounding.
     * `sa2_import_budget_release()` ends it at the same place in every run:
     * the moment the guest's own call returns. */
    if (g_call_budget && !g_budget_released && g_calls >= g_call_budget) {
        sa2_trap_note("the import-call budget of %u was reached at %s!%s from %#010x: this is "
                      "SA2_IMPORT_BUDGET stopping the run at the same call in every run, not "
                      "a wall", g_call_budget, it->info.dll, it->info.name, caller);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, it->info.thunk);
    }
    if (!it->info.first_seen) {
        it->info.first_seen = 1;
        it->info.first_caller = caller;
        it->info.first_arg0 = argv[0];
    }
    it->info.calls++;
    g_calls++;
    /* The spin diagnostic, off unless SA2_SPIN_ADVANCE is set. It lives here
     * and not in `sa2_sched_yield` because the first version lived there and
     * fired *zero* times against a guest that made 3.6 million import calls
     * without progressing: an uncontended EnterCriticalSection is not a
     * hand-off point, so the poll loop never reaches the scheduler at all. A
     * diagnostic placed where the thing it diagnoses does not pass is the
     * bound whose range contains nothing. */
    sa2_sched_spin_tick();
    /* The *last* calls, as well as the first. `g_log` fills from the start and
     * stops at SA2_IMPORT_LOG_MAX, so at fourteen thousand calls its tail is
     * the CRT's startup and the end of the run is not in it -- which is how a
     * null read after a subsystem gave up came with nothing to place it. This
     * ring is the same idea as the function table's, one store per call. */
    {
        sa2_import_recent *rr = &g_recent[g_recent_next % SA2_IMPORT_RECENT];
        rr->seq = g_calls;
        rr->index = (uint16_t)idx;
        rr->caller = caller;
        rr->arg0 = argv[0];
        rr->ret_valid = 0;
        rr->thread = (uint8_t)sa2_sched_current();
        /* Where this thread is in the image, kept per thread: see
         * sa2_sched_first_caller. */
        sa2_sched_note_caller(caller);
        g_recent_next++;
        g_recent_cur = rr;
        g_recent_seq = g_calls;
    }
    sa2_import_call *rec = g_log_used < SA2_IMPORT_LOG_MAX ? &g_log[g_log_used++] : NULL;
    if (rec) {
        memset(rec, 0, sizeof *rec);
        rec->seq = g_calls;
        rec->index = (uint16_t)idx;
        rec->implemented = it->info.implemented;
        rec->argc = it->info.argc;
        rec->caller = caller;
        rec->thread = (uint8_t)sa2_sched_current();
        memcpy(rec->arg, argv, sizeof argv);
    }

    if (g_stop_import[0] && !strcmp(it->info.name, g_stop_import)
        && g_stop_import_seen++ >= g_stop_import_after) {
        g_stop_import_fired = 1;
        sa2_trap_note("stopping at %s!%s called from %#010x, as the driver asked: this is "
                      "the edge of what this slice implements, not a wall",
                      it->info.dll, it->info.name, caller);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }

    if (!it->impl) {
        /* The presentation survey, which is off unless a driver asks for it.
         * An unimplemented import from a presentation DLL answers 0 and the
         * call is recorded, so one run walks the whole surface the game asks
         * of a graphics, audio or input stack instead of stopping at the
         * first entry point. That enumeration is S5's denominator (design 5,
         * M7) and this is the cheapest place to collect it. It is a survey and
         * not an implementation: `presentation_surveyed` in the run's JSON
         * counts exactly how many answers were fabricated, and a run with a
         * non-zero count is not a run of the game. */
        if (presentation_survey_takes(it->info.dll)) {
            g_present_surveyed++;
            if (rec) rec->surveyed = 1;
            if (g_present_survey_budget > 0) g_present_survey_budget--;
            sa2_rt_set_gpr(s, SA2_EAX, 0);
            sa2_rt_set_gpr(s, SA2_ESP, esp + 4u +
                           (it->info.conv == SA2_IMP_STDCALL ? 4u * it->info.argc : 0u));
            return m;
        }
        /* The loud default. The call is recorded first, so the run that
         * stopped here is evidence and not only a trap message. */
        sa2_trap_note("unimplemented import %s!%s called from %#010x (slot %#010x, thunk %#010x)%s",
                      it->info.dll, it->info.name, caller, it->info.slot, it->info.thunk,
                      presentation_dll(it->info.dll)
                          ? "; SA2_PRESENT_SURVEY=<n> walks the presentation surface instead"
                          : "");
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, pc);
    }

    /* Saved and restored rather than cleared: DispatchMessageW runs the
     * guest's own window procedure, which calls imports of its own, and a
     * nested call that cleared these would leave the outer stub unable to name
     * itself in a trap. */
    sa2_import_info *prev_current = g_current;
    sa2_import_call *prev_rec = g_current_rec;
    sa2_import_recent *mine = g_recent_cur;
    const uint32_t mine_seq = g_recent_seq;
    const uint32_t prev_caller = g_current_caller;
    g_current_caller = caller;
    g_current = &it->info;
    g_current_rec = rec;
    const uint32_t ret = it->impl(s, argv);
    g_current_rec = prev_rec;
    g_current = prev_current;
    g_current_caller = prev_caller;
    /* This call's own slot, not whatever the ring's head is now: a nested call
     * (DispatchMessageW into the guest's window procedure) moves the head on,
     * and the ring can wrap past this slot while the nested calls run. The
     * sequence number is what makes the slot still this call's; without it the
     * return would be attributed to whichever call now owns the slot, and a
     * `ret` on the wrong row reads exactly like a right one. */
    if (mine && mine->seq == mine_seq) { mine->ret = ret; mine->ret_valid = 1; }
    if (rec) rec->ret = ret;
    sa2_rt_set_gpr(s, SA2_EAX, ret);
    /* The callee's `ret`, plus stdcall's argument cleanup. */
    uint32_t pop = 4u + (it->info.conv == SA2_IMP_STDCALL ? 4u * it->info.argc : 0u);
    sa2_rt_set_gpr(s, SA2_ESP, esp + pop);
    return m;
}

/* ---------------------------------------------------------------- bind */

void sa2_imports_config_from_env(void) {
    const char *v = getenv("SA2_IMPORT_BIND");
    if (v && *v) g_bind_enabled = atoi(v) != 0;
    v = getenv("SA2_GUEST_CWD");
    if (v && *v) snprintf(g_cwd, sizeof g_cwd, "%s", v);
    v = getenv("SA2_GUEST_EXE_PATH");
    if (v && *v) snprintf(g_exe_path, sizeof g_exe_path, "%s", v);
    /* The guest file system takes its guest-side root from the same string
     * GetCurrentDirectoryA returns, so the two can never disagree: if the cwd
     * moves, what `C:\\SA2` means moves with it. */
    sa2_vfs_config(g_cwd);
    /* The window model and the pump's host controls: SA2_PUMP_POLLS,
     * SA2_PUMP_INJECT, SA2_WND_SCREEN. */
    sa2_window_config_from_env();
    /* SA2_DIALOG_STRICT: every fabricated dialog or clipboard success refuses
     * instead, so a run shows whether the guest depends on one. */
    sa2_dialog_config_from_env();
    sa2_gdi_config_from_env();
    /* The capture's device profile, read once and before every module that
     * reads it: the processor count and masks right below, and the XInput
     * stub, whose SA2_XINPUT_PAD refusal reads it at its configuration. With
     * no SA2_DEVICE_PROFILE it is the default profile, a plain keyboard and
     * mouse, one processor (runtime/sa2_devprofile.h). */
    sa2_devprofile_config_from_env();
    g_cpus = sa2_dp_processors();
    g_process_mask = sa2_dp_process_mask();
    g_system_mask = sa2_dp_system_mask();
    sa2_iface_config_from_env();
    sa2_d3d9_config_from_env();
    sa2_dsound_config_from_env();
    /* After the profile: it refuses a capture's profile with the device off
     * (runtime/sa2_ddraw.h, Environment). */
    sa2_ddraw_config_from_env();
    /* After the profile: it answers the mastering voice's details from it, and
     * refuses a capture's profile with the device off (runtime/sa2_xaudio2.h). */
    sa2_xaudio2_config_from_env();
    sa2_dinput_config_from_env();
    sa2_wbem_config_from_env();
    v = getenv("SA2_PRESENT_SURVEY");
    if (v && *v) g_present_survey_budget = (int32_t)strtol(v, NULL, 0);
    /* The virtual epoch, in FILETIME units. Moving it moves every derived time
     * value, which is how a probe shows that its oracle is recomputing the
     * guest's arithmetic rather than carrying the answer. */
    v = getenv("SA2_VIRTUAL_EPOCH");
    if (v && *v) g_epoch_100ns = strtoull(v, NULL, 0);
    /* QueryPerformanceFrequency's break (see SA2_QPC_HZ): `qpf` answers the
     * placeholder before 2026-09-27, and the counter follows it, so counter over
     * frequency stays the virtual elapsed time either way. */
    v = getenv("SA2_TIME_BREAK");
    if (v && *v) {
        if (strcmp(v, "qpf")) {
            sa2_trap_note("SA2_TIME_BREAK=%s: the one break is `qpf`, QueryPerformanceFrequency "
                          "answering 3,579,545 as before 2026-09-27", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
        }
        g_qpc_hz = SA2_QPC_HZ_BEFORE;
    }
    v = getenv("SA2_IMPORT_BUDGET");
    if (v && *v) g_call_budget = (uint32_t)strtoul(v, NULL, 0);
    v = getenv("SA2_GUEST_PID");
    if (v && *v) g_pid = (uint32_t)strtoul(v, NULL, 0);
    sa2_threads_config_from_env();
    sa2_stall_start();             /* SA2_STALL_DUMP_MS: the stall watch's thread (sa2_stall.h) */
    v = getenv("SA2_GUEST_TID");
    if (v && *v) { g_tid = (uint32_t)strtoul(v, NULL, 0); sa2_sched_set_base_tid(g_tid); }
    v = getenv("SA2_GUEST_CMDLINE");
    if (v) snprintf(g_cmdline_tail, sizeof g_cmdline_tail, "%s", v);
    v = getenv("SA2_GUEST_ACP");
    if (v && *v) g_acp = (uint32_t)strtoul(v, NULL, 0);
    v = getenv("SA2_GUEST_CPUS");
    if (v && *v) {
        /* An experiment's count, for runs on the default profile. A capture's
         * replay answers the capture's count, and a count beside it would be a
         * machine the capture never ran on: refused. The replay tools strip
         * the variable (tools/s4/device_profile.py, clean_environ). */
        if (!sa2_devprofile_is_default()) {
            sa2_trap_note("SA2_GUEST_CPUS is set and device profile %s is a capture's: the "
                          "processor count is the capture's", sa2_devprofile_source());
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
        }
        g_cpus = (uint32_t)strtoul(v, NULL, 0);
        g_process_mask = g_system_mask = cpu_mask(g_cpus);
    }
    /* The character data's break and its recorder (see "The character data"). */
    v = getenv("SA2_CTYPE_BREAK");
    if (v && *v) {
        if (!strcmp(v, "rules")) g_ctype_break = CTYPE_RULES;
        else if (!strcmp(v, "nobestfit")) g_ctype_break = CTYPE_NOBESTFIT;
        else {
            sa2_trap_note("SA2_CTYPE_BREAK=%s: the breaks are `rules` (the answers before "
                          "2026-09-26) and `nobestfit` (Wine's data, converted back without "
                          "the best fit)", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
        }
    }
    v = getenv("SA2_CTYPE_ANSWERS");
    if (v && *v) {
        g_ctype_answers = fopen(v, "w");
        if (!g_ctype_answers) {
            sa2_trap_note("SA2_CTYPE_ANSWERS=%s cannot be opened for writing", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
        }
    }
    v = getenv("SA2_LOCALE_ANSWERS");
    if (v && *v) {
        g_locale_answers = fopen(v, "w");
        if (!g_locale_answers) {
            sa2_trap_note("SA2_LOCALE_ANSWERS=%s cannot be opened for writing", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
        }
    }
    v = getenv("SA2_GUEST_LCID");
    if (v && *v) g_lcid = (uint32_t)strtoul(v, NULL, 0);
    /* The locale (see "The locale"). Its break is read first, so a refused
     * override is refused whatever the break says. */
    v = getenv("SA2_LOCALE_BREAK");
    if (v && *v) {
        if (!strcmp(v, "refuse")) g_locale_break = LOCALE_BREAK_REFUSE;
        else if (!strcmp(v, "noenum")) g_locale_break = LOCALE_BREAK_NOENUM;
        else {
            sa2_trap_note("SA2_LOCALE_BREAK=%s: the breaks are `refuse` (every locale answer "
                          "as before 2026-09-26) and `noenum` (no locale enumerated)", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
        }
    }
    if (sa2_dp_locale_recorded()) {
        static const char *const OVERRIDES[2] = {"SA2_GUEST_LCID", "SA2_GUEST_ACP"};
        for (unsigned i = 0; i < 2u; i++) {
            const char *o = getenv(OVERRIDES[i]);
            if (o && *o) {
                sa2_trap_note("%s is set and device profile %s records the capture's locale: "
                              "the locale is the capture's", OVERRIDES[i],
                              sa2_devprofile_source());
                sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
            }
        }
        /* IsValidLocale answers from Wine's LCID index, and the capture's host
         * called its user's locale valid (the reader requires it): the two must
         * agree, or the index is not the capture's host's. */
        if (!sa2_locale_valid(sa2_dp_locale_lcid())) {
            sa2_trap_note("device profile %s records the user's locale 0x%04x, which the "
                          "runtime's locale data does not hold (runtime/sa2_locale.h)",
                          sa2_devprofile_source(), sa2_dp_locale_lcid());
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
        }
        if (g_locale_break != LOCALE_BREAK_REFUSE) {
            g_acp = sa2_dp_locale_acp();       /* 1252: the reader refuses any other */
            g_oemcp = sa2_dp_locale_oemcp();
        }
    }
    /* The environment block and the standard handles (see "GetEnvironmentStringsW"
     * and "The standard handles"). */
    v = getenv("SA2_ENV_BREAK");
    if (v && *v) {
        if (strcmp(v, "empty")) {
            sa2_trap_note("SA2_ENV_BREAK=%s: the one break is `empty`, the block before "
                          "profiles recorded the environment", v);
            sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
        }
        g_env_break = 1;
    }
    v = getenv("SA2_STDIO_BREAK");
    const int std_break = v && *v;
    if (std_break && strcmp(v, "pipe")) {
        sa2_trap_note("SA2_STDIO_BREAK=%s: the one break is `pipe`, the replay configuration's "
                      "standard handles before profiles recorded them", v);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
    }
    v = getenv("SA2_STD_HANDLES");
    if (sa2_dp_std_recorded() && v && *v) {
        sa2_trap_note("SA2_STD_HANDLES is set and device profile %s records the standard "
                      "handles: the handles are the capture's", sa2_devprofile_source());
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, SA2_GUEST_IMPORT_BASE);
    }
    if (std_break) g_std_mode = STD_PIPES;
    else if (sa2_dp_std_recorded()) g_std_mode = STD_PROFILE;
    else g_std_mode = v && !strcmp(v, "pipe") ? STD_PIPES : STD_NONE;
    v = getenv("SA2_GUEST_WINVER");     /* "major.minor.build" */
    if (v && *v) {
        unsigned ma = g_ver_major, mi = g_ver_minor, bu = g_ver_build;
        if (sscanf(v, "%u.%u.%u", &ma, &mi, &bu) >= 2) {
            g_ver_major = ma; g_ver_minor = mi; g_ver_build = bu;
        }
    }
}

int sa2_imports_bound(void) { return g_bound; }

int sa2_imports_bind(void) {
    if (g_bound) return (int)g_import_count;
    if (!g_bind_enabled) return 0;
    if (!sa2_guest.ready) return SA2_E_INIT;

    /* The PE headers as the loader mapped them, read out of guest memory: the
     * runtime does not open the file a second time and copies no bytes out of
     * the image. */
    const uint32_t ib = SA2_GUEST_IMAGE_BASE;
    uint32_t nt = ib + gpeek32(ib + 0x3Cu);
    if (gpeek32(nt) != 0x00004550u) {
        fprintf(stderr, "sa2_imports_bind: no PE signature at %#010x\n", nt);
        return SA2_E_IMAGE;
    }
    uint32_t opt = nt + 24u;                       /* COFF header is 20 bytes + 4 sig */
    uint32_t imp_rva = gpeek32(opt + 104u);        /* DataDirectory[1].VirtualAddress */
    if (!imp_rva) {
        fprintf(stderr, "sa2_imports_bind: the image has no import directory\n");
        return SA2_E_IMAGE;
    }

    uint32_t cap = 64, n = 0;
    import_slot *rows = (import_slot *)calloc(cap, sizeof *rows);
    if (!rows) return SA2_E_NOMEM;

    for (uint32_t d = ib + imp_rva; ; d += 20u) {
        uint32_t oft = gpeek32(d), name_rva = gpeek32(d + 12u), ft = gpeek32(d + 16u);
        if (!oft && !ft && !name_rva) break;
        char dll[128];
        if (gstr(ib + name_rva, dll, sizeof dll) < 0) { free(rows); return SA2_E_IMAGE; }
        const char *dll_s = intern(dll, strlen(dll));
        uint32_t names = oft ? oft : ft;
        for (uint32_t k = 0; ; k++) {
            uint32_t slot = ib + ft + 4u * k;
            uint32_t ent = gpeek32(ib + names + 4u * k);
            if (!ent) break;
            if (n == cap) {
                cap *= 2;
                import_slot *p = (import_slot *)realloc(rows, cap * sizeof *rows);
                if (!p) { free(rows); return SA2_E_NOMEM; }
                memset(p + n, 0, (cap - n) * sizeof *p);
                rows = p;
            }
            import_slot *it = &rows[n];
            it->info.dll = dll_s;
            it->info.slot = slot;
            it->info.unbound = gpeek32(slot);
            if (ent & 0x80000000u) {
                char buf[32];
                it->info.ordinal = (uint16_t)(ent & 0xFFFFu);
                snprintf(buf, sizeof buf, "Ordinal#%u", it->info.ordinal);
                it->info.name = intern(buf, strlen(buf));
            } else {
                char buf[256];
                if (gstr(ib + ent + 2u, buf, sizeof buf) < 0) { free(rows); return SA2_E_IMAGE; }
                it->info.name = intern(buf, strlen(buf));
            }
            n++;
        }
    }

    if (n > SA2_GUEST_IMPORT_SLOTS) {
        fprintf(stderr, "sa2_imports_bind: %u imports but only %u thunk slots at %#010x; "
                        "raise SA2_GUEST_IMPORT_SLOTS\n",
                n, SA2_GUEST_IMPORT_SLOTS, SA2_GUEST_IMPORT_BASE);
        free(rows);
        return SA2_E_RANGE;
    }

    /* 0xCC across the region, so a thunk address that is ever *decoded* rather
     * than dispatched shows up as int3 instead of as whatever was there. */
    if (sa2_guest_fill(SA2_GUEST_IMPORT_BASE,
                       0xCC, SA2_GUEST_IMPORT_SLOTS * SA2_GUEST_IMPORT_STRIDE) != SA2_OK) {
        fprintf(stderr, "sa2_imports_bind: the thunk region %#010x..%#010x is not mappable\n",
                SA2_GUEST_IMPORT_BASE, SA2_GUEST_IMPORT_END);
        free(rows);
        return SA2_E_RANGE;
    }

    for (uint32_t i = 0; i < n; i++) {
        import_slot *it = &rows[i];
        it->info.thunk = SA2_GUEST_IMPORT_BASE + i * SA2_GUEST_IMPORT_STRIDE;
        const stub_row *st = stub_for(it->info.dll, it->info.name);
        if (st) {
            it->impl = st->impl;
            it->info.argc = st->argc;
            it->info.conv = st->conv;
            it->info.implemented = 1;
        }
        if (sa2_poke(it->info.slot, 4, &it->info.thunk) != SA2_OK) {
            fprintf(stderr, "sa2_imports_bind: IAT slot %#010x is not writable guest memory\n",
                    it->info.slot);
            free(rows);
            return SA2_E_RANGE;
        }
    }

    g_imports = rows;
    g_import_count = n;
    for (uint32_t i = 0; i < n; i++) {
        if (sa2_ft_add(rows[i].info.thunk, import_native, rows[i].info.name, SA2_FN_NATIVE) < 0) {
            fprintf(stderr, "sa2_imports_bind: the function table refused row %u (%s!%s); "
                            "sa2_runtime_init needs more extra_entries\n",
                    i, rows[i].info.dll, rows[i].info.name);
            g_imports = NULL;
            g_import_count = 0;
            free(rows);
            return SA2_E_NOMEM;
        }
    }
    g_bound = 1;
    return (int)n;
}

/* ------------------------------------------------------------ accessors */

uint32_t sa2_import_count(void) { return g_import_count; }

const sa2_import_info *sa2_import_at(uint32_t i) {
    return i < g_import_count ? &g_imports[i].info : NULL;
}

const sa2_import_info *sa2_import_by_name(const char *dll, const char *name) {
    for (uint32_t i = 0; i < g_import_count; i++) {
        const sa2_import_info *in = &g_imports[i].info;
        if ((!dll || ieq(in->dll, dll)) && !strcmp(in->name, name)) return in;
    }
    return NULL;
}

const sa2_import_info *sa2_import_of_index(uint32_t index) {
    if (index >= SA2_GUEST_IMPORT_DYN_FIRST) {
        uint32_t d = index - SA2_GUEST_IMPORT_DYN_FIRST;
        return d < g_dyn_count ? &g_dyn[d].info : NULL;
    }
    return index < g_import_count ? &g_imports[index].info : NULL;
}

const sa2_import_info *sa2_import_for_thunk(uint32_t thunk) {
    if (thunk < SA2_GUEST_IMPORT_BASE || thunk >= SA2_GUEST_IMPORT_END) return NULL;
    if ((thunk - SA2_GUEST_IMPORT_BASE) % SA2_GUEST_IMPORT_STRIDE) return NULL;
    return sa2_import_of_index((thunk - SA2_GUEST_IMPORT_BASE) / SA2_GUEST_IMPORT_STRIDE);
}

uint32_t sa2_dynimport_count(void) { return g_dyn_count; }
const sa2_import_info *sa2_dynimport_at(uint32_t i) {
    return i < g_dyn_count ? &g_dyn[i].info : NULL;
}
uint32_t    sa2_dynimport_missing_count(void) { return g_dyn_missing_count; }
const char *sa2_dynimport_missing_at(uint32_t i) {
    return i < g_dyn_missing_count && i < SA2_DYN_MISS_MAX ? g_dyn_missing[i] : NULL;
}
uint32_t    sa2_loadlibrary_refused_count(void) { return g_loadlib_refused_count; }
const char *sa2_loadlibrary_refused_at(uint32_t i) {
    return i < g_loadlib_refused_count && i < SA2_LOADLIB_MAX ? g_loadlib_refused[i] : NULL;
}
const char *sa2_loadlibrary_refused_why(uint32_t i) {
    return i < g_loadlib_refused_count && i < SA2_LOADLIB_MAX ? g_loadlib_refused_why[i] : NULL;
}

uint32_t sa2_heap_alloc_owned(uint32_t owner, uint32_t bytes) {
    return heap_alloc(owner, bytes, HEAP_ZERO_MEMORY);
}

void sa2_heap_free_owned(uint32_t owner, uint32_t payload) {
    heap_free(owner, payload);     /* traps unless it is a live block of this owner */
}

uint32_t sa2_heap_alloc_module(uint32_t bytes) {
    return sa2_heap_alloc_owned(SA2_MODULE_OWNER, bytes);
}

void sa2_heap_stats(sa2_heap_stats_t *out) {
    if (!out) return;
    out->arena_base = SA2_HEAP_ARENA_BASE;
    out->arena_end = SA2_HEAP_ARENA_END;
    out->ready = (uint32_t)g_heap_ready;
    out->allocs = g_heap_allocs;
    out->frees = g_heap_frees;
    out->failures = g_heap_failures;
    out->fail_last_bytes = g_heap_fail_last;
    out->fail_max_bytes = g_heap_fail_max;
    out->live_blocks = g_heap_live_blocks;
    out->live_bytes = g_heap_live_bytes;
    out->peak_bytes = g_heap_peak_bytes;
    /* Walked from the arena, not accumulated, so the number is a reading of
     * the structure the guest can see rather than of a counter beside it. A
     * mismatch between `live_blocks` and `walk_inuse` is a corrupted heap. */
    out->walk_blocks = out->walk_inuse = out->walk_largest_free = 0;
    out->walk_inuse_bytes = 0;
    if (g_heap_ready) {
        for (uint32_t b = SA2_HEAP_ARENA_BASE; b < SA2_HEAP_ARENA_END; ) {
            uint32_t total = blk_total(b);
            if (!total) { out->walk_blocks = 0xffffffffu; break; }   /* a corrupt header */
            out->walk_blocks++;
            if (blk_inuse(b)) { out->walk_inuse++; out->walk_inuse_bytes += total; }
            else if (total > out->walk_largest_free) out->walk_largest_free = total;
            b += total;
        }
    }
}

uint32_t    sa2_msgbox_count(void) { return g_msgbox_count; }
const char *sa2_msgbox_text(uint32_t i) {
    return i < g_msgbox_count && i < SA2_MSGBOX_MAX ? g_msgbox_text[i] : "";
}
const char *sa2_msgbox_caption(uint32_t i) {
    return i < g_msgbox_count && i < SA2_MSGBOX_MAX ? g_msgbox_caption[i] : "";
}

uint32_t sa2_import_recent_count(void) {
    return g_recent_next < SA2_IMPORT_RECENT ? g_recent_next : SA2_IMPORT_RECENT;
}
const sa2_import_recent *sa2_import_recent_at(uint32_t i) {
    const uint32_t n = sa2_import_recent_count();
    if (i >= n) return NULL;
    const uint32_t first = g_recent_next < SA2_IMPORT_RECENT ? 0u : g_recent_next % SA2_IMPORT_RECENT;
    return &g_recent[(first + i) % SA2_IMPORT_RECENT];
}
const sa2_import_info *sa2_import_at_index(uint32_t index) {
    return index < g_import_count ? &g_imports[index].info : NULL;
}

uint32_t sa2_import_calls(void) { return g_calls; }
uint32_t sa2_import_calls_logged(void) { return g_log_used; }

const sa2_import_call *sa2_import_call_at(uint32_t i) {
    return i < g_log_used ? &g_log[i] : NULL;
}

void sa2_import_log_reset(void) { g_log_used = 0; g_calls = 0; }

uint32_t    sa2_import_last_error(void) { return g_last_error; }
int         sa2_import_exited(void) { return g_exited; }
uint32_t    sa2_import_exit_code(void) { return g_exit_code; }
uint64_t    sa2_import_virtual_ms(void) { return g_virtual_ms; }
const char *sa2_import_cwd(void) { return g_cwd; }
const char *sa2_import_exe_path(void) { return g_exe_path; }
