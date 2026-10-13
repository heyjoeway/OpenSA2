/* The recorder (host/sa2_present_rec.c) on the recording device (runtime/sa2_d3d9.c)
 * with no game: made-up calls through the device's vtable, as lifted code makes them.
 *
 *   usage: rec_harness --out PATH [--scenario main|edge|notes]
 *          rec_harness --methods PATH
 *
 * Documentation/scripts/tests/test_s5_stream.py builds it with the pinned zig from
 * this file, runtime/sa2_d3d9.c and, in every build but the control, the recorder
 * and the -Wl,-wrap flags a presentation build links it with; runs it with the
 * recorder on (SA2_REC_TRACE, SA2_REC_COUNTS set) and off; and reads the stream back
 * with tools/s5/stream_dump.py.
 *
 * The runtime pieces sa2_d3d9.c and the recorder call -- guest memory, the function
 * table, the CPU state, the trap, the scheduler's frame hooks, the window's numbers
 * and the guest heap -- are stubs below over a 20 MiB host buffer standing for guest
 * addresses 0x03000000..0x04400000. Every byte handed to the device is made up here:
 * no image, no guest code, no byte of the game's.
 *
 * The main scenario is the boot's first frame in miniature: Direct3DCreate9,
 * CreateDevice, a vertex shader whose comment holds the end token's value and whose
 * `def` holds it as a literal, a declaration on the stack, constants, viewport,
 * scissor, a Clear with a rectangle, a triangle fan through DrawPrimitiveUP,
 * BeginScene -- whose frame boundary runs one more device call, as another guest
 * thread would run inside the hand-off -- EndScene and Present; then a D3DX texture
 * filled through a level surface's lock, a second level locked by rectangle,
 * D3DXFilterTexture, and a DDS loaded from memory.
 *
 * The edge scenario is the paths the boot does not take: two shader model 1 pixel
 * shaders whose `def` literals look like a comment token and like the end token;
 * GetFunction and GetDeclaration handing bytecode and elements back; two levels of
 * one texture locked at once; a vertex buffer's nested locks; Presents with a dirty
 * region of two rectangles and of twenty; D3DXCreateCubeTexture,
 * D3DXCreateVolumeTexture, both file loads of a cube and a volume DDS, and both
 * d3dx Save calls; then DrawIndexedPrimitiveUP, a swap chain's Present, and a
 * Present whose source rectangle and dirty region another guest thread rewrites
 * inside its frame wait.
 *
 * The notes scenario is the recorder's own losses: a LockRect whose rectangle does
 * not read (its bytes left out, and the lock unsized), the Unlock of that lock, an
 * Unlock with no lock open, and SetRenderState -- which the REC_HARNESS_OUTFLAG
 * build, linked with -Wl,-wrap=sa2_d3d9_method_flags, has the device flag as
 * writing through a pointer, a method with no rule.
 *
 * --out writes what the guest saw, and what the test holds the stream to: every
 * call's EAX, the bytes it popped and a digest of the whole register file after
 * it -- this file's stand-in for it, probe_state below: nine 32-bit registers and
 * the x87 control word, 40 bytes in this file's layout, not the runtime's State, so
 * a store through the state pointer is seen only where it lands in those 40 bytes --
 * what the function table was handed for each thunk (address, name and kind),
 * the recording device's own counters, an FNV-1a digest of all of guest memory, the
 * bytes this file wrote for each blob, and the size of the stream file on disk
 * right after the first Present returned.
 * --methods writes the recording device's method table: each named (type, slot)
 * with its interface, name, argument count, flags and whether it answers.
 */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_d3d9.h"
#include "sa2_threads.h"
#include "sa2_window.h"

/* ------------------------------------------------------------ the stubs */

#define GUEST_LO    0x03000000u
#define GUEST_HI    0x04400000u
#define HEAP_LO     0x04000000u
#define STACK_TOP   0x030eff00u     /* the calling thread's stack */
#define STACK2_TOP  0x030dff00u     /* the stack of the "other thread" inside BeginScene */
#define SCRATCH     0x03004000u     /* made-up data the calls point at */

static uint8_t *g_mem;
static uint32_t g_heap = HEAP_LO;

int sa2_peek(uint32_t addr, uint32_t len, void *out) {
    if (addr < GUEST_LO || addr >= GUEST_HI || len > GUEST_HI - addr) return SA2_E_RANGE;
    memcpy(out, g_mem + (addr - GUEST_LO), len);
    return SA2_OK;
}
int sa2_poke(uint32_t addr, uint32_t len, const void *in) {
    if (addr < GUEST_LO || addr >= GUEST_HI || len > GUEST_HI - addr) return SA2_E_RANGE;
    memcpy(g_mem + (addr - GUEST_LO), in, len);
    return SA2_OK;
}
int sa2_guest_fill(uint32_t addr, int byte, uint32_t len) {
    if (addr < GUEST_LO || addr >= GUEST_HI || len > GUEST_HI - addr) return SA2_E_RANGE;
    memset(g_mem + (addr - GUEST_LO), byte, len);
    return SA2_OK;
}
/* A bump heap of zeroed blocks, as the runtime's guest heap hands them out. */
uint32_t sa2_heap_alloc_owned(uint32_t owner, uint32_t bytes) {
    (void)owner;
    const uint32_t at = (g_heap + 15u) & ~15u;
    if (bytes > GUEST_HI - at) return 0;
    g_heap = at + bytes;
    return at;
}
void sa2_heap_free_owned(uint32_t owner, uint32_t payload) { (void)owner; (void)payload; }

static uint64_t fnv1a_more(uint64_t h, const void *p, size_t n) {
    for (size_t i = 0; i < n; i++) { h ^= ((const uint8_t *)p)[i]; h *= 0x100000001b3ull; }
    return h;
}
#define FNV_BASIS 0xcbf29ce484222325ull

/* The function table: each thunk's function, and a digest of everything else a
 * registration hands the table -- the address, the name and the kind, which a
 * lookup answers whatever function stands behind it. */
#define THUNKS (SA2_D3D9_MAX_TYPES * SA2_D3D9_MAX_SLOTS)
static sa2_lifted_fn g_fn[THUNKS];
static uint32_t g_ft_regs;
static uint64_t g_ft_fnv = FNV_BASIS;
int sa2_ft_add(uint32_t va, sa2_lifted_fn fn, const char *name, uint8_t kind) {
    if (va < SA2_D3D9_THUNK_BASE || va >= SA2_D3D9_THUNK_END) return -1;
    g_fn[(va - SA2_D3D9_THUNK_BASE) / SA2_D3D9_THUNK_STRIDE] = fn;
    g_ft_regs++;
    g_ft_fnv = fnv1a_more(g_ft_fnv, &va, sizeof va);
    g_ft_fnv = fnv1a_more(g_ft_fnv, &kind, sizeof kind);
    g_ft_fnv = fnv1a_more(g_ft_fnv, name ? name : "", name ? strlen(name) + 1u : 1u);
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
static uint64_t g_frames, g_virtual_us = 5000000000ull;   /* past 2^32, for the u64 */
static void pk32(uint32_t a, uint32_t v);
uint64_t sa2_sched_frames(void) { return g_frames; }
uint64_t sa2_virtual_us(void) { return g_virtual_us; }
static int g_thread;                                      /* 1 inside the hand-off */
int sa2_sched_current(void) { return g_thread; }
void sa2_window_stats(sa2_wnd_stats *out) {
    memset(out, 0, sizeof *out);
    out->polls = 77u;
}
uint32_t sa2_window_screen_w(void) { return 1920u; }
uint32_t sa2_window_screen_h(void) { return 1080u; }

static char g_note[1024];
void sa2_trap_note(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_note, sizeof g_note, fmt, ap);
    va_end(ap);
}
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    fprintf(stderr, "rec_harness: an unexpected trap %u at %#010x: %s\n", status, exit_pc,
            g_note);
    exit(2);
}

/* ------------------------------------------------------------ calls */

#define CALLER  0x00401000u
#define MAX_CALLS 96
#define D3DX_DEFAULT 0xFFFFFFFFu

/* Per call: EAX, how far ESP moved, and a digest of the whole stand-in register
 * file after the call, all 40 bytes of probe_state (the recorder is handed it and
 * must leave it as the device left it). */
static uint64_t g_answers[MAX_CALLS][3];
static int g_ncalls;

static void pk32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static uint32_t peek32(uint32_t a) {
    uint32_t v = 0;
    sa2_peek(a, 4, &v);
    return v;
}

/* Slot `slot` of the object at `self`, as lifted code calls it: `this` and the
 * arguments on the guest stack under a return address, the thunk the vtable names
 * entered with ESP at that return address. Keeps EAX and how far ESP moved. */
static uint32_t call_on(uint32_t top, uint32_t self, uint32_t slot, uint32_t argc,
                        const uint32_t *argv) {
    const uint32_t thunk = peek32(peek32(self) + 4u * slot);
    probe_state st;
    memset(&st, 0, sizeof st);
    const uint32_t esp0 = top - 4u * (argc + 2u);
    st.gpr[SA2_ESP] = esp0;
    pk32(esp0, CALLER);
    pk32(esp0 + 4u, self);
    for (uint32_t i = 0; i < argc; i++) pk32(esp0 + 8u + 4u * i, argv[i]);
    sa2_lifted_fn fn = (thunk >= SA2_D3D9_THUNK_BASE && thunk < SA2_D3D9_THUNK_END)
                           ? g_fn[(thunk - SA2_D3D9_THUNK_BASE) / SA2_D3D9_THUNK_STRIDE] : NULL;
    if (!fn) {
        fprintf(stderr, "rec_harness: slot %u of %#010x names %#010x, which no thunk answers\n",
                slot, self, thunk);
        exit(2);
    }
    fn(&st, thunk, NULL);
    if (g_ncalls < MAX_CALLS) {
        g_answers[g_ncalls][0] = st.gpr[SA2_EAX];
        g_answers[g_ncalls][1] = st.gpr[SA2_ESP] - esp0;
        g_answers[g_ncalls][2] = fnv1a_more(FNV_BASIS, &st, sizeof st);
        g_ncalls++;
    }
    return st.gpr[SA2_EAX];
}
#define CALL(self, slot, ...) \
    call_on(STACK_TOP, self, slot, \
            (uint32_t)(sizeof((uint32_t[]){__VA_ARGS__}) / sizeof(uint32_t)), \
            (const uint32_t[]){__VA_ARGS__})
#define CALL0(self, slot) call_on(STACK_TOP, self, slot, 0, NULL)

static void note_dx(uint32_t eax) {
    if (g_ncalls < MAX_CALLS) {
        g_answers[g_ncalls][0] = eax;
        g_answers[g_ncalls][1] = 0;
        g_answers[g_ncalls][2] = 0;
        g_ncalls++;
    }
}

/* The frame boundary a call's frame wait crosses: in the main scenario another guest
 * thread runs here, inside BeginScene, and makes one device call of its own on its
 * own stack; in the edge scenario it rewrites a Present's rectangle and region
 * (g_rewrite_*), as another thread may while the Present waits. */
static uint32_t g_dev;
static int g_nested_done, g_nest;
static uint32_t g_rewrite_rect, g_rewrite_rgn;
#define NESTED_DATA (SCRATCH + 0x3000u)
void sa2_sched_frame_boundary(void) {
    g_frames++;
    g_virtual_us += 16666u;
    if (g_rewrite_rect) {
        for (uint32_t i = 0; i < 4u; i++) pk32(g_rewrite_rect + 4u * i, 9u);
        g_rewrite_rect = 0;
    }
    if (g_rewrite_rgn) {
        for (uint32_t i = 0; i < 8u; i++) pk32(g_rewrite_rgn + 32u + 4u * i, 0x77u);
        g_rewrite_rgn = 0;
    }
    if (!g_nest || g_nested_done || !g_dev) return;
    g_nested_done = 1;
    g_thread = 1;
    call_on(STACK2_TOP, g_dev, 109u, 3, (const uint32_t[]){5u, NESTED_DATA, 1u});
    g_thread = 0;
}

/* ------------------------------------------------------------ made-up data */

static void put_words(uint32_t at, const uint32_t *w, uint32_t n) {
    for (uint32_t i = 0; i < n; i++) pk32(at + 4u * i, w[i]);
}
static void put_pattern(uint32_t at, uint32_t n, uint32_t seed) {
    for (uint32_t i = 0; i < n; i++) {
        const uint8_t b = (uint8_t)((i * 131u + seed * 7u + (i >> 8)) & 0xFFu);
        sa2_poke(at + i, 1, &b);
    }
}

static uint64_t fnv1a(const uint8_t *p, size_t n) {
    uint64_t h = 0xcbf29ce484222325ull;
    for (size_t i = 0; i < n; i++) { h ^= p[i]; h *= 0x100000001b3ull; }
    return h;
}

static FILE *g_out;
static int g_nbytes;
/* One entry of the "bytes" object: `n` guest bytes at `at`, as hex. */
static void bytes_of(const char *name, uint32_t at, uint32_t n) {
    fprintf(g_out, "%s\"%s\": \"", g_nbytes++ ? ", " : "", name);
    for (uint32_t i = 0; i < n; i++) {
        uint8_t b = 0;
        sa2_peek(at + i, 1, &b);
        fprintf(g_out, "%02x", b);
    }
    fputc('"', g_out);
}

/* Addresses of the made-up inputs, fixed so the test can name them. */
#define PP          (SCRATCH + 0x000u)
#define OUTS        (SCRATCH + 0x100u)          /* one dword per out-parameter */
#define SHADER      (SCRATCH + 0x200u)
#define CONSTS      (SCRATCH + 0x300u)
#define VIEWPORT    (SCRATCH + 0x380u)
#define SCISSOR     (SCRATCH + 0x3a0u)
#define RECTS       (SCRATCH + 0x3c0u)
#define LOCKED      (SCRATCH + 0x3e0u)
#define RECT1       (SCRATCH + 0x3f0u)
#define VERTS       (SCRATCH + 0x400u)
#define DECL        (STACK_TOP - 0x200u)        /* on the stack, as the game's is */
#define DDS         (SCRATCH + 0x1000u)
#define DDS_PAYLOAD 2048u

/* The device, as the game makes it: 1280x720 A8R8G8B8, windowed, D24S8. */
static uint32_t make_device(void) {
    const uint32_t pp[14] = {1280u, 720u, 21u, 1u, 0u, 0u, 1u, 0u, 1u, 1u, 75u, 0u, 0u, 0u};
    put_words(PP, pp, 14);
    const uint32_t d3d9 = sa2_d3d9_make_d3d9(CALLER);
    CALL(d3d9, 16u, 0u, 1u, 0x7e000000u, 0x40u, PP, OUTS + 0u);
    g_dev = peek32(OUTS + 0u);
    return d3d9;
}

static void scenario_main(void) {
    g_nest = 1;
    const uint32_t d3d9 = make_device();

    /* vs_3_0: a comment whose first word is the end token's value, dcl_position v0,
     * def c0 with 0x0000FFFF as its first literal, then the end token. */
    const uint32_t shader[] = {
        0xFFFE0300u,
        0x0002FFFEu, 0x0000FFFFu, 0x12345678u,
        0x0200001Fu, 0x80000000u, 0x900F0000u,
        0x05000051u, 0xA00F0000u, 0x0000FFFFu, 0x3F800000u, 0x00000000u, 0x3F000000u,
        0x0000FFFFu,
    };
    put_words(SHADER, shader, sizeof shader / 4u);
    put_words(SHADER + sizeof shader, (const uint32_t[]){0xDDDDDDDDu, 0x0000FFFFu}, 2);
    CALL(g_dev, 91u, SHADER, OUTS + 4u);                       /* CreateVertexShader */
    const uint32_t vs = peek32(OUTS + 4u);

    /* POSITION float3 at 0, TEXCOORD0 float2 at 12, then D3DDECL_END. */
    const uint32_t decl[] = {0x00000000u, 0x00000002u, 0x000C0000u, 0x00050001u,
                             0x000000FFu, 0x00000011u, 0xEEEEEEEEu, 0xEEEEEEEEu};
    put_words(DECL, decl, 6);
    put_words(DECL + 24u, (const uint32_t[]){0xEEEEEEEEu, 0xEEEEEEEEu}, 2);
    CALL(g_dev, 86u, DECL, OUTS + 8u);                         /* CreateVertexDeclaration */
    const uint32_t vdecl = peek32(OUTS + 8u);
    CALL(g_dev, 87u, vdecl);                                   /* SetVertexDeclaration */
    CALL(g_dev, 92u, vs);                                      /* SetVertexShader */

    put_pattern(CONSTS, 32u, 1u);
    CALL(g_dev, 94u, 4u, CONSTS, 2u);                          /* SetVertexShaderConstantF */
    put_pattern(NESTED_DATA, 16u, 9u);
    const uint32_t vp[6] = {0u, 0u, 1280u, 720u, 0x00000000u, 0x3F800000u};
    put_words(VIEWPORT, vp, 6);
    CALL(g_dev, 47u, VIEWPORT);                                /* SetViewport */
    const uint32_t sc[4] = {16u, 8u, 1264u, 712u};
    put_words(SCISSOR, sc, 4);
    CALL(g_dev, 75u, SCISSOR);                                 /* SetScissorRect */

    CALL0(g_dev, 41u);                                         /* BeginScene */
    const uint32_t rc[4] = {0u, 0u, 640u, 360u};
    put_words(RECTS, rc, 4);
    CALL(g_dev, 43u, 1u, RECTS, 1u, 0xFF000000u, 0x3F800000u, 0u);   /* Clear */
    put_pattern(VERTS, 4u * 24u, 2u);
    CALL(g_dev, 83u, 6u, 2u, VERTS, 24u);                      /* DrawPrimitiveUP, a fan */
    CALL0(g_dev, 42u);                                         /* EndScene */
    CALL(g_dev, 17u, 0u, 0u, 0u, 0u);                          /* Present */
    long after_present = -1;
    const char *trace = getenv("SA2_REC_TRACE");
    struct stat sb;
    if (trace && stat(trace, &sb) == 0) after_present = (long)sb.st_size;

    /* A 64x64 DXT1 texture with the complete chain, level 0 filled through its
     * surface's lock, and level 1 locked by rectangle: rows 8..24, block rows 2..6. */
    const uint32_t dxt1 = 0x31545844u;
    note_dx(sa2_d3d9_d3dx_create_texture(g_dev, 64u, 64u, 0u, 0u, dxt1, 1u, OUTS + 12u,
                                         CALLER));
    const uint32_t tex = peek32(OUTS + 12u);
    CALL(tex, 18u, 0u, OUTS + 16u);                            /* GetSurfaceLevel(0) */
    const uint32_t s0 = peek32(OUTS + 16u);
    CALL(s0, 13u, LOCKED, 0u, 0u);                             /* LockRect */
    const uint32_t pitch0 = peek32(LOCKED), bits0 = peek32(LOCKED + 4u);
    put_pattern(bits0, pitch0 * 16u, 3u);
    CALL0(s0, 14u);                                            /* UnlockRect */
    CALL(tex, 18u, 1u, OUTS + 20u);                            /* GetSurfaceLevel(1) */
    const uint32_t s1 = peek32(OUTS + 20u);
    put_words(RECT1, (const uint32_t[]){0u, 8u, 32u, 24u}, 4);
    CALL(s1, 13u, LOCKED, RECT1, 0u);
    const uint32_t pitch1 = peek32(LOCKED), bits1 = peek32(LOCKED + 4u);
    put_pattern(bits1, pitch1 * 4u, 4u);
    CALL0(s1, 14u);
    note_dx(sa2_d3d9_d3dx_filter_texture((const uint32_t[]){tex, 0u, 0u, D3DX_DEFAULT},
                                         CALLER));

    /* A DDS: 64x32 DXT5, one level in the file, and one byte after it, so the blob
     * that carries it is not a whole number of dwords. */
    const uint32_t dds_head[32] = {
        0x20534444u, 124u, 0x00081007u, 32u, 64u, 2048u, 0u, 0u,
        0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u,
        32u, 4u, 0x35545844u, 0u, 0u, 0u, 0u, 0u, 0x1000u, 0u, 0u, 0u, 0u,
    };
    put_words(DDS, dds_head, 32);
    put_pattern(DDS + 128u, DDS_PAYLOAD + 1u, 5u);
    const uint32_t dargs[15] = {g_dev, DDS, 128u + DDS_PAYLOAD + 1u, D3DX_DEFAULT,
                                D3DX_DEFAULT, D3DX_DEFAULT, 0u, 0u, 1u, D3DX_DEFAULT,
                                D3DX_DEFAULT, 0u, 0u, 0u, OUTS + 24u};
    note_dx(sa2_d3d9_d3dx_texture_from_memory(dargs, CALLER));
    const uint32_t ftex = peek32(OUTS + 24u);

    CALL0(tex, 2u);                                            /* Texture::Release */
    CALL0(vs, 2u);

    fprintf(g_out, " \"objects\": {\"d3d9\": %u, \"device\": %u, \"vs\": %u, \"decl\": %u, "
                   "\"texture\": %u, \"level0\": %u, \"level1\": %u, \"file_texture\": %u},\n",
            d3d9, g_dev, vs, vdecl, tex, s0, s1, ftex);
    fprintf(g_out, " \"locks\": [[%u, %u], [%u, %u]],\n", pitch0, bits0, pitch1, bits1);
    fprintf(g_out, " \"after_present\": %ld,\n", after_present);
    fprintf(g_out, " \"bytes\": {");
    bytes_of("pp", PP, 56u);
    bytes_of("shader", SHADER, (uint32_t)sizeof shader);
    bytes_of("decl", DECL, 24u);
    bytes_of("consts", CONSTS, 32u);
    bytes_of("nested", NESTED_DATA, 16u);
    bytes_of("viewport", VIEWPORT, 24u);
    bytes_of("scissor", SCISSOR, 16u);
    bytes_of("rects", RECTS, 16u);
    bytes_of("verts", VERTS, 96u);
    bytes_of("level0", bits0, pitch0 * 16u);
    bytes_of("level1", bits1, pitch1 * 4u);
    bytes_of("dds", DDS, 128u + DDS_PAYLOAD + 1u);
    fprintf(g_out, "},\n");
}

/* The edge scenario's inputs. */
#define PS1A     (SCRATCH + 0x800u)
#define PS1B     (SCRATCH + 0x840u)
#define VS3      (SCRATCH + 0x880u)
#define FUNC     (SCRATCH + 0x8c0u)             /* GetFunction's buffer */
#define ELEMS    (SCRATCH + 0x900u)             /* GetDeclaration's buffer */
#define RGN2     (SCRATCH + 0xa00u)
#define RGN20    (SCRATCH + 0xb00u)
#define DDSCUBE  (SCRATCH + 0x2000u)
#define DDSVOL   (SCRATCH + 0x2200u)
#define PATH     (SCRATCH + 0x2400u)
#define IUP_IDX  (SCRATCH + 0x2500u)            /* DrawIndexedPrimitiveUP's indices */
#define IUP_VTX  (SCRATCH + 0x2540u)            /* ... and its vertices */
#define SRCRECT  (SCRATCH + 0x25a0u)            /* a Present's source rectangle */
#define RGN2B    (SCRATCH + 0x2600u)            /* ... and its dirty region */
#define BADRECT  0x05000000u                    /* guest address space no byte of reads */

static void put_rgn(uint32_t at, uint32_t n) {
    put_words(at, (const uint32_t[]){32u, 1u, n, 16u * n, 0u, 0u, 64u, 64u}, 8);
    for (uint32_t i = 0; i < n; i++)
        put_words(at + 32u + 16u * i, (const uint32_t[]){i, i, i + 2u, i + 3u}, 4);
}

static void put_dds(uint32_t at, uint32_t caps2, uint32_t depth, uint32_t payload) {
    const uint32_t flags = 0x00081007u | (depth ? 0x00800000u : 0u);
    uint32_t h[32] = {0};
    h[0] = 0x20534444u; h[1] = 124u; h[2] = flags; h[3] = 4u; h[4] = 4u; h[5] = 8u;
    h[6] = depth; h[19] = 32u; h[20] = 4u; h[21] = 0x31545844u; h[27] = 0x1000u;
    h[28] = caps2;
    put_words(at, h, 32);
    put_pattern(at + 128u, payload, 6u);
}

static void scenario_edge(void) {
    make_device();

    /* ps_1_1: def c0 whose first literal is 0x3F7FFFFE (low half: a comment token's)
     * or 0x0000FFFF (the end token's); then mov r0, c0 and the end: 44 bytes each. */
    const uint32_t ps1a[11] = {0xFFFF0101u, 0x00000051u, 0xA00F0000u, 0x3F7FFFFEu,
                               0x3F800000u, 0x3F800000u, 0x3F800000u, 0x00000001u,
                               0x800F0000u, 0xA0E40000u, 0x0000FFFFu};
    uint32_t ps1b[11];
    memcpy(ps1b, ps1a, sizeof ps1b);
    ps1b[3] = 0x0000FFFFu;
    put_words(PS1A, ps1a, 11);
    put_words(PS1B, ps1b, 11);
    CALL(g_dev, 106u, PS1A, OUTS + 32u);                       /* CreatePixelShader */
    CALL(g_dev, 106u, PS1B, OUTS + 36u);

    /* vs_3_0, dcl_position v0, end: GetFunction's size, then its bytes. */
    const uint32_t vs3[5] = {0xFFFE0300u, 0x0200001Fu, 0x80000000u, 0x900F0000u, 0x0000FFFFu};
    put_words(VS3, vs3, 5);
    CALL(g_dev, 91u, VS3, OUTS + 40u);
    const uint32_t vs = peek32(OUTS + 40u);
    CALL(vs, 4u, 0u, OUTS + 44u);                              /* GetFunction(NULL, &n) */
    CALL(vs, 4u, FUNC, OUTS + 44u);                            /* GetFunction(buf, &n) */

    /* A declaration's elements handed back. */
    const uint32_t decl[] = {0x00000000u, 0x00000002u, 0x000C0000u, 0x00050001u,
                             0x000000FFu, 0x00000011u};
    put_words(DECL, decl, 6);
    CALL(g_dev, 86u, DECL, OUTS + 48u);
    const uint32_t vdecl = peek32(OUTS + 48u);
    CALL(vdecl, 4u, 0u, OUTS + 52u);                           /* GetDeclaration(NULL, &n) */
    CALL(vdecl, 4u, ELEMS, OUTS + 52u);

    /* Levels 0 and 1 of a 64x64 A8R8G8B8 texture locked at once, unlocked in order. */
    note_dx(sa2_d3d9_d3dx_create_texture(g_dev, 64u, 64u, 0u, 0u, 21u, 1u, OUTS + 56u,
                                         CALLER));
    const uint32_t tex = peek32(OUTS + 56u);
    CALL(tex, 19u, 0u, LOCKED, 0u, 0u);                        /* Texture::LockRect(0) */
    const uint32_t pitch0 = peek32(LOCKED), bits0 = peek32(LOCKED + 4u);
    CALL(tex, 19u, 1u, LOCKED + 8u, 0u, 0u);                   /* LockRect(1) */
    const uint32_t pitch1 = peek32(LOCKED + 8u), bits1 = peek32(LOCKED + 12u);
    put_pattern(bits0, pitch0 * 64u, 7u);
    put_pattern(bits1, pitch1 * 32u, 8u);
    CALL(tex, 20u, 0u);                                        /* UnlockRect(0) */
    CALL(tex, 20u, 1u);                                        /* UnlockRect(1) */

    /* A 256-byte vertex buffer: Lock(0, 64), then Lock(64, 0) inside it. */
    CALL(g_dev, 26u, 256u, 0u, 0u, 1u, OUTS + 60u, 0u);       /* CreateVertexBuffer */
    const uint32_t vb = peek32(OUTS + 60u);
    CALL(vb, 11u, 0u, 64u, OUTS + 64u, 0u);
    CALL(vb, 11u, 64u, 0u, OUTS + 68u, 0u);
    const uint32_t vb0 = peek32(OUTS + 64u), vb1 = peek32(OUTS + 68u);
    put_pattern(vb0, 256u, 9u);
    CALL0(vb, 12u);                                            /* closes Lock(64, 0) */
    CALL0(vb, 12u);                                            /* closes Lock(0, 64) */

    /* Presents with a dirty region: two rectangles are kept; twenty are left out. */
    put_rgn(RGN2, 2u);
    put_rgn(RGN20, 20u);
    CALL(g_dev, 17u, 0u, 0u, 0u, RGN2);
    CALL(g_dev, 17u, 0u, 0u, 0u, RGN20);

    /* The d3dx creators and loads the boot never calls, and both Save calls. */
    note_dx(sa2_d3d9_d3dx_create_cube_texture(
        (const uint32_t[]){g_dev, 16u, 0u, 0u, 0x31545844u, 1u, OUTS + 72u}, CALLER));
    note_dx(sa2_d3d9_d3dx_create_volume_texture(
        (const uint32_t[]){g_dev, 8u, 8u, 4u, 0u, 0u, 21u, 1u, OUTS + 76u}, CALLER));
    put_dds(DDSCUBE, 0x0000FE00u, 0u, 6u * 8u);
    note_dx(sa2_d3d9_d3dx_cube_from_memory(
        (const uint32_t[]){g_dev, DDSCUBE, 128u + 48u, D3DX_DEFAULT, D3DX_DEFAULT, 0u, 0u, 1u,
                           D3DX_DEFAULT, D3DX_DEFAULT, 0u, 0u, 0u, OUTS + 80u}, CALLER));
    put_dds(DDSVOL, 0x00200000u, 2u, 2u * 8u);
    note_dx(sa2_d3d9_d3dx_volume_from_memory(
        (const uint32_t[]){g_dev, DDSVOL, 128u + 16u, D3DX_DEFAULT, D3DX_DEFAULT, D3DX_DEFAULT,
                           D3DX_DEFAULT, 0u, 0u, 1u, D3DX_DEFAULT, D3DX_DEFAULT, 0u, 0u, 0u,
                           OUTS + 84u}, CALLER));
    const char path[] = "C:\\SA2\\shot.dds";
    for (uint32_t i = 0; i < sizeof path; i++) sa2_poke(PATH + i, 1, &path[i]);
    CALL(tex, 18u, 0u, OUTS + 88u);                            /* GetSurfaceLevel(0) */
    note_dx(sa2_d3d9_d3dx_save_texture_to_file((const uint32_t[]){PATH, 4u, tex, 0u}, CALLER));
    note_dx(sa2_d3d9_d3dx_save_surface_to_file(
        (const uint32_t[]){PATH, 4u, peek32(OUTS + 88u), 0u, 0u}, CALLER));

    /* DrawIndexedPrimitiveUP: a triangle list of 2 over vertices 2..5 with 16-bit
     * indices and a 12-byte stride, so 6 indices of 2 bytes and (2 + 4) vertices of
     * 12 bytes, from vertex 0 of the array. */
    put_pattern(IUP_IDX, 12u, 10u);
    put_pattern(IUP_VTX, 72u, 11u);
    CALL(g_dev, 84u, 4u, 2u, 4u, 2u, IUP_IDX, 101u, IUP_VTX, 12u);

    /* A swap chain's Present: it has its FRAME, and counts as a Present. */
    CALL(g_dev, 14u, 0u, OUTS + 92u);                          /* GetSwapChain(0) */
    const uint32_t sc = peek32(OUTS + 92u);
    CALL(sc, 3u, 0u, 0u, 0u, 0u, 0u);                          /* IDirect3DSwapChain9::Present */

    /* A Present whose rectangle and region another guest thread rewrites inside its
     * frame wait (no BeginScene came before it): the stream holds them as they were
     * when the call was made. */
    put_words(SRCRECT, (const uint32_t[]){0u, 0u, 64u, 64u}, 4);
    put_rgn(RGN2B, 2u);
    g_rewrite_rect = SRCRECT;
    g_rewrite_rgn = RGN2B;
    CALL(g_dev, 17u, SRCRECT, 0u, 0u, RGN2B);

    fprintf(g_out, " \"objects\": {\"vs\": %u, \"decl\": %u, \"texture\": %u, \"vb\": %u, "
                   "\"cube\": %u, \"volume\": %u, \"cube_file\": %u, \"volume_file\": %u, "
                   "\"swap_chain\": %u},\n",
            vs, vdecl, tex, vb, peek32(OUTS + 72u), peek32(OUTS + 76u), peek32(OUTS + 80u),
            peek32(OUTS + 84u), sc);
    fprintf(g_out, " \"locks\": [[%u, %u], [%u, %u], [0, %u], [0, %u]],\n", pitch0, bits0,
            pitch1, bits1, vb0, vb1);
    fprintf(g_out, " \"after_present\": -1,\n");
    fprintf(g_out, " \"bytes\": {");
    bytes_of("ps1a", PS1A, 44u);
    bytes_of("ps1b", PS1B, 44u);
    bytes_of("vs3", VS3, 20u);
    bytes_of("function", FUNC, 20u);
    bytes_of("elements", ELEMS, 24u);
    bytes_of("level0", bits0, pitch0 * 64u);
    bytes_of("level1", bits1, pitch1 * 32u);
    bytes_of("vb", vb0, 256u);
    bytes_of("rgn2", RGN2, 64u);
    bytes_of("dds_cube", DDSCUBE, 128u + 48u);
    bytes_of("dds_volume", DDSVOL, 128u + 16u);
    bytes_of("iup_indices", IUP_IDX, 12u);
    bytes_of("iup_vertices", IUP_VTX, 72u);
    bytes_of("srcrect_after", SRCRECT, 16u);
    bytes_of("rgn2b_after", RGN2B, 64u);
    fprintf(g_out, "},\n");
}

#ifdef REC_HARNESS_OUTFLAG
/* The device's method flags with SetRenderState (IDirect3DDevice9, slot 57) said to
 * write through a pointer: a method with no rule that a device flags "out", which
 * the recorder must note. Linked with -Wl,-wrap=sa2_d3d9_method_flags. */
const char *__real_sa2_d3d9_method_flags(uint32_t type, uint32_t slot);
const char *__wrap_sa2_d3d9_method_flags(uint32_t type, uint32_t slot) {
    if (type == 1u && slot == 57u) return "out";
    return __real_sa2_d3d9_method_flags(type, slot);
}
#endif

/* The recorder's own losses, each a NOTE and a count: a rectangle that does not
 * read (the LockRect's IN blob left out, and the lock unsized), the Unlock of that
 * lock, an Unlock with no lock open, and a flagged method with no rule
 * (REC_HARNESS_OUTFLAG only). */
static void scenario_notes(void) {
    make_device();
    note_dx(sa2_d3d9_d3dx_create_texture(g_dev, 64u, 64u, 1u, 0u, 21u, 1u, OUTS + 12u,
                                         CALLER));
    const uint32_t tex = peek32(OUTS + 12u);
    CALL(tex, 18u, 0u, OUTS + 16u);                            /* GetSurfaceLevel(0) */
    const uint32_t s0 = peek32(OUTS + 16u);
    CALL(s0, 13u, LOCKED, BADRECT, 0u);                        /* LockRect */
    const uint32_t pitch0 = peek32(LOCKED), bits0 = peek32(LOCKED + 4u);
    put_pattern(bits0, pitch0 * 64u, 12u);
    CALL0(s0, 14u);                                            /* UnlockRect: unsized */
    CALL0(s0, 14u);                                            /* UnlockRect: none open */
    CALL(g_dev, 57u, 7u, 1u);                                  /* SetRenderState, twice */
    CALL(g_dev, 57u, 7u, 0u);
    fprintf(g_out, " \"objects\": {\"texture\": %u, \"level0\": %u},\n", tex, s0);
    fprintf(g_out, " \"locks\": [[%u, %u]],\n", pitch0, bits0);
    fprintf(g_out, " \"after_present\": -1,\n \"bytes\": {},\n");
}

/* The recording device's method table, as its own getters answer it. */
static int write_methods(const char *path) {
    FILE *f = fopen(path, "w");
    if (!f) return 2;
    fprintf(f, "[");
    int first = 1;
    for (uint32_t t = 0; t < sa2_d3d9_type_count(); t++)
        for (uint32_t k = 0; k < SA2_D3D9_MAX_SLOTS; k++) {
            const char *name = sa2_d3d9_method_name(t, k);
            if (!name) continue;
            fprintf(f, "%s\n [%u, %u, \"%s\", \"%s\", %d, \"%s\", %d]", first ? "" : ",", t, k,
                    sa2_d3d9_type_name(t), name, sa2_d3d9_method_argc(t, k),
                    sa2_d3d9_method_flags(t, k), sa2_d3d9_method_implemented(t, k));
            first = 0;
        }
    fprintf(f, "\n]\n");
    fclose(f);
    return 0;
}

int main(int argc, char **argv) {
    const char *out_path = NULL, *methods = NULL, *scenario = "main";
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--out") && i + 1 < argc) out_path = argv[++i];
        else if (!strcmp(argv[i], "--scenario") && i + 1 < argc) scenario = argv[++i];
        else if (!strcmp(argv[i], "--methods") && i + 1 < argc) methods = argv[++i];
    }
    if (methods) return write_methods(methods);
    if (!out_path || (strcmp(scenario, "main") && strcmp(scenario, "edge")
                      && strcmp(scenario, "notes"))) {
        fprintf(stderr, "usage: rec_harness --out PATH [--scenario main|edge|notes] | "
                        "--methods PATH\n");
        return 2;
    }
    g_mem = (uint8_t *)calloc(1, GUEST_HI - GUEST_LO);
    if (!g_mem) return 2;
    setenv("SA2_D3D9_DEVICE", "1", 1);
    sa2_d3d9_config_from_env();
    g_out = fopen(out_path, "w");
    if (!g_out) return 2;
    fprintf(g_out, "{\n");
    if (!strcmp(scenario, "main")) scenario_main();
    else if (!strcmp(scenario, "edge")) scenario_edge();
    else scenario_notes();

    /* What the guest saw. */
    sa2_d3d9_stats st;
    sa2_d3d9_stats_get(&st);
    fprintf(g_out, " \"answers\": [");
    for (int i = 0; i < g_ncalls; i++)
        fprintf(g_out, "%s[%llu, %llu, \"%016llx\"]", i ? ", " : "",
                (unsigned long long)g_answers[i][0], (unsigned long long)g_answers[i][1],
                (unsigned long long)g_answers[i][2]);
    fprintf(g_out, "],\n \"function_table\": [%u, \"%016llx\"],\n", g_ft_regs,
            (unsigned long long)g_ft_fnv);
    fprintf(g_out, " \"guest_fnv\": \"%016llx\",\n",
            (unsigned long long)fnv1a(g_mem, GUEST_HI - GUEST_LO));
    fprintf(g_out, " \"stats\": [%u, %u, %u, %u, %u, %u, %u, %u, %u, %u, %u]\n}\n",
            st.method_calls, st.objects_built, st.present_calls, st.beginscene_calls,
            st.draw_calls, st.clear_calls, st.guest_bytes_allocated, st.tex_from_file,
            st.d3dx_filter_calls, st.noop, st.from_guest);
    fclose(g_out);
    return 0;
}
