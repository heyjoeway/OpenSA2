/* The recording device's closed world (runtime/sa2_d3d9.c; the user's ruling of
 * 2026-10-09, item 1), driven through the real device and its real guest vtables over
 * a stand-in guest memory, heap, function table and register file. No game, no shipped
 * bytes, no player.
 *
 *   usage: d3d9_closed_world_probe MODE RESULTS.json
 *
 * Modes (Documentation/scripts/tests/test_d3d9_closed_world.py runs each):
 *   table     every named (interface, slot) row with its listed and implemented flags,
 *             for the test to hold against the receipt's called set
 *   mode      the world mode sa2_d3d9_config_from_env chose from this environment
 *   listed    a sequence of listed methods only, one trace row per call (EAX, how far
 *             ESP moved, an FNV-1a digest of the whole guest window after the call),
 *             and the device's counters: what a build that traps and a build that does
 *             not must agree on byte for byte
 *   trap      every named row off the list, called through its thunk, must trap by
 *             name with its caller and `this`, without moving ESP or a guest byte;
 *             a listed method still answers afterwards; an unnamed slot refuses as before
 *   unlisted  four unlisted device methods (two m_ok/m_zero no-ops, a real handler and
 *             one with no handler) and an unnamed slot, once or more each: under log or
 *             open mode the first three answer and are traced like `listed`; the fourth
 *             and the unnamed slot refuse by their old notes
 *
 * Every mode writes RESULTS.json with its checks, the failures by id and the device's
 * closed_world block (sa2_d3d9_closed_world_json) as the run record carries it.
 *
 * Compiled with -DPROBE_PRE_CLOSED_WORLD it builds against a device from before the
 * closed world (no closed-world API), with only the table-free modes `mode` and
 * `listed`, so a listed trace can be held to the device as it was.
 */
#include <setjmp.h>
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

/* ------------------------------------------------------------ the stand-ins */

#define GUEST_LO   0x03000000u
#define GUEST_HI   0x03300000u    /* covers the d3d9 objects and vtables (sa2_d3d9.h) */
#define STACK_TOP  0x030eff00u
#define SCRATCH    0x03004000u

static uint8_t *g_mem;

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
#define HEAP_LO 0x03100008u
#define HEAP_HI 0x03180000u
static uint32_t g_heap_next = HEAP_LO, g_heap_allocs, g_heap_frees;
uint32_t sa2_heap_alloc_owned(uint32_t owner, uint32_t bytes) {
    (void)owner;
    if (!bytes || g_heap_next >= HEAP_HI || bytes > HEAP_HI - g_heap_next) return 0;
    const uint32_t a = g_heap_next;
    g_heap_next = (a + bytes + 7u) & ~7u;
    sa2_guest_fill(a, 0, bytes);
    g_heap_allocs++;
    return a;
}
void sa2_heap_free_owned(uint32_t owner, uint32_t payload) { (void)owner; (void)payload; g_heap_frees++; }

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
uint32_t sa2_rt_get_gpr(const void *state, int idx) { return ((const probe_state *)state)->gpr[idx]; }
void sa2_rt_set_gpr(void *state, int idx, uint32_t value) { ((probe_state *)state)->gpr[idx] = value; }
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
static jmp_buf g_trap_env;
static int g_trap_armed;
static uint32_t g_trap_status, g_trap_pc;
void sa2_trap_note(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_note, sizeof g_note, fmt, ap);
    va_end(ap);
}
void sa2_trap_raise(uint32_t status, uint32_t pc) {
    g_trap_status = status;
    g_trap_pc = pc;
    if (g_trap_armed) longjmp(g_trap_env, 1);
    fprintf(stderr, "unexpected device trap %u at %#x: %s\n", status, pc, g_note);
    exit(3);
}

/* ------------------------------------------------------------ checks */

static uint32_t g_checks, g_fails;
static char g_failed[64][96];
static void check(int cond, const char *id, const char *fmt, ...) __attribute__((format(printf, 3, 4)));
static void check(int cond, const char *id, const char *fmt, ...) {
    g_checks++;
    if (cond) return;
    if (g_fails < 64) snprintf(g_failed[g_fails], sizeof g_failed[0], "%s", id);
    g_fails++;
    char msg[600];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    printf("FAIL %s: %s\n", id, msg);
}

static void pk32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static uint32_t pe32(uint32_t a) {
    uint32_t v = 0;
    return sa2_peek(a, 4, &v) == SA2_OK ? v : 0xDEADBEEFu;
}
static uint64_t digest(void) {               /* FNV-1a 64 over the whole guest window */
    uint64_t h = 1469598103934665603ull;
    for (uint32_t i = 0; i < GUEST_HI - GUEST_LO; i++) { h ^= g_mem[i]; h *= 1099511628211ull; }
    return h;
}

/* ------------------------------------------------------------ calls */

#define RETADDR 0x00401000u    /* the guest return address every call is made with */

/* The type index of interface `iface` and the slot of its method `method`, both read
 * from the device's own table; the probe stops when either is missing. */
static uint32_t type_of(const char *iface) {
    for (uint32_t t = 0; t < sa2_d3d9_type_count(); t++)
        if (!strcmp(sa2_d3d9_type_name(t), iface)) return t;
    fprintf(stderr, "the device has no interface %s\n", iface);
    exit(2);
}
static uint32_t slot_of(const char *iface, const char *method) {
    const uint32_t t = type_of(iface);
    for (uint32_t k = 0; k < SA2_D3D9_MAX_SLOTS; k++) {
        const char *n = sa2_d3d9_method_name(t, k);
        if (n && !strcmp(n, method)) return k;
    }
    fprintf(stderr, "the device has no method %s::%s\n", iface, method);
    exit(2);
}

/* The notes of the traps a mode chose to keep, in order, for the results file. */
#define NOTES_MAX 16
static char g_notes[NOTES_MAX][300];
static uint32_t g_nnotes;
static int g_record_notes;

typedef struct {
    int      trapped;
    uint32_t eax, popped, esp0, esp1, status, pc;
    uint64_t before, after;      /* guest digests with the arguments pushed, and after */
    char     note[300];
} answer;

/* Enter the thunk of (t, slot) with `self` and `argc` arguments on the guest stack under
 * RETADDR, as lifted code does. A trap is caught and reported, not fatal. */
static answer call_thunk(uint32_t t, uint32_t slot, uint32_t self, uint32_t argc,
                         const uint32_t *argv) {
    answer r;
    memset(&r, 0, sizeof r);
    const uint32_t thunk = SA2_D3D9_THUNK_BASE + (t * SA2_D3D9_MAX_SLOTS + slot) * SA2_D3D9_THUNK_STRIDE;
    sa2_lifted_fn fn = g_fn[(thunk - SA2_D3D9_THUNK_BASE) / SA2_D3D9_THUNK_STRIDE];
    if (!fn) { fprintf(stderr, "no thunk at %#010x (no object built yet?)\n", thunk); exit(2); }
    static probe_state st;
    memset(&st, 0, sizeof st);
    const uint32_t esp0 = STACK_TOP - 4u * (argc + 2u);
    st.gpr[SA2_ESP] = esp0;
    st.gpr[SA2_EAX] = 0xA5A5A5A5u;
    pk32(esp0, RETADDR);
    pk32(esp0 + 4u, self);
    for (uint32_t i = 0; i < argc; i++) pk32(esp0 + 8u + 4u * i, argv[i]);
    r.esp0 = esp0;
    r.before = digest();
    g_note[0] = 0;
    g_trap_armed = 1;
    if (setjmp(g_trap_env)) {
        g_trap_armed = 0;
        r.trapped = 1;
        r.status = g_trap_status;
        r.pc = g_trap_pc;
        snprintf(r.note, sizeof r.note, "%s", g_note);
        if (g_record_notes && g_nnotes < NOTES_MAX)
            snprintf(g_notes[g_nnotes++], sizeof g_notes[0], "%s", g_note);
    } else {
        fn(&st, thunk, NULL);
        g_trap_armed = 0;
    }
    r.esp1 = st.gpr[SA2_ESP];
    r.popped = st.gpr[SA2_ESP] - esp0;
    r.eax = st.gpr[SA2_EAX];
    r.after = digest();
    return r;
}
static answer call_vt(uint32_t self, const char *iface, const char *method, uint32_t argc,
                      const uint32_t *argv) {
    return call_thunk(type_of(iface), slot_of(iface, method), self, argc, argv);
}

/* The trace a listed or answered unlisted call leaves, for two builds to compare. */
#define TRACE_MAX 128
typedef struct { const char *name; uint32_t eax, popped; uint64_t after; int trapped; } trace_row;
static trace_row g_trace[TRACE_MAX];
static uint32_t g_ntrace;

static uint32_t traced(uint32_t self, const char *iface, const char *method, uint32_t argc,
                       const uint32_t *argv) {
    const answer r = call_vt(self, iface, method, argc, argv);
    if (g_ntrace < TRACE_MAX)
        g_trace[g_ntrace++] = (trace_row){method, r.eax, r.popped, r.after, r.trapped};
    check(!r.trapped, "listed-no-trap", "%s::%s trapped: %s", iface, method, r.note);
    check(r.popped == 4u * (argc + 2u), "listed-pop", "%s::%s popped %u", iface, method, r.popped);
    return r.eax;
}
#define T0(self, iface, method) traced(self, iface, method, 0, NULL)
#define T(self, iface, method, n, ...) traced(self, iface, method, n, (const uint32_t[]){__VA_ARGS__})

#define PP     (SCRATCH + 0x0000u)
#define OUT    (SCRATCH + 0x0100u)   /* one dword per out-parameter, 16 of them */
#define BUF    (SCRATCH + 0x0200u)   /* 0x600 bytes: caps, identifiers, descriptions */
#define VPORT  (SCRATCH + 0x0800u)
#define RECT   (SCRATCH + 0x0840u)
#define DECL   (SCRATCH + 0x0880u)
#define CONSTS (SCRATCH + 0x0900u)
#define VERTS  (SCRATCH + 0x0A00u)
#define IID    (SCRATCH + 0x0B00u)

static void configure(void) {
    setenv("SA2_D3D9_DEVICE", "1", 1);
    unsetenv("SA2_D3D9_BREAK");
    setenv("SA2_D3DX_STRICT", "0", 1);
    setenv("SA2_D3DX_CONSTANT_TABLE", "0", 1);
    sa2_d3d9_config_from_env();
}

/* IDirect3D9 and its device, through listed methods only. */
static uint32_t make_device(uint32_t *d3d9_out) {
    const uint32_t d3d9 = sa2_d3d9_make_d3d9(0x00401234u);
    const uint32_t pp[14] = {640u, 480u, 22u, 1u, 0u, 0u, 1u, 0u, 1u, 1u, 75u, 0u, 0u, 0u};
    sa2_poke(PP, sizeof pp, pp);
    T0(d3d9, "IDirect3D9", "GetAdapterCount");
    T(d3d9, "IDirect3D9", "GetAdapterIdentifier", 3, 0u, 0u, BUF);
    T(d3d9, "IDirect3D9", "GetAdapterDisplayMode", 2, 0u, BUF);
    T(d3d9, "IDirect3D9", "CheckDeviceMultiSampleType", 6, 0u, 1u, 22u, 1u, 0u, OUT);
    T(d3d9, "IDirect3D9", "GetAdapterMonitor", 1, 0u);
    pk32(OUT, 0);
    T(d3d9, "IDirect3D9", "CreateDevice", 6, 0u, 1u, 0u, 0x40u, PP, OUT);
    *d3d9_out = d3d9;
    return pe32(OUT);
}

/* ------------------------------------------------------------ modes */

static void mode_listed(void) {
    uint32_t d3d9 = 0;
    const uint32_t dev = make_device(&d3d9);
    check(dev != 0, "device", "CreateDevice wrote no device");
    const char *D = "IDirect3DDevice9";
    T0(dev, D, "TestCooperativeLevel");
    T(dev, D, "GetDeviceCaps", 1, BUF);
    T(dev, D, "QueryInterface", 2, IID, OUT + 0x3cu);
    /* a texture, its level, its surface locked and unlocked */
    T(dev, D, "CreateTexture", 8, 64u, 64u, 2u, 0u, 21u, 1u, OUT + 4u, 0u);
    const uint32_t tex = pe32(OUT + 4u);
    T0(tex, "IDirect3DTexture9", "GetLevelCount");
    T(tex, "IDirect3DTexture9", "GetLevelDesc", 2, 1u, BUF);
    T(tex, "IDirect3DTexture9", "GetSurfaceLevel", 2, 0u, OUT + 8u);
    const uint32_t surf = pe32(OUT + 8u);
    T(surf, "IDirect3DSurface9", "LockRect", 3, BUF + 0x40u, 0u, 0u);
    T0(surf, "IDirect3DSurface9", "UnlockRect");
    T(dev, D, "SetTexture", 2, 0u, tex);
    T(dev, D, "SetSamplerState", 3, 0u, 5u, 2u);
    /* buffers, a declaration, streams and indices */
    T(dev, D, "CreateVertexBuffer", 6, 96u, 8u, 0u, 0u, OUT + 12u, 0u);
    const uint32_t vb = pe32(OUT + 12u);
    T(vb, "IDirect3DVertexBuffer9", "Lock", 4, 0u, 0u, OUT + 16u, 0u);
    T0(vb, "IDirect3DVertexBuffer9", "Unlock");
    T(dev, D, "CreateIndexBuffer", 6, 12u, 8u, 101u, 0u, OUT + 20u, 0u);
    const uint32_t ib = pe32(OUT + 20u);
    T(ib, "IDirect3DIndexBuffer9", "Lock", 4, 0u, 0u, OUT + 24u, 0u);
    T0(ib, "IDirect3DIndexBuffer9", "Unlock");
    const uint32_t decl[4] = {0x00000000u, 0x00000202u, 0x000000ffu, 0x00000011u};
    sa2_poke(DECL, sizeof decl, decl);
    T(dev, D, "CreateVertexDeclaration", 2, DECL, OUT + 28u);
    const uint32_t vd = pe32(OUT + 28u);
    T(dev, D, "SetVertexDeclaration", 1, vd);
    T(dev, D, "SetStreamSource", 4, 0u, vb, 0u, 12u);
    T(dev, D, "SetStreamSourceFreq", 2, 0u, 1u);
    T(dev, D, "SetIndices", 1, ib);
    /* state, viewport, scissor, constants */
    T(dev, D, "SetRenderState", 2, 7u, 1u);
    T(dev, D, "GetRenderState", 2, 7u, OUT + 32u);
    T(dev, D, "GetRenderState", 2, 8u, OUT + 36u);
    const uint32_t vp[6] = {0u, 0u, 640u, 480u, 0u, 0x3f800000u};
    sa2_poke(VPORT, sizeof vp, vp);
    T(dev, D, "SetViewport", 1, VPORT);
    T(dev, D, "GetViewport", 1, VPORT + 0x20u);
    const uint32_t rc[4] = {1u, 2u, 300u, 200u};
    sa2_poke(RECT, sizeof rc, rc);
    T(dev, D, "SetScissorRect", 1, RECT);
    T(dev, D, "GetScissorRect", 1, RECT + 0x10u);
    for (uint32_t i = 0; i < 8u; i++) pk32(CONSTS + 4u * i, 0x3f800000u + i);
    T(dev, D, "SetVertexShaderConstantF", 3, 0u, CONSTS, 2u);
    T(dev, D, "SetPixelShaderConstantF", 3, 4u, CONSTS, 1u);
    T(dev, D, "SetVertexShaderConstantB", 3, 0u, CONSTS, 1u);
    T(dev, D, "SetPixelShaderConstantI", 3, 0u, CONSTS, 1u);
    /* targets */
    T(dev, D, "GetBackBuffer", 4, 0u, 0u, 0u, OUT + 40u);
    T(dev, D, "GetRenderTarget", 2, 0u, OUT + 44u);
    T(dev, D, "GetDepthStencilSurface", 1, OUT + 48u);
    T(dev, D, "CreateDepthStencilSurface", 8, 64u, 64u, 75u, 0u, 0u, 0u, OUT + 52u, 0u);
    T(dev, D, "SetDepthStencilSurface", 1, pe32(OUT + 52u));
    T(dev, D, "SetRenderTarget", 2, 0u, pe32(OUT + 44u));
    /* a frame */
    T0(dev, D, "BeginScene");
    T(dev, D, "Clear", 6, 0u, 0u, 7u, 0xff0000ffu, 0x3f800000u, 0u);
    T(dev, D, "DrawPrimitive", 3, 4u, 0u, 1u);
    T(dev, D, "DrawIndexedPrimitive", 6, 4u, 0u, 0u, 3u, 0u, 1u);
    T(dev, D, "DrawPrimitiveUP", 4, 4u, 1u, VERTS, 12u);
    T0(dev, D, "EndScene");
    T(dev, D, "Present", 4, 0u, 0u, 0u, 0u);
    /* releases */
    T(dev, D, "SetTexture", 2, 0u, 0u);
    T0(surf, "IDirect3DSurface9", "Release");
    T0(tex, "IDirect3DTexture9", "Release");
    T0(vb, "IDirect3DVertexBuffer9", "Release");
    T0(ib, "IDirect3DIndexBuffer9", "Release");
    T0(vd, "IDirect3DVertexDeclaration9", "Release");
    T0(dev, D, "Release");
    T0(d3d9, "IDirect3D9", "Release");
#ifndef PROBE_PRE_CLOSED_WORLD
    check(sa2_d3d9_unlisted_count() == 0u && sa2_d3d9_unlisted_calls() == 0u, "listed-unlisted",
          "a listed-only sequence counted %u unlisted calls", sa2_d3d9_unlisted_calls());
#endif
}

#ifndef PROBE_PRE_CLOSED_WORLD
/* Every named row off the list, called through its thunk on the device object, must
 * trap by name before its handler runs: no guest byte and no register moves. */
static void mode_trap(void) {
    uint32_t d3d9 = 0;
    const uint32_t dev = make_device(&d3d9);
    uint32_t unlisted = 0, trapped_right = 0, wrong = 0;
    for (uint32_t t = 0; t < sa2_d3d9_type_count(); t++)
        for (uint32_t k = 0; k < SA2_D3D9_MAX_SLOTS; k++) {
            const char *nm = sa2_d3d9_method_name(t, k);
            if (!nm || sa2_d3d9_method_listed(t, k)) continue;
            unlisted++;
            uint32_t args[16] = {0};
            for (uint32_t i = 0; i < 16u; i++) args[i] = OUT + 4u * i;
            const uint32_t argc = (uint32_t)sa2_d3d9_method_argc(t, k);
            const answer r = call_thunk(t, k, dev, argc, args);
            char want[300];
            snprintf(want, sizeof want, "d3d9 closed world: %s::%s called from %#010x on %#010x "
                     "is off the M7 list (runtime/sa2_d3d9_m7_list.h); gates and automated runs "
                     "trap here", sa2_d3d9_type_name(t), nm, RETADDR, dev);
            const uint32_t thunk = SA2_D3D9_THUNK_BASE + (t * SA2_D3D9_MAX_SLOTS + k) * SA2_D3D9_THUNK_STRIDE;
            const int ok = r.trapped && r.status == SA2_TRAP_CONTROL_LEFT && r.pc == thunk &&
                           !strcmp(r.note, want) && strlen(r.note) < SA2_TRAP_NOTE_MAX &&
                           r.esp1 == r.esp0 && r.eax == 0xA5A5A5A5u && r.after == r.before;
            if (ok) trapped_right++;
            else if (wrong++ < 8)
                check(0, "trap-unlisted", "%s::%s: trapped %d status %u pc %#x esp moved %u eax %#x "
                      "guest %s; note \"%s\"", sa2_d3d9_type_name(t), nm, r.trapped, r.status,
                      r.pc, r.esp1 - r.esp0, r.eax, r.after == r.before ? "same" : "moved", r.note);
        }
    check(unlisted > 0 && trapped_right == unlisted, "trap-every-unlisted",
          "%u of %u unlisted rows trapped by name", trapped_right, unlisted);
    check(sa2_d3d9_unlisted_count() == unlisted && sa2_d3d9_unlisted_calls() == unlisted,
          "trap-rows", "%u rows, %u calls, for %u unlisted methods", sa2_d3d9_unlisted_count(),
          sa2_d3d9_unlisted_calls(), unlisted);
    const sa2_d3d9_unlisted *first = sa2_d3d9_unlisted_at(0);
    check(first && first->outcome == SA2_D3D9_UNL_TRAPPED && first->first_caller == RETADDR &&
          first->first_self == dev && first->calls == 1u, "trap-row-fields",
          "the first row does not name the trap, the caller or this");
    /* The device answers a listed method after all of that. */
    const answer ok = call_vt(dev, "IDirect3DDevice9", "TestCooperativeLevel", 0, NULL);
    check(!ok.trapped && ok.eax == 0u && ok.popped == 8u, "trap-then-listed",
          "TestCooperativeLevel after the traps: trapped %d eax %#x popped %u", ok.trapped,
          ok.eax, ok.popped);
    /* An unnamed slot refuses by its own note, as before the ruling, and is counted. */
    const answer un = call_thunk(type_of("IDirect3DDevice9"), 120u, dev, 0, NULL);
    check(un.trapped && strstr(un.note, "vtable slot 120 called from 0x00401000") &&
          strstr(un.note, "this runtime does not name that slot"), "trap-unnamed",
          "slot 120: trapped %d, note \"%s\"", un.trapped, un.note);
    const sa2_d3d9_unlisted *last = sa2_d3d9_unlisted_at(sa2_d3d9_unlisted_count() - 1u);
    check(last && last->slot == 120u && last->outcome == SA2_D3D9_UNL_UNNAMED, "trap-unnamed-row",
          "the unnamed slot's row is missing or misclassed");
    sa2_d3d9_stats s;
    sa2_d3d9_stats_get(&s);
    check(s.refused == unlisted + 1u, "trap-refused-count",
          "%u calls recorded as refused, wanted %u", s.refused, unlisted + 1u);
}

/* Four unlisted device methods and an unnamed slot, for log and open builds. */
static void mode_unlisted(void) {
    uint32_t d3d9 = 0;
    const uint32_t dev = make_device(&d3d9);
    const char *D = "IDirect3DDevice9";
    g_ntrace = 0;
    g_record_notes = 1;
    T0(dev, D, "EvictManagedResources");                 /* m_ok */
    T0(dev, D, "EvictManagedResources");
    for (uint32_t i = 0; i < 3u; i++)
        T(dev, D, "SetGammaRamp", 3, 0u, 0u, BUF);       /* m_zero, void */
    T(dev, D, "SetFVF", 1, 0x142u);                      /* a real handler */
    const answer nohandler = call_vt(dev, D, "GetPaletteEntries", 2, (const uint32_t[]){0u, BUF});
    char want[300];
    snprintf(want, sizeof want, "IDirect3DDevice9::GetPaletteEntries called from %#010x on %#010x: "
             "it writes through a pointer the guest then dereferences, and this runtime has no "
             "honest value to put there", RETADDR, dev);
    check(nohandler.trapped && !strcmp(nohandler.note, want), "unlisted-nohandler",
          "GetPaletteEntries: trapped %d, note \"%s\"", nohandler.trapped, nohandler.note);
    const answer un = call_thunk(type_of(D), 120u, dev, 0, NULL);
    check(un.trapped && strstr(un.note, "this runtime does not name that slot"), "unlisted-unnamed",
          "slot 120: trapped %d, note \"%s\"", un.trapped, un.note);
    check(sa2_d3d9_unlisted_count() == 5u && sa2_d3d9_unlisted_calls() == 8u, "unlisted-counts",
          "%u methods, %u calls; wanted 5 and 8", sa2_d3d9_unlisted_count(),
          sa2_d3d9_unlisted_calls());
    static const struct { const char *method; uint32_t calls; uint8_t outcome; } want_rows[] = {
        {"EvictManagedResources", 2u, SA2_D3D9_UNL_ANSWERED},
        {"SetGammaRamp", 3u, SA2_D3D9_UNL_ANSWERED},
        {"SetFVF", 1u, SA2_D3D9_UNL_ANSWERED},
        {"GetPaletteEntries", 1u, SA2_D3D9_UNL_REFUSED},
        {NULL, 1u, SA2_D3D9_UNL_UNNAMED},
    };
    for (uint32_t i = 0; i < 5u; i++) {
        const sa2_d3d9_unlisted *r = sa2_d3d9_unlisted_at(i);
        const char *nm = r ? sa2_d3d9_method_name(r->type, r->slot) : NULL;
        const int same = r && r->calls == want_rows[i].calls && r->outcome == want_rows[i].outcome &&
                         r->first_caller == RETADDR && r->first_self == dev &&
                         (want_rows[i].method ? nm && !strcmp(nm, want_rows[i].method) : !nm);
        check(same, "unlisted-row", "row %u is not %s x%u", i,
              want_rows[i].method ? want_rows[i].method : "<unnamed>", want_rows[i].calls);
    }
}

#endif  /* PROBE_PRE_CLOSED_WORLD */

/* ------------------------------------------------------------ output */

static void write_results(const char *path, const char *mode) {
    FILE *f = fopen(path, "w");
    if (!f) { perror(path); exit(2); }
    fprintf(f, "{\"mode\": \"%s\", \"checks\": %u, \"failures\": %u, \"failed\": [", mode,
            g_checks, g_fails);
    for (uint32_t i = 0; i < g_fails && i < 64u; i++) fprintf(f, "%s\"%s\"", i ? ", " : "", g_failed[i]);
    fprintf(f, "],\n \"notes\": [");
    for (uint32_t i = 0; i < g_nnotes; i++) {
        fprintf(f, "%s\n  \"", i ? "," : "");
        for (const char *c = g_notes[i]; *c; c++) {
            if (*c == '"' || *c == '\\') fputc('\\', f);
            fputc(*c, f);
        }
        fputc('"', f);
    }
    fprintf(f, "],\n \"trace\": [");
    for (uint32_t i = 0; i < g_ntrace; i++)
        fprintf(f, "%s\n  {\"method\": \"%s\", \"eax\": \"0x%08x\", \"popped\": %u, "
                   "\"guest\": \"%016llx\", \"trapped\": %s}", i ? "," : "", g_trace[i].name,
                g_trace[i].eax, g_trace[i].popped, (unsigned long long)g_trace[i].after,
                g_trace[i].trapped ? "true" : "false");
    sa2_d3d9_stats s;
    sa2_d3d9_stats_get(&s);
    fprintf(f, "],\n \"stats\": {\"method_calls\": %u, \"distinct_methods\": %u, \"refused\": %u, "
               "\"noop\": %u, \"from_guest\": %u, \"invented\": %u, \"objects\": %u, "
               "\"unset\": %u, \"objects_built\": %u, \"objects_destroyed\": %u, "
               "\"guest_bytes_allocated\": %u, \"guest_bytes_freed\": %u, \"draw_calls\": %u, "
               "\"present_calls\": %u, \"log\": %u, \"heap_allocs\": %u, \"heap_frees\": %u}",
            s.method_calls, s.distinct_methods, s.refused, s.noop, s.from_guest, s.invented,
            s.objects, s.unset, s.objects_built, s.objects_destroyed, s.guest_bytes_allocated,
            s.guest_bytes_freed, s.draw_calls, s.present_calls, sa2_d3d9_log_count(),
            g_heap_allocs, g_heap_frees);
#ifndef PROBE_PRE_CLOSED_WORLD
    fprintf(f, ",\n \"world_mode\": \"%s\", \"table\": [", sa2_d3d9_world_mode_name());
    uint32_t n = 0;
    if (!strcmp(mode, "table"))
        for (uint32_t t = 0; t < sa2_d3d9_type_count(); t++)
            for (uint32_t k = 0; k < SA2_D3D9_MAX_SLOTS; k++) {
                const char *nm = sa2_d3d9_method_name(t, k);
                if (!nm) continue;
                fprintf(f, "%s\n  {\"type\": %u, \"interface\": \"%s\", \"method\": \"%s\", "
                           "\"slot\": %u, \"listed\": %s, \"implemented\": %s}", n++ ? "," : "",
                        t, sa2_d3d9_type_name(t), nm, k,
                        sa2_d3d9_method_listed(t, k) ? "true" : "false",
                        sa2_d3d9_method_implemented(t, k) ? "true" : "false");
            }
    fprintf(f, "],\n \"listed_count\": %u, \"list_digest\": \"%s\",\n ",
            sa2_d3d9_listed_count(), sa2_d3d9_list_digest());
    sa2_d3d9_closed_world_json(f);
#endif
    fprintf(f, "}\n");
    fclose(f);
}

int main(int argc, char **argv) {
    if (argc != 3) {
        fprintf(stderr, "usage: d3d9_closed_world_probe table|mode|listed|trap|unlisted RESULTS.json\n");
        return 2;
    }
    const char *mode = argv[1];
    g_mem = calloc(1, GUEST_HI - GUEST_LO);
    if (!g_mem) return 2;
    configure();
    if (!strcmp(mode, "listed")) mode_listed();
#ifndef PROBE_PRE_CLOSED_WORLD
    else if (!strcmp(mode, "trap")) mode_trap();
    else if (!strcmp(mode, "unlisted")) mode_unlisted();
#endif
    else if (strcmp(mode, "table") && strcmp(mode, "mode")) {
        fprintf(stderr, "unknown mode %s\n", mode);
        return 2;
    }
    write_results(argv[2], mode);
    printf("d3d9_closed_world_probe %s: %u checks, %u failures\n", mode, g_checks, g_fails);
    fflush(stdout);
    free(g_mem);
    return g_fails ? 1 : 0;
}
