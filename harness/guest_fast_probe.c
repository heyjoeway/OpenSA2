/* The guest-memory fast path against the intrinsics, over the real runtime.
 *
 * tools/s2/corpus_run/state_local.py ("The guest-memory fast path") makes a localized
 * function's one- to eight-byte guest accesses loads and stores through
 * `sa2_guest_fast_base`, and sends every access it does not make inline to
 * `sa2_guest_read_slow` or `sa2_guest_write_slow`. That is what the intrinsics do only while
 * the runtime keeps its side (runtime/guest_memory.h, "the inlined fast path"): the base set
 * while guest memory is mapped with the poison tier off and NULL otherwise, and the slow
 * halves the intrinsics' own checked path, which tests nothing the inline path skips. This
 * probe holds the runtime to both.
 *
 * Documentation/scripts/tests/test_state_local.py builds it over runtime/guest_memory.c,
 * remill_runtime.cpp, sa2_trap.c and function_table_impl.c, each compiled by the link's own
 * command (tools/s4/link_s4.py `phase_compile`), with the pass's helpers exactly as it splices
 * them behind `wread` and `wwrite` (the test's `_fast_wrappers`, compiled by the fold's
 * command), and runs it twice on a small PE32 the test writes:
 *
 *   intrinsic  every access through __remill_read_memory_* and __remill_write_memory_*
 *   fast       every access through the pass's helpers
 *
 * Four rounds, each a fresh `sa2_guest_init` under one poison mode (0, 2, 1, 0), make the
 * twelve accesses at every address of ADDRS: the null guard's edge and the end's, past 2^31,
 * and every kind of page the PE leaves -- headers, code, file-backed data, the .bss tail past
 * it, pages nothing wrote -- with reads before writes, so the poison tier meets unwritten
 * pages. One line per access: the trap's status, exit_pc and note, the value read (or
 * whether a write returned its Memory token), the guest bytes around the address after it,
 * the poison counters, and whether the access's first and last pages count as written. The
 * two outputs must be byte-identical. Around the rounds, one line per point of the base's
 * lifecycle: NULL before init, after a failed init, while a poison mode is on (set before
 * init, as harness/plan_driver.c sets it, and while mapped) and after shutdown; the mapping
 * otherwise.
 *
 *   guest_fast_probe <pe> <pe the runtime refuses> intrinsic|fast */
#define _GNU_SOURCE
#include <setjmp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "guest_memory.h"
#include "sa2_trap.h"

struct Memory;
uint8_t  __remill_read_memory_8(struct Memory *, uint32_t);
uint16_t __remill_read_memory_16(struct Memory *, uint32_t);
uint32_t __remill_read_memory_32(struct Memory *, uint32_t);
uint64_t __remill_read_memory_64(struct Memory *, uint32_t);
float    __remill_read_memory_f32(struct Memory *, uint32_t);
double   __remill_read_memory_f64(struct Memory *, uint32_t);
struct Memory *__remill_write_memory_8(struct Memory *, uint32_t, uint8_t);
struct Memory *__remill_write_memory_16(struct Memory *, uint32_t, uint16_t);
struct Memory *__remill_write_memory_32(struct Memory *, uint32_t, uint32_t);
struct Memory *__remill_write_memory_64(struct Memory *, uint32_t, uint64_t);
struct Memory *__remill_write_memory_f32(struct Memory *, uint32_t, float);
struct Memory *__remill_write_memory_f64(struct Memory *, uint32_t, double);

/* The pass's helpers: `op` is the index into state_local.FAST_ACCESS, the six reads (8, 16,
 * 32 and 64 bits, float, double) and then the six writes in the same widths. A read returns
 * the bits it read; a write stores the low bits of `v` and returns the helper's result. */
uint64_t wread(int op, uint32_t a);
struct Memory *wwrite(int op, uint32_t a, uint64_t v, struct Memory *m);

#define OPS 12
static const unsigned SIZE[OPS] = {1, 2, 4, 8, 4, 8, 1, 2, 4, 8, 4, 8};
static const char *const NAME[OPS] = {"r8", "r16", "r32", "r64", "rf32", "rf64",
                                      "w8", "w16", "w32", "w64", "wf32", "wf64"};
/* What each write stores; the float and double ones are signalling NaNs' bits, which no
 * store may quiet. */
static const uint64_t STORED[OPS] = {0, 0, 0, 0, 0, 0, 0xA5u, 0xA55Au, 0xA55A1234u,
                                     0x0123456789ABCDEFull, 0x7FA00001u, 0x7FF4000000000001ull};

#define END SA2_GUEST_END
static const uint32_t ADDRS[] = {
    0x00000000u, 0x00000001u, 0x00000FF8u, 0x00000FFCu, 0x00000FFEu, 0x00000FFFu,  /* guard */
    0x00001000u, 0x00001001u,                     /* the first page past it: nothing wrote */
    0x00200000u,                                  /* the import thunks: nothing wrote */
    0x00400000u, 0x00400FFEu,                     /* headers, and straddling into code */
    0x00401000u, 0x00401007u,                     /* code */
    0x00402000u, 0x00402FFEu,                     /* file-backed data, straddling into .bss */
    0x00403000u, 0x00404FFCu,                     /* .bss past the raw data: nothing wrote */
    0x02800000u, 0x03F00000u, 0x0400FFFCu,        /* thread stacks, thread 0's TEB, heap */
    END - 16, END - 9, END - 8, END - 7, END - 5, END - 4, END - 3, END - 2, END - 1, END,
    END + 1, 0x7FFFFFFFu, 0x80000000u, 0xFFFFF000u, 0xFFFFFFF0u, 0xFFFFFFF8u, 0xFFFFFFFCu,
    0xFFFFFFFFu};
#define NADDR (sizeof ADDRS / sizeof *ADDRS)

static struct Memory *const TOKEN = (struct Memory *)(uintptr_t)0x1234u;
static int g_fast, g_op;
static uint32_t g_addr;
static uint64_t g_value;
static struct Memory *g_returned;

static uint64_t intrinsic_read(int op, uint32_t a) {
    switch (op) {
    case 0: return __remill_read_memory_8(TOKEN, a);
    case 1: return __remill_read_memory_16(TOKEN, a);
    case 2: return __remill_read_memory_32(TOKEN, a);
    case 3: return __remill_read_memory_64(TOKEN, a);
    case 4: { float f = __remill_read_memory_f32(TOKEN, a); uint32_t u; memcpy(&u, &f, 4); return u; }
    default: { double d = __remill_read_memory_f64(TOKEN, a); uint64_t u; memcpy(&u, &d, 8); return u; }
    }
}

static struct Memory *intrinsic_write(int op, uint32_t a, uint64_t v) {
    switch (op) {
    case 6: return __remill_write_memory_8(TOKEN, a, (uint8_t)v);
    case 7: return __remill_write_memory_16(TOKEN, a, (uint16_t)v);
    case 8: return __remill_write_memory_32(TOKEN, a, (uint32_t)v);
    case 9: return __remill_write_memory_64(TOKEN, a, v);
    case 10: { uint32_t u = (uint32_t)v; float f; memcpy(&f, &u, 4); return __remill_write_memory_f32(TOKEN, a, f); }
    default: { double d; memcpy(&d, &v, 8); return __remill_write_memory_f64(TOKEN, a, d); }
    }
}

static void access_once(void) {
    if (g_op < 6)
        g_value = g_fast ? wread(g_op, g_addr) : intrinsic_read(g_op, g_addr);
    else
        g_returned = g_fast ? wwrite(g_op, g_addr, STORED[g_op], TOKEN)
                            : intrinsic_write(g_op, g_addr, STORED[g_op]);
}

/* One access inside a trap window, as sa2_call makes a call: the trap's status, 0 if none. */
static int run_access(void) {
    sa2_trap_clear();
    if (sigsetjmp(*sa2_trap_jmpbuf(), 1) == 0) {
        sa2_trap_window(1);
        access_once();
        sa2_trap_window(0);
        return 0;
    }
    sa2_trap_window(0);
    return (int)sa2_trap_last()->status;
}

/* The guest bytes from a-2 to a+9, ".." where sa2_peek refuses one. */
static void bytes_around(uint32_t a, char *out, size_t n) {
    size_t k = 0;
    out[0] = 0;
    for (int64_t i = -2; i < 10 && k + 3 < n; i++) {
        uint8_t b;
        uint32_t x = (uint32_t)((int64_t)a + i);
        k += (size_t)(sa2_peek(x, 1, &b) == SA2_OK ? snprintf(out + k, n - k, "%02x", b)
                                                    : snprintf(out + k, n - k, ".."));
    }
}

/* '1' when the page holding guest byte x counts as written, '0' when it is poison, '-'
 * when x is outside the guest space. */
static char page_mark(uint64_t x) {
    if (x >= END) return '-';
    return sa2_guest_is_poison((uint32_t)x, 1) ? '0' : '1';
}

static void one_case(int round, const char *mode, int op, uint32_t a) {
    sa2_guest_poison_reads = 0;
    sa2_guest_poison_first = 0;
    g_op = op;
    g_addr = a;
    g_value = 0;
    g_returned = NULL;
    const int status = run_access();
    const sa2_trap_t *t = sa2_trap_last();
    char value[40] = "", around[40];
    if (!status && op < 6)
        snprintf(value, sizeof value, "%0*llx", (int)(2 * SIZE[op]), (unsigned long long)g_value);
    else if (!status)
        snprintf(value, sizeof value, "token %s", g_returned == TOKEN ? "kept" : "LOST");
    bytes_around(a, around, sizeof around);
    printf("round=%d poison=%s %s a=%#010x status=%d exit_pc=%#010x note=[%s] value=[%s] "
           "after=[%s] poison_reads=%llu poison_first=%#010x pages=%c%c\n",
           round, mode, NAME[op], a, status, status ? t->exit_pc : 0u, status ? t->note : "",
           value, around, (unsigned long long)sa2_guest_poison_reads, sa2_guest_poison_first,
           page_mark(a), page_mark((uint64_t)a + SIZE[op] - 1));
}

static int g_problems;

/* One point of the base's lifecycle: what the base is, and whether that is what it should
 * be (the mapping when `mapped`, NULL otherwise). */
static void lifecycle(const char *when, int mapped) {
    const char *is = sa2_guest_fast_base == NULL ? "NULL"
                   : sa2_guest_fast_base == sa2_guest.base ? "the mapping" : "another pointer";
    const int ok = mapped ? sa2_guest_fast_base != NULL && sa2_guest_fast_base == sa2_guest.base
                          : sa2_guest_fast_base == NULL;
    printf("lifecycle %s: %s%s\n", when, is, ok ? "" : " (WRONG)");
    g_problems += !ok;
}

static void set_poison(const char *mode) {
    setenv("SA2_GUEST_POISON", mode, 1);
    sa2_guest_poison_from_env();
}

int main(int argc, char **argv) {
    if (argc < 4) {
        fprintf(stderr, "usage: guest_fast_probe <pe> <pe the runtime refuses> intrinsic|fast\n");
        return 2;
    }
    setvbuf(stdout, NULL, _IOFBF, 1 << 16);
    g_fast = strcmp(argv[3], "fast") == 0;
    sa2_trap_install_handlers();

    lifecycle("before init", 0);
    set_poison("0");
    lifecycle("poison 0, before init", 0);
    if (sa2_guest_init("/nonexistent/sa2-guest-fast-probe.exe") == SA2_OK) return 2;
    lifecycle("after a failed init (no file)", 0);
    if (sa2_guest_init(argv[2]) == SA2_OK) return 2;
    lifecycle("after a failed init (refused image)", 0);

    static const char *const MODES[] = {"0", "2", "1", "0"};
    for (int r = 0; r < 4; r++) {
        char when[64];
        set_poison(MODES[r]);
        if (sa2_guest_init(argv[1]) != SA2_OK) { fprintf(stderr, "guest init failed\n"); return 2; }
        snprintf(when, sizeof when, "round %d, poison %s, after init", r, MODES[r]);
        lifecycle(when, MODES[r][0] == '0');
        for (int op = 0; op < OPS; op++)
            for (unsigned i = 0; i < NADDR; i++) one_case(r, MODES[r], op, ADDRS[i]);
        sa2_guest_shutdown();
        snprintf(when, sizeof when, "round %d, after shutdown", r);
        lifecycle(when, 0);
    }

    /* The poison mode changed while guest memory is mapped. */
    set_poison("0");
    if (sa2_guest_init(argv[1]) != SA2_OK) { fprintf(stderr, "guest init failed\n"); return 2; }
    lifecycle("mapped, poison 0", 1);
    set_poison("2");
    lifecycle("mapped, poison set to 2", 0);
    set_poison("1");
    lifecycle("mapped, poison set to 1", 0);
    set_poison("0");
    lifecycle("mapped, poison set to 0", 1);
    sa2_guest_shutdown();
    lifecycle("after shutdown", 0);
    sa2_guest_shutdown();
    lifecycle("after a second shutdown", 0);

    printf("lifecycle problems: %d\n", g_problems);
    return g_problems != 0;
}
