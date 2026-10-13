/* The d3d9 object pool across its two blocks (runtime/sa2_d3d9.h), driven
 * through the real runtime's guest vtable thunks.
 *
 * Guest memory is a host window over 0x00000000..0x03300000 that refuses the
 * 4 KiB null guard, so both blocks, the vtables and the scratch below them are
 * real guest bytes. The guest heap is a bump allocator over
 * 0x00400000..0x02800000 that checks every free against what it handed out.
 * No shipped bytes, native DLL, game run, player or GPU.
 *
 * Modes (argv[1]); each writes RESULTS.json (argv[2]) and exits 1 on a failed
 * check. A host fault (SIGSEGV or SIGBUS: a runtime reading past its own
 * arrays) stops the mode and is a failed check too, named by the observation
 * it struck (or host-fault outside one) and recorded in host_fault, so a
 * mutant that crashes says where:
 *
 *   first  boot a device and build exactly the first block's 16384 objects by
 *          a fixed recipe -- vertex buffers, textures with level surfaces, a
 *          constant table, two of every three released to destruction -- and
 *          report the block's bytes, every object's address, and the table's
 *          handle. It uses nothing the overflow block added, so it also builds
 *          against the runtime as it was before the block existed, and the two
 *          reports must be equal: the first 16384 objects are where they were,
 *          with the bytes they had.
 *   pool   the same, then the overflow block: a texture at the last
 *          first-block index whose level surfaces cross the boundary, a slot
 *          never reused after a final release, a texture past index 65535,
 *          every index to the pool's capacity and one past it, and a walk of
 *          every object's four words.
 *   wide   SA2_D3D9_BREAK=wideobj: 4096 objects 64 bytes apart in the first
 *          block and the 4097th refused; the overflow block stays unwritten.
 *   table  constant tables past the first block: one at index 20000 whose
 *          handle carries that index, one at 32767, and a refusal by name at
 *          32768 that leaves the pool able to build the next object.
 */
#include <setjmp.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "../runtime/cpu_state.h"
#include "../runtime/function_table.h"
#include "../runtime/guest_memory.h"
#include "../runtime/remill_runtime.h"
#include "../runtime/sa2_d3d9.h"
#include "../runtime/sa2_imports.h"
#include "../runtime/sa2_threads.h"
#include "../runtime/sa2_trap.h"
#include "../runtime/sa2_window.h"

/* ------------------------------------------------------------ the stubs */

#define GUEST_HI   0x03300000u    /* both blocks, the vtables and the scratch below */
#define NULL_TOP   0x00001000u
#define HEAP_LO    0x00400000u
#define HEAP_HI    0x02800000u
#define STACK_TOP  0x030eff00u
#define SCRATCH    0x03004000u

static uint8_t *g_mem;

static int in_window(uint32_t addr, uint32_t len) {
    return addr >= NULL_TOP && addr < GUEST_HI && len <= GUEST_HI - addr;
}
int sa2_peek(uint32_t addr, uint32_t len, void *out) {
    if (!in_window(addr, len)) return SA2_E_RANGE;
    memcpy(out, g_mem + addr, len);
    return SA2_OK;
}
int sa2_poke(uint32_t addr, uint32_t len, const void *in) {
    if (!in_window(addr, len)) return SA2_E_RANGE;
    memcpy(g_mem + addr, in, len);
    return SA2_OK;
}
int sa2_guest_fill(uint32_t addr, int byte, uint32_t len) {
    if (!in_window(addr, len)) return SA2_E_RANGE;
    memset(g_mem + addr, byte, len);
    return SA2_OK;
}

/* A bump heap: allocations are in address order, so a free finds its block by
 * bisection, and a free of anything it did not hand out, or twice, or by
 * another owner, is an error the results report. */
#define HEAP_MAX 400000u
struct allocation { uint32_t owner, addr, bytes, freed; };
static struct allocation *HEAP;
static uint32_t HEAP_N, HEAP_FREES, HEAP_NEXT = HEAP_LO, HEAP_ERRORS;
uint32_t sa2_heap_alloc_owned(uint32_t owner, uint32_t bytes) {
    if (!bytes || HEAP_N >= HEAP_MAX || HEAP_NEXT >= HEAP_HI || bytes > HEAP_HI - HEAP_NEXT)
        return 0;
    const uint32_t addr = HEAP_NEXT;
    HEAP_NEXT = (addr + bytes + 7u) & ~7u;
    HEAP[HEAP_N++] = (struct allocation){owner, addr, bytes, 0u};
    sa2_guest_fill(addr, 0, bytes);
    return addr;
}
void sa2_heap_free_owned(uint32_t owner, uint32_t payload) {
    uint32_t lo = 0, hi = HEAP_N;
    while (lo < hi) {
        const uint32_t mid = lo + (hi - lo) / 2u;
        if (HEAP[mid].addr < payload) lo = mid + 1u; else hi = mid;
    }
    if (lo == HEAP_N || HEAP[lo].addr != payload || HEAP[lo].owner != owner || HEAP[lo].freed) {
        HEAP_ERRORS++;
        return;
    }
    HEAP[lo].freed = 1u;
    HEAP_FREES++;
}
static uint32_t heap_active(void) { return HEAP_N - HEAP_FREES; }

#define THUNKS (SA2_D3D9_MAX_TYPES * SA2_D3D9_MAX_SLOTS)
static sa2_lifted_fn g_fn[THUNKS];
int sa2_ft_add(uint32_t va, sa2_lifted_fn fn, const char *name, uint8_t kind) {
    (void)name; (void)kind;
    if (va < SA2_D3D9_THUNK_BASE || va >= SA2_D3D9_THUNK_END) return -1;
    g_fn[(va - SA2_D3D9_THUNK_BASE) / SA2_D3D9_THUNK_STRIDE] = fn;
    return 0;
}
sa2_lifted_fn sa2_ft_lookup(uint32_t va) {
    if (va < SA2_D3D9_THUNK_BASE || va >= SA2_D3D9_THUNK_END ||
        (va - SA2_D3D9_THUNK_BASE) % SA2_D3D9_THUNK_STRIDE) return NULL;
    return g_fn[(va - SA2_D3D9_THUNK_BASE) / SA2_D3D9_THUNK_STRIDE];
}

typedef struct { uint32_t gpr[SA2_GPR_COUNT]; uint16_t cw; } probe_state;
uint32_t sa2_rt_get_gpr(const void *state, int idx) {
    return ((const probe_state *)state)->gpr[idx];
}
void sa2_rt_set_gpr(void *state, int idx, uint32_t value) {
    ((probe_state *)state)->gpr[idx] = value;
}
uint16_t sa2_rt_get_cw(const void *state) { return ((const probe_state *)state)->cw; }
void sa2_rt_set_cw(void *state, uint16_t cw) { ((probe_state *)state)->cw = cw; }
void sa2_rt_set_host_round(int mode) { (void)mode; }

uint64_t sa2_sched_budget_us(void) { return 0; }
int sa2_sched_budget_hit(void) { return 0; }
void sa2_sched_frame_boundary(void) {}
uint64_t sa2_sched_frames(void) { return 0; }
uint32_t sa2_window_screen_w(void) { return 640u; }
uint32_t sa2_window_screen_h(void) { return 480u; }

static char g_note[1024];
static jmp_buf TRAP_ENV;
static int TRAP_ARMED;
static uint32_t TRAP_STATUS, TRAP_PC;
void sa2_trap_note(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); vsnprintf(g_note, sizeof g_note, fmt, ap); va_end(ap);
}
void sa2_trap_raise(uint32_t status, uint32_t pc) {
    TRAP_STATUS = status; TRAP_PC = pc;
    if (TRAP_ARMED) longjmp(TRAP_ENV, 1);
    fprintf(stderr, "unexpected product trap %u at %#x: %s\n", status, pc, g_note);
    exit(3);
}

/* ------------------------------------------------------------ the checks */

static uint32_t g_checks, g_fails;
static char g_failed[256][64];
static void check(int cond, const char *id, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));
static void check(int cond, const char *id, const char *fmt, ...) {
    g_checks++;
    if (cond) return;
    if (g_fails < 256) snprintf(g_failed[g_fails], sizeof g_failed[0], "%s", id);
    g_fails++;
    char msg[512];
    va_list ap; va_start(ap, fmt); vsnprintf(msg, sizeof msg, fmt, ap); va_end(ap);
    printf("FAIL %s: %s\n", id, msg);
}

struct observation { const char *id; uint32_t n, v[16]; };
static struct observation OBS[128];
static uint32_t OBS_N;
static void observe(const char *id, uint32_t n, const uint32_t *v) {
    if (OBS_N >= 128u || n > 16u) exit(2);
    OBS[OBS_N].id = id; OBS[OBS_N].n = n; memcpy(OBS[OBS_N++].v, v, n * 4u);
}
#define OBSERVE(id, ...) observe(id, sizeof((uint32_t[]){__VA_ARGS__}) / 4u, (uint32_t[]){__VA_ARGS__})

/* A host fault inside an observation is that observation's failure: g_at names
 * the observation under way (NULL outside one), and main records the fault
 * against it and writes the results. */
static sigjmp_buf FAULT_ENV;
static const char *volatile g_at;
static volatile sig_atomic_t g_fault;
static const char *g_fault_at;
static void on_fault(int sig) {
    g_fault = sig;
    siglongjmp(FAULT_ENV, 1);
}

static void pk32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static uint32_t peek32(uint32_t a) {
    uint32_t v = 0;
    return sa2_peek(a, 4, &v) == SA2_OK ? v : 0xDEADBEEFu;
}

#define RETADDR 0x00401000u
#define CALLER  0x00866a5cu
#define PP      (SCRATCH + 0x0000u)
#define OUT     (SCRATCH + 0x0104u)
#define DESC    (SCRATCH + 0x0204u)
#define LOCK    (SCRATCH + 0x0304u)
#define NAME    (SCRATCH + 0x0400u)
#define SHADER  (SCRATCH + 0x1000u)
#define TAG     0x44334439u          /* 'D3D9', runtime/sa2_d3d9.c obj_new */

/* Call vtable slot `slot` of `self` as lifted code does: `this` and the
 * arguments on the guest stack under a return address. */
static uint32_t call_slot(uint32_t self, uint32_t slot, uint32_t argc, const uint32_t *argv) {
    const uint32_t thunk = peek32(peek32(self) + 4u * slot);
    probe_state st;
    memset(&st, 0, sizeof st);
    const uint32_t esp0 = STACK_TOP - 4u * (argc + 2u);
    st.gpr[SA2_ESP] = esp0;
    pk32(esp0, RETADDR);
    pk32(esp0 + 4u, self);
    for (uint32_t i = 0; i < argc; i++) pk32(esp0 + 8u + 4u * i, argv[i]);
    sa2_lifted_fn fn = (thunk >= SA2_D3D9_THUNK_BASE && thunk < SA2_D3D9_THUNK_END)
                           ? g_fn[(thunk - SA2_D3D9_THUNK_BASE) / SA2_D3D9_THUNK_STRIDE] : NULL;
    if (!fn) {
        fprintf(stderr, "slot %u of %#010x names %#010x, which no thunk answers\n", slot, self,
                thunk);
        exit(2);
    }
    fn(&st, thunk, NULL);
    if (st.gpr[SA2_ESP] - esp0 != 4u * (argc + 2u)) {
        fprintf(stderr, "slot %u of %#010x popped %u bytes\n", slot, self, st.gpr[SA2_ESP] - esp0);
        exit(2);
    }
    return st.gpr[SA2_EAX];
}
#define CALL0(self, slot) call_slot(self, slot, 0, NULL)
#define CALL(self, slot, n, ...) call_slot(self, slot, n, (const uint32_t[]){__VA_ARGS__})

static sa2_d3d9_stats stats(void) { sa2_d3d9_stats s; sa2_d3d9_stats_get(&s); return s; }
static uint32_t built(void) { return stats().objects_built; }

static uint32_t g_dev;
static void device(const char *brk) {
    setenv("SA2_D3D9_DEVICE", "1", 1);
    if (brk) setenv("SA2_D3D9_BREAK", brk, 1); else unsetenv("SA2_D3D9_BREAK");
    setenv("SA2_D3DX_STRICT", "0", 1);
    setenv("SA2_D3DX_CONSTANT_TABLE", "1", 1);
    sa2_d3d9_config_from_env();
    const uint32_t d3d9 = sa2_d3d9_make_d3d9(0x00401234u);
    const uint32_t pp[14] = {640u, 480u, 22u, 1u, 0u, 0u, 1u, 0u, 1u, 1u, 75u, 0u, 0u, 0u};
    sa2_poke(PP, sizeof pp, pp);
    check(CALL(d3d9, 16u, 6u, 0u, 1u, 0u, 0x40u, PP, OUT) == 0u, "device", "CreateDevice failed");
    g_dev = peek32(OUT);
}

/* IDirect3DDevice9::CreateVertexBuffer(16, 0, 0, D3DPOOL_MANAGED): one object. */
static uint32_t vb(void) {
    pk32(OUT, 0u);
    check(CALL(g_dev, 26u, 6u, 16u, 0u, 0u, 1u, OUT, 0u) == 0u, "create-vb", "failed");
    return peek32(OUT);
}
/* IDirect3DDevice9::CreateTexture(4, 4, levels, 0, A8R8G8B8, MANAGED): one object. */
static uint32_t tex(uint32_t levels) {
    pk32(OUT, 0u);
    check(CALL(g_dev, 23u, 8u, 4u, 4u, levels, 0u, 21u, 1u, OUT, 0u) == 0u, "create-tex", "failed");
    return peek32(OUT);
}
static uint32_t level(uint32_t t, uint32_t lv) {
    pk32(OUT, 0u);
    check(CALL(t, 18u, 2u, lv, OUT) == 0u, "get-surface-level", "level %u of %#x", lv, t);
    return peek32(OUT);
}
static uint32_t release(uint32_t o) { return CALL0(o, 2u); }

/* A made-up vertex shader whose CTAB comment declares one float constant,
 * "c0", in the layout harness/d3dx_table_probe.c's write_shader gives each of
 * its constants. */
static void write_shader(uint32_t at) {
    uint8_t d[96];
    memset(d, 0, sizeof d);
    uint32_t off = 28u + 20u;
    const uint32_t creator = off; memcpy(d + off, "pool probe", 11); off += 11u;
    const uint32_t target = off; memcpy(d + off, "vs_3_0", 7); off += 7u;
    const uint32_t name = off; memcpy(d + off, "c0", 3); off += 3u;
    const uint32_t type = off;
    const uint16_t ti[8] = {0u, 3u, 1u, 1u, 1u, 0u, 0u, 0u};
    memcpy(d + off, ti, 16u); off += 16u;
    const uint16_t set = 2u, reg = 0u, count = 1u;
    memcpy(d + 28u, &name, 4); memcpy(d + 32u, &set, 2); memcpy(d + 34u, &reg, 2);
    memcpy(d + 36u, &count, 2); memcpy(d + 40u, &type, 4);
    const uint32_t hdr[7] = {28u, creator, 0xFFFE0300u, 1u, 28u, 0u, target};
    memcpy(d, hdr, sizeof hdr);
    const uint32_t dwords = (off + 3u) / 4u;
    uint32_t w[40], n = 0;
    w[n++] = 0xFFFE0300u;
    w[n++] = ((dwords + 1u) << 16) | 0xFFFEu;
    w[n++] = 0x42415443u;                          /* 'CTAB' */
    memcpy(w + n, d, 4u * dwords); n += dwords;
    w[n++] = 0x0000FFFFu;
    sa2_poke(at, 4u * n, w);
}
static uint32_t table(void) {
    pk32(OUT, 0u);
    const uint32_t a[2] = {SHADER, OUT};
    check(sa2_d3d9_d3dx_get_shader_constant_table(a, CALLER) == 0u, "create-table", "failed");
    return peek32(OUT);
}
static uint32_t constant(uint32_t t) {
    sa2_poke(NAME, 3, "c0");
    return CALL(t, 9u, 2u, 0u, NAME);
}

/* ------------------------------------------------------- the pool's shape */

static uint64_t fnv(const uint8_t *p, size_t n) {
    uint64_t h = 1469598103934665603ull;
    for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 1099511628211ull; }
    return h;
}

/* The address the 16-byte layout gives index `i`, as sa2_d3d9.h states it, in
 * literal constants rather than through the header's helper, so a helper that
 * drifted from its comment fails here. */
static uint32_t expected_addr(uint32_t i) {
    return i < 16384u ? 0x03200000u + 16u * i : 0x00010000u + 16u * (i - 16384u);
}

/* Every object of indices [from, to) has the four words obj_new writes: its
 * type's vtable, the tag, its type, its index. Answers the failures. */
static uint32_t walk(uint32_t from, uint32_t to, uint32_t stride, uint32_t base) {
    uint32_t bad = 0;
    for (uint32_t i = from; i < to; i++) {
        const uint32_t a = stride == 16u ? expected_addr(i) : base + stride * i;
        uint32_t w[4];
        if (sa2_peek(a, 16, w) != SA2_OK || w[1] != TAG || w[3] != i ||
            w[2] >= SA2_D3D9_MAX_TYPES || w[0] != 0x03240000u + w[2] * 512u) {
            if (bad < 4u) printf("object %u at %#010x: %#x %#x %#x %#x\n", i, a, w[0], w[1], w[2], w[3]);
            bad++;
        }
    }
    return bad;
}

/* The first block, built by a fixed recipe from a fresh device: textures with
 * three level surfaces, released; vertex buffers, two of every three released
 * to destruction; one constant table, kept; and a two-level texture at index
 * 16383, kept, so the block ends full. Every object lands where the formula
 * says. Answers that texture. */
static uint32_t g_addrs[16384];
static uint32_t g_table, g_table_handle;
static uint32_t first_block(void) {
    device(NULL);
    write_shader(SHADER);
    uint32_t misplaced = 0, k = 0;
    for (uint32_t i = 0; i < built(); i++) g_addrs[i] = expected_addr(i);
    while (built() < 16383u) {
        const uint32_t at = built();
        if (at == 40u) {
            g_table = table();
            g_table_handle = constant(g_table);
            g_addrs[at] = g_table;
            misplaced += g_table != expected_addr(at);
        } else if (at % 97u == 0u && at + 4u < 16383u) {
            const uint32_t t = tex(3u);
            g_addrs[at] = t;
            uint32_t s[3];
            for (uint32_t lv = 0; lv < 3u; lv++) {
                s[lv] = level(t, lv);
                g_addrs[at + 1u + lv] = s[lv];
                misplaced += s[lv] != expected_addr(at + 1u + lv);
            }
            for (uint32_t lv = 0; lv < 3u; lv++) release(s[lv]);
            check(release(t) == 0u, "first-tex-release", "texture %#x kept a count", t);
            misplaced += t != expected_addr(at);
        } else {
            const uint32_t v = vb();
            g_addrs[at] = v;
            misplaced += v != expected_addr(at);
            if (k++ % 3u) check(release(v) == 0u, "first-vb-release", "%#x kept a count", v);
        }
    }
    const uint32_t t = tex(2u);
    g_addrs[16383] = t;
    misplaced += t != expected_addr(16383u);
    check(built() == 16384u, "first-block-full", "built %u", built());
    check(misplaced == 0u, "first-block-addresses", "%u objects not at 0x03200000 + 16 * index",
          misplaced);
    check(t == 0x0323FFF0u, "first-block-last", "index 16383 at %#x", t);
    check(g_table_handle == (0x80000000u | (40u << 16)), "first-table-handle", "handle %#x",
          g_table_handle);
    return t;
}
static uint64_t first_block_fnv(void) { return fnv(g_mem + 0x03200000u, 0x40000u); }

/* ------------------------------------------------------------ the modes */

static void report_first(void) {
    const sa2_d3d9_stats s = stats();
    const uint32_t bad = walk(0u, 16384u, 16u, 0u);
    check(bad == 0u, "first-block-words", "%u of 16384 objects lack their four words", bad);
    OBSERVE("first-block", s.objects_built, s.objects_destroyed, s.level_surfaces,
            bad, g_table, g_table_handle,
            (uint32_t)(first_block_fnv() >> 32), (uint32_t)first_block_fnv(),
            (uint32_t)(fnv((const uint8_t *)g_addrs, sizeof g_addrs) >> 32),
            (uint32_t)fnv((const uint8_t *)g_addrs, sizeof g_addrs));
}

#ifdef SA2_D3D9_OBJ_OVF_BASE
static void trapped_vb(uint32_t *status, uint32_t *pc) {
    TRAP_ARMED = 1; TRAP_STATUS = 0u; g_note[0] = 0;
    if (!setjmp(TRAP_ENV)) CALL(g_dev, 26u, 6u, 16u, 0u, 0u, 1u, OUT, 0u);
    TRAP_ARMED = 0;
    *status = TRAP_STATUS; *pc = TRAP_PC;
}

static void mode_pool(void) {
    check(SA2_D3D9_POOL_OBJECTS == 143360u && sa2_d3d9_obj_addr(16384u) == 0x00010000u &&
          sa2_d3d9_obj_index(0x00010000u) == 16384 && sa2_d3d9_obj_index(0x0323FFF0u) == 16383 &&
          sa2_d3d9_obj_index(0x00200000u) == -1 && sa2_d3d9_obj_index(0x03240000u) == -1 &&
          sa2_d3d9_obj_index(0x00010008u) == -1 && sa2_d3d9_obj_addr(143360u) == 0u,
          "header-helpers", "sa2_d3d9_obj_index/addr disagree with sa2_d3d9.h");
    const uint32_t t = first_block();
    report_first();
    const uint64_t first = first_block_fnv();
    const sa2_d3d9_stats a = stats();

    /* A texture at index 16383 whose level surfaces cross into the overflow
     * block, and the same surface on every GetSurfaceLevel. */
    const uint32_t s0 = level(t, 0u), s0b = level(t, 0u), s1 = level(t, 1u);
    check(s0 == 0x00010000u && s0b == s0 && s1 == 0x00010010u, "crossing-surfaces",
          "level 0 %#x then %#x, level 1 %#x", s0, s0b, s1);
    uint32_t w[4];
    sa2_peek(s0, 16, w);
    const uint32_t t_surf = w[2];
    check(w[0] == 0x03240000u + t_surf * 512u && w[1] == TAG && w[3] == 16384u,
          "crossing-words", "%#x %#x %#x %#x", w[0], w[1], w[2], w[3]);
    pk32(DESC + 24u, 0u);
    check(CALL(s0, 12u, 1u, DESC) == 0u && peek32(DESC + 24u) == 4u && peek32(DESC + 28u) == 4u,
          "crossing-desc", "level 0 is %ux%u", peek32(DESC + 24u), peek32(DESC + 28u));
    check(CALL(s0, 13u, 3u, LOCK, 0u, 0u) == 0u && peek32(LOCK) == 16u &&
          peek32(LOCK + 4u) >= HEAP_LO && peek32(LOCK + 4u) < HEAP_HI,
          "crossing-lock", "pitch %u bits %#x", peek32(LOCK), peek32(LOCK + 4u));
    pk32(peek32(LOCK + 4u), 0x5A5A5A5Au);
    CALL0(s0, 14u);
    const uint32_t r[4] = {release(s0), release(s0b), release(s1), release(t)};
    const sa2_d3d9_stats b = stats();
    check(r[0] == 3u && r[1] == 2u && r[2] == 1u && r[3] == 0u &&
          b.objects_destroyed - a.objects_destroyed == 3u && b.level_surfaces - a.level_surfaces == 2u,
          "crossing-lifetime", "counts %u %u %u %u, destroyed %u", r[0], r[1], r[2], r[3],
          b.objects_destroyed - a.objects_destroyed);
    OBSERVE("crossing", s0, s1, w[3], r[0], r[1], r[2], r[3], b.objects_destroyed - a.objects_destroyed);

    /* No slot is reused: the next object takes index 16386, and one built
     * after a final release takes the one after it. */
    const uint32_t v = vb();
    check(v == 0x00010020u && CALL0(v, 1u) == 2u && release(v) == 1u, "overflow-vb",
          "%#x", v);
    check(CALL(v, 13u, 1u, DESC) == 0u && peek32(DESC + 16u) == 16u, "overflow-vb-desc",
          "size %u", peek32(DESC + 16u));
    check(release(v) == 0u, "overflow-vb-release", "kept a count");
    const uint32_t after = vb();
    check(after == 0x00010030u, "no-reuse", "the object after a final release is at %#x", after);
    release(after);
    OBSERVE("no-reuse", v, after);

    /* A texture past index 65535: its level surface's index needs more than
     * sixteen bits, and GetSurfaceLevel must hand back the same one again. */
    while (built() < 70000u) release(vb());
    const uint32_t t2 = tex(2u), l2 = level(t2, 0u), l2b = level(t2, 0u);
    sa2_peek(l2, 16, w);
    check(t2 == 0x000E1700u && l2 == 0x000E1710u && l2b == l2 && w[2] == t_surf && w[3] == 70001u,
          "past-65535", "texture %#x level %#x then %#x (index %u)", t2, l2, l2b, w[3]);
    release(l2); release(l2b);
    check(release(t2) == 0u, "past-65535-release", "kept a count");
    OBSERVE("past-65535", t2, l2, l2b, w[3]);

    /* Every index to the capacity, then one past it. */
    while (built() < 143359u) release(vb());
    const uint32_t last = vb();
    sa2_peek(last, 16, w);
    check(last == 0x001FFFF0u && w[3] == 143359u, "last-index", "%#x index %u", last, w[3]);
    /* A Lock through a destroyed overflow object finds that object, destroyed,
     * and stops by name: its slot still names it. */
    TRAP_ARMED = 1; TRAP_STATUS = 0u; g_note[0] = 0;
    if (!setjmp(TRAP_ENV)) CALL(v, 11u, 4u, 0u, 16u, LOCK, 0u);
    TRAP_ARMED = 0;
    check(TRAP_STATUS == SA2_TRAP_CONTROL_LEFT && TRAP_PC == v &&
          strstr(g_note, "after its final release") != NULL, "destroyed-overflow-lock",
          "status %u pc %#x: %s", TRAP_STATUS, TRAP_PC, g_note);
    const uint32_t lock_status = TRAP_STATUS, lock_pc = TRAP_PC;
    const sa2_d3d9_stats full = stats();
    uint32_t status, pc;
    trapped_vb(&status, &pc);
    const sa2_d3d9_stats over = stats();
    check(status == SA2_TRAP_CONTROL_LEFT && pc == 0x03200000u &&
          strstr(g_note, "the fabricated-object pool of 143360 is exhausted") != NULL &&
          over.objects_built == 143360u, "exhausted", "status %u pc %#x built %u: %s", status, pc,
          over.objects_built, g_note);
    printf("EXHAUSTED: %s\n", g_note);

    const uint32_t bad = walk(0u, 143360u, 16u, 0u);
    check(bad == 0u, "every-object", "%u of 143360 objects lack their four words", bad);
    check(peek32(0x0000FFFCu) == 0u && peek32(0x00200000u) == 0u, "block-edges",
          "a word was written beside the overflow block");
    check(first_block_fnv() == first, "first-block-kept", "the first block's bytes moved");
    check(full.unknown_this == 0u && full.releases_past_zero == 0u && full.calls_on_destroyed == 1u,
          "pool-health", "unknown this %u, past zero %u, on destroyed %u", full.unknown_this,
          full.releases_past_zero, full.calls_on_destroyed);
    OBSERVE("capacity", last, w[3], full.objects_built, over.objects_built, status, pc, bad,
            lock_status, lock_pc, full.unknown_this, full.calls_on_destroyed);
}

static void mode_table(void) {
    device(NULL);
    write_shader(SHADER);
    while (built() < 20000u) release(vb());
    const uint32_t t1 = table(), h1 = constant(t1);
    pk32(DESC, 0u); pk32(DESC + 48u, 0u);
    const uint32_t d1 = CALL(t1, 6u, 3u, h1, DESC, DESC + 48u);
    check(t1 == 0x0001E200u && h1 == 0xCE200000u && d1 == 0u && peek32(DESC + 48u) == 1u,
          "table-20000", "table %#x handle %#x desc %#x count %u", t1, h1, d1, peek32(DESC + 48u));
    while (built() < 32767u) release(vb());
    const uint32_t t2 = table(), h2 = constant(t2);
    check(t2 == 0x0004FFF0u && h2 == 0xFFFF0000u, "table-32767", "table %#x handle %#x", t2, h2);
    const uint32_t before = built(), active = heap_active();
    TRAP_ARMED = 1; TRAP_STATUS = 0u; g_note[0] = 0;
    if (!setjmp(TRAP_ENV)) {
        const uint32_t a[2] = {SHADER, OUT};
        sa2_d3d9_d3dx_get_shader_constant_table(a, CALLER);
    }
    TRAP_ARMED = 0;
    check(TRAP_STATUS == SA2_TRAP_CONTROL_LEFT && strstr(g_note, "pool index 32768") != NULL &&
          strstr(g_note, "fifteen bits") != NULL && built() == before && heap_active() == active,
          "table-32768-refused", "status %u built %u->%u: %s", TRAP_STATUS, before, built(), g_note);
    printf("TABLE REFUSED: %s\n", g_note);
    const uint32_t next = vb();
    check(next == 0x00050000u, "after-table-refusal", "next object at %#x", next);
    OBSERVE("table", t1, h1, t2, h2, TRAP_STATUS, before, built(), next);
}

/* A cube texture's face surfaces take the pool indices after the cube's, so a cube
 * at 16383 puts all six in the overflow block and a cube at 70000 puts them past
 * 65535. Each face is asked for twice: the second GetCubeMapSurface must hand back
 * the surface the first built (m_CubeGetSurface's lvl entry, which holds the
 * surface's pool index, not one derived from the first block's address). */
static uint32_t cube(uint32_t levels) {
    pk32(OUT, 0u);
    /* CreateCubeTexture(EdgeLength 4, Levels, Usage 0, D3DFMT_R5G6B5, D3DPOOL_MANAGED, out, 0) */
    check(CALL(g_dev, 25u, 7u, 4u, levels, 0u, 21u, 1u, OUT, 0u) == 0u, "create-cube", "failed");
    return peek32(OUT);
}

static uint32_t face(uint32_t c, uint32_t f, uint32_t lv) {
    pk32(OUT, 0u);
    /* IDirect3DCubeTexture9::GetCubeMapSurface(FaceType, Level, out) */
    check(CALL(c, 18u, 3u, f, lv, OUT) == 0u, "get-cube-surface", "face %u level %u of %#x", f,
          lv, c);
    return peek32(OUT);
}

static void cube_at(uint32_t at, const char *id) {
    while (built() < at) release(vb());
    g_at = id;
    const uint32_t c = cube(1u);
    uint32_t s[6], s2[6], bad = 0;
    for (uint32_t f = 0; f < 6u; f++) s[f] = face(c, f, 0u);
    for (uint32_t f = 0; f < 6u; f++) s2[f] = face(c, f, 0u);
    for (uint32_t f = 0; f < 6u; f++) {
        uint32_t w[4];
        sa2_peek(s[f], 16, w);
        if (s[f] != expected_addr(at + 1u + f) || s2[f] != s[f] || w[3] != at + 1u + f ||
            w[1] != TAG)
            bad++;
    }
    const uint32_t b0 = built();
    for (uint32_t f = 0; f < 6u; f++) { release(s[f]); release(s2[f]); }
    const uint32_t rc = release(c);
    check(bad == 0u && b0 == at + 7u && rc == 0u, id, "%u bad faces, built %u, count %u", bad, b0,
          rc);
    OBSERVE(id, c, s[0], s[5], s2[5], bad, b0, rc);
    g_at = NULL;
}

static void mode_cube(void) {
    device(NULL);
    cube_at(16383u, "cube-crossing");
    cube_at(70000u, "cube-past-65535");
    const sa2_d3d9_stats s = stats();
    check(s.unknown_this == 0u && s.calls_on_destroyed == 0u && s.releases_past_zero == 0u,
          "cube-health", "unknown %u destroyed-calls %u past-zero %u", s.unknown_this,
          s.calls_on_destroyed, s.releases_past_zero);
}
#endif

static void mode_wide(void) {
    device("wideobj");
    const sa2_d3d9_stats s = stats();
    check(s.object_stride == 64u && s.object_fill == SA2_D3D9_FILL_SENTINEL, "wide-layout",
          "stride %u fill %u", s.object_stride, s.object_fill);
    uint32_t misplaced = 0, last = 0;
    while (built() < 4096u) {
        const uint32_t at = built();
        last = vb();
        misplaced += last != 0x03200000u + 64u * at;
        release(last);
    }
    TRAP_ARMED = 1; TRAP_STATUS = 0u; g_note[0] = 0;
    if (!setjmp(TRAP_ENV)) CALL(g_dev, 26u, 6u, 16u, 0u, 0u, 1u, OUT, 0u);
    TRAP_ARMED = 0;
    const uint32_t bad = walk(0u, 4096u, 64u, 0x03200000u);
    check(misplaced == 0u && last == 0x0323FFC0u && bad == 0u, "wide-addresses",
          "%u misplaced, last %#x, %u bad", misplaced, last, bad);
    check(TRAP_STATUS == SA2_TRAP_CONTROL_LEFT &&
          strstr(g_note, "the fabricated-object pool of 4096 is exhausted") != NULL &&
          built() == 4096u, "wide-exhausted", "status %u built %u: %s", TRAP_STATUS, built(), g_note);
    check(peek32(0x00010000u) == 0u && peek32(0x00010010u) == 0u, "wide-no-overflow",
          "the overflow block was written under a wide layout");
    printf("WIDE EXHAUSTED: %s\n", g_note);
    OBSERVE("wide", last, built(), TRAP_STATUS, misplaced, bad, peek32(0x00010000u));
}

static void write_results(const char *path, const char *mode) {
    FILE *f = fopen(path, "wx");
    if (!f) { perror(path); exit(2); }
    const sa2_d3d9_stats s = stats();
    fprintf(f, "{\"mode\":\"%s\",\"checks\":%u,\"failures\":%u,\"failed_ids\":[", mode, g_checks,
            g_fails);
    for (uint32_t i = 0; i < g_fails && i < 256u; i++) fprintf(f, "%s\"%s\"", i ? "," : "", g_failed[i]);
    fprintf(f, "],\"observations\":[");
    for (uint32_t i = 0; i < OBS_N; i++) {
        fprintf(f, "%s{\"id\":\"%s\",\"actual\":[", i ? "," : "", OBS[i].id);
        for (uint32_t j = 0; j < OBS[i].n; j++) fprintf(f, "%s%u", j ? "," : "", OBS[i].v[j]);
        fprintf(f, "]}");
    }
    fprintf(f, "],\"stats\":{\"objects_built\":%u,\"objects_destroyed\":%u,\"level_surfaces\":%u,"
               "\"unknown_this\":%u,\"calls_on_destroyed\":%u,\"releases_past_zero\":%u,"
               "\"object_stride\":%u,\"object_fill\":%u},",
            s.objects_built, s.objects_destroyed, s.level_surfaces, s.unknown_this,
            s.calls_on_destroyed, s.releases_past_zero, s.object_stride, s.object_fill);
    if (g_fault)
        fprintf(f, "\"host_fault\":{\"signal\":%d,\"at\":\"%s\"},", (int)g_fault, g_fault_at);
    else
        fprintf(f, "\"host_fault\":null,");
    fprintf(f, "\"heap\":{\"allocations\":%u,\"frees\":%u,\"active\":%u,\"errors\":%u}}\n", HEAP_N,
            HEAP_FREES, heap_active(), HEAP_ERRORS);
    if (fclose(f)) { perror(path); exit(2); }
}

int main(int argc, char **argv) {
    const char *mode = argc == 3 ? argv[1] : "";
    const int known = !strcmp(mode, "first") || !strcmp(mode, "wide")
#ifdef SA2_D3D9_OBJ_OVF_BASE
                      || !strcmp(mode, "pool") || !strcmp(mode, "table") || !strcmp(mode, "cube")
#endif
        ;
    if (!known) {
        fprintf(stderr, "usage: d3d9_pool_probe first|pool|wide|table|cube RESULTS.json\n");
        return 2;
    }
    g_mem = calloc(1, GUEST_HI);
    HEAP = calloc(HEAP_MAX, sizeof *HEAP);
    if (!g_mem || !HEAP) return 2;
    struct sigaction sa;
    memset(&sa, 0, sizeof sa);
    sa.sa_handler = on_fault;
    sigemptyset(&sa.sa_mask);
    if (sigaction(SIGSEGV, &sa, NULL) || sigaction(SIGBUS, &sa, NULL)) return 2;
    if (!sigsetjmp(FAULT_ENV, 1)) {
        if (!strcmp(mode, "first")) { first_block(); report_first(); }
        else if (!strcmp(mode, "wide")) mode_wide();
#ifdef SA2_D3D9_OBJ_OVF_BASE
        else if (!strcmp(mode, "pool")) mode_pool();
        else if (!strcmp(mode, "cube")) mode_cube();
        else mode_table();
#endif
    } else {
        signal(SIGSEGV, SIG_DFL);
        signal(SIGBUS, SIG_DFL);
        g_fault_at = g_at ? g_at : "host-fault";
        check(0, g_fault_at, "host fault (signal %d); the mode stopped here", (int)g_fault);
    }
    check(HEAP_ERRORS == 0u, "heap-final", "%u bad frees", HEAP_ERRORS);
    write_results(argv[2], mode);
    printf("d3d9_pool_probe %s: %u checks, %u failures\n", mode, g_checks, g_fails);
    free(g_mem); free(HEAP);
    return g_fails ? 1 : 0;
}
