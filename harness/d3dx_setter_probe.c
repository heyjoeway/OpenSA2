/* Authored CTAB setter CPU probe. No native DLL, shipped bytes, GPU, recorder/player or M7 claim. */
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
#include "../runtime/sa2_guest_call.h"
#include "../runtime/sa2_call.h"
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
static int g_corrupt_snapshot, g_fail_scratch, g_retired_code;
#define CODE (SCRATCH+0x8000u)

int sa2_peek(uint32_t addr, uint32_t len, void *out) {
    if (g_retired_code && addr >= CODE && addr < CODE+4096u) return SA2_E_RANGE;
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
typedef struct { uint32_t at,size,owner,requested; int live; } heap_block;
static heap_block g_heap[4096];
static uint32_t g_heap_count,g_heap_next=0x03100000u,g_heap_live,g_heap_frees;
static uint32_t g_scratch_allocs,g_scratch_frees;
uint32_t sa2_heap_alloc_owned(uint32_t owner,uint32_t bytes) {
    if (bytes==16u) { if (g_fail_scratch) return 0; g_scratch_allocs++; }
    for (uint32_t i=0;i<g_heap_count;i++) if (!g_heap[i].live && g_heap[i].size>=bytes) {
        g_heap[i].live=1; g_heap[i].owner=owner; g_heap[i].requested=bytes; g_heap_live++;
        sa2_guest_fill(g_heap[i].at,0,g_heap[i].size); return g_heap[i].at;
    }
    uint32_t at=(g_heap_next+31u)&~31u;
    if (g_heap_count==4096u || at>=SA2_D3D9_OBJ_BASE || bytes>SA2_D3D9_OBJ_BASE-at) return 0;
    g_heap[g_heap_count++]=(heap_block){at,bytes,owner,bytes,1};
    g_heap_next=at+bytes; g_heap_live++; sa2_guest_fill(at,0,bytes); return at;
}
void sa2_heap_free_owned(uint32_t owner,uint32_t payload) {
    for (uint32_t i=0;i<g_heap_count;i++) if (g_heap[i].at==payload && g_heap[i].live && g_heap[i].owner==owner) {
        if (g_heap[i].requested==16u) g_scratch_frees++;
        g_heap[i].live=0; g_heap_live--; g_heap_frees++;
        sa2_guest_fill(payload,0xdd,g_heap[i].size); return;
    }
    fprintf(stderr,"invalid/double fixture heap free %#x owner %#x\n",payload,owner); exit(2);
}

#define THUNKS (SA2_D3D9_MAX_TYPES * SA2_D3D9_MAX_SLOTS)
static sa2_lifted_fn g_fn[THUNKS];
static struct {uint32_t va;sa2_lifted_fn fn;} extra[64];static unsigned extra_count;
int sa2_ft_add(uint32_t va,sa2_lifted_fn fn,const char *name,uint8_t kind) {
 (void)name;(void)kind;
 if(va>=SA2_D3D9_THUNK_BASE&&va<SA2_D3D9_THUNK_END&&!((va-SA2_D3D9_THUNK_BASE)%SA2_D3D9_THUNK_STRIDE)) {
  g_fn[(va-SA2_D3D9_THUNK_BASE)/SA2_D3D9_THUNK_STRIDE]=fn;return 0;
 }
 for(unsigned i=0;i<extra_count;i++) if(extra[i].va==va){extra[i].fn=fn;return 0;}
 if(extra_count==64u) return -1;
 extra[extra_count].va=va;extra[extra_count++].fn=fn;return 0;
}
sa2_lifted_fn sa2_ft_lookup(uint32_t va) {
 if(va>=SA2_D3D9_THUNK_BASE&&va<SA2_D3D9_THUNK_END&&!((va-SA2_D3D9_THUNK_BASE)%SA2_D3D9_THUNK_STRIDE))
  return g_fn[(va-SA2_D3D9_THUNK_BASE)/SA2_D3D9_THUNK_STRIDE];
 for(unsigned i=0;i<extra_count;i++)if(extra[i].va==va)return extra[i].fn;
 return NULL;
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


struct setter_case {const char *id;unsigned pixel;const char *name;unsigned api;uint32_t input[16];};
static const struct setter_case cases[]={
{"setter-vs-s-SetBool",0u,"s",0u,{0x00000007u}},
{"setter-vs-s-SetInt",0u,"s",1u,{0xffffffefu}},
{"setter-vs-s-SetFloat",0u,"s",2u,{0x3fa00000u}},
{"setter-vs-s-SetVector",0u,"s",3u,{0x3fa00000u,0xc0200000u,0x40700000u,0xc0900000u}},
{"setter-vs-s-SetMatrix",0u,"s",4u,{0x3f800000u,0x40000000u,0x40400000u,0x40800000u,0x40a00000u,0x40c00000u,0x40e00000u,0x41000000u,0x41100000u,0x41200000u,0x41300000u,0x41400000u,0x41500000u,0x41600000u,0x41700000u,0x41800000u}},
{"setter-vs-v-SetBool",0u,"v",0u,{0x00000007u}},
{"setter-vs-v-SetInt",0u,"v",1u,{0xffffffefu}},
{"setter-vs-v-SetFloat",0u,"v",2u,{0x3fa00000u}},
{"setter-vs-v-SetVector",0u,"v",3u,{0x3fa00000u,0xc0200000u,0x40700000u,0xc0900000u}},
{"setter-vs-v-SetMatrix",0u,"v",4u,{0x3f800000u,0x40000000u,0x40400000u,0x40800000u,0x40a00000u,0x40c00000u,0x40e00000u,0x41000000u,0x41100000u,0x41200000u,0x41300000u,0x41400000u,0x41500000u,0x41600000u,0x41700000u,0x41800000u}},
{"setter-vs-i-SetBool",0u,"i",0u,{0x00000007u}},
{"setter-vs-i-SetInt",0u,"i",1u,{0xffffffefu}},
{"setter-vs-i-SetFloat",0u,"i",2u,{0x3fa00000u}},
{"setter-vs-i-SetVector",0u,"i",3u,{0x3fa00000u,0xc0200000u,0x40700000u,0xc0900000u}},
{"setter-vs-i-SetMatrix",0u,"i",4u,{0x3f800000u,0x40000000u,0x40400000u,0x40800000u,0x40a00000u,0x40c00000u,0x40e00000u,0x41000000u,0x41100000u,0x41200000u,0x41300000u,0x41400000u,0x41500000u,0x41600000u,0x41700000u,0x41800000u}},
{"setter-vs-b-SetBool",0u,"b",0u,{0x00000007u}},
{"setter-vs-b-SetInt",0u,"b",1u,{0xffffffefu}},
{"setter-vs-b-SetFloat",0u,"b",2u,{0x3fa00000u}},
{"setter-vs-b-SetVector",0u,"b",3u,{0x3fa00000u,0xc0200000u,0x40700000u,0xc0900000u}},
{"setter-vs-b-SetMatrix",0u,"b",4u,{0x3f800000u,0x40000000u,0x40400000u,0x40800000u,0x40a00000u,0x40c00000u,0x40e00000u,0x41000000u,0x41100000u,0x41200000u,0x41300000u,0x41400000u,0x41500000u,0x41600000u,0x41700000u,0x41800000u}},
{"setter-vs-mr-SetBool",0u,"mr",0u,{0x00000007u}},
{"setter-vs-mr-SetInt",0u,"mr",1u,{0xffffffefu}},
{"setter-vs-mr-SetFloat",0u,"mr",2u,{0x3fa00000u}},
{"setter-vs-mr-SetVector",0u,"mr",3u,{0x3fa00000u,0xc0200000u,0x40700000u,0xc0900000u}},
{"setter-vs-mr-SetMatrix",0u,"mr",4u,{0x3f800000u,0x40000000u,0x40400000u,0x40800000u,0x40a00000u,0x40c00000u,0x40e00000u,0x41000000u,0x41100000u,0x41200000u,0x41300000u,0x41400000u,0x41500000u,0x41600000u,0x41700000u,0x41800000u}},
{"setter-vs-mc-SetBool",0u,"mc",0u,{0x00000007u}},
{"setter-vs-mc-SetInt",0u,"mc",1u,{0xffffffefu}},
{"setter-vs-mc-SetFloat",0u,"mc",2u,{0x3fa00000u}},
{"setter-vs-mc-SetVector",0u,"mc",3u,{0x3fa00000u,0xc0200000u,0x40700000u,0xc0900000u}},
{"setter-vs-mc-SetMatrix",0u,"mc",4u,{0x3f800000u,0x40000000u,0x40400000u,0x40800000u,0x40a00000u,0x40c00000u,0x40e00000u,0x41000000u,0x41100000u,0x41200000u,0x41300000u,0x41400000u,0x41500000u,0x41600000u,0x41700000u,0x41800000u}},
{"setter-ps-s-SetBool",1u,"s",0u,{0x00000000u}},
{"setter-ps-s-SetInt",1u,"s",1u,{0xffffffe1u}},
{"setter-ps-s-SetFloat",1u,"s",2u,{0xc0200000u}},
{"setter-ps-s-SetVector",1u,"s",3u,{0x40b00000u,0xc0c80000u,0x40f80000u,0xc1080000u}},
{"setter-ps-s-SetMatrix",1u,"s",4u,{0x42ca0000u,0x42cc0000u,0x42ce0000u,0x42d00000u,0x42d20000u,0x42d40000u,0x42d60000u,0x42d80000u,0x42da0000u,0x42dc0000u,0x42de0000u,0x42e00000u,0x42e20000u,0x42e40000u,0x42e60000u,0x42e80000u}},
{"setter-ps-v-SetBool",1u,"v",0u,{0x00000000u}},
{"setter-ps-v-SetInt",1u,"v",1u,{0xffffffe1u}},
{"setter-ps-v-SetFloat",1u,"v",2u,{0xc0200000u}},
{"setter-ps-v-SetVector",1u,"v",3u,{0x40b00000u,0xc0c80000u,0x40f80000u,0xc1080000u}},
{"setter-ps-v-SetMatrix",1u,"v",4u,{0x42ca0000u,0x42cc0000u,0x42ce0000u,0x42d00000u,0x42d20000u,0x42d40000u,0x42d60000u,0x42d80000u,0x42da0000u,0x42dc0000u,0x42de0000u,0x42e00000u,0x42e20000u,0x42e40000u,0x42e60000u,0x42e80000u}},
{"setter-ps-i-SetBool",1u,"i",0u,{0x00000000u}},
{"setter-ps-i-SetInt",1u,"i",1u,{0xffffffe1u}},
{"setter-ps-i-SetFloat",1u,"i",2u,{0xc0200000u}},
{"setter-ps-i-SetVector",1u,"i",3u,{0x40b00000u,0xc0c80000u,0x40f80000u,0xc1080000u}},
{"setter-ps-i-SetMatrix",1u,"i",4u,{0x42ca0000u,0x42cc0000u,0x42ce0000u,0x42d00000u,0x42d20000u,0x42d40000u,0x42d60000u,0x42d80000u,0x42da0000u,0x42dc0000u,0x42de0000u,0x42e00000u,0x42e20000u,0x42e40000u,0x42e60000u,0x42e80000u}},
{"setter-ps-b-SetBool",1u,"b",0u,{0x00000000u}},
{"setter-ps-b-SetInt",1u,"b",1u,{0xffffffe1u}},
{"setter-ps-b-SetFloat",1u,"b",2u,{0xc0200000u}},
{"setter-ps-b-SetVector",1u,"b",3u,{0x40b00000u,0xc0c80000u,0x40f80000u,0xc1080000u}},
{"setter-ps-b-SetMatrix",1u,"b",4u,{0x42ca0000u,0x42cc0000u,0x42ce0000u,0x42d00000u,0x42d20000u,0x42d40000u,0x42d60000u,0x42d80000u,0x42da0000u,0x42dc0000u,0x42de0000u,0x42e00000u,0x42e20000u,0x42e40000u,0x42e60000u,0x42e80000u}},
{"setter-ps-mr-SetBool",1u,"mr",0u,{0x00000000u}},
{"setter-ps-mr-SetInt",1u,"mr",1u,{0xffffffe1u}},
{"setter-ps-mr-SetFloat",1u,"mr",2u,{0xc0200000u}},
{"setter-ps-mr-SetVector",1u,"mr",3u,{0x40b00000u,0xc0c80000u,0x40f80000u,0xc1080000u}},
{"setter-ps-mr-SetMatrix",1u,"mr",4u,{0x42ca0000u,0x42cc0000u,0x42ce0000u,0x42d00000u,0x42d20000u,0x42d40000u,0x42d60000u,0x42d80000u,0x42da0000u,0x42dc0000u,0x42de0000u,0x42e00000u,0x42e20000u,0x42e40000u,0x42e60000u,0x42e80000u}},
{"setter-ps-mc-SetBool",1u,"mc",0u,{0x00000000u}},
{"setter-ps-mc-SetInt",1u,"mc",1u,{0xffffffe1u}},
{"setter-ps-mc-SetFloat",1u,"mc",2u,{0xc0200000u}},
{"setter-ps-mc-SetVector",1u,"mc",3u,{0x40b00000u,0xc0c80000u,0x40f80000u,0xc1080000u}},
{"setter-ps-mc-SetMatrix",1u,"mc",4u,{0x42ca0000u,0x42cc0000u,0x42ce0000u,0x42d00000u,0x42d20000u,0x42d40000u,0x42d60000u,0x42d80000u,0x42da0000u,0x42dc0000u,0x42de0000u,0x42e00000u,0x42e20000u,0x42e40000u,0x42e60000u,0x42e80000u}}
};
#define SINK_DEV (SCRATCH+0x5000u)
#define SINK_VT (SCRATCH+0x5100u)
#define INPUT (SCRATCH+0x6000u)
#define OUTPUT (SCRATCH+0x7000u)
#define RETADDR 0x00401000u
#define SINK_PC 0x00410000u
static const char *active="setup",*mode="default";
static uint32_t global_device,outer_table,inner_table,inner_handle,log_start;
static uint32_t sink_calls,failures,outer_scratch,inner_scratch;
static void *live_state;
static char note[1024];
static uint32_t peek32(uint32_t a){uint32_t v;if(sa2_peek(a,4,&v)!=SA2_OK){fprintf(stderr,"provider peek %#x\n",a);exit(2);}return v;}
static void put32(uint32_t a,uint32_t v){if(sa2_poke(a,4,&v)!=SA2_OK)exit(2);}
static void need(int yes,const char *id){if(!yes){failures++;printf("{\"event\":\"check-failed\",\"case\":\"%s\",\"id\":\"%s\"}\n",active,id);}}
static void logs(void){
 for(uint32_t i=log_start;i<sa2_d3d9_log_count();i++){
  const sa2_d3d9_call *r=sa2_d3d9_log_at(i);
  printf("{\"event\":\"log\",\"case\":\"%s\",\"seq\":%u,\"caller\":%u,\"type\":%u,\"slot\":%u,\"self\":%u,\"answered\":%u,\"ret\":%u}\n",active,r->seq,r->caller,r->type,r->slot,r->self,r->answered,r->ret);
 }
}
void sa2_trap_note(const char *fmt,...){va_list a;va_start(a,fmt);vsnprintf(note,sizeof note,fmt,a);va_end(a);}
void sa2_trap_raise(uint32_t status,uint32_t pc){
 printf("{\"event\":\"trap\",\"case\":\"%s\",\"status\":%u,\"pc\":%u,\"sink_calls\":%u,\"helper_calls\":%u,\"scratch_allocs\":%u,\"scratch_frees\":%u,\"heap_live\":%u}\n",active,status,pc,sink_calls,sa2_guest_stdcall_count(),g_scratch_allocs,g_scratch_frees,g_heap_live);
 logs();fprintf(stderr,"%s\n",note);fflush(stdout);exit(3);
}
static uint32_t invoke(uint32_t self,unsigned slot,unsigned n,const uint32_t *args,probe_state *state){
 uint32_t target=peek32(peek32(self)+4u*slot),base=STACK_TOP-4u*(n+2u);
 memset(state,0,sizeof *state);state->gpr[SA2_ESP]=base;state->gpr[SA2_EIP]=target;
 state->gpr[SA2_EBX]=0x13579bdfu;state->gpr[SA2_ESI]=0x2468ace0u;state->gpr[SA2_EDI]=0x11223344u;state->gpr[SA2_EBP]=0x55667788u;
 put32(base,RETADDR);put32(base+4u,self);for(unsigned i=0;i<n;i++)put32(base+8u+4u*i,args[i]);
 put32(base-4u,0xf00dfaceu);put32(STACK_TOP,0xaabbccddu);
 sa2_lifted_fn fn=sa2_ft_lookup(target);if(!fn)exit(2);fn(state,target,NULL);
 need(state->gpr[SA2_ESP]==STACK_TOP,"outer-pop");need(state->gpr[SA2_EIP]==target,"outer-eip");
 need(state->gpr[SA2_EBX]==0x13579bdfu&&state->gpr[SA2_ESI]==0x2468ace0u&&state->gpr[SA2_EDI]==0x11223344u&&state->gpr[SA2_EBP]==0x55667788u,"callee-saved");
 need(peek32(base)==RETADDR&&peek32(base+4u)==self&&peek32(base-4u)==0xf00dfaceu&&peek32(STACK_TOP)==0xaabbccddu,"outer-frame-canaries");
 return state->gpr[SA2_EAX];
}
static uint32_t simple(uint32_t self,unsigned slot,unsigned n,const uint32_t *args){probe_state s;return invoke(self,slot,n,args,&s);}
static uint32_t append(uint8_t *b,uint32_t *used,const void *data,uint32_t len){uint32_t a=*used;memcpy(b+a,data,len);*used+=len;return a;}
static void u16(uint8_t *p,uint16_t x){memcpy(p,&x,2);}
static void u32(uint8_t *p,uint32_t x){memcpy(p,&x,4);}
static uint32_t make_table(unsigned pixel,unsigned variant){
 uint8_t b[2048]={0};uint32_t used=148u;const uint32_t f[12]={0x3f800000u,0x40000000u,0x40400000u,0x40800000u,0x40a00000u,0x40c00000u,0x40e00000u,0x41000000u,0x41100000u,0x41200000u,0x41300000u,0x41400000u};
 const uint32_t iv[4]={17,0,0,0},bv=1;
 uint32_t defaults[3]={append(b,&used,&bv,4),append(b,&used,iv,16),append(b,&used,f,48)};
 const char *names[6]={"s","v","i","b","mr","mc"};uint32_t sets[6]={2,2,1,0,2,2},indices[6]={7,11,2,3,20,24},counts[6]={1,1,1,1,2,3},cls[6]={0,1,0,0,2,3},types[6]={3,3,2,1,3,3},nr[6]={1,1,1,1,2,2},nc[6]={1,3,1,1,3,3};
 for(unsigned i=0;i<6;i++){
  uint8_t t[16]={0};u16(t,cls[i]);u16(t+2,types[i]);u16(t+4,nr[i]);u16(t+6,nc[i]);u16(t+8,1);
  if(i==0&&variant==2){u16(t+8,2);counts[i]=2;}
  if(i==0&&variant==3){
   uint8_t child[16]={0};u16(child+2,3);u16(child+4,1);u16(child+6,1);u16(child+8,1);
   uint32_t ct=append(b,&used,child,16),name=append(b,&used,"member",7);uint32_t m[2]={name,ct};uint32_t mi=append(b,&used,m,8);
   u16(t,5);u16(t+2,0);u16(t+10,1);u32(t+12,mi);
  }
  if(i==0&&variant==4){u16(t,1);u16(t+6,4);}
  if(i==0&&variant==5)indices[i]=256;
  if((i==0&&variant==7)||(i==1&&variant==8)||(i==4&&variant==9)||(i==5&&variant==10))counts[i]++;
  uint32_t type=append(b,&used,t,16),name=append(b,&used,names[i],strlen(names[i])+1u);uint8_t *info=b+28u+20u*i;
  u32(info,name);u16(info+4,sets[i]);u16(info+6,indices[i]);u16(info+8,counts[i]);u32(info+12,type);u32(info+16,defaults[sets[i]]);
 }
 uint32_t creator=append(b,&used,"authored CPU setters",21),target=append(b,&used,pixel?"ps_3_0":"vs_3_0",7),ver=pixel?0xffff0300u:0xfffe0300u;
 uint32_t header[7]={28,creator,variant==1?(ver^0x10000u):variant==6?0xfffe0200u:ver,6,28,0,target};memcpy(b,header,28);
 unsigned words=(used+3u)/4u;uint32_t bytecode=variant==6?0xfffe0200u:ver;
 g_retired_code=0;put32(CODE,bytecode);put32(CODE+4u,((words+1u)<<16)|0xfffeu);put32(CODE+8u,0x42415443u);sa2_poke(CODE+12u,words*4u,b);put32(CODE+12u+words*4u,0xffffu);
 uint32_t a[2]={CODE,OUTPUT};need(sa2_d3d9_d3dx_get_shader_constant_table(a,RETADDR)==0,"table-create");uint32_t table=peek32(OUTPUT);
 sa2_guest_fill(CODE,0xee,4096);g_retired_code=1;return table;
}
static uint32_t handle(uint32_t table,const char *name){sa2_poke(OUTPUT+32u,strlen(name)+1u,name);uint32_t a[2]={0,OUTPUT+32u};return simple(table,9,2,a);}
static void *sink_second(void *,uint32_t,void *);
static void *sink(void *state,uint32_t pc,void *memory){
 uint32_t esp=sa2_rt_get_gpr(state,SA2_ESP),self=peek32(esp+4u),index=peek32(esp+8u),scratch=peek32(esp+12u),count=peek32(esp+16u);
 unsigned slot=(pc-SINK_PC)/16u;if(slot>=128u)slot-=128u;unsigned words=slot==98u||slot==113u?1u:4u;
 sink_calls++;need(state==live_state&&self==SINK_DEV&&count==1u,"live-sink-abi");need(peek32(esp)==SA2_CALL_SENTINEL,"nested-sentinel");
 printf("{\"event\":\"sink\",\"case\":\"%s\",\"slot\":%u,\"pc\":%u,\"this\":%u,\"index\":%u,\"count\":%u,\"scratch\":%u,\"bytes\":\"",active,slot,pc,self,index,count,scratch);
 for(unsigned i=0;i<words*4u;i++){uint8_t v;need(sa2_peek(scratch+i,1,&v)==SA2_OK,"sink-input");printf("%02x",v);}printf("\",\"live_state\":true}\n");
 if(!strcmp(mode,"forward")||!strcmp(mode,"later-failure")||!strcmp(mode,"bad-pop")){
  uint32_t argv[4]={global_device,index,scratch,count};uint32_t target=peek32(peek32(global_device)+4u*slot);
  need(sa2_guest_stdcall(state,target,4,argv)==0,"forward-hr");
  if(!strcmp(mode,"later-failure")||!strcmp(mode,"bad-pop")){
   uint32_t getargs[4]={global_device,index,OUTPUT+512u,1u};
   need(sa2_guest_stdcall(state,peek32(peek32(global_device)+4u*(slot+1u)),4,getargs)==0,"partial-bank-get");
   printf("{\"event\":\"completed-effect\",\"case\":\"%s\",\"index\":%u,\"words\":[%u,%u,%u,%u]}\n",active,index,peek32(OUTPUT+512u),peek32(OUTPUT+516u),peek32(OUTPUT+520u),peek32(OUTPUT+524u));
  }
 }
 if(!strcmp(mode,"nested")&&sink_calls==1u){
  outer_scratch=scratch;sa2_guest_fill(INPUT,0xee,64);sa2_guest_fill(scratch,0xa5,16);const uint32_t a[1]={outer_table};need(sa2_guest_stdcall(state,peek32(peek32(outer_table)+8u),1,a)==0,"nested-release");
  const char *saved=active;active="nested-inner";uint32_t b[4]={inner_table,SINK_DEV,inner_handle,0xc0200000u};
  need(sa2_guest_stdcall(state,peek32(peek32(inner_table)+68u),4,b)==0,"nested-setter");active=saved;
 }else if(!strcmp(mode,"nested")&&!strcmp(active,"nested-inner")){
  inner_scratch=scratch;need(inner_scratch!=outer_scratch,"distinct-scratch");unsigned live16=0;
  for(unsigned i=0;i<g_heap_count;i++)live16+=g_heap[i].live&&g_heap[i].requested==16u;
  need(live16==2u,"concurrent-scratch");printf("{\"event\":\"nested-scratch\",\"outer\":%u,\"inner\":%u,\"live16\":%u}\n",outer_scratch,inner_scratch,live16);
 }
 if(!strcmp(mode,"fresh-slot")&&sink_calls==1u)put32(SINK_VT+4u*slot,SINK_PC+16u*(slot+128u));
 if(!strcmp(mode,"fresh-ft")&&sink_calls==1u)need(sa2_ft_add(pc,sink_second,"replacement",1)==0,"ft-replace");
 uint32_t hr=!strcmp(mode,"observed-failure")?0x8876086cu:!strcmp(mode,"single-failure")?0x8876086cu:!strcmp(mode,"later-failure")&&sink_calls==2u?0x80004005u:0;
 sa2_rt_set_gpr(state,SA2_EAX,hr);sa2_rt_set_gpr(state,SA2_ESP,esp+(!strcmp(mode,"bad-pop")?16u:20u));sa2_rt_set_gpr(state,SA2_EIP,SA2_CALL_SENTINEL);return memory;
}
static void *sink_second(void *s,uint32_t pc,void *m){printf("{\"event\":\"replacement\",\"case\":\"%s\",\"pc\":%u}\n",active,pc);return sink(s,pc,m);}
static uint32_t run_setter(uint32_t table,const struct setter_case *c){
 active=c->id;uint32_t h=handle(table,c->name),input=c->input[0];if(c->api>=3u){sa2_poke(INPUT,c->api==3u?16u:64u,c->input);input=INPUT;}
 log_start=sa2_d3d9_log_count();uint32_t before=sa2_guest_stdcall_count(),allocs=g_scratch_allocs,frees=g_scratch_frees,heap=g_heap_live;
 probe_state state;live_state=&state;uint32_t args[3]={SINK_DEV,h,input},slot=13u+2u*c->api;
 uint32_t hr=invoke(table,slot,3,args,&state);
 printf("{\"event\":\"return\",\"case\":\"%s\",\"hr\":%u,\"helper_delta\":%u,\"scratch_alloc_delta\":%u,\"scratch_free_delta\":%u,\"heap_live_delta\":%d,\"pop\":20,\"eip\":%u,\"failures\":%u}\n",active,hr,sa2_guest_stdcall_count()-before,g_scratch_allocs-allocs,g_scratch_frees-frees,(int)g_heap_live-(int)heap,state.gpr[SA2_EIP],failures);logs();return hr;
}
static void release(uint32_t table){simple(table,2,0,NULL);}
static void setup(void){
 g_mem=calloc(1,GUEST_HI-GUEST_LO);if(!g_mem)exit(2);setenv("SA2_D3D9_DEVICE","1",1);setenv("SA2_D3DX_CONSTANT_TABLE","1",1);unsetenv("SA2_D3D9_BREAK");sa2_d3d9_config_from_env();
 uint32_t d=sa2_d3d9_make_d3d9(RETADDR),pp[14]={640,480,22,1,0,0,1,0,1,1,75,0,0,0};sa2_poke(OUTPUT+128u,sizeof pp,pp);uint32_t a[6]={0,1,0,0x40,OUTPUT+128u,OUTPUT};need(simple(d,16,6,a)==0,"device");global_device=peek32(OUTPUT);
 printf("{\"event\":\"setup\",\"device\":%u,\"sink\":%u}\n",global_device,SINK_DEV);
 put32(SINK_DEV,SINK_VT);unsigned slots[6]={94,96,98,109,111,113};for(unsigned i=0;i<6;i++){unsigned s=slots[i];put32(SINK_VT+4u*s,SINK_PC+16u*s);need(sa2_ft_add(SINK_PC+16u*s,sink,"authored sink",1)==0,"register-sink");need(sa2_ft_add(SINK_PC+16u*(s+128u),sink_second,"authored second sink",1)==0,"register-second");}
}
static void forward_checks(void){
 uint32_t vs=make_table(0,0),ps=make_table(1,0),slots[6]={94,109,96,111,98,113},index[6]={7,7,2,2,3,3};
 const uint32_t sentinel[4]={0x12345678u,0x89abcdefu,0x10203040u,0x50607080u};sa2_poke(INPUT+128u,16,sentinel);
 for(unsigned i=0;i<6;i++){uint32_t a[3]={index[i]-1u,INPUT+128u,1};need(simple(global_device,slots[i],3,a)==0,"neighbor-set");}
 unsigned chosen[6]={2,32,11,41,15,45};
 for(unsigned step=0;step<6;step++){
  need(run_setter(step&1u?ps:vs,&cases[chosen[step]])==0,"forward-setter");
  sa2_d3d9_stats before,after;sa2_d3d9_stats_get(&before);
  for(unsigned bank=0;bank<6;bank++){
   uint32_t a[3]={index[bank],OUTPUT,1};simple(global_device,slots[bank]+1u,3,a);
   printf("{\"event\":\"bank\",\"step\":%u,\"bank\":%u,\"words\":[",step,bank);unsigned n=bank<4?4:1;for(unsigned k=0;k<n;k++)printf("%s%u",k?",":"",peek32(OUTPUT+4*k));printf("],\"neighbor\":[");
   a[0]--;simple(global_device,slots[bank]+1u,3,a);for(unsigned k=0;k<n;k++)printf("%s%u",k?",":"",peek32(OUTPUT+4*k));printf("]}\n");
  }
  sa2_d3d9_stats_get(&after);printf("{\"event\":\"unset\",\"step\":%u,\"delta\":%u}\n",step,after.unset-before.unset);
 }
 release(vs);release(ps);
}
int main(int argc,char **argv){
 if(argc!=2)return 2;mode=argv[1];setup();
 if(!strcmp(mode,"default")){
  uint32_t t[2]={make_table(0,0),make_table(1,0)};for(unsigned i=0;i<60;i++)need(run_setter(t[cases[i].pixel],&cases[i])==0,"default-hr");release(t[0]);release(t[1]);
 }else if(!strcmp(mode,"forward"))forward_checks();
 else{
  unsigned variant=!strcmp(mode,"version-mismatch")?1:!strcmp(mode,"array-child")?2:!strcmp(mode,"member-child")?3:!strcmp(mode,"shape")?4:!strcmp(mode,"extent")?5:!strcmp(mode,"version2")?6:!strcmp(mode,"over-scalar")?7:!strcmp(mode,"over-vector")?8:!strcmp(mode,"over-rows")?9:!strcmp(mode,"over-columns")?10:0;
  outer_table=make_table(0,variant);struct setter_case c=cases[2];c.id=mode;
  if(!strcmp(mode,"nested")){inner_table=make_table(1,0);inner_handle=handle(inner_table,"s");c=cases[29];c.id="nested-outer";}
  if(!strcmp(mode,"fresh-slot")||!strcmp(mode,"fresh-ft")||!strcmp(mode,"later-failure")){c=cases[29];c.id=mode;}
  if(!strcmp(mode,"single-failure")){c=cases[1];c.id=mode;}
  if(!strcmp(mode,"over-vector")){c=cases[8];c.id=mode;}
  if(!strcmp(mode,"over-rows")){c=cases[24];c.id=mode;}
  if(!strcmp(mode,"over-columns")){c=cases[29];c.id=mode;}
  if(!strcmp(mode,"array-child"))c.name="s[0]";if(!strcmp(mode,"member-child"))c.name="s.member";
  if(!strcmp(mode,"conversion")||!strcmp(mode,"conversion-overflow")){c=cases[12];c.input[0]=!strcmp(mode,"conversion")?0x7fc00000u:0x4f000000u;c.id=mode;}
  if(!strcmp(mode,"alloc"))g_fail_scratch=1;
  if(!strcmp(mode,"unregistered"))put32(SINK_VT+4u*94u,0x004f0000u);
  if(!strcmp(mode,"wrong-device")||!strcmp(mode,"dead-device")||!strcmp(mode,"null-vector")||!strcmp(mode,"null-matrix")||!strcmp(mode,"null-device")||!strcmp(mode,"null-handle")||!strcmp(mode,"guest-name")||!strcmp(mode,"foreign-handle")||!strcmp(mode,"stale-handle")){
   active=mode;uint32_t h=handle(outer_table,"s"),dev=SINK_DEV,value=0x3fa00000u,slot=17u;
   if(!strcmp(mode,"wrong-device"))dev=outer_table;
   if(!strcmp(mode,"dead-device")){dev=global_device;release(dev);}
   if(!strcmp(mode,"null-device"))dev=0;
   if(!strcmp(mode,"null-handle"))h=0;
   if(!strcmp(mode,"guest-name"))h=OUTPUT+32u;
   if(!strcmp(mode,"foreign-handle")||!strcmp(mode,"stale-handle")){inner_table=make_table(0,0);h=handle(inner_table,"s");if(!strcmp(mode,"stale-handle"))release(inner_table);}
   if(!strcmp(mode,"null-vector")){slot=19;value=0;}if(!strcmp(mode,"null-matrix")){slot=21;value=0;}
   log_start=sa2_d3d9_log_count();probe_state state;live_state=&state;uint32_t a[3]={dev,h,value};uint32_t hr=invoke(outer_table,slot,3,a,&state);
   printf("{\"event\":\"policy-return\",\"case\":\"%s\",\"hr\":%u,\"sink_calls\":%u,\"scratch_allocs\":%u,\"scratch_frees\":%u}\n",active,hr,sink_calls,g_scratch_allocs,g_scratch_frees);logs();release(outer_table);if(inner_table&&strcmp(mode,"stale-handle"))release(inner_table);
  }else{need(run_setter(outer_table,&c)==0,"case-hr");if(strcmp(mode,"nested"))release(outer_table);else release(inner_table);}
 }
 printf("{\"event\":\"summary\",\"failures\":%u,\"sink_calls\":%u,\"scratch_allocs\":%u,\"scratch_frees\":%u,\"heap_live\":%u,\"helper_depth\":%u}\n",failures,sink_calls,g_scratch_allocs,g_scratch_frees,g_heap_live,sa2_guest_stdcall_max_depth());free(g_mem);return failures?1:0;
}
