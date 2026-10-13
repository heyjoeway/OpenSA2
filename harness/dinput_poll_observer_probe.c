/* Authored FIFO input through real DirectInput guest vtables, no executable. */
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>
#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_devprofile.h"
#include "sa2_dinput.h"
#include "sa2_trap.h"
#include "sa2_window.h"

#define LO 0x03000000u
#define HI 0x03300000u
#define DATA (LO + 0x4000u)
#define STACK (LO + 0xeff00u)
static uint8_t mem[HI-LO];
static sa2_lifted_fn ft[SA2_DI_MAX_TYPES*SA2_DI_MAX_SLOTS];
static unsigned checks, fails, calls;
static char note[1024];
static void check(int yes, const char *name) {
    ++checks;
    if (!yes) { ++fails; fprintf(stderr, "FAIL %s\n", name); }
}
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a<LO || a>=HI || n>HI-a) return SA2_E_RANGE;
    memcpy(out, mem+a-LO, n); return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    if (a<LO || a>=HI || n>HI-a) return SA2_E_RANGE;
    memcpy(mem+a-LO, in, n); return SA2_OK;
}
int sa2_guest_fill(uint32_t a, int v, uint32_t n) {
    if (a<LO || a>=HI || n>HI-a) return SA2_E_RANGE;
    memset(mem+a-LO, v, n); return SA2_OK;
}
int sa2_ft_add(uint32_t a, sa2_lifted_fn f, const char *name, uint8_t kind) {
    (void)name; (void)kind;
    if (a<SA2_DI_THUNK_BASE || a>=SA2_DI_THUNK_END || (a-SA2_DI_THUNK_BASE)%SA2_DI_THUNK_STRIDE) return -1;
    ft[(a-SA2_DI_THUNK_BASE)/SA2_DI_THUNK_STRIDE]=f; return 0;
}
sa2_lifted_fn sa2_ft_lookup(uint32_t a) {
    if (a<SA2_DI_THUNK_BASE || a>=SA2_DI_THUNK_END || (a-SA2_DI_THUNK_BASE)%SA2_DI_THUNK_STRIDE) return NULL;
    return ft[(a-SA2_DI_THUNK_BASE)/SA2_DI_THUNK_STRIDE];
}
uint32_t sa2_rt_get_gpr(const void *s, int i) { return ((const sa2_cpu_state_t*)s)->gpr[i]; }
void sa2_rt_set_gpr(void *s, int i, uint32_t v) { ((sa2_cpu_state_t*)s)->gpr[i]=v; }
void sa2_trap_note(const char *fmt, ...) {
    va_list ap; va_start(ap,fmt); vsnprintf(note,sizeof note,fmt,ap); va_end(ap);
}
void sa2_trap_raise(uint32_t status, uint32_t pc) {
    fprintf(stderr,"TRAP %u %#x %s\n",status,pc,note); exit(3);
}
const sa2_wnd_window *sa2_window_of(uint32_t hwnd) {
    static const sa2_wnd_window w={0}; return hwnd==1 ? &w : NULL;
}
void sa2_window_stats(sa2_wnd_stats *out) { memset(out,0,sizeof *out); }
void sa2_window_post_quit(uint32_t code, int host) { (void)code;(void)host;check(0,"unexpected-quit"); }
static uint32_t get32(uint32_t a) { uint32_t v=0;check(sa2_peek(a,4,&v)==SA2_OK,"peek");return v; }
static void put32(uint32_t a,uint32_t v) { check(sa2_poke(a,4,&v)==SA2_OK,"poke"); }
static uint32_t slot(uint32_t self,uint32_t k,unsigned argc,const uint32_t *a) {
    sa2_cpu_state_t st;memset(&st,0x5a,sizeof st);
    for (unsigned i=0;i<SA2_GPR_COUNT;i++) st.gpr[i]=0xa0000000u+i;
    uint32_t esp=STACK-4u*(argc+2u);st.gpr[SA2_ESP]=esp;
    put32(esp,0x00401000u);put32(esp+4,self);
    for (unsigned i=0;i<argc;i++) put32(esp+8+4*i,a[i]);
    sa2_cpu_state_t before=st;
    uint32_t thunk=get32(get32(self)+4*k);sa2_lifted_fn f=sa2_ft_lookup(thunk);
    check(f!=NULL,"registered-vtable-thunk");if (!f) exit(2);
    f(&st,thunk,NULL);++calls;
    check(st.gpr[SA2_ESP]==STACK,"stdcall-pop");
    for (unsigned i=0;i<SA2_GPR_COUNT;i++) if (i!=SA2_ESP && i!=SA2_EAX) check(st.gpr[i]==before.gpr[i],"preserved-register");
    before.gpr[SA2_ESP]=st.gpr[SA2_ESP];before.gpr[SA2_EAX]=st.gpr[SA2_EAX];
    check(!memcmp(&before,&st,sizeof st),"preserved-rest-of-cpu");return st.gpr[SA2_EAX];
}
static uint32_t keyboard(void) {
    static const uint8_t iid[16]={0x30,0x80,0x79,0xbf,0x3a,0x48,0xa2,0x4d,0xaa,0x99,0x5d,0x64,0xed,0x36,0x97,0};
    static const uint8_t guid[16]={0x61,0x2b,0x1d,0x6f,0xa0,0xd5,0xcf,0x11,0xbf,0xc7,0x44,0x45,0x53,0x54,0,0};
    sa2_poke(DATA,16,iid);sa2_poke(DATA+16,16,guid);
    uint32_t a[5]={0,0x800,DATA,DATA+32,0};check(sa2_dinput_create8(a,0x00401000)==0,"create8");
    uint32_t b[3]={DATA+16,DATA+36,0};check(slot(get32(DATA+32),3,3,b)==0,"create-keyboard");
    uint32_t k=get32(DATA+36),fmt[6]={24,16,2,256,256,0};sa2_poke(DATA+64,sizeof fmt,fmt);
    uint32_t f=DATA+64;check(slot(k,11,1,&f)==0,"set-format");
    uint32_t coop[2]={1,6};check(slot(k,13,2,coop)==0,"set-coop");
    check(slot(k,7,0,NULL)==0,"acquire");return k;
}
typedef struct {uint32_t poll,first,last,count;} observation;
static observation rows[16];static unsigned used;
#ifndef NO_OBSERVER
void sa2_dinput_host_records_read(uint32_t poll,uint32_t first,uint32_t last,uint32_t count) {
    sa2_di_input_stats st;sa2_dinput_input_stats_get(&st);
    check(st.polls==poll+1,"observer-after-poll-counter");
    check(st.last_seq==last && st.records>=count,"observer-after-record-counters");
    check(used<16,"observer-capacity");if(used<16)rows[used++]=(observation){poll,first,last,count};
}
#endif
static void poll_keys(uint32_t k,const uint8_t *keys,unsigned n) {
    uint32_t a[2]={256,DATA+512};sa2_guest_fill(a[1],0x55,256);check(slot(k,9,2,a)==0,"poll-hr");
    uint8_t got[256];sa2_peek(a[1],256,got);
    for(unsigned i=0;i<256;i++) {unsigned want=0;for(unsigned j=0;j<n;j++)if(keys[j]==i)want=0x80;check(got[i]==want,"literal-key-byte");}
}
static void record(int fd,uint32_t seq,const uint8_t *keys,unsigned n,unsigned from,unsigned to) {
    uint8_t r[264]={0};uint32_t magic=0x4b324153;
    for(unsigned i=0;i<4;i++){r[i]=(uint8_t)(magic>>(8*i));r[4+i]=(uint8_t)(seq>>(8*i));}
    for(unsigned i=0;i<n;i++)r[8+keys[i]]=0x80;
    check(write(fd,r+from,to-from)==(ssize_t)(to-from),"whole-authored-write");
}
int main(int argc,char **argv) {
    if(argc!=3)return 2;
    const char *mode=argv[1],*path=argv[2];
    unsetenv("SA2_DEVICE_PROFILE");unsetenv("SA2_DINPUT_BREAK");unsetenv("SA2_INPUT_DI_SOURCE");unsetenv("SA2_INPUT_DI_SCRIPT");unsetenv("SA2_INPUT_DI_LOG");setenv("SA2_DINPUT_DEVICE","1",1);
    if(!strcmp(mode,"fifo")){check(!mkfifo(path,0600),"mkfifo");setenv("SA2_INPUT_DI_SOURCE",path,1);}
    if(!strcmp(mode,"script")){FILE *f=fopen(path,"wx");if(!f)return 2;fputs("1 1 0x1C\n",f);fclose(f);setenv("SA2_INPUT_DI_SCRIPT",path,1);}
    sa2_devprofile_config_from_env();sa2_dinput_config_from_env();uint32_t k=keyboard();
    if(!strcmp(mode,"fifo")) {
        const uint8_t enter[]={0x1c},up[]={0xc8},zx[]={0x2c,0x2d},zxup[]={0x2c,0x2d,0xc8},esc[]={1},space[]={0x39},space0[]={0,0x39};
        int fd=open(path,O_RDWR|O_NONBLOCK);check(fd>=0,"open-writer");if(fd<0)return 2;
        poll_keys(k,NULL,0);record(fd,0,enter,1,0,264);record(fd,1,up,1,0,100);poll_keys(k,enter,1);
        record(fd,1,up,1,100,264);record(fd,2,zx,2,0,264);poll_keys(k,zxup,3);poll_keys(k,zx,2);
        record(fd,5,NULL,0,0,264);poll_keys(k,NULL,0);record(fd,6,esc,1,0,264);close(fd);poll_keys(k,esc,1);
        fd=open(path,O_WRONLY|O_NONBLOCK);check(fd>=0,"second-writer");if(fd<0)return 2;
        record(fd,7,space0,2,0,264);record(fd,8,enter,1,0,10);poll_keys(k,space,1);close(fd);poll_keys(k,NULL,0);
        sa2_di_input_stats st;sa2_dinput_input_stats_get(&st);
        check(st.polls==8 && st.polls_held==5 && st.keys_written==8 && st.records==6 && st.last_seq==7 && st.seq_gaps==1 && st.polls_without_writer==2 && st.partial_dropped==10,"literal-fifo-counters");
#ifndef NO_OBSERVER
        static const observation want[]={{1,0,0,1},{2,1,2,2},{4,5,5,1},{5,6,6,1},{6,7,7,1}};
        check(used==5,"observer-row-count");check(used==5 && !memcmp(rows,want,sizeof want),"literal-observer-rows");
#else
        check(used==0,"undefined-observer-no-rows");
#endif
    } else {
        const uint8_t enter[]={0x1c};poll_keys(k,NULL,0);poll_keys(k,!strcmp(mode,"script")?enter:NULL,!strcmp(mode,"script")?1:0);poll_keys(k,NULL,0);check(used==0,"no-nonfifo-observer");
        sa2_di_input_stats st;sa2_dinput_input_stats_get(&st);check(st.records==0,"no-nonfifo-records");
        check(st.polls==(!strcmp(mode,"script")?3:0),"literal-nonfifo-polls");
    }
    for(unsigned i=0;i<used;i++)printf("ROW %u %u %u %u\n",rows[i].poll,rows[i].first,rows[i].last,rows[i].count);
    printf("RESULT checks=%u fails=%u vtable_calls=%u\n",checks,fails,calls);return fails?1:0;
}
