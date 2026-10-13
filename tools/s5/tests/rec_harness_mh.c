/* The recorder (host/sa2_present_rec.c) on the recording device (runtime/sa2_d3d9.c)
 * with no game, making the calls Metal Harbor makes beyond the boot.
 *
 *   usage: rec_harness_mh --out PATH [--stale]
 *
 * Documentation/scripts/tests/test_s5_stream.py builds it as it builds
 * tools/s5/tests/rec_harness.c (the boot's miniature): with the pinned zig, from this
 * file, runtime/sa2_d3d9.c and, in every build but the control, the recorder and the
 * six -Wl,-wrap flags a presentation build links it with.
 *
 * The runtime pieces sa2_d3d9.c and the recorder call are stubs below over a host
 * buffer standing for guest addresses 0x03000000..0x04400000, as in rec_harness.c.
 * Every byte handed to the device is made up here: no image, no guest code, no byte
 * of the game's.
 *
 * The scenario: CreateDevice; GetBackBuffer, GetRenderTarget and
 * GetDepthStencilSurface; a vertex buffer made, locked at an offset for part of its
 * length and filled, then locked whole with DISCARD and filled again; a render-target
 * texture, its level surface and a depth surface of its own set as the targets; a
 * DrawPrimitive from the buffer; the device's targets set back; a
 * D3DXLoadSurfaceFromSurface from the back buffer into the texture's level with a
 * destination rectangle; then three runs of REC_WATCH_EVERY (64) frames of
 * BeginScene, DrawPrimitive, EndScene and Present, each ending in one check of the
 * recorder's buffer watch: the first with the buffer unlocked, the second with it
 * locked whole again and a nested lock inside that opened, filled, closed and its
 * range written again under the outer lock (so not watched: a lock is still open),
 * the third after the outer Unlock and the final Release, with its old store
 * rewritten as the heap's next owner would (so not the buffer's, and not watched
 * either).
 *
 * --stale writes one byte into the buffer after its second Unlock, before the
 * frames: the write Direct3D forbids and the recording device cannot stop, which
 * the recorder's buffer watch must see at its first check.
 *
 * --out writes what the guest saw and what the test holds the stream to: every
 * call's EAX and the bytes it popped, the recording device's counters, an FNV-1a
 * digest of all of guest memory, the objects, the two locks' addresses and the bytes
 * this file wrote through each.
 */
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

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
#define STACK_TOP   0x030eff00u
#define SCRATCH     0x03004000u

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
uint32_t sa2_heap_alloc_owned(uint32_t owner, uint32_t bytes) {
    (void)owner;
    const uint32_t at = (g_heap + 15u) & ~15u;
    if (bytes > GUEST_HI - at) return 0;
    g_heap = at + bytes;
    return at;
}
void sa2_heap_free_owned(uint32_t owner, uint32_t payload) { (void)owner; (void)payload; }

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
static uint64_t g_frames, g_virtual_us = 1000000ull;
uint64_t sa2_sched_frames(void) { return g_frames; }
uint64_t sa2_virtual_us(void) { return g_virtual_us; }
int sa2_sched_current(void) { return 0; }
void sa2_window_stats(sa2_wnd_stats *out) {
    memset(out, 0, sizeof *out);
    out->polls = (uint32_t)g_frames;
}
uint32_t sa2_window_screen_w(void) { return 1920u; }
uint32_t sa2_window_screen_h(void) { return 1080u; }
void sa2_sched_frame_boundary(void) {
    g_frames++;
    g_virtual_us += 16666u;
}

static char g_note[1024];
void sa2_trap_note(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_note, sizeof g_note, fmt, ap);
    va_end(ap);
}
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    fprintf(stderr, "rec_harness_mh: an unexpected trap %u at %#010x: %s\n", status, exit_pc,
            g_note);
    exit(2);
}

/* ------------------------------------------------------------ calls */

#define CALLER  0x00401000u
#define MAX_CALLS 512

static uint32_t g_answers[MAX_CALLS][2];
static int g_ncalls;

static void pk32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static uint32_t peek32(uint32_t a) {
    uint32_t v = 0;
    sa2_peek(a, 4, &v);
    return v;
}

/* Slot `slot` of the object at `self`, as lifted code calls it (rec_harness.c's). */
static uint32_t call_on(uint32_t self, uint32_t slot, uint32_t argc, const uint32_t *argv) {
    const uint32_t thunk = peek32(peek32(self) + 4u * slot);
    probe_state st;
    memset(&st, 0, sizeof st);
    const uint32_t esp0 = STACK_TOP - 4u * (argc + 2u);
    st.gpr[SA2_ESP] = esp0;
    pk32(esp0, CALLER);
    pk32(esp0 + 4u, self);
    for (uint32_t i = 0; i < argc; i++) pk32(esp0 + 8u + 4u * i, argv[i]);
    sa2_lifted_fn fn = (thunk >= SA2_D3D9_THUNK_BASE && thunk < SA2_D3D9_THUNK_END)
                           ? g_fn[(thunk - SA2_D3D9_THUNK_BASE) / SA2_D3D9_THUNK_STRIDE] : NULL;
    if (!fn) {
        fprintf(stderr, "rec_harness_mh: slot %u of %#010x names %#010x, which no thunk answers\n",
                slot, self, thunk);
        exit(2);
    }
    fn(&st, thunk, NULL);
    if (g_ncalls < MAX_CALLS) {
        g_answers[g_ncalls][0] = st.gpr[SA2_EAX];
        g_answers[g_ncalls][1] = st.gpr[SA2_ESP] - esp0;
        g_ncalls++;
    }
    return st.gpr[SA2_EAX];
}
#define CALL(self, slot, ...) \
    call_on(self, slot, (uint32_t)(sizeof((uint32_t[]){__VA_ARGS__}) / sizeof(uint32_t)), \
            (const uint32_t[]){__VA_ARGS__})
#define CALL0(self, slot) call_on(self, slot, 0, NULL)

static void note_dx(uint32_t eax) {
    if (g_ncalls < MAX_CALLS) {
        g_answers[g_ncalls][0] = eax;
        g_answers[g_ncalls][1] = 0;
        g_ncalls++;
    }
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
static void hex_of(FILE *f, uint32_t at, uint32_t n) {
    fputc('"', f);
    for (uint32_t i = 0; i < n; i++) {
        uint8_t b = 0;
        sa2_peek(at + i, 1, &b);
        fprintf(f, "%02x", b);
    }
    fputc('"', f);
}

#define PP       (SCRATCH + 0x000u)
#define OUTS     (SCRATCH + 0x100u)          /* one dword per out-parameter */
#define DSTRECT  (SCRATCH + 0x200u)
#define FRAMES   64u                         /* host/sa2_present_rec.c REC_WATCH_EVERY */

/* `n` frames of BeginScene, DrawPrimitive, EndScene and Present. */
static void frames(uint32_t dev, uint32_t n) {
    for (uint32_t k = 0; k < n; k++) {
        CALL0(dev, 41u);                                                /* BeginScene */
        CALL(dev, 81u, 4u, 0u, 2u);                                     /* DrawPrimitive */
        CALL0(dev, 42u);                                                /* EndScene */
        CALL(dev, 17u, 0u, 0u, 0u, 0u);                                 /* Present */
    }
}

int main(int argc, char **argv) {
    const char *out_path = NULL;
    int stale = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--out") && i + 1 < argc) out_path = argv[++i];
        else if (!strcmp(argv[i], "--stale")) stale = 1;
    }
    if (!out_path) {
        fprintf(stderr, "usage: rec_harness_mh --out PATH [--stale]\n");
        return 2;
    }
    g_mem = (uint8_t *)calloc(1, GUEST_HI - GUEST_LO);
    if (!g_mem) return 2;
    setenv("SA2_D3D9_DEVICE", "1", 1);
    sa2_d3d9_config_from_env();

    const uint32_t pp[14] = {1280u, 720u, 21u, 1u, 0u, 0u, 1u, 0u, 1u, 1u, 75u, 0u, 0u, 0u};
    put_words(PP, pp, 14);
    const uint32_t d3d9 = sa2_d3d9_make_d3d9(CALLER);
    CALL(d3d9, 16u, 0u, 1u, 0x7e000000u, 0x40u, PP, OUTS + 0u);          /* CreateDevice */
    const uint32_t dev = peek32(OUTS + 0u);

    CALL(dev, 18u, 0u, 0u, 0u, OUTS + 4u);                              /* GetBackBuffer */
    const uint32_t bb = peek32(OUTS + 4u);
    CALL(dev, 38u, 0u, OUTS + 8u);                                      /* GetRenderTarget */
    const uint32_t rt = peek32(OUTS + 8u);
    CALL(dev, 40u, OUTS + 12u);                                         /* GetDepthStencilSurface */
    const uint32_t ds = peek32(OUTS + 12u);

    /* A vertex buffer of 0x600 bytes: 0x200 of it at 0x100, then all of it. */
    CALL(dev, 26u, 0x600u, 8u, 0u, 0u, OUTS + 16u, 0u);                 /* CreateVertexBuffer */
    const uint32_t vb = peek32(OUTS + 16u);
    CALL(vb, 11u, 0x100u, 0x200u, OUTS + 20u, 0u);                      /* Lock */
    const uint32_t part = peek32(OUTS + 20u);
    put_pattern(part, 0x200u, 2u);
    FILE *f = fopen(out_path, "w");
    if (!f) return 2;
    fprintf(f, "{\n \"bytes\": {\"part\": ");
    hex_of(f, part, 0x200u);
    CALL0(vb, 12u);                                                     /* Unlock */
    CALL(vb, 11u, 0u, 0u, OUTS + 20u, 0x2000u);                         /* Lock, DISCARD */
    const uint32_t whole = peek32(OUTS + 20u);
    put_pattern(whole, 0x600u, 3u);
    fprintf(f, ", \"whole\": ");
    hex_of(f, whole, 0x600u);
    fprintf(f, "},\n");
    CALL0(vb, 12u);                                                     /* Unlock */

    /* A render-target texture's level and a depth surface of its own, as the targets. */
    CALL(dev, 23u, 256u, 256u, 1u, 1u, 21u, 0u, OUTS + 24u, 0u);         /* CreateTexture */
    const uint32_t rtt = peek32(OUTS + 24u);
    CALL(rtt, 18u, 0u, OUTS + 28u);                                     /* GetSurfaceLevel */
    const uint32_t rts = peek32(OUTS + 28u);
    CALL(dev, 29u, 256u, 256u, 75u, 0u, 0u, 1u, OUTS + 32u, 0u);         /* CreateDepthStencilSurface */
    const uint32_t ds2 = peek32(OUTS + 32u);
    CALL(dev, 37u, 0u, rts);                                            /* SetRenderTarget */
    CALL(dev, 39u, ds2);                                                /* SetDepthStencilSurface */
    CALL0(dev, 41u);                                                    /* BeginScene */
    CALL(dev, 100u, 0u, vb, 0u, 24u);                                   /* SetStreamSource */
    CALL(dev, 81u, 4u, 0u, 2u);                                         /* DrawPrimitive */
    CALL0(dev, 42u);                                                    /* EndScene */
    CALL(dev, 37u, 0u, rt);
    CALL(dev, 39u, ds);

    /* The back buffer into the texture's level, to a rectangle of it. */
    put_words(DSTRECT, (const uint32_t[]){0u, 0u, 128u, 128u}, 4);
    note_dx(sa2_d3d9_d3dx_load_surface_from_surface(
        (const uint32_t[]){rts, 0u, DSTRECT, bb, 0u, 0u, 0xFFFFFFFFu, 0u}, CALLER));

    if (stale) {
        const uint8_t b = 0xA5u;
        sa2_poke(whole + 5u, 1, &b);          /* after the Unlock: the forbidden write */
    }
    frames(dev, FRAMES);                      /* the watch's first check: the stale write */
    /* Locked whole again, and a nested lock of 0x40 bytes at 0x40 inside it, filled and
     * closed; that range is written again under the outer lock, which D3D9 allows, and
     * the second check comes while the outer lock is open, so it must count nothing. */
    CALL(vb, 11u, 0u, 0u, OUTS + 20u, 0u);                              /* Lock, outer */
    const uint32_t again = peek32(OUTS + 20u);
    put_pattern(again, 0x600u, 6u);
    CALL(vb, 11u, 0x40u, 0x40u, OUTS + 36u, 0u);                        /* Lock, nested */
    const uint32_t inner = peek32(OUTS + 36u);
    put_pattern(inner, 0x40u, 8u);
    fprintf(f, " \"inner\": ");
    hex_of(f, inner, 0x40u);
    fprintf(f, ",\n");
    CALL0(vb, 12u);                                                     /* Unlock, nested */
    put_pattern(inner, 0x40u, 9u);
    frames(dev, FRAMES);
    fprintf(f, " \"again\": ");
    hex_of(f, again, 0x600u);
    fprintf(f, ",\n");
    CALL0(vb, 12u);                                                     /* Unlock, outer */
    CALL0(vb, 2u);                                                      /* Release: 0 */
    /* The store is the heap's again, and what the heap's next owner writes there is
     * not the buffer's: the third check must not count it. */
    put_pattern(again, 0x600u, 7u);
    frames(dev, FRAMES);

    sa2_d3d9_stats st;
    sa2_d3d9_stats_get(&st);
    fprintf(f, " \"answers\": [");
    for (int i = 0; i < g_ncalls; i++)
        fprintf(f, "%s[%u, %u]", i ? ", " : "", g_answers[i][0], g_answers[i][1]);
    fprintf(f, "],\n \"guest_fnv\": \"%016llx\",\n",
            (unsigned long long)fnv1a(g_mem, GUEST_HI - GUEST_LO));
    fprintf(f, " \"stats\": [%u, %u, %u, %u, %u, %u, %u, %u, %u, %u, %u],\n",
            st.method_calls, st.objects_built, st.present_calls, st.beginscene_calls,
            st.draw_calls, st.clear_calls, st.guest_bytes_allocated, st.tex_from_file,
            st.d3dx_filter_calls, st.noop, st.from_guest);
    fprintf(f, " \"objects\": {\"device\": %u, \"back_buffer\": %u, \"render_target\": %u, "
               "\"depth\": %u, \"buffer\": %u, \"rt_texture\": %u, \"rt_level\": %u, "
               "\"rt_depth\": %u},\n", dev, bb, rt, ds, vb, rtt, rts, ds2);
    fprintf(f, " \"locks\": [%u, %u, %u, %u],\n \"frames\": %u\n}\n", part, whole, again, inner,
            3u * FRAMES);
    fclose(f);
    return 0;
}
