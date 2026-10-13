/* Authored cube-surface observations through the real runtime guest vtable.
 * Fixture services include a bounded functional owned guest heap. No shipped
 * bytes, native DLL, game run, player or GPU. Literal 4x4/two-mip expectations
 * cover identity, parent refs, 80-byte face chains, 480-byte storage, copies and
 * binding lifetime. The boundaries mode measures named runtime capacity guards,
 * not native HRESULTs. Autogen observations cover metadata only.
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
/* A bounded functional fixture heap, disjoint from scratch/stack/COM records. */
#define HEAP_LO 0x03100008u
#define HEAP_HI 0x03180000u
struct allocation {uint32_t owner, addr, bytes, freed;};
static struct allocation HEAP[128];
static uint32_t HEAP_N, HEAP_FREES, HEAP_NEXT=HEAP_LO, HEAP_ERRORS;
uint32_t sa2_heap_alloc_owned(uint32_t owner, uint32_t bytes) {
    if (!bytes || HEAP_N >= 128u || HEAP_NEXT >= HEAP_HI || bytes > HEAP_HI-HEAP_NEXT) return 0;
    uint32_t addr=HEAP_NEXT;
    HEAP_NEXT=(addr+bytes+7u)&~7u;
    HEAP[HEAP_N++]=(struct allocation){owner,addr,bytes,0u};
    sa2_guest_fill(addr,0,bytes);
    return addr;
}
void sa2_heap_free_owned(uint32_t owner, uint32_t payload) {
    for(uint32_t i=0;i<HEAP_N;i++) if(HEAP[i].addr==payload) {
        if(HEAP[i].owner!=owner || HEAP[i].freed) {HEAP_ERRORS++;return;}
        HEAP[i].freed=1u;HEAP_FREES++;return;
    }
    HEAP_ERRORS++;
}
static uint32_t heap_active(void) {return HEAP_N-HEAP_FREES;}

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
    va_list ap;va_start(ap,fmt);vsnprintf(g_note,sizeof g_note,fmt,ap);va_end(ap);
}
void sa2_trap_raise(uint32_t status,uint32_t pc) {
    TRAP_STATUS=status;TRAP_PC=pc;
    if(TRAP_ARMED) longjmp(TRAP_ENV,1);
    fprintf(stderr,"unexpected product trap %u at %#x: %s\n",status,pc,g_note);
    exit(3);
}

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


struct abi_observation {const char *id;uint32_t slot,argc,eax,popped;};
static struct abi_observation ABI[768];static uint32_t ABI_N;
static uint32_t answer(const char *id,uint32_t self,uint32_t slot,uint32_t argc,const uint32_t *argv) {
    uint32_t popped=0,eax=call_slot(self,slot,argc,argv,&popped);
    if(ABI_N>=768u) exit(2);
    ABI[ABI_N++]=(struct abi_observation){id,slot,argc,eax,popped};
    check(popped==4u*(argc+2u),"ABI","%s slot %u popped %u",id,slot,popped);
    return eax;
}
#define CALL0(id,self,slot) answer(id,self,slot,0,NULL)
#define CALL(id,self,slot,n,...) answer(id,self,slot,n,(const uint32_t[]){__VA_ARGS__})
#define PP (SCRATCH+0x0000u)
#define OUT (SCRATCH+0x0104u)
#define DESC (SCRATCH+0x0204u)
#define LOCK (SCRATCH+0x0304u)
#define IID (SCRATCH+0x0400u)

struct observation {const char *id;uint32_t n,v[16];};
static struct observation OBS[160];static uint32_t OBS_N;
static void observe(const char *id,uint32_t n,const uint32_t *v) {
    if(OBS_N>=160u || n>16u) exit(2);
    OBS[OBS_N].id=id;OBS[OBS_N].n=n;memcpy(OBS[OBS_N++].v,v,n*4u);
}
#define OBSERVE(id,...) observe(id,sizeof((uint32_t[]){__VA_ARGS__})/4u,(uint32_t[]){__VA_ARGS__})
static void guards(uint32_t out,uint32_t bytes) {
    pk32(out-4u,0x12345678u);pk32(out+bytes,0x87654321u);
    sa2_guest_fill(out,0xa5,bytes);
}
static void intact(uint32_t out,uint32_t bytes) {
    check(peek32(out-4u)==0x12345678u && peek32(out+bytes)==0x87654321u,
          "output-canaries","%#x/%u",out,bytes);
}
static sa2_d3d9_stats stats(void) {sa2_d3d9_stats s;sa2_d3d9_stats_get(&s);return s;}
static uint32_t create_cube(uint32_t dev,uint32_t levels,uint32_t usage) {
    guards(OUT,4u);
    uint32_t hr=CALL("create-cube",dev,25u,7u,4u,levels,usage,21u,1u,OUT,0u);
    intact(OUT,4u);check(hr==0u && peek32(OUT),"create-cube","hr %#x",hr);
    return peek32(OUT);
}
static uint32_t get_cube(uint32_t cube,uint32_t face,uint32_t level) {
    guards(OUT,4u);
    uint32_t hr=CALL("get-cube",cube,18u,3u,face,level,OUT);
    intact(OUT,4u);check(hr==0u && peek32(OUT),"get-cube","hr %#x",hr);
    return peek32(OUT);
}
static void get_desc(uint32_t surf,uint32_t w,uint32_t usage) {
    guards(DESC,32u);
    check(CALL("surface-desc",surf,12u,1u,DESC)==0u,"surface-desc","failed");
    intact(DESC,32u);
    const uint32_t want[8]={21u,1u,usage,1u,0u,0u,w,w};
    uint32_t actual[8];sa2_peek(DESC,32u,actual);
    check(!memcmp(actual,want,sizeof want),"surface-desc","wrong eight description words");
}
static uint32_t lock_surface(uint32_t surf,uint32_t pitch) {
    guards(LOCK,8u);
    check(CALL("surface-lock",surf,13u,3u,LOCK,0u,0u)==0u,"surface-lock","failed");
    intact(LOCK,8u);
    check(peek32(LOCK)==pitch,"surface-pitch","got %u wanted %u",peek32(LOCK),pitch);
    return peek32(LOCK+4u);
}
static void unlock_surface(uint32_t surf) {
    check(CALL0("surface-unlock",surf,14u)==0u,"surface-unlock","failed");
}
static void refs(uint32_t self,uint32_t want,const char *id) {
    uint32_t a=CALL0(id,self,1u),r=CALL0(id,self,2u);
    check(a==want+1u && r==want,id,"AddRef %u Release %u wanted %u/%u",a,r,want+1u,want);
}
static uint32_t device(void) {
    setenv("SA2_D3D9_DEVICE","1",1);unsetenv("SA2_D3D9_BREAK");
    setenv("SA2_D3DX_STRICT","0",1);setenv("SA2_D3DX_CONSTANT_TABLE","0",1);
    sa2_d3d9_config_from_env();
    uint32_t d3d9=sa2_d3d9_make_d3d9(0x00401234u);
    const uint32_t pp[14]={640u,480u,22u,1u,0u,0u,1u,0u,1u,1u,75u,0u,0u,0u};
    sa2_poke(PP,sizeof pp,pp);
    check(CALL("device",d3d9,16u,6u,0u,1u,0u,0x40u,PP,OUT)==0u,"device","failed");
    return peek32(OUT);
}
static void invalid_inputs(uint32_t cube) {
    const uint32_t bad[4][2]={{6u,0u},{0xffffffffu,0u},{0u,2u},{0u,0xffffffffu}};
    sa2_d3d9_stats a=stats();uint32_t alloc=HEAP_N,free_count=HEAP_FREES;
    uint32_t hrs[5];
    for(uint32_t i=0;i<4u;i++) {
        guards(OUT,4u);hrs[i]=CALL("invalid-cube-view",cube,18u,3u,bad[i][0],bad[i][1],OUT);
        intact(OUT,4u);
        check(hrs[i]==0x8876086cu && peek32(OUT)==0u,"invalid-view","case %u hr %#x",i,hrs[i]);
    }
    hrs[4]=CALL("null-cube-out",cube,18u,3u,0u,0u,0u);
    check(hrs[4]==0x8876086cu,"null-out","hr %#x",hrs[4]);
    sa2_d3d9_stats b=stats();
    check(b.objects_built==a.objects_built && b.level_surfaces==a.level_surfaces &&
          HEAP_N==alloc && HEAP_FREES==free_count,"invalid-no-mutation","object/allocation changed");
    refs(cube,13u,"invalid-parent-count");
    observe("invalid-hr",5u,hrs);
    OBSERVE("invalid-deltas",b.objects_built-a.objects_built,b.level_surfaces-a.level_surfaces,
            HEAP_N-alloc,HEAP_FREES-free_count);
    TRAP_ARMED=1;TRAP_STATUS=0u;g_note[0]=0;
    if(!setjmp(TRAP_ENV)) {uint32_t pop;call_slot(cube,18u,3u,
                  (const uint32_t[]){0u,0u,GUEST_HI-2u},&pop);}
    TRAP_ARMED=0;
    b=stats();check(TRAP_STATUS!=0u && strstr(g_note,"guest") && b.objects_built==a.objects_built &&
                   HEAP_N==alloc,"invalid-guest-span","status %u note %s",TRAP_STATUS,g_note);
    OBSERVE("invalid-span",TRAP_STATUS,TRAP_PC,b.objects_built-a.objects_built,HEAP_N-alloc);
}
static void storage_and_lifetime(uint32_t dev) {
    uint32_t cube=create_cube(dev,2u,0u),v[12],ptr[12],pitch[12];
    sa2_d3d9_stats begin=stats();
    uint32_t alloc_begin=HEAP_N,free_begin=HEAP_FREES,active_begin=heap_active();
    check(HEAP[alloc_begin-1u].bytes==95u,"creator-top-storage","got %u",HEAP[alloc_begin-1u].bytes);
    OBSERVE("creator-storage",HEAP[alloc_begin-1u].bytes,begin.guest_bytes_allocated);
    check(CALL0("explicit-level-count",cube,13u)==2u,"explicit-level-count","wanted 2");
    for(uint32_t f=0;f<6u;f++) for(uint32_t l=0;l<2u;l++) {
        uint32_t i=2u*f+l;v[i]=get_cube(cube,f,l);get_desc(v[i],l?2u:4u,0u);
        for(uint32_t j=0;j<i;j++) check(v[i]!=v[j],"face-level-identity","views %u and %u alias",i,j);
    }
    sa2_d3d9_stats built=stats();
    OBSERVE("view-build-deltas",built.objects_built-begin.objects_built,built.level_surfaces-begin.level_surfaces);
    refs(cube,13u,"parent-view-count");
    uint32_t repeat=get_cube(cube,5u,1u);
    check(repeat==v[11],"repeat-identity","repeat moved");refs(cube,14u,"repeat-parent-count");
    const uint32_t iid[4]={0xff9be57eu,0x46fc2900u,0xb98fde81u,0xa0dcb792u};
    sa2_poke(IID,sizeof iid,iid);guards(OUT,4u);
    check(CALL("container",v[11],11u,2u,IID,OUT)==0u && peek32(OUT)==cube,
          "container-identity","wrong parent");intact(OUT,4u);
    refs(cube,15u,"container-parent-count");
    check(CALL0("container-release",peek32(OUT),2u)==14u,"container-release","wanted 14");
    check(CALL0("repeat-release",repeat,2u)==13u,"repeat-release","wanted 13");
    invalid_inputs(cube);
    /* Lock the last view first: the full six-face allocation must already be stable. */
    ptr[11]=lock_surface(v[11],8u);unlock_surface(v[11]);
    uint32_t base=ptr[11]-464u;
    check(HEAP_N==alloc_begin+1u && HEAP_FREES==free_begin+1u &&
          HEAP[HEAP_N-1u].bytes==511u && heap_active()==active_begin,
          "six-face-reserve","wrong growth/allocation/free count");
    sa2_d3d9_stats reserved=stats();
    OBSERVE("six-face-reserve",HEAP_N-alloc_begin,HEAP_FREES-free_begin,HEAP[HEAP_N-1u].bytes,
            reserved.guest_bytes_allocated-begin.guest_bytes_allocated,
            reserved.guest_bytes_freed-begin.guest_bytes_freed);
    static const uint32_t offsets[12]={0u,64u,80u,144u,160u,224u,240u,304u,320u,384u,400u,464u};
    for(uint32_t i=0;i<12u;i++) {
        pitch[i]=(i&1u)?8u:16u;ptr[i]=lock_surface(v[i],pitch[i]);
        check(ptr[i]==base+offsets[i],"face-storage-offset","view %u offset %u wanted %u",i,ptr[i]-base,offsets[i]);
        uint32_t n=(i&1u)?16u:64u,token=0xc0000000u+i;
        for(uint32_t k=0;k<n;k+=4u) pk32(ptr[i]+k,token+k);
        unlock_surface(v[i]);
    }
    for(uint32_t i=0;i<12u;i++) {
        uint32_t got=get_cube(cube,i/2u,i&1u),addr=lock_surface(got,pitch[i]),n=(i&1u)?16u:64u;
        check(got==v[i] && addr==ptr[i] && peek32(addr)==0xc0000000u+i &&
              peek32(addr+n-4u)==0xc0000000u+i+n-4u,"face-storage-stable","view %u changed",i);
        get_desc(got,(i&1u)?2u:4u,0u);
        uint32_t desc[8];sa2_peek(DESC,32u,desc);
        uint32_t row[12]={i/2u,i&1u,addr-base,pitch[i],desc[0],desc[1],desc[2],desc[3],
                          desc[6],desc[7],peek32(addr),peek32(addr+n-4u)};
        observe("surface",12u,row);observe("description",8u,desc);unlock_surface(got);
        check(CALL0("stable-release",got,2u)==13u,"stable-release","wanted 13");
    }
    uint32_t args[8]={v[2],0u,0u,v[0],0u,0u,1u,0u};
    check(sa2_d3d9_d3dx_load_surface_from_surface(args,RETADDR)==0u,"copy-same-cube","failed");
    uint32_t other=create_cube(dev,2u,0u),other_view=get_cube(other,3u,0u);
    args[0]=other_view;
    check(sa2_d3d9_d3dx_load_surface_from_surface(args,RETADDR)==0u,"copy-other-cube","failed");
    uint32_t other_ptr=lock_surface(other_view,16u);unlock_surface(other_view);
    check(peek32(other_ptr)==0xc0000000u && peek32(other_ptr+60u)==0xc000003cu,
          "copy-other-bytes","wrong destination bytes");
    uint32_t copied[12];
    for(uint32_t i=0;i<12u;i++) {
        uint32_t addr=lock_surface(v[i],pitch[i]);copied[i]=peek32(addr);
        check(addr==ptr[i] && copied[i]==0xc0000000u+(i==2u?0u:i),
              "copy-shared-accessor","view %u bytes/offset changed",i);unlock_surface(v[i]);
    }
    observe("after-copy-first-words",12u,copied);
    OBSERVE("other-copy",peek32(other_ptr),peek32(other_ptr+60u),HEAP[HEAP_N-1u].bytes);
    uint32_t same_copy_words[16],other_copy_words[16];
    sa2_peek(ptr[2],64u,same_copy_words);sa2_peek(other_ptr,64u,other_copy_words);
    for(uint32_t i=0;i<16u;i++) {
        check(same_copy_words[i]==0xc0000000u+4u*i && other_copy_words[i]==0xc0000000u+4u*i,
              "copy-all-words","word %u differs",i);
    }
    observe("same-copy-all-words",16u,same_copy_words);observe("other-copy-all-words",16u,other_copy_words);
    uint32_t original=0;
    check(CALL("original-target",dev,38u,2u,0u,OUT)==0u,"original-target","failed");original=peek32(OUT);
    check(CALL("bind-cube",dev,37u,2u,0u,v[11])==0u,"bind-cube","failed");
    sa2_d3d9_stats held=stats();uint32_t frees=HEAP_FREES;
    for(uint32_t i=0;i<12u;i++) {
        uint32_t remaining=CALL0("view-release",v[i],2u);
        check(remaining==12u-i,"shared-view-release","view %u count %u wanted %u",i,remaining,12u-i);
    }
    check(CALL0("parent-release",cube,2u)==0u,"parent-release","wanted zero");
    sa2_d3d9_stats zero=stats();
    check(zero.objects_destroyed==held.objects_destroyed && HEAP_FREES==frees,
          "binding-retains-all","destroyed/freed while bound");
    guards(OUT,4u);
    check(CALL("get-bound",dev,38u,2u,0u,OUT)==0u && peek32(OUT)==v[11],
          "binding-get-identity","wrong surface");intact(OUT,4u);
    refs(cube,1u,"binding-get-parent-ref");
    for(uint32_t i=0;i<12u;i++) {
        uint32_t addr=lock_surface(v[i],pitch[i]);
        check(addr==ptr[i] && peek32(addr)==copied[i],"binding-retains-all-bytes","view %u",i);
        unlock_surface(v[i]);
    }
    check(CALL0("bound-acquired-release",v[11],2u)==0u,"bound-acquired-release","wanted zero");
    check(CALL("replace-bound",dev,37u,2u,0u,original)==0u,"replace-bound","failed");
    sa2_d3d9_stats final=stats();
    check(final.objects_destroyed-held.objects_destroyed==13u && HEAP_FREES-frees==1u &&
          final.guest_bytes_freed-held.guest_bytes_freed==480u,
          "binding-final-destroy","destroyed %u frees %u bytes %u",
          final.objects_destroyed-held.objects_destroyed,HEAP_FREES-frees,
          final.guest_bytes_freed-held.guest_bytes_freed);
    OBSERVE("binding-lifetime",zero.objects_destroyed-held.objects_destroyed,
            final.objects_destroyed-held.objects_destroyed,HEAP_FREES-frees,
            final.guest_bytes_freed-held.guest_bytes_freed);
    CALL0("original-target-release",original,2u);
    check(CALL0("other-view-release",other_view,2u)==1u,"other-view-release","wanted 1");
    check(CALL0("other-parent-release",other,2u)==0u,"other-parent-release","wanted 0");
    OBSERVE("all-cube-frees",HEAP_N-alloc_begin+1u,HEAP_FREES-free_begin,heap_active(),HEAP_ERRORS);
}
static void two_dimensional_control(uint32_t dev) {
    uint32_t a=HEAP_N,f=HEAP_FREES;sa2_d3d9_stats before=stats();
    check(CALL("create-2d",dev,23u,8u,4u,4u,1u,0u,21u,1u,OUT,0u)==0u,"create-2d","failed");
    uint32_t tex=peek32(OUT);
    uint32_t count=CALL0("2d-count",tex,13u);
    check(CALL("2d-view",tex,18u,2u,2u,OUT)==0u,"2d-existing-bound","legacy full-chain bound changed");
    uint32_t s=peek32(OUT);
    check(CALL("2d-repeat",tex,18u,2u,2u,OUT)==0u && peek32(OUT)==s,"2d-identity","changed");
    uint32_t ptr=lock_surface(s,4u);pk32(ptr,0x01020304u);unlock_surface(s);
    guards(LOCK,8u);
    check(CALL("2d-texture-lock",tex,19u,4u,2u,LOCK,0u,0u)==0u,"2d-texture-lock","failed");
    intact(LOCK,8u);
    OBSERVE("2d-existing",count,peek32(LOCK),peek32(LOCK+4u)==ptr,peek32(ptr),HEAP[HEAP_N-1u].bytes);
    check(count==1u && peek32(LOCK)==4u && peek32(LOCK+4u)==ptr && peek32(ptr)==0x01020304u,
          "2d-byte-sharing","changed");
    CALL("2d-unlock",tex,20u,1u,2u);
    check(CALL0("2d-view-release",s,2u)==2u && CALL0("2d-repeat-release",s,2u)==1u &&
          CALL0("2d-parent-release",tex,2u)==0u,"2d-refs","changed");
    sa2_d3d9_stats after=stats();
    OBSERVE("2d-final",after.objects_destroyed-before.objects_destroyed,HEAP_N-a,HEAP_FREES-f,
            after.guest_bytes_freed-before.guest_bytes_freed);
}
static void metadata_counts(uint32_t dev) {
    const uint32_t levels[3]={0u,9u,2u},usage[3]={0u,0u,0x400u};
    for(uint32_t i=0;i<3u;i++) {
        uint32_t c=create_cube(dev,levels[i],usage[i]),count=CALL0("metadata-count",c,13u);
        uint32_t want=i==2u?1u:3u,views[3]={0u,0u,0u};
        check(count==want,"metadata-count","case %u got %u",i,count);
        for(uint32_t lv=0;lv<want;lv++) {views[lv]=get_cube(c,0u,lv);get_desc(views[lv],4u>>lv,usage[i]);}
        guards(OUT,4u);
        uint32_t hr=CALL("metadata-invalid",c,18u,3u,0u,want,OUT);
        check(hr==0x8876086cu && peek32(OUT)==0u,"metadata-exposed-bound","case %u",i);intact(OUT,4u);
        uint32_t ptr=lock_surface(views[0],16u);unlock_surface(views[0]);
        check(ptr!=0u && HEAP[HEAP_N-1u].bytes==535u,"metadata-stored-reserve","case %u",i);
        OBSERVE("metadata",levels[i],usage[i],count,hr,HEAP[HEAP_N-1u].bytes);
        for(uint32_t lv=0;lv<want;lv++) CALL0("metadata-view-release",views[lv],2u);
        check(CALL0("metadata-parent-release",c,2u)==0u,"metadata-parent-release","failed");
    }
}
static void boundary_cases(uint32_t dev) {
    const uint32_t cases[5][3]={{16384u,0u,21u},{65536u,1u,21u},{0xffffffffu,1u,0x31545844u},
                               {4u,1u,0xdeadbeefu},{0u,1u,21u}};
    for(uint32_t i=0;i<5u;i++) {
        sa2_d3d9_stats a=stats();uint32_t alloc=HEAP_N,frees=HEAP_FREES;
        guards(OUT,4u);TRAP_ARMED=1;TRAP_STATUS=0u;g_note[0]=0;
        if(!setjmp(TRAP_ENV)) {uint32_t pop;call_slot(dev,25u,7u,
            (const uint32_t[]){cases[i][0],cases[i][1],0u,cases[i][2],1u,OUT,0u},&pop);}
        TRAP_ARMED=0;intact(OUT,4u);sa2_d3d9_stats b=stats();
        check(TRAP_STATUS==SA2_TRAP_CONTROL_LEFT && strstr(g_note,"IDirect3DCubeTexture9::surface storage") &&
              strstr(g_note,i<3u?"not representable":"supported byte layout"),
              "capacity-refusal","case %u status %u note %s",i,TRAP_STATUS,g_note);
        check(a.objects_built==b.objects_built && HEAP_N==alloc && HEAP_FREES==frees && peek32(OUT)==0xa5a5a5a5u,
              "capacity-no-mutation","case %u changed",i);
        OBSERVE("capacity",cases[i][0],cases[i][1],cases[i][2],TRAP_STATUS,
                b.objects_built-a.objects_built,HEAP_N-alloc,HEAP_FREES-frees,peek32(OUT));
        printf("BOUNDARY %u: %s\n",i,g_note);
    }
}
static void write_results(const char *path) {
    FILE *f=fopen(path,"wx");if(!f) {perror(path);exit(2);}
    fprintf(f,"{\"scope\":\"authored CPU cube surfaces; no native, renderer, M7 or autogen pixel claim\",\"checks\":%u,\"failures\":%u,\"failed_ids\":[",g_checks,g_fails);
    for(uint32_t i=0;i<g_fails && i<256u;i++) fprintf(f,"%s\"%s\"",i?",":"",g_failed[i]);
    fprintf(f,"],\"observations\":[");
    for(uint32_t i=0;i<OBS_N;i++) {
        fprintf(f,"%s{\"id\":\"%s\",\"actual\":[",i?",":"",OBS[i].id);
        for(uint32_t j=0;j<OBS[i].n;j++) fprintf(f,"%s%u",j?",":"",OBS[i].v[j]);
        fprintf(f,"]}");
    }
    fprintf(f,"],\"abi\":[");
    for(uint32_t i=0;i<ABI_N;i++) fprintf(f,"%s{\"id\":\"%s\",\"slot\":%u,\"argc\":%u,\"eax\":%u,\"popped\":%u}",
        i?",":"",ABI[i].id,ABI[i].slot,ABI[i].argc,ABI[i].eax,ABI[i].popped);
    fprintf(f,"],\"heap\":{\"allocations\":%u,\"frees\":%u,\"active\":%u,\"errors\":%u}}\n",HEAP_N,HEAP_FREES,heap_active(),HEAP_ERRORS);
    if(fclose(f)) {perror(path);exit(2);}
}
int main(int argc,char **argv) {
    if(argc!=3 || (strcmp(argv[1],"positive") && strcmp(argv[1],"boundaries"))) {
        fprintf(stderr,"usage: d3d9_cube_surface_probe positive|boundaries RESULTS.json\n");return 2;
    }
    g_mem=calloc(1,GUEST_HI-GUEST_LO);if(!g_mem) return 2;
    uint32_t dev=device();
    if(!strcmp(argv[1],"positive")) {storage_and_lifetime(dev);two_dimensional_control(dev);metadata_counts(dev);}
    else boundary_cases(dev);
    check(HEAP_ERRORS==0u && heap_active()==0u,"heap-final","active %u errors %u",heap_active(),HEAP_ERRORS);
    write_results(argv[2]);
    printf("d3d9_cube_surface_probe: %u checks, %u failures\n",g_checks,g_fails);
    free(g_mem);return g_fails?1:0;
}
