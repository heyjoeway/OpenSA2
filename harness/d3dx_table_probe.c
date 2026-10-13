/* Owned numeric/sampler CTAB reflection driven through real guest vtables.
 * This standalone CPU fixture authors every shader byte and supplies guest
 * memory/heap, CPU/thunk/trap providers. It checks literal x86 descriptions,
 * lookup identities, caller-buffer independence and final ownership release.
 * Existing COM, switch, malformed input and unimplemented-slot refusals remain.
 * The local allocator deliberately reuses freed blocks. Diagnostic saturation
 * must never remove semantic records. No shipped bytes or native/game/replay
 * claim is supplied. Selected held Wine/Proton observations are documented in
 * the preparation packet; pointer/token numeric values are not native oracles.
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
static int g_corrupt_snapshot;

int sa2_peek(uint32_t addr, uint32_t len, void *out) {
    if (addr < GUEST_LO || addr >= GUEST_HI || len > GUEST_HI - addr) return SA2_E_RANGE;
    memcpy(out, g_mem + (addr - GUEST_LO), len);
    /* Authored provider changes only the second, complete CTAB read. */
    if (g_corrupt_snapshot && len==400u) {
        const uint32_t bad_info=0xfffffff0u;
        memcpy((uint8_t *)out+16u,&bad_info,4u);
    }
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
/* Local authored heap provider. Freed blocks are deliberately reused so
 * stale opaque handles are challenged independently of pointer reuse. */
typedef struct { uint32_t at,size,owner; int live; } heap_block;
static heap_block g_heap[4096];
static uint32_t g_heap_count,g_heap_next=0x03100000u,g_heap_live,g_heap_frees;
uint32_t sa2_heap_alloc_owned(uint32_t owner,uint32_t bytes) {
    for (uint32_t i=0;i<g_heap_count;i++) if (!g_heap[i].live && g_heap[i].size>=bytes) {
        g_heap[i].live=1; g_heap[i].owner=owner; g_heap_live++;
        sa2_guest_fill(g_heap[i].at,0,g_heap[i].size); return g_heap[i].at;
    }
    uint32_t at=(g_heap_next+31u)&~31u;
    if (g_heap_count==4096u || at>=SA2_D3D9_OBJ_BASE || bytes>SA2_D3D9_OBJ_BASE-at) return 0;
    g_heap[g_heap_count++]=(heap_block){at,bytes,owner,1};
    g_heap_next=at+bytes; g_heap_live++; sa2_guest_fill(at,0,bytes); return at;
}
void sa2_heap_free_owned(uint32_t owner,uint32_t payload) {
    for (uint32_t i=0;i<g_heap_count;i++) if (g_heap[i].at==payload && g_heap[i].live && g_heap[i].owner==owner) {
        g_heap[i].live=0; g_heap_live--; g_heap_frees++;
        sa2_guest_fill(payload,0xdd,g_heap[i].size); return;
    }
    fprintf(stderr,"invalid/double fixture heap free %#x owner %#x\n",payload,owner); exit(2);
}

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

/* A trap: inside a child case it is the case's answer, written to the pipe
 * with what the case left behind -- the guest word at `g_report_slot` (the
 * entry point's out-pointer slot, 0 for none) and the tables built -- so a
 * refusal that built or wrote a table first shows; in the probe itself it is a
 * defect, and ends the run. */
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
    fprintf(stderr, "d3dx_table_probe: an unexpected trap %u at %#010x: %s\n", status, exit_pc,
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

#define CALLER 0x0086722au   /* where D3DXGetShaderConstantTable returns to in the image */
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

/* An answered call: it must pop exactly 4 * (argc + 2) bytes. */
static uint32_t answer(const char *id, uint32_t self, uint32_t slot, uint32_t argc,
                       const uint32_t *argv) {
    uint32_t popped = 0;
    const uint32_t eax = call_slot(self, slot, argc, argv, &popped);
    check(popped == 4u * (argc + 2u), id, "slot %u popped %u bytes, wanted %u", slot, popped,
          4u * (argc + 2u));
    return eax;
}
#define CALL0(id, self, slot)        answer(id, self, slot, 0, NULL)
#define CALL(id, self, slot, n, ...) answer(id, self, slot, n, (const uint32_t[]){__VA_ARGS__})

/* A case run in a child, so a refusal ends the child and nothing it changed
 * reaches the next case. `what` 0 calls a slot, 1 the entry point, 2 the entry
 * point after reconfiguring from the environment the child sets. Answers the
 * child's report: "TRAP <status> <exit_pc> <note>" for a refusal, or "ANSWER
 * <eax> <popped>" for a call that came back. */
typedef struct {
    int what;
    uint32_t self, slot, argc, argv[4];
    uint32_t a[2];
    const char *env_name, *env_value;
} child_case;

static void run_child(const child_case *k, char *report, size_t n) {
    int fds[2];
    report[0] = 0;
    if (pipe(fds) != 0) { perror("pipe"); exit(2); }
    fflush(stdout);
    const pid_t pid = fork();
    if (pid < 0) { perror("fork"); exit(2); }
    if (pid == 0) {
        close(fds[0]);
        g_child_fd = fds[1];
        uint32_t eax = 0, popped = 0;
        if (k->what == 0) {
            eax = call_slot(k->self, k->slot, k->argc, k->argv, &popped);
        } else {
            if (k->what == 2) {
                setenv(k->env_name, k->env_value, 1);
                sa2_d3d9_config_from_env();
            }
            eax = sa2_d3d9_d3dx_get_shader_constant_table(k->a, CALLER);
        }
        dprintf(fds[1], "ANSWER %#010x %u", eax, popped);
        _exit(0);
    }
    close(fds[1]);
    size_t got = 0;
    ssize_t r;
    while (got + 1 < n && (r = read(fds[0], report + got, n - 1 - got)) > 0) got += (size_t)r;
    report[got] = 0;
    close(fds[0]);
    int status = 0;
    waitpid(pid, &status, 0);
    if (!got && WIFSIGNALED(status)) snprintf(report,n,"SIGNAL %d",WTERMSIG(status));
}

/* A call that must refuse by name: the child's report is a trap whose note
 * holds `must_name`. A refusal traps before the callee pops, so it pops
 * nothing, and the note is what tells it from an answer. */
static void refuses(const char *id, const child_case *k, const char *must_name) {
    char report[1400];
    run_child(k, report, sizeof report);
    const int trapped = !strncmp(report, "TRAP ", 5);
    check(trapped && strstr(report, must_name) != NULL, id,
          "wanted a refusal naming \"%s\", got: %.300s", must_name, report);
}

/* An entry-point refusal: one child, whose trap note holds `must_name` and,
 * when given, `also`, and whose report shows the case left nothing behind --
 * `left`, the out-pointer's slot still the sentinel and the tables built still
 * the ones this file made. */
static void refuses_entry(const char *id, const child_case *k, const char *must_name,
                          const char *also, const char *left) {
    char report[1400];
    run_child(k, report, sizeof report);
    const int trapped = !strncmp(report, "TRAP ", 5);
    check(trapped && strstr(report, must_name) != NULL && (!also || strstr(report, also) != NULL)
          && strstr(report, left) != NULL, id,
          "wanted a refusal naming \"%s\"%s%s%s that leaves %s, got: %.400s", must_name,
          also ? " and \"" : "", also ? also : "", also ? "\"" : "", left, report);
}

/* ------------------------------------------------------ made-up bytecode */

/* D3DSIO_COMMENT with its length in DWORDs. */
static uint32_t comment_token(uint32_t dwords) { return (dwords << 16) | 0xFFFEu; }

/* Writes at `at` a made-up shader: version token `version`, one NOP, a comment
 * that is not a CTAB, then a CTAB comment holding a D3DXSHADER_CONSTANTTABLE
 * of `constants` descriptions (each 20 bytes, named "c0", "c1", ...), a
 * creator and a target string, then one more NOP and the end token. `header`
 * is the header's Size field (28 when well formed), and `info` its
 * ConstantInfo offset, or UINT32_MAX for "right after the header". Answers
 * the blob's length in bytes and, through `ctab_bytes`, the CTAB comment's
 * data bytes after its fourcc. */
static uint32_t write_shader(uint32_t at, uint32_t version, uint32_t constants,
                             uint32_t header, uint32_t info, uint32_t *ctab_bytes) {
    uint32_t w[512];
    uint32_t n = 0;
    w[n++] = version;
    w[n++] = 0x00000000u;                         /* D3DSIO_NOP */
    w[n++] = comment_token(2);                    /* a comment the walk steps over */
    w[n++] = 0x45425250u;                         /* 'PRBE' */
    w[n++] = 0x0badc0deu;
    /* The CTAB data, built in bytes and then padded to whole DWORDs. */
    uint8_t d[1600];
    memset(d, 0, sizeof d);
    const uint32_t infos = 28u;
    uint32_t off = infos + 20u * constants;
    const uint32_t creator = off;
    memcpy(d + off, "SA2 probe shader compiler", 26); off += 26u;
    const uint32_t target = off;
    memcpy(d + off, (version >> 16) == 0xFFFFu ? "ps_3_0" : "vs_3_0", 7); off += 7u;
    for (uint32_t i = 0; i < constants; i++) {
        const uint32_t name = off;
        d[off++] = 'c';
        d[off++] = (uint8_t)('0' + i % 10u);
        d[off++] = 0;
        const uint32_t e = infos + 20u * i;
        memcpy(d + e + 0u, &name, 4);                     /* Name */
        const uint16_t set = 2, reg = (uint16_t)i, count = 1, reserved = 0;
        memcpy(d + e + 4u, &set, 2);                      /* RegisterSet: D3DXRS_FLOAT4 */
        memcpy(d + e + 6u, &reg, 2);                      /* RegisterIndex */
        memcpy(d + e + 8u, &count, 2);                    /* RegisterCount */
        memcpy(d + e + 10u, &reserved, 2);
        const uint32_t type=off;
        const uint16_t ti[8]={0u,3u,1u,1u,1u,0u,0u,0u};
        memcpy(d+off,ti,16u); off+=16u;
        const uint32_t def=off;
        const uint32_t values[4]={0x3f800000u+i,0u,0u,0u};
        memcpy(d+off,values,16u); off+=16u;
        memcpy(d+e+12u,&type,4); memcpy(d+e+16u,&def,4);
    }
    const uint32_t hdr[7] = {header, creator, version, constants,
                             info == UINT32_MAX ? infos : info, 0u, target};
    memcpy(d, hdr, sizeof hdr);
    const uint32_t dwords = (off + 3u) / 4u;
    w[n++] = comment_token(dwords + 1u);
    w[n++] = 0x42415443u;                         /* 'CTAB' */
    memcpy(w + n, d, 4u * dwords);
    n += dwords;
    w[n++] = 0x00000000u;                         /* D3DSIO_NOP */
    w[n++] = 0x0000FFFFu;                         /* D3DSIO_END */
    sa2_poke(at, 4u * n, w);
    *ctab_bytes = 4u * dwords;
    return 4u * n;
}

/* Writes at `at` a vertex shader whose well-formed CTAB comment lies past the
 * walk's bound: two comments of 0x7FFF DWORDs each after the version token,
 * then write_shader's tokens (its own version token lands on the second
 * comment's last data DWORD, which the walk steps over). Its NOP is DWORD
 * 65537 from the version token and its CTAB comment's token 65541, a few steps
 * into a walk that counts steps and past one that counts DWORDs to 64K. */
static void write_long_shader(uint32_t at) {
    uint32_t cb = 0, off = 1;
    pk32(at, 0xFFFE0300u);
    for (int c = 0; c < 2; c++) {
        pk32(at + 4u * off, comment_token(0x7FFFu));
        sa2_guest_fill(at + 4u * (off + 1u), 0, 4u * 0x7FFFu);
        off += 1u + 0x7FFFu;
    }
    write_shader(at + 4u * (off - 1u), 0xFFFE0300u, 1u, 28u, UINT32_MAX, &cb);
}

/* ------------------------------------------------------------- the IIDs */

#define IIDS          (SCRATCH + 0x3000u)
#define IID_UNKNOWN   (IIDS + 0x00u)
#define IID_BUFFER    (IIDS + 0x10u)
#define IID_TABLE42   (IIDS + 0x20u)
#define IID_TABLE43   (IIDS + 0x30u)
#define IID_MADEUP    (IIDS + 0x40u)

static void write_iids(void) {
    static const uint8_t unk[16] = {0,0,0,0, 0,0, 0,0, 0xC0,0,0,0,0,0,0,0x46};
    /* {8BA5FB08-5195-40E2-AC58-0D989C3A0102} */
    static const uint8_t buf[16] = {0x08,0xFB,0xA5,0x8B, 0x95,0x51, 0xE2,0x40,
                                    0xAC,0x58,0x0D,0x98,0x9C,0x3A,0x01,0x02};
    /* {9DCA3190-38B9-4FC3-92E3-39C6DDFB358B}: d3dx9_42 and earlier */
    static const uint8_t t42[16] = {0x90,0x31,0xCA,0x9D, 0xB9,0x38, 0xC3,0x4F,
                                    0x92,0xE3,0x39,0xC6,0xDD,0xFB,0x35,0x8B};
    /* {AB3C758F-093E-4356-B762-4DB18F1B3A01}: d3dx9_43 */
    static const uint8_t t43[16] = {0x8F,0x75,0x3C,0xAB, 0x3E,0x09, 0x56,0x43,
                                    0xB7,0x62,0x4D,0xB1,0x8F,0x1B,0x3A,0x01};
    static const uint8_t madeup[16] = {0x53,0x41,0x32,0x50, 0x52,0x4F, 0x42,0x45,
                                       1,2,3,4,5,6,7,8};
    sa2_poke(IID_UNKNOWN, 16, unk);
    sa2_poke(IID_BUFFER, 16, buf);
    sa2_poke(IID_TABLE42, 16, t42);
    sa2_poke(IID_TABLE43, 16, t43);
    sa2_poke(IID_MADEUP, 16, madeup);
}

/* Three authored roots: float3, an array of two numeric structs, sampler2D.
 * All offsets and expected descriptor fields below are literal fixture data. */
static uint32_t write_reflection_shader(uint32_t at) {
    uint8_t d[400]; memset(d,0,sizeof d);
    const uint32_t header[7]={28u,136u,0xfffe0300u,3u,28u,0u,144u};
    memcpy(d,header,sizeof header);
    memcpy(d+100,"vFloat",7); memcpy(d+112,"data",5); memcpy(d+120,"tex",4);
    memcpy(d+124,"x",2); memcpy(d+126,"y",2); memcpy(d+136,"probe",6); memcpy(d+144,"vs_3_0",7);
    const uint32_t v[5]={100u,2u|(7u<<16),1u,160u,320u};
    const uint32_t a[5]={112u,2u|(20u<<16),4u,176u,336u};
    const uint32_t sm[5]={120u,3u|(3u<<16),1u,192u,0u};
    memcpy(d+28,v,20); memcpy(d+48,a,20); memcpy(d+68,sm,20);
    const uint16_t vector[8]={1,3,1,3,1,0,0,0};
    const uint16_t st[8]={5,0,1,1,2,2,256,0};
    const uint16_t sampler[8]={4,12,1,1,1,0,0,0};
    const uint16_t scalar[8]={0,3,1,1,1,0,0,0};
    memcpy(d+160,vector,16); memcpy(d+176,st,16); memcpy(d+192,sampler,16); memcpy(d+208,scalar,16);
    const uint32_t members[4]={124u,208u,126u,160u}; memcpy(d+256,members,16);
    const uint32_t defaults[4]={0x3f800000u,0x40000000u,0x40400000u,0u};
    memcpy(d+320,defaults,16); memcpy(d+336,defaults,16); memcpy(d+352,defaults,16);
    memcpy(d+368,defaults,16); memcpy(d+384,defaults,16);
    pk32(at,0xfffe0300u); pk32(at+4,comment_token(101u)); pk32(at+8,0x42415443u);
    sa2_poke(at+12,400,d); pk32(at+412,0x0000ffffu); return 416u;
}
static uint32_t named(const char *id,uint32_t table,uint32_t parent,const char *name) {
    const uint32_t at=SCRATCH+0x8000u;
    sa2_poke(at,(uint32_t)strlen(name)+1u,name);
    return CALL(id,table,9u,2u,parent,at);
}
static void reflection_checks(void) {
    const uint32_t desc=SCRATCH+0x9000u,count=desc+48u,out=desc+64u;
    const uint32_t input=sa2_heap_alloc_owned(0x50524f42u,416u);
    uint8_t original[400]; write_reflection_shader(input); sa2_peek(input+12,400,original);
    uint32_t a[2]={input,out};
    check(sa2_d3d9_d3dx_get_shader_constant_table(a,CALLER)==0u,"reflect-create","table creation failed");
    uint32_t t=peek32(out),v=named("lookup-vFloat",t,0,"vFloat");
    check(v!=0u,"lookup-vFloat","literal vFloat lookup is null");
    check(named("lookup-repeat",t,0,"vFloat")==v,"lookup-repeat","lookup is unstable");
    check(named("lookup-case",t,0,"VFloat")==0u,"lookup-case","case changed lookup");
    check(named("lookup-missing",t,0,"missing")==0u,"lookup-missing","missing name found");
    pk32(count,0u);
    check(CALL("desc-vFloat",t,6u,3u,v,desc,count)==0u,"desc-vFloat","descriptor refused");
    uint32_t d[12]={0}; sa2_peek(desc,48,d);
    const uint32_t expected[10]={2u,7u,1u,1u,3u,1u,3u,1u,0u,12u};
    check(!memcmp(d+1,expected,40u) && peek32(count)==1u,"desc-fields","float3 descriptor differs");
    uint32_t owned=d[0]-100u;
    char name[7]={0}; sa2_peek(d[0],7,name);
    check(!memcmp(name,"vFloat",7) && d[0]!=input+112u && d[11]==owned+320u,
          "desc-owned-addresses","Name/DefaultValue not owned CTAB pointers");
    uint8_t copy[400]={0}; sa2_peek(owned,400,copy);
    check(!memcmp(copy,original,400),"owns-complete-ctab","owned CTAB bytes differ");
    check(peek32(d[11])==0x3f800000u && peek32(d[11]+4u)==0x40000000u &&
          peek32(d[11]+8u)==0x40400000u,"desc-defaults","default float3 bytes differ");
    check(CALL("desc-name-handle",t,6u,3u,d[0],desc,0u)==0u,"desc-name-handle","owned name handle refused");
    check(CALL("desc-count-only",t,6u,3u,v,0u,count)==0u && peek32(count)==1u,
          "desc-count-only","optional descriptor/count behavior differs");
    check(CALL("desc-neither",t,6u,3u,v,0u,0u)==0u,"desc-neither","both optional outputs refused");
    sa2_poke(SCRATCH+0x8000u,7,"vFloat");
    check(CALL("desc-string-handle",t,6u,3u,SCRATCH+0x8000u,desc,count)==0u,
          "desc-string-handle","external string handle refused");
    pk32(count,0u);
    check(CALL("desc-count-zero",t,6u,3u,v,desc,count)==0u && peek32(count)==1u,
          "desc-count-zero","count0 did not write one descriptor");
    pk32(count,2u);
    check(CALL("desc-count-two",t,6u,3u,v,desc,count)==0u && peek32(count)==1u,
          "desc-count-two","count2 did not write one descriptor");
    sa2_poke(SCRATCH+0xa000u,15u,"not-a-constant");
    check(named("lookup-invalid-name-parent",t,SCRATCH+0xa000u,"vFloat")==v,
          "lookup-invalid-name-parent","invalid readable parent did not use root lookup");
    pk32(count,99u); pk32(desc,0xabcdef01u);
    check(CALL("desc-null",t,6u,3u,0u,desc,count)==0x8876086cu && peek32(count)==99u &&
          peek32(desc)==0xabcdef01u,"desc-null","invalid handle changed optional outputs");
    uint32_t data=named("lookup-data",t,0,"data");
    uint32_t elem=named("lookup-element",t,0,"data[1]");
    uint32_t member=named("lookup-member",t,elem,"y");
    check(data && elem && member && elem!=data && member!=elem,
          "lookup-member","parent/member/array handles are not distinct");
    check(named("lookup-full-path",t,0,"data[1].y")==member &&
          named("lookup-relative-array",t,data,"[1].y")==0u,"lookup-full-path","member path identity differs");
    check(CALL("desc-member",t,6u,3u,member,desc,count)==0u,"desc-member","member descriptor refused");
    sa2_peek(desc,48,d);
    const uint32_t me[10]={2,23,1,1,3,1,3,1,0,12};
    check(!memcmp(d+1,me,40) && d[0]==owned+126 && d[11]==owned+384,
          "desc-member-fields","member register/default/Name mapping differs");
    check(CALL("desc-array",t,6u,3u,data,desc,count)==0u,"desc-array","array descriptor refused");
    sa2_peek(desc,48,d); const uint32_t ae[10]={2,20,4,5,0,1,1,2,2,8};
    check(!memcmp(d+1,ae,40),"desc-array-fields","array descriptor differs");
    check(named("lookup-oob",t,0,"data[2]")==0 && named("lookup-overflow",t,0,"data[42949672960]")==0 &&
          named("lookup-bad-path",t,0,"data[1].")==0,"lookup-oob","malformed/oob path accepted");
    uint32_t sampler=named("lookup-sampler",t,0,"tex");
    check(sampler && CALL("desc-sampler",t,6u,3u,sampler,desc,count)==0u,"desc-sampler","sampler not reflected");
    sa2_peek(desc,48,d); const uint32_t se[10]={3,3,1,4,12,1,1,1,0,4};
    check(!memcmp(d+1,se,40) && d[11]==0u,"desc-sampler-fields","sampler descriptor differs");
    a[1]=out+4u;
    check(sa2_d3d9_d3dx_get_shader_constant_table(a,CALLER)==0u,"reflect-second-table","second table failed");
    uint32_t t2=peek32(out+4u),v2=named("lookup-table2",t2,0,"vFloat");
    check(v2 && v2!=v,"lookup-table-isolation","two tables share a handle");
    check(named("lookup-foreign-root",t2,v,"vFloat")==0u,
          "lookup-foreign-root","foreign parent resolved an existing root");
    check(CALL("desc-foreign",t2,6u,3u,v,desc,count)==0x8876086cu &&
          named("lookup-foreign-parent",t2,data,"x")==0u,"desc-foreign","foreign handle accepted");
    check(CALL("desc-foreign-name-string",t2,6u,3u,owned+100u,desc,count)==0u,
          "desc-foreign-name-string","readable foreign Name did not resolve by string value");
    sa2_guest_fill(input,0x77,416); sa2_heap_free_owned(0x50524f42u,input);
    check(CALL("desc-after-input-free",t,6u,3u,v,desc,count)==0u,"desc-after-input-free","reflection depends on freed input");
    sa2_peek(desc,48,d);
    check(!memcmp(d+1,expected,40) && peek32(d[11])==0x3f800000u &&
          named("lookup-after-input-free",t,0,"data[1].y")==member,
          "owned-after-input-free","descriptor/lookup changed after overwrite/free");
    uint32_t frees=g_heap_frees;
    check(CALL0("reflect-final-release",t,2u)==0u,"reflect-final-release","final Release failed");
    check(g_heap_frees==frees+1u,
          "owned-storage-release","owned block not freed exactly at final release");
    check(CALL("desc-stale-handle",t2,6u,3u,v,desc,count)==0x8876086cu,
          "desc-stale-handle","stale foreign handle accepted");
    check(named("lookup-stale-root",t2,v,"vFloat")==0u,
          "lookup-stale-root","stale parent resolved an existing root");
    child_case k={0,t,6u,3u,{v,desc,count,0u},{0u,0u},NULL,NULL};
    refuses("after-final-desc",&k,"use after free");
    k.slot=9u; k.argc=2u; k.argv[0]=0u; k.argv[1]=SCRATCH+0x8000u;
    refuses("after-final-lookup",&k,"use after free");
    CALL0("reflect-table2-release",t2,2u);
    /* The next table reuses a freed heap block; old tokens still cannot resolve.
     * Creation after diagnostic row saturation must retain real semantic records. */
    uint32_t previous=v2;
    for (uint32_t i=0;i<257u;i++) {
        uint32_t cb; write_shader(SCRATCH+0x4000u,0xfffe0300u,1u,28u,UINT32_MAX,&cb);
        a[0]=SCRATCH+0x4000u; a[1]=out;
        check(sa2_d3d9_d3dx_get_shader_constant_table(a,CALLER)==0u,"saturated-create","table failed beyond diagnostic capacity");
        uint32_t ti=peek32(out),hi=named("saturated-lookup",ti,0,"c0");
        check(hi && hi!=previous && CALL("saturated-desc",ti,6u,3u,hi,desc,count)==0u &&
              peek32(desc+8u)==0u && CALL("reused-stale",ti,6u,3u,previous,0u,0u)==0x8876086cu,
              "saturated-owned-records","row capacity/heap reuse affects semantics");
        previous=hi; CALL0("saturated-release",ti,2u);
    }
    check(g_heap_live==0u,"all-owned-storage-freed","%u owned blocks remain",g_heap_live);
    check(sa2_d3d9_ctab_count()==256u && sa2_d3d9_ctab_rows_dropped()==12u,
          "diagnostic-capacity-only","retained/dropped row counts differ");
}

/* ---------------------------------------------------------------- main */

#define PP        (SCRATCH + 0x0000u)
#define OUT_D3D   (SCRATCH + 0x0100u)
#define OUT_DEV   (SCRATCH + 0x0104u)
#define OUT_SH    (SCRATCH + 0x0108u)
#define OUT_SIZE  (SCRATCH + 0x010cu)
#define OUT_TAB   (SCRATCH + 0x0110u)
#define OUT_PPV   (SCRATCH + 0x0114u)
#define OUT_MORE  (SCRATCH + 0x0120u)   /* eight more tables' out-pointers */
#define BLOB      (SCRATCH + 0x1000u)   /* the shader as created */
#define BUF       (SCRATCH + 0x2000u)   /* the bytecode GetFunction copies back */
#define BAD       (SCRATCH + 0x4000u)   /* malformed bytecode for the refusals */
#define SENTINEL  0xBADBAD00u

static const char *const OTHER_METHODS[] = {
    "GetBufferPointer", "GetBufferSize", "GetDesc", "GetConstantDesc", "GetSamplerIndex",
    "GetConstant", "GetConstantByName", "GetConstantElement", "SetDefaults", "SetValue",
    "SetBool", "SetBoolArray", "SetInt", "SetIntArray", "SetFloat", "SetFloatArray",
    "SetVector", "SetVectorArray", "SetMatrix", "SetMatrixArray", "SetMatrixPointerArray",
    "SetMatrixTranspose", "SetMatrixTransposeArray", "SetMatrixTransposePointerArray",
};

/* The table's interface index in the device's type table, by its name. */
static uint32_t table_type(void) {
    for (uint32_t i = 0; i < sa2_d3d9_type_count(); i++)
        if (!strcmp(sa2_d3d9_type_name(i), "ID3DXConstantTable")) return i;
    fprintf(stderr, "the device has no ID3DXConstantTable type\n");
    exit(2);
}

int main(int argc, char **argv) {
    const char *json_path = NULL;
    for (int i = 1; i < argc; i++)
        if (!strcmp(argv[i], "--json") && i + 1 < argc) json_path = argv[++i];
    const char *brk = getenv("SA2_D3D9_BREAK");
    g_mem = calloc(1, GUEST_HI - GUEST_LO);
    if (!g_mem) { perror("calloc"); return 2; }

    /* ---- 0. configuration: the device and the switch on ---- */
    setenv("SA2_D3D9_DEVICE", "1", 1);
    setenv("SA2_D3DX_CONSTANT_TABLE", "1", 1);
    sa2_d3d9_config_from_env();
    sa2_d3d9_stats st;
    sa2_d3d9_stats_get(&st);
    check(sa2_d3d9_constant_table_enabled() == 1 && st.constant_table == 1u, "config-on",
          "SA2_D3DX_CONSTANT_TABLE=1 reads enabled %d, echoed %u",
          sa2_d3d9_constant_table_enabled(), st.constant_table);
    write_iids();

    /* ---- 1. the device, the shader, its bytecode read back ---- */
    const uint32_t d3d9 = sa2_d3d9_make_d3d9(0x00401234u);
    check(d3d9 != 0u, "device", "Direct3DCreate9 answered no object");
    const uint32_t pp[14] = {640u, 480u, 22u, 1u, 0u, 0u, 1u, 0u, 1u, 1u, 75u, 0u, 0u, 0u};
    sa2_poke(PP, sizeof pp, pp);
    uint32_t hr = CALL("device", d3d9, 16, 6, 0u, 1u, 0u, 0x40u, PP, OUT_DEV);
    const uint32_t dev = peek32(OUT_DEV);
    check(hr == 0u && dev != 0u, "device", "CreateDevice answered %#010x and %#010x", hr, dev);
    uint32_t ctab_bytes = 0;
    const uint32_t blob_len = write_shader(BLOB, 0xFFFE0300u, 2u, 28u, UINT32_MAX, &ctab_bytes);
    hr = CALL("shader", dev, 91, 2, BLOB, OUT_SH);
    const uint32_t sh = peek32(OUT_SH);
    check(hr == 0u && sh != 0u, "shader", "CreateVertexShader answered %#010x and %#010x", hr, sh);
    hr = CALL("shader", sh, 4, 2, 0u, OUT_SIZE);
    check(hr == 0u && peek32(OUT_SIZE) == blob_len, "shader",
          "GetFunction(NULL) answered %#010x and %u bytes, wanted %u", hr, peek32(OUT_SIZE),
          blob_len);
    hr = CALL("shader", sh, 4, 2, BUF, OUT_SIZE);
    check(hr == 0u && !memcmp(g_mem + (BUF - GUEST_LO), g_mem + (BLOB - GUEST_LO), blob_len),
          "shader", "GetFunction(buffer) answered %#010x or copied other bytes", hr);

    /* ---- 2. the entry point ---- */
    pk32(OUT_TAB, SENTINEL);
    uint32_t a[2] = {BUF, OUT_TAB};
    hr = sa2_d3d9_d3dx_get_shader_constant_table(a, CALLER);
    const uint32_t t1 = peek32(OUT_TAB);
    check(hr == 0u, "entry-answers", "D3DXGetShaderConstantTable answered %#010x, wanted D3D_OK",
          hr);
    check(t1 != SENTINEL && sa2_d3d9_obj_index(t1) >= 0, "entry-answers",
          "the out-pointer holds %#010x, not an object the device built", t1);
    const uint32_t vt = peek32(t1);
    check(vt >= SA2_D3D9_VT_BASE && vt < SA2_D3D9_VT_BASE + SA2_D3D9_MAX_TYPES * SA2_D3D9_VT_STRIDE,
          "entry-answers", "the table's vtable pointer %#010x is outside the vtables", vt);
    sa2_d3d9_stats_get(&st);
    check(st.constant_tables_built == 1u && st.constant_tables_destroyed == 0u, "entry-counts",
          "built %u destroyed %u after one call, wanted 1 and 0", st.constant_tables_built,
          st.constant_tables_destroyed);
    const sa2_d3d9_ctab *r1 = sa2_d3d9_ctab_at(0);
    check(sa2_d3d9_ctab_count() == 1u && r1 && r1->seq == 1u && r1->caller == CALLER &&
          r1->bytecode == BUF && r1->table == t1 && r1->version == 0xFFFE0300u &&
          r1->constants == 2u && r1->ctab_bytes == ctab_bytes && r1->refs == 1u &&
          r1->released_by == 0u && r1->released_seq == 0u, "entry-row",
          "row: count %u, seq %u caller %#010x bytecode %#010x table %#010x version %#010x "
          "constants %u ctab_bytes %u (wanted %u) refs %u released_by %#010x",
          sa2_d3d9_ctab_count(), r1 ? r1->seq : 0u, r1 ? r1->caller : 0u,
          r1 ? r1->bytecode : 0u, r1 ? r1->table : 0u, r1 ? r1->version : 0u,
          r1 ? r1->constants : 0u, r1 ? r1->ctab_bytes : 0u, ctab_bytes, r1 ? r1->refs : 0u,
          r1 ? r1->released_by : 0u);

    /* ---- 3. AddRef, Release, QueryInterface ---- */
    check(CALL0("addref-release", t1, 1) == 2u, "addref-release", "the first AddRef is not 2");
    check(CALL0("addref-release", t1, 1) == 3u, "addref-release", "the second AddRef is not 3");
    check(CALL0("addref-release", t1, 2) == 2u, "addref-release", "Release from 3 is not 2");
    check(CALL0("addref-release", t1, 2) == 1u, "addref-release", "Release from 2 is not 1");
    const struct { const char *id, *what; uint32_t iid; int answered; } qi[] = {
        {"qi-iunknown", "IUnknown", IID_UNKNOWN, 1},
        {"qi-buffer", "ID3DXBuffer", IID_BUFFER, 1},
        {"qi-table42", "d3dx9_42's ID3DXConstantTable", IID_TABLE42, 1},
        {"qi-table43", "d3dx9_43's ID3DXConstantTable", IID_TABLE43, 0},
        {"qi-madeup", "a made-up IID", IID_MADEUP, 0},
    };
    uint32_t want_answered = 0, want_declined = 0;
    for (size_t i = 0; i < sizeof qi / sizeof qi[0]; i++) {
        pk32(OUT_PPV, SENTINEL);
        hr = CALL(qi[i].id, t1, 0, 2, qi[i].iid, OUT_PPV);
        const uint32_t ppv = peek32(OUT_PPV);
        if (qi[i].answered) {
            want_answered++;
            check(hr == 0u && ppv == t1, qi[i].id, "QueryInterface(%s) answered %#010x and "
                  "%#010x, wanted S_OK and the table %#010x", qi[i].what, hr, ppv, t1);
            check(CALL0(qi[i].id, t1, 2) == 1u, qi[i].id, "QueryInterface(%s) did not take a "
                  "count: the Release after it is not 1", qi[i].what);
        } else {
            want_declined++;
            check(hr == 0x80004002u && ppv == 0u, qi[i].id, "QueryInterface(%s) answered "
                  "%#010x and %#010x, wanted E_NOINTERFACE and null", qi[i].what, hr, ppv);
            check(CALL0(qi[i].id, t1, 1) == 2u && CALL0(qi[i].id, t1, 2) == 1u, qi[i].id,
                  "QueryInterface(%s) moved the count", qi[i].what);
        }
    }
    sa2_d3d9_stats_get(&st);
    check(st.constant_table_qi_answered == want_answered &&
          st.constant_table_qi_declined == want_declined, "qi-counts",
          "QueryInterface counted %u answered and %u declined, wanted %u and %u",
          st.constant_table_qi_answered, st.constant_table_qi_declined, want_answered,
          want_declined);
    {
        child_case k = {0, t1, 0, 2, {IID_UNKNOWN, 0u, 0u, 0u}, {0u, 0u}, NULL, NULL};
        refuses("qi-null-ppv", &k, "ppvObject is null");
        k.argv[0] = 0u;
        k.argv[1] = OUT_PPV;
        refuses("qi-null-riid", &k, "riid is not sixteen readable bytes");
    }

    /* ---- 4. remaining unimplemented methods refuse; called setters reject null handles ---- */
    for (uint32_t slot = 3; slot <= 26; slot++) {
        if (slot==6u || slot==9u) continue;
        char id[48], name[96];
        snprintf(id, sizeof id, "refuses-%s", OTHER_METHODS[slot - 3u]);
        snprintf(name, sizeof name, "ID3DXConstantTable::%s called from", OTHER_METHODS[slot - 3u]);
        const int argc_of = sa2_d3d9_method_argc(table_type(), slot);
        child_case k = {0, t1, slot, argc_of > 0 ? (uint32_t)argc_of : 0u,
                        {0u, 0u, 0u, 0u}, {0u, 0u}, NULL, NULL};
        if (slot==13u || slot==15u || slot==17u || slot==19u || slot==21u) {
            char report[1400];
            run_child(&k, report, sizeof report);
            snprintf(id, sizeof id, "null-handle-%s", OTHER_METHODS[slot - 3u]);
            check(!strcmp(report, "ANSWER 0x8876086c 20"), id,
                  "null handle wants INVALIDCALL and a 20-byte frame, got: %.300s", report);
        } else refuses(id, &k, name);
    }
    {
        child_case k = {0, t1, 27, 0, {0u, 0u, 0u, 0u}, {0u, 0u}, NULL, NULL};
        refuses("refuses-unnamed-slot-27", &k, "ID3DXConstantTable vtable slot 27");
        k.slot = 127;
        refuses("refuses-unnamed-slot-127", &k, "ID3DXConstantTable vtable slot 127");
    }

    /* ---- 5. the final Release, and calls through the table after it ---- */
    const uint32_t destroyed_before = st.objects_destroyed;
    check(CALL0("final-release", t1, 2) == 0u, "final-release", "the final Release is not 0");
    sa2_d3d9_stats_get(&st);
    check(st.constant_tables_destroyed == 1u && st.objects_destroyed == destroyed_before + 1u,
          "final-release-destroys", "after the final Release: tables destroyed %u, objects "
          "destroyed %u (was %u), wanted 1 and one more", st.constant_tables_destroyed,
          st.objects_destroyed, destroyed_before);
    r1 = sa2_d3d9_ctab_at(0);
    check(r1 && r1->refs == 0u && r1->released_by == RETADDR && r1->released_seq != 0u,
          "final-release-row", "the row reads refs %u released_by %#010x released_seq %u, "
          "wanted 0, %#010x and the Release's sequence number", r1 ? r1->refs : 9u,
          r1 ? r1->released_by : 0u, r1 ? r1->released_seq : 0u, RETADDR);
    {
        child_case k = {0, t1, 1, 0, {0u, 0u, 0u, 0u}, {0u, 0u}, NULL, NULL};
        refuses("after-final-addref", &k, "use after free");
        k.slot = 2;
        refuses("after-final-release", &k, "use after free");
        k.slot = 0;
        k.argc = 2;
        k.argv[0] = IID_UNKNOWN;
        k.argv[1] = OUT_PPV;
        refuses("after-final-qi", &k, "use after free");
    }

    /* ---- 6. eight more, released in another order than built ---- */
    uint32_t more[8];
    for (uint32_t i = 0; i < 8; i++) {
        uint32_t cb = 0;
        write_shader(BUF, i % 2u ? 0xFFFF0300u : 0xFFFE0300u, i, 28u, UINT32_MAX, &cb);
        a[0] = BUF;
        a[1] = OUT_MORE + 4u * i;
        check(sa2_d3d9_d3dx_get_shader_constant_table(a, CALLER) == 0u, "more-built",
              "table %u was not answered", i);
        more[i] = peek32(OUT_MORE + 4u * i);
        const sa2_d3d9_ctab *r = sa2_d3d9_ctab_at(1u + i);
        check(r && r->table == more[i] && r->constants == i && r->ctab_bytes == cb &&
              r->version == (i % 2u ? 0xFFFF0300u : 0xFFFE0300u), "more-rows",
              "table %u's row does not hold its shader's header", i);
    }
    for (uint32_t i = 0; i < 8; i++) CALL0("more-addref", more[i], 1);          /* each at 2 */
    static const uint32_t order[8] = {5, 0, 7, 2, 4, 1, 6, 3};
    for (uint32_t k = 0; k < 8; k++) {
        const uint32_t i = order[k];
        check(CALL0("more-release", more[i], 2) == 1u, "more-release",
              "table %u's first Release is not 1", i);
        sa2_d3d9_stats_get(&st);
        check(st.constant_tables_destroyed == 1u + k, "more-destroyed-in-order",
              "after table %u's Release to 1: %u tables destroyed, wanted the %u before it",
              i, st.constant_tables_destroyed, 1u + k);
        check(CALL0("more-release", more[i], 2) == 0u, "more-release",
              "table %u's final Release is not 0", i);
        sa2_d3d9_stats_get(&st);
        check(st.constant_tables_destroyed == 2u + k, "more-destroyed-in-order",
              "table %u's final Release: %u destroyed, wanted %u", i,
              st.constant_tables_destroyed, 2u + k);
        const sa2_d3d9_ctab *r = sa2_d3d9_ctab_at(1u + i);
        const sa2_d3d9_ctab *prev = k ? sa2_d3d9_ctab_at(1u + order[k - 1u]) : r1;
        check(r && r->released_by == RETADDR && r->refs == 0u && prev &&
              r->released_seq > prev->released_seq, "more-release-rows",
              "table %u's row: refs %u released_by %#010x released_seq %u, after the one "
              "before it at %u", i, r ? r->refs : 9u, r ? r->released_by : 0u,
              r ? r->released_seq : 0u, prev ? prev->released_seq : 0u);
    }
    sa2_d3d9_stats_get(&st);
    check(st.constant_tables_built == 9u && st.constant_tables_destroyed == 9u, "release-count",
          "built %u and destroyed %u, wanted 9 and 9: every table's final Release destroys it",
          st.constant_tables_built, st.constant_tables_destroyed);

    /* ---- 7. what the entry point refuses ----
     * Each refusal runs in a child, which reports the out-pointer's slot and
     * the tables built at its trap: the sentinel, and the nine sections 2 and
     * 6 built, or the refusal built or wrote a table before it refused. */
    {
        uint32_t cb = 0;
        char left[64];
        snprintf(left, sizeof left, "[out %#010x built %u]", SENTINEL, 1u + 8u);
        pk32(OUT_TAB, SENTINEL);
        g_report_slot = OUT_TAB;
        child_case k = {1, 0u, 0u, 0u, {0u, 0u, 0u, 0u}, {BAD, OUT_TAB}, NULL, NULL};
        /* A version token that is not a vertex or pixel shader's: the four
         * other kinds D3DXFindShaderComment accepts, each with a well-formed
         * CTAB, and one it does not. The oracle's Ex function answers every
         * one D3D_OK with a null table, and the refusal must say so. */
        static const struct { const char *id; uint32_t version; } kinds[] = {
            {"entry-kind-fx", 0x46580300u}, {"entry-kind-tx", 0x54580300u},
            {"entry-kind-7ffe", 0x7FFE0300u}, {"entry-kind-7fff", 0x7FFF0300u},
            {"entry-version-kind", 0x12340300u},
        };
        for (size_t i = 0; i < sizeof kinds / sizeof kinds[0]; i++) {
            write_shader(BAD, kinds[i].version, 1u, 28u, UINT32_MAX, &cb);
            refuses_entry(kinds[i].id, &k, "is not a vertex or pixel shader's",
                          "D3D_OK with a null table", left);
        }
        /* no CTAB: the same shader with the CTAB fourcc changed */
        write_shader(BAD, 0xFFFE0300u, 1u, 28u, UINT32_MAX, &cb);
        pk32(BAD + 24u, 0x42415444u);
        refuses_entry("entry-no-ctab", &k, "no CTAB comment before its end token", NULL, left);
        write_shader(BAD, 0xFFFE0300u, 1u, 29u, UINT32_MAX, &cb);
        refuses_entry("entry-header-size", &k, "Size is not the 28 bytes", NULL, left);
        write_shader(BAD, 0xFFFE0300u, 3u, 28u, 0x7FFFu, &cb);
        refuses_entry("entry-infos-past", &k, "run past the comment", NULL, left);
        write_shader(BAD, 0xFFFE0300u, 1u, 28u, UINT32_MAX, &cb);
        pk32(BAD + 20u, comment_token(4u));       /* the CTAB comment: 3 DWORDs of data */
        refuses_entry("entry-short-ctab", &k, "shorter than a D3DXSHADER_CONSTANTTABLE", NULL,
                      left);
        /* No malformed type may publish an object or write the out-pointer. */
        static const struct { const char *id; uint32_t off,value; const char *why; } malformed[] = {
            {"entry-type-offset",28u+12u,399u,"TypeInfo is outside"},
            {"entry-name-offset",28u,399u,"constant name is not"},
            {"entry-default-offset",28u+16u,399u,"register/default extent"},
            {"entry-type-class",160u,3u|(3u<<16),"numeric class/type"},
            {"entry-type-elements",160u+8u,0u,"dimensions/elements"},
            {"entry-struct-offset",176u+12u,399u,"struct member layout"},
            {"entry-struct-cycle",256u+4u,176u,"cyclic"},
            {"entry-member-name",256u,399u,"member name is not"},
            {"entry-register-extent",28u+4u,2u|(65535u<<16),"register/default extent"},
            {"entry-object-kind",192u,4u|(4u<<16),"object form is unsupported"},
        };
        for (uint32_t i=0;i<sizeof malformed/sizeof malformed[0];i++) {
            write_reflection_shader(BAD);
            pk32(BAD+12u+malformed[i].off,malformed[i].value);
            if (i==1u || i==7u) sa2_guest_fill(BAD+12u+399u,1,1);
            if (i==3u) pk32(BAD+12u+160u,1u|(1u<<16));
            if (i==8u) pk32(BAD+12u+28u+8u,2u);
            refuses_entry(malformed[i].id,&k,malformed[i].why,NULL,left);
        }
        write_reflection_shader(BAD); g_corrupt_snapshot=1;
        refuses_entry("snapshot-header-drift",&k,"snapshot",NULL,left);
        g_corrupt_snapshot=0;
        write_long_shader(BAD);
        refuses_entry("entry-past-64k-tokens", &k, "no end token within its first 64K tokens",
                      NULL, left);
        k.a[0] = 0x00001000u;                     /* below the guest space the stubs map */
        refuses_entry("entry-version-unreadable", &k, "version token is not in the guest space",
                      NULL, left);
        k.a[0] = 0u;
        refuses_entry("entry-null-code", &k, "pFunction is null", NULL, left);
        k.a[0] = BUF;
        k.a[1] = 0u;
        refuses_entry("entry-null-out", &k, "ppConstantTable is null", NULL, left);
        child_case off = {2, 0u, 0u, 0u, {0u, 0u, 0u, 0u}, {BUF, OUT_TAB},
                          "SA2_D3D9_DEVICE", "0"};
        refuses_entry("entry-device-off", &off, "SA2_D3D9_DEVICE", NULL, left);
        /* With the switch off the import never calls the entry point, and the
         * entry point refuses a caller that does. */
        off.env_name = "SA2_D3DX_CONSTANT_TABLE";
        refuses_entry("entry-switch-off", &off, "SA2_D3DX_CONSTANT_TABLE is off", NULL, left);
        g_report_slot = 0u;
    }

    /* ---- 8. the switch off ---- */
    {
        int fds[2];
        if (pipe(fds) != 0) { perror("pipe"); return 2; }
        fflush(stdout);
        const pid_t pid = fork();
        if (pid == 0) {
            close(fds[0]);
            setenv("SA2_D3DX_CONSTANT_TABLE", "0", 1);
            sa2_d3d9_config_from_env();
            sa2_d3d9_stats s2;
            sa2_d3d9_stats_get(&s2);
            dprintf(fds[1], "%d %u", sa2_d3d9_constant_table_enabled(), s2.constant_table);
            _exit(0);
        }
        close(fds[1]);
        char buf[64] = {0};
        ssize_t got = read(fds[0], buf, sizeof buf - 1);
        close(fds[0]);
        int status = 0;
        waitpid(pid, &status, 0);
        check(got > 0 && !strcmp(buf, "0 0"), "config-off",
              "SA2_D3DX_CONSTANT_TABLE=0 reads \"%s\" (enabled, echoed), wanted \"0 0\"", buf);
    }

    reflection_checks();

    /* ---- the record ---- */
    sa2_d3d9_stats_get(&st);
    printf("d3dx_table_probe: break %s; %u checks, %u failures; tables built %u destroyed %u, "
           "QueryInterface answered %u declined %u\n", brk && *brk ? brk : "(none)", g_checks,
           g_fails, st.constant_tables_built, st.constant_tables_destroyed,
           st.constant_table_qi_answered, st.constant_table_qi_declined);
    if (json_path) {
        FILE *f = fopen(json_path, "w");
        if (!f) { perror(json_path); return 2; }
        fprintf(f, "{\"probe\": \"harness/d3dx_table_probe.c\", \"break\": \"%s\", "
                   "\"checks\": %u, \"failures\": %u, \"failed\": [",
                brk ? brk : "", g_checks, g_fails);
        for (uint32_t i = 0; i < g_fails && i < 64; i++)
            fprintf(f, "%s\"%s\"", i ? ", " : "", g_failed[i]);
        fprintf(f, "],\n \"tables\": {\"built\": %u, \"destroyed\": %u, \"qi_answered\": %u, "
                   "\"qi_declined\": %u, \"rows\": [", st.constant_tables_built,
                st.constant_tables_destroyed, st.constant_table_qi_answered,
                st.constant_table_qi_declined);
        for (uint32_t i = 0; i < sa2_d3d9_ctab_count(); i++) {
            const sa2_d3d9_ctab *r = sa2_d3d9_ctab_at(i);
            fprintf(f, "%s{\"seq\": %u, \"version\": \"0x%08x\", \"constants\": %u, "
                       "\"ctab_bytes\": %u, \"refs\": %u, \"released\": %s}", i ? ", " : "",
                    r->seq, r->version, r->constants, r->ctab_bytes, r->refs,
                    r->released_by && r->released_seq ? "true" : "false");
        }
        fprintf(f, "]}}\n");
        fclose(f);
    }
    return g_fails ? 1 : 0;
}
