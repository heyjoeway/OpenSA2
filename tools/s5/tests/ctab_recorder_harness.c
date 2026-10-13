/* Authored CTAB calls through the existing real recorder provider, without a game. */
#define main rec_harness_original_main
#include "rec_harness.c"
#undef main
#include "sa2_guest_call.h"

_Static_assert(sizeof(probe_state) == 40, "provider state extent");
static unsigned failures;
static void require(int ok, const char *name) {
    if (!ok) { fprintf(stderr, "ctab-recorder: %s\n", name); failures++; }
}
static void put16(uint8_t *p, uint16_t v) { memcpy(p, &v, 2); }
static void put32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }
static uint32_t add(uint8_t *b, uint32_t *n, const void *p, uint32_t bytes) {
    uint32_t at = *n; memcpy(b + at, p, bytes); *n += bytes; return at;
}
static uint32_t table(unsigned pixel, const char *outdir) {
    uint8_t b[1024] = {0}; uint32_t used = 148;
    const char *names[6] = {"s", "v", "i", "b", "mr", "mc"};
    const uint16_t sets[6] = {2,2,1,0,2,2}, indices[6] = {7,11,2,3,20,24};
    const uint16_t counts[6] = {1,1,1,1,2,3}, classes[6] = {0,1,0,0,2,3};
    const uint16_t types[6] = {3,3,2,1,3,3}, rows[6] = {1,1,1,1,2,2}, cols[6] = {1,3,1,1,3,3};
    for (unsigned i = 0; i < 6; i++) {
        uint8_t t[16] = {0}; put16(t, classes[i]); put16(t+2, types[i]);
        put16(t+4, rows[i]); put16(t+6, cols[i]); put16(t+8, 1);
        uint32_t ty = add(b, &used, t, 16), nm = add(b, &used, names[i], (uint32_t)strlen(names[i])+1);
        uint8_t *info = b + 28 + 20*i;
        put32(info, nm); put16(info+4, sets[i]); put16(info+6, indices[i]);
        put16(info+8, counts[i]); put32(info+12, ty);
    }
    uint32_t creator = add(b, &used, "authored recorder", 18);
    uint32_t target = add(b, &used, pixel ? "ps_3_0" : "vs_3_0", 7);
    uint32_t ver = pixel ? 0xffff0300u : 0xfffe0300u;
    uint32_t head[7] = {28, creator, ver, 6, 28, 0, target}; memcpy(b, head, 28);
    uint32_t words = (used+3)/4, code = SCRATCH + 0x6000;
    pk32(code, ver); pk32(code+4, ((words+1)<<16)|0xfffeu); pk32(code+8, 0x42415443);
    require(sa2_poke(code+12, words*4, b) == SA2_OK, "bytecode-write");
    pk32(code+12+words*4, 0xffff);
    char path[4096]; snprintf(path, sizeof path, "%s/%s.bytecode", outdir, pixel ? "ps" : "vs");
    FILE *f = fopen(path, "wb"); require(f != NULL, "bytecode-artifact-open");
    if (f) { require(fwrite(g_mem+code-GUEST_LO, 1, 16+words*4, f) == 16+words*4, "bytecode-artifact-write"); require(fclose(f)==0, "bytecode-artifact-close"); }
    uint32_t a[2] = {code, SCRATCH+0x7000};
    require(sa2_d3d9_d3dx_get_shader_constant_table(a, CALLER) == 0, "table-create");
    uint32_t result = peek32(a[1]);
    require(sa2_guest_fill(code, 0xee, 4096) == SA2_OK, "retire-source-bytecode");
    return result;
}
static uint32_t constant(uint32_t t, const char *name) {
    uint32_t p = SCRATCH+0x7100; require(sa2_poke(p, (uint32_t)strlen(name)+1, name)==SA2_OK, "name-write");
    return CALL(t, 9, 0, p);
}
static void outer(unsigned order, uint32_t t, unsigned slot, uint32_t h, uint32_t input, unsigned delta) {
    uint32_t target = peek32(peek32(t)+4*slot), esp = STACK_TOP-20;
    probe_state s, expected; memset(&s, 0xa5, sizeof s);
    for (unsigned i=0; i<SA2_GPR_COUNT; i++) s.gpr[i]=0x11223300u+i;
    s.gpr[SA2_ESP]=esp; s.gpr[SA2_EIP]=target; s.cw=0x007f; expected=s;
    pk32(esp, CALLER); pk32(esp+4,t); pk32(esp+8,g_dev); pk32(esp+12,h); pk32(esp+16,input);
    pk32(esp-68,0xf00dface); pk32(esp-4,0xfeedbeef); pk32(STACK_TOP,0xaabbccdd);
    uint32_t start=sa2_d3d9_log_count(), count=sa2_guest_stdcall_count();
    sa2_lifted_fn fn=sa2_ft_lookup(target); require(fn!=NULL,"outer-registry-entry");
    if (!fn) return;
    fn(&s,target,NULL);
    expected.gpr[SA2_EAX]=0; expected.gpr[SA2_ESP]=STACK_TOP;
    require(memcmp(&s,&expected,sizeof s)==0,"outer-cpu-state");
    require(peek32(esp)==CALLER&&peek32(esp+4)==t&&peek32(esp+8)==g_dev&&peek32(esp+12)==h&&peek32(esp+16)==input,"outer-frame-words");
    require(peek32(esp-68)==0xf00dface&&peek32(esp-4)==0xfeedbeef&&peek32(STACK_TOP)==0xaabbccdd,"outer-frame-canaries");
    require(sa2_guest_stdcall_count()-count==delta,"real-helper-count");
    require(sa2_guest_stdcall_max_depth()==1,"real-helper-depth");
    require(sa2_d3d9_log_count()-start==delta+1,"runtime-group-count");
    const sa2_d3d9_call *c=sa2_d3d9_log_at(start);
    require(c&&c->type==15&&c->slot==slot&&c->self==t&&c->caller==CALLER&&c->argc==3&&c->arg[0]==g_dev&&c->arg[1]==h&&c->arg[2]==input&&c->ret==0&&c->answered==2,"outer-record-settlement");
    const sa2_d3d9_slot_stat *st=sa2_d3d9_slot_stat_at(15,slot);
    require(st&&st->answered==2,"outer-slot-settlement");
    printf("{\"event\":\"outer\",\"order\":%u,\"table\":%u,\"slot\":%u,\"handle\":%u,\"input\":%u,\"helper_delta\":%u,\"state\":\"",order,t,slot,h,input,delta);
    for (unsigned i=0;i<sizeof s;i++) printf("%02x",((uint8_t*)&s)[i]);
    printf("\"}\n");
}
static void read_bank(unsigned slot, unsigned reg, unsigned n, const uint32_t *words) {
    const uint32_t out=SCRATCH+0x7400;
    require(CALL(g_dev,slot,reg,out,1)==0,"bank-get");
    for(unsigned i=0;i<n;i++) require(peek32(out+4*i)==words[i],"bank-literal");
    require(CALL(g_dev,slot,reg+1,out,1)==0,"bank-neighbor-get");
    /* Matrix neighbors are checked once after each full matrix below. */
    printf("{\"event\":\"bank\",\"slot\":%u,\"reg\":%u,\"words\":[",slot,reg);
    require(CALL(g_dev,slot,reg,out,1)==0,"bank-reget");
    for(unsigned i=0;i<n;i++) printf("%s%u",i?",":"",peek32(out+4*i));
    printf("]}\n");
}
static void descriptor(unsigned which, uint32_t t, uint32_t h, unsigned outputs) {
    const uint32_t desc = SCRATCH+0x7500, count = SCRATCH+0x7540;
    for (unsigned i=0;i<12;i++) pk32(desc+4*i,0x1234abcd);
    pk32(desc-4,0xaabbccdd); pk32(desc+48,0xdeadbeef); pk32(count,0xdecafbad);
    uint32_t result=CALL(t,6,h,(outputs&1)?desc:0,(outputs&2)?count:0);
    require(result==(which==4?SA2_D3DERR_INVALIDCALL:0),"descriptor-result");
    require(peek32(desc-4)==0xaabbccdd&&peek32(desc+48)==0xdeadbeef,"descriptor-canaries");
    if (which!=4&&(outputs&1)) {
        char name[2]={0};
        require(sa2_peek(peek32(desc),2,name)==SA2_OK&&!memcmp(name,"s",2),"descriptor-owned-name");
    }
    printf("{\"event\":\"descriptor\",\"case\":%u,\"table\":%u,\"handle\":%u,\"desc_arg\":%u,\"count_arg\":%u,\"result\":%u,\"count\":%u,\"words\":[",which,t,h,(outputs&1)?desc:0,(outputs&2)?count:0,result,peek32(count));
    for(unsigned i=0;i<12;i++)printf("%s%u",i?",":"",peek32(desc+4*i));
    printf("]}\n");
}
int main(int argc, char **argv) {
    if(argc!=2) return 2;
    unsetenv("SA2_D3D9_BREAK"); unsetenv("SA2_REC_AUDIO");
    setenv("SA2_D3D9_DEVICE","1",1); setenv("SA2_D3DX_CONSTANT_TABLE","1",1);
    g_mem=calloc(1,GUEST_HI-GUEST_LO); if(!g_mem)return 2;
    sa2_d3d9_config_from_env(); (void)make_device();
    uint32_t vs=table(0,argv[1]),ps=table(1,argv[1]),h[2][6];
    const char *names[6]={"s","v","i","b","mr","mc"};
    for(unsigned p=0;p<2;p++)for(unsigned i=0;i<6;i++) { h[p][i]=constant(p?ps:vs,names[i]);require(h[p][i]!=0,"owned-handle"); }
    const uint32_t vector[4]={0x3f800000,0x40000000,0x40400000,0x40800000};
    const uint32_t matrix[16]={0x3f800000,0x40000000,0x40400000,0x40800000,0x40a00000,0x40c00000,0x40e00000,0x41000000,0x41100000,0x41200000,0x41300000,0x41400000,0x41500000,0x41600000,0x41700000,0x41800000};
    const uint32_t vp=SCRATCH+0x7200,mp=SCRATCH+0x7300;
    put_words(vp,vector,4); put_words(mp,matrix,16);
    uint32_t start=sa2_d3d9_log_count();
    outer(1,vs,17,h[0][0],0x3fa00000,1); outer(2,vs,15,h[0][2],0xfffffffe,1); outer(3,vs,13,h[0][3],1,1);
    outer(4,ps,17,h[1][0],0xc0200000,1); outer(5,ps,15,h[1][2],0xfffffffe,1); outer(6,ps,13,h[1][3],1,1);
    outer(7,vs,19,h[0][1],vp,1); outer(8,vs,21,h[0][4],mp,2); outer(9,ps,21,h[1][5],mp,3);
    require(sa2_d3d9_log_count()-start==21,"runtime-core-21");
    for(uint32_t i=start;i<sa2_d3d9_log_count();i++) {
        const sa2_d3d9_call *c=sa2_d3d9_log_at(i);
        printf("{\"event\":\"log\",\"seq\":%u,\"type\":%u,\"slot\":%u,\"self\":%u,\"caller\":%u,\"answered\":%u,\"ret\":%u,\"args\":[%u,%u,%u]}\n",c->seq,c->type,c->slot,c->self,c->caller,c->answered,c->ret,c->arg[0],c->arg[1],c->arg[2]);
    }
    const unsigned slots[12]={95,97,99,110,112,114,95,95,95,110,110,110};
    const unsigned regs[12]={7,2,3,7,2,3,11,20,21,24,25,26};
    const unsigned ns[12]={4,4,1,4,4,1,4,4,4,4,4,4};
    const uint32_t expected[12][4]={{0x3fa00000,0,0,0},{0xfffffffe,0,1,0},{1}, {0xc0200000,0,0,0},{0xfffffffe,0,1,0},{1},{0x3f800000,0x40000000,0x40400000,0},{0x3f800000,0x40000000,0x40400000,0},{0x40a00000,0x40c00000,0x40e00000,0},{0x3f800000,0x40a00000,0,0},{0x40000000,0x40c00000,0,0},{0x40400000,0x40e00000,0,0}};
    for(unsigned i=0;i<12;i++)read_bank(slots[i],regs[i],ns[i],expected[i]);
    const unsigned neighbors[6]={8,3,4,8,3,4};
    for(unsigned i=0;i<6;i++){require(CALL(g_dev,slots[i],neighbors[i],SCRATCH+0x7400,1)==0,"unset-bank-get");for(unsigned j=0;j<ns[i];j++)require(peek32(SCRATCH+0x7400+4*j)==0,"bank-neighbor-zero");}
    for(unsigned i=0;i<3;i++){unsigned index=(unsigned[]){12,22,27}[i],slot=i==2?110:95;require(CALL(g_dev,slot,index,SCRATCH+0x7400,1)==0,"matrix-neighbor-get");for(unsigned j=0;j<4;j++)require(peek32(SCRATCH+0x7400+4*j)==0,"matrix-neighbor-zero");}
    descriptor(0,vs,h[0][0],3); descriptor(1,vs,h[0][0],1);
    descriptor(2,vs,h[0][0],2); descriptor(3,vs,h[0][0],0);
    descriptor(4,vs,0,3);
    printf("{\"event\":\"summary\",\"device\":%u,\"helper_calls\":%u,\"max_depth\":%u,\"ft_registrations\":%u,\"ft_digest\":\"%016llx\",\"memory_digest\":\"%016llx\",\"failures\":%u}\n",g_dev,sa2_guest_stdcall_count(),sa2_guest_stdcall_max_depth(),g_ft_regs,(unsigned long long)g_ft_fnv,(unsigned long long)fnv1a_more(FNV_BASIS,g_mem,GUEST_HI-GUEST_LO),failures);
    return failures?1:0;
}
