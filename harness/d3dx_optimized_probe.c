/* Authored optimized CTAB reflection using the existing guest providers. */
#define main d3dx_table_original_main
#include "d3dx_table_probe.c"
#undef main

static void opt16(uint8_t *p, uint16_t v) { memcpy(p, &v, 2); }
static void opt32(uint8_t *p, uint32_t v) { memcpy(p, &v, 4); }

static void observation(uint32_t table, const char *label, uint32_t root_default) {
    const uint32_t h=named(label,table,0,label), desc=SCRATCH+0x100, count=desc+48;
    check(h!=0,"optimized-handle","missing %s",label);
    check(CALL(label,table,6,3,h,desc,count)==0,"optimized-desc","failed %s",label);
    uint32_t words[12]; sa2_peek(desc,48,words);
    const char *expected=strrchr(label,'.');expected=expected?expected+1:"value";
    char name[6]={0}; sa2_peek(words[0],(uint32_t)strlen(expected)+1u,name);
    check(!strcmp(name,expected),"optimized-owned-name","name changed");
    printf("{\"name\":\"%s\",\"count\":%u,\"fields\":[",label,peek32(count));
    for(unsigned i=1;i<11;i++)printf("%s%u",i==1?"":",",words[i]);
    printf("],\"default_offset\":%u,\"default_word\":%u}\n",
           words[11] ? words[11]-root_default : 0,words[11] ? peek32(words[11]) : 0);
}

int main(int argc, char **argv) {
    g_mem=calloc(1,GUEST_HI-GUEST_LO);if(!g_mem)return 2;
    setenv("SA2_D3D9_DEVICE","1",1);setenv("SA2_D3DX_CONSTANT_TABLE","1",1);
    unsetenv("SA2_D3D9_BREAK");sa2_d3d9_config_from_env();
    const uint32_t code=SCRATCH+0x8000,out=SCRATCH+0x80;
    unsigned elements=0;int structure=0;
    if(argc==3&&!strcmp(argv[1],"file")) {
        FILE *f=fopen(argv[2],"rb");if(!f)return 2;
        uint8_t bytes[65536];size_t n=fread(bytes,1,sizeof bytes,f);
        int good=n&&n<sizeof bytes&&!ferror(f)&&feof(f);fclose(f);
        if(!good||sa2_poke(code,(uint32_t)n,bytes)!=SA2_OK)return 2;
    } else {
        if(argc!=4)return 2;
        char *end=NULL;unsigned long raw=strtoul(argv[2],&end,10);
        if(!*argv[2]||*end||raw>65535)return 2;
        unsigned set=2,cl=3,type=3,rows=2,cols=3,stride=3;
        elements=3;
        if(!strcmp(argv[1],"float")){cl=1;rows=1;cols=4;elements=2;stride=1;}
        else if(!strcmp(argv[1],"int")){set=1;cl=1;type=2;rows=1;cols=3;stride=1;}
        else if(!strcmp(argv[1],"bool")){set=0;cl=0;type=1;rows=1;cols=1;elements=4;stride=1;}
        else if(!strcmp(argv[1],"struct")){structure=1;cl=5;type=0;rows=1;cols=1;stride=6;}
        else if(strcmp(argv[1],"matrix"))return 2;
        const unsigned unit=set==0?4:16, logical=stride*elements;
        unsigned payload=logical*unit;
        if(!strcmp(argv[3],"short"))payload=unit*(logical-1);
        else if(strcmp(argv[3],"full")&&strcmp(argv[3],"none"))return 2;
        const unsigned defaults=structure?160u:72u;
        uint8_t b[512]={0};const uint32_t ver=0xfffe0300;
        const uint32_t hdr[7]={28,0,ver,1,28,0,0};memcpy(b,hdr,28);
        opt32(b+28,64);opt16(b+32,(uint16_t)set);opt16(b+34,20);
        opt16(b+36,(uint16_t)raw);opt32(b+40,48);
        opt32(b+44,!strcmp(argv[3],"none")?0:defaults);
        opt16(b+48,(uint16_t)cl);opt16(b+50,(uint16_t)type);
        opt16(b+52,(uint16_t)rows);opt16(b+54,(uint16_t)cols);opt16(b+56,(uint16_t)elements);
        memcpy(b+64,"value",6);
        if(structure) {
            opt16(b+58,3);opt32(b+60,72);
            for(unsigned i=0;i<3;i++) {
                const unsigned at=96+16*i;
                opt32(b+72+8*i,144+2*i);opt32(b+76+8*i,at);
                opt16(b+at,i==0?0:i==1?1:3);opt16(b+at+2,3);
                opt16(b+at+4,i==2?4:1);opt16(b+at+6,i==0?1:i==1?3:4);
                opt16(b+at+8,1);b[144+2*i]=(uint8_t)(i==0?'x':i==1?'y':'m');
            }
        }
        for(unsigned i=0;i<payload/4;i++)opt32(b+defaults+4*i,0x3f800000+i);
        unsigned size=defaults+payload;pk32(code,ver);pk32(code+4,((size/4+1)<<16)|0xfffe);
        pk32(code+8,0x42415443);sa2_poke(code+12,size,b);pk32(code+12+size,0xffff);
    }
    uint32_t args[2]={code,out};pk32(out,0x1234abcd);
    check(sa2_d3d9_d3dx_get_shader_constant_table(args,CALLER)==0,"optimized-create","table failed");
    uint32_t table=peek32(out);
    if(elements) {
        uint32_t h=named("root",table,0,"value"),desc=SCRATCH+0x100;
        check(CALL("root",table,6,3,h,desc,desc+48)==0,"optimized-root","root failed");
        uint32_t def=peek32(desc+44);sa2_guest_fill(code,0xee,4096);
        observation(table,"value",def);
        for(unsigned i=0;i<elements;i++) {
            char name[32];snprintf(name,sizeof name,"value[%u]",i);observation(table,name,def);
            if(structure)for(unsigned k=0;k<3;k++) {
                snprintf(name,sizeof name,"value[%u].%c",i,k==0?'x':k==1?'y':'m');
                observation(table,name,def);
            }
        }
    } else printf("{\"created\":true}\n");
    check(CALL0("optimized-release",table,2)==0&&g_heap_live==0,"optimized-free","owned storage remains");
    return g_fails?1:0;
}
