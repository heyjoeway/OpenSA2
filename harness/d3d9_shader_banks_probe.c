/* F/I/B shader constants, driven through the real recording device's guest vtable.
 *
 * Usage: d3d9_shader_banks_probe OWNED_RESULTS.json
 * Build: zig cc -target x86_64-linux-musl -D_GNU_SOURCE -O1 -Wall -Wextra -Werror
 *        -Iruntime harness/d3d9_shader_banks_probe.c runtime/sa2_d3d9.c -lm -o PROBE
 *
 * The authored guest memory, CPU state, function table, window and scheduler
 * below provide fixture services. Actual runtime dispatch and Set/Get methods
 * run unchanged. No image, shipped bytes, native DLL, replay or GPU is used.
 * Distinct literal Float/Int values and opposite Bool values must survive writes
 * to the other type/stage. Register 11 reads check separate unset bitmaps.
 * JSON also records actual EAX/ESP results for all 32 calls, independently
 * checked by test_d3d9_shader_banks_probe.py against literal slot/ABI rows.
 *
 * The 86 checks and driver are preserved from the independently reviewed
 * extended probe. This covers those banks and calls only: native bounds,
 * Reset, stateblocks and multiple-device lifetime remain untested here.
 */
#include <setjmp.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/wait.h>
#include <unistd.h>

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
/* These constant-bank calls need no guest heap allocation. */
uint32_t sa2_heap_alloc_owned(uint32_t owner, uint32_t bytes) { (void)owner; (void)bytes; return 0; }
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
void sa2_sched_frame_boundary(void) {}
uint64_t sa2_sched_frames(void) { return 0; }
uint32_t sa2_window_screen_w(void) { return 640u; }
uint32_t sa2_window_screen_h(void) { return 480u; }

/* Legacy fixture trap services are retained; this driver creates no child. */
static char g_note[1024];
static int g_child_fd = -1;
static uint32_t g_report_slot;
void sa2_trap_note(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(g_note, sizeof g_note, fmt, ap);
    va_end(ap);
}
void sa2_trap_raise(uint32_t status, uint32_t exit_pc) {
    if (g_child_fd >= 0) {
        uint32_t out = 0;
        if (g_report_slot) sa2_peek(g_report_slot, 4, &out);
        sa2_d3d9_stats s;
        sa2_d3d9_stats_get(&s);
        dprintf(g_child_fd, "TRAP %u %#010x [out %#010x built %u] %s", status, exit_pc, out,
                s.constant_tables_built, g_note);
        _exit(3);
    }
    fprintf(stderr, "d3d9_shader_banks_probe: an unexpected trap %u at %#010x: %s\n", status, exit_pc,
            g_note);
    exit(2);
}

/* ------------------------------------------------------------ the checks */

static uint32_t g_checks, g_fails;
static char g_failed[64][48];

static void check(int cond, const char *id, const char *fmt, ...)
    __attribute__((format(printf, 3, 4)));
static void check(int cond, const char *id, const char *fmt, ...) {
    g_checks++;
    if (cond) return;
    if (g_fails < 64) snprintf(g_failed[g_fails], sizeof g_failed[0], "%s", id);
    g_fails++;
    char msg[512];
    va_list ap;
    va_start(ap, fmt);
    vsnprintf(msg, sizeof msg, fmt, ap);
    va_end(ap);
    printf("FAIL %s: %s\n", id, msg);
}

static void pk32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static uint32_t peek32(uint32_t a) {
    uint32_t v = 0;
    return sa2_peek(a, 4, &v) == SA2_OK ? v : 0xDEADBEEFu;
}

#define RETADDR 0x00401000u  /* the return address every vtable call is made with */

/* Call vtable slot `slot` of the object at `self` as lifted code does: `this`
 * and the arguments on the guest stack under a return address, the thunk the
 * vtable names entered with ESP at that return address. Answers EAX and, in
 * `popped`, how far ESP moved. */
static uint32_t call_slot(uint32_t self, uint32_t slot, uint32_t argc, const uint32_t *argv,
                          uint32_t *popped) {
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
    *popped = st.gpr[SA2_ESP] - esp0;
    return st.gpr[SA2_EAX];
}

struct abi_observation { uint32_t slot, argc, eax, popped; };
static struct abi_observation ABI[64];
static uint32_t ABI_N;

/* An answered call: it must pop exactly 4 * (argc + 2) bytes. */
static uint32_t answer(const char *id, uint32_t self, uint32_t slot, uint32_t argc,
                       const uint32_t *argv) {
    uint32_t popped = 0;
    const uint32_t eax = call_slot(self, slot, argc, argv, &popped);
    ABI[ABI_N++] = (struct abi_observation){slot, argc, eax, popped};
    check(popped == 4u * (argc + 2u), id, "slot %u popped %u bytes, wanted %u", slot, popped,
          4u * (argc + 2u));
    return eax;
}
#define CALL0(id, self, slot)        answer(id, self, slot, 0, NULL)
#define CALL(id, self, slot, n, ...) answer(id, self, slot, n, (const uint32_t[]){__VA_ARGS__})


/* Authored constant-bank driver; values do not come from runtime storage. */
#define PP (SCRATCH + 0x0000u)
#define OUT_DEV (SCRATCH + 0x0104u)
#define IN_F (SCRATCH + 0x1000u)
#define IN_I (SCRATCH + 0x1100u)
#define IN_B (SCRATCH + 0x1200u)
#define READ_OUT (SCRATCH + 0x1300u)

static const uint32_t VS_F[4] = {0x3f800000u, 0x40000000u, 0x40400000u, 0x40800000u};
static const uint32_t PS_F[4] = {0x40a00000u, 0x40c00000u, 0x40e00000u, 0x41000000u};
static const uint32_t VS_I[4] = {11u, 22u, 33u, 44u};
static const uint32_t PS_I[4] = {55u, 66u, 77u, 88u};
static const uint32_t WANT_B[4] = {1u, 0u, 0u, 0u};
struct observation {
    const char *id;
    uint32_t n, actual[4], expected[4];
    int matches;
};
static struct observation OBS[32];
static uint32_t OBS_N;

static void set_bank(uint32_t dev, uint32_t slot, uint32_t addr, const uint32_t *v,
                     uint32_t n, const char *id) {
    sa2_poke(addr, 4u*n, v);
    uint32_t hr = CALL(id, dev, slot, 3, 0u, addr, 1u);
    check(hr == 0u, id, "Set returned %#010x, wanted D3D_OK", hr);
}
static void read_bank(uint32_t dev, uint32_t slot, const uint32_t *wanted,
                      uint32_t n, const char *id) {
    struct observation *o=&OBS[OBS_N++];
    o->id=id; o->n=n;
    for(uint32_t k=0;k<4;k++) pk32(READ_OUT+4u*k, 0xa5a5a5a5u);
    uint32_t hr = CALL(id, dev, slot, 3, 0u, READ_OUT, 1u);
    check(hr == 0u, id, "Get returned %#010x, wanted D3D_OK", hr);
    sa2_peek(READ_OUT, 4u*n, o->actual);
    memcpy(o->expected, wanted, 4u*n);
    o->matches=!memcmp(o->actual, o->expected, 4u*n);
    check(o->matches, id, "read %#010x/%#010x/%#010x/%#010x wanted %#010x/%#010x/%#010x/%#010x",
          o->actual[0], o->actual[1], o->actual[2], o->actual[3],
          o->expected[0], o->expected[1], o->expected[2], o->expected[3]);
}
static void read_bank_indexed(uint32_t dev, uint32_t slot, uint32_t index, const uint32_t *wanted,
                      uint32_t n, const char *id) {
    struct observation *o=&OBS[OBS_N++];
    o->id=id; o->n=n;
    for(uint32_t k=0;k<4;k++) pk32(READ_OUT+4u*k, 0xa5a5a5a5u);
    uint32_t hr = CALL(id, dev, slot, 3, index, READ_OUT, 1u);
    check(hr == 0u, id, "Get returned %#010x, wanted D3D_OK", hr);
    sa2_peek(READ_OUT, 4u*n, o->actual);
    memcpy(o->expected, wanted, 4u*n);
    o->matches=!memcmp(o->actual, o->expected, 4u*n);
    check(o->matches, id, "read %#010x/%#010x/%#010x/%#010x wanted %#010x/%#010x/%#010x/%#010x",
          o->actual[0], o->actual[1], o->actual[2], o->actual[3],
          o->expected[0], o->expected[1], o->expected[2], o->expected[3]);
}
int main(int argc, char **argv) {
    if(argc!=2) {fprintf(stderr,"usage: d3d9_shader_banks_probe OWNED_RESULTS.json\n");return 2;}
    g_mem=calloc(1,GUEST_HI-GUEST_LO);
    if(!g_mem) return 2;
    setenv("SA2_D3D9_DEVICE","1",1);
    unsetenv("SA2_D3D9_BREAK");
    setenv("SA2_D3DX_CONSTANT_TABLE","0",1);
    sa2_d3d9_config_from_env();
    const uint32_t d3d9=sa2_d3d9_make_d3d9(0x00401234u);
    check(d3d9!=0u,"device","Direct3DCreate9 answered no object");
    const uint32_t pp[14]={640u,480u,22u,1u,0u,0u,1u,0u,1u,1u,75u,0u,0u,0u};
    sa2_poke(PP,sizeof pp,pp);
    uint32_t hr=CALL("device",d3d9,16,6,0u,1u,0u,0x40u,PP,OUT_DEV);
    const uint32_t dev=peek32(OUT_DEV);
    check(hr==0u&&dev!=0u,"device","CreateDevice returned %#010x, object %#010x",hr,dev);

    set_bank(dev,94,IN_F,VS_F,4,"VS-F-set");
    set_bank(dev,109,IN_F,PS_F,4,"PS-F-set");
    read_bank(dev,95,VS_F,4,"VS-F-alone");
    read_bank(dev,110,PS_F,4,"PS-F-alone");
    set_bank(dev,96,IN_I,VS_I,4,"VS-I-set");
    set_bank(dev,111,IN_I,PS_I,4,"PS-I-set");
    read_bank(dev,97,VS_I,4,"VS-I-alone");
    read_bank(dev,112,PS_I,4,"PS-I-alone");
    read_bank(dev,95,VS_F,4,"VS-F-after-I");
    read_bank(dev,110,PS_F,4,"PS-F-after-I");
    set_bank(dev,98,IN_B,WANT_B,1,"VS-B-set");
    set_bank(dev,113,IN_B,WANT_B,1,"PS-B-set");
    read_bank(dev,99,WANT_B,1,"VS-B-alone");
    read_bank(dev,114,WANT_B,1,"PS-B-alone");
    read_bank(dev,95,VS_F,4,"VS-F-after-B");
    read_bank(dev,110,PS_F,4,"PS-F-after-B");
    read_bank(dev,97,VS_I,4,"VS-I-after-B");
    read_bank(dev,112,PS_I,4,"PS-I-after-B");

    /* Independent reviewer literals: stage Bool values and unset type maps. */
    static const uint32_t ZERO[4] = {0u,0u,0u,0u};
    set_bank(dev,98,IN_B,ZERO,1,"review-VS-B-zero-set");
    read_bank(dev,99,ZERO,1,"review-VS-B-zero");
    read_bank(dev,114,WANT_B,1,"review-PS-B-one-survives");
    set_bank(dev,98,IN_B,WANT_B,1,"review-VS-B-one-set");
    set_bank(dev,113,IN_B,ZERO,1,"review-PS-B-zero-set");
    read_bank(dev,99,WANT_B,1,"review-VS-B-one-survives");
    read_bank(dev,114,ZERO,1,"review-PS-B-zero");
    sa2_poke(IN_F,16u,VS_F);
    check(CALL("review-VS-F-register11",dev,94,3,11u,IN_F,1u)==0u,
          "review-VS-F-register11", "expected D3D_OK");
    sa2_poke(IN_F,16u,PS_F);
    check(CALL("review-PS-F-register11",dev,109,3,11u,IN_F,1u)==0u,
          "review-PS-F-register11", "expected D3D_OK");
    sa2_d3d9_stats before,after;
    sa2_d3d9_stats_get(&before);
    read_bank_indexed(dev,97,11u,ZERO,4,"review-VS-I-unset-after-F");
    read_bank_indexed(dev,99,11u,ZERO,1,"review-VS-B-unset-after-F");
    read_bank_indexed(dev,112,11u,ZERO,4,"review-PS-I-unset-after-F");
    read_bank_indexed(dev,114,11u,ZERO,1,"review-PS-B-unset-after-F");
    sa2_d3d9_stats_get(&after);
    check(after.unset==before.unset+4u,"review-independent-unset-maps",
          "four untouched type banks must report unset; before %u after %u",before.unset,after.unset);

    FILE *f=fopen(argv[1],"wx");
    if(!f) {perror(argv[1]);return 2;}
    fprintf(f,"{\"scope\":\"F/I/B and VS/PS bank independence; authored CPU guest-vtable fixture\",\"checks\":%u,\"failures\":%u,\"observations\":[",g_checks,g_fails);
    for(uint32_t j=0;j<OBS_N;j++) {
        struct observation *o=&OBS[j];
        fprintf(f,"%s{\"id\":\"%s\",\"matches\":%s,\"actual_words\":[",j?",":"",o->id,o->matches?"true":"false");
        for(uint32_t k=0;k<o->n;k++)fprintf(f,"%s%u",k?",":"",o->actual[k]);
        fprintf(f,"],\"expected_words\":[");
        for(uint32_t k=0;k<o->n;k++)fprintf(f,"%s%u",k?",":"",o->expected[k]);
        fprintf(f,"]}");
    }
    fprintf(f,"],\"abi\":[");
    for(uint32_t j=0;j<ABI_N;j++) {
        struct abi_observation *a=&ABI[j];
        fprintf(f,"%s{\"slot\":%u,\"argc\":%u,\"eax\":%u,\"popped\":%u}",
                j?",":"",a->slot,a->argc,a->eax,a->popped);
    }
    fprintf(f,"],\"unset_before\":%u,\"unset_after\":%u}\n",before.unset,after.unset);
    if(fclose(f)!=0) {perror(argv[1]);free(g_mem);return 2;}
    printf("d3d9_shader_banks_probe: %u checks, %u failures\n",g_checks,g_fails);
    free(g_mem);
    return g_fails?1:0;
}
