/* runtime/sa2_emulate.cpp against the host CPU, one instruction at a time.
 *
 * Each case is an instruction's bytes and a machine context: eight general
 * registers, the MMX and XMM registers, an FSAVE image of the x87 state, MXCSR,
 * and bytes to place in guest memory. The case runs twice:
 *
 *   emulated  a remill State built from the context, guest memory a window at
 *             a fixed address, sa2_emulate on the bytes placed there;
 *   native    the same bytes in an executable page, run on the host between a
 *             loader and a saver written in assembly, with the same registers,
 *             the same window at the same address, and rsp switched into it so
 *             an [esp] operand addresses what it addresses in the guest.
 *
 * Both print what they end with, and Documentation/scripts/tests/test_emulate.py
 * compares what remill's model says should agree. The window's addresses are
 * below 4 GiB, so the 32-bit guest's addressing and the 64-bit host's reach
 * the same bytes. Built with -mno-red-zone: the native path pushes inside
 * inline assembly.
 *
 *   emulate_probe < cases       one line per case in, one line per case out
 *
 * In:  <mode m|x|r> <instruction hex> <native hex or -> <context hex> [<addr>:<hex>,...]
 *      (the native bytes differ only where 64-bit mode reads the guest's
 *      differently: an absolute [disp32] is RIP-relative there)
 * Out: native <context hex> <changes> | emu <status> <next or fault> <context hex> <changes>
 * where <changes> is the window's differing bytes as addr:hex runs, or "-".
 */
#include <cfenv>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>

#include <sys/mman.h>
#include <xmmintrin.h>

#include "remill/Arch/X86/Runtime/State.h"

extern "C" {
#include "guest_memory.h"
#include "sa2_emulate.h"
}

namespace {

constexpr uint32_t kWin = 0x10000000u;      /* guest address == host address */
constexpr uint32_t kWinSize = 0x00100000u;
constexpr uint32_t kCode = kWin + 0x000F0000u;
/* A second region over the image's own addresses at 0x0087E530, where the
 * codec's paddusw sites read a constant by absolute address: a case places the
 * image's bytes there, so those sites run on the value the game uses. */
constexpr uint32_t kAux = 0x00870000u;
constexpr uint32_t kAuxSize = 0x00010000u;
uint8_t *g_win;
uint8_t *g_aux;

/* The context both paths start from and end with. */
struct __attribute__((packed)) Ctx {
    uint32_t gpr[8];          /* eax ecx edx ebx esp ebp esi edi */
    uint64_t mm[8];
    uint8_t  xmm[8][16];
    uint8_t  x87[108];        /* FSAVE, 32-bit protected-mode layout */
    uint32_t mxcsr;
};
static_assert(sizeof(Ctx) == 336, "context layout");

/* What the assembly reads and writes; offsets are passed in as immediates. */
struct alignas(16) Native {
    uint64_t gpr[8];
    uint64_t mm[8];
    uint8_t  xmm[8][16];
    uint8_t  x87[108];
    uint8_t  pad0[4];
    uint32_t mxcsr;
    uint32_t mode;            /* 0: x87 then MMX registers; 1: x87 last */
    uint64_t saved_rsp;
    uint64_t ret;
    uint64_t code;
    uint8_t  x87_out[108];
    uint8_t  pad1[4];
    uint32_t mxcsr_out;
};

__attribute__((noinline)) void run_native(Native *n) {
    __asm__ volatile(
        "push %%rbx\n\t push %%rbp\n\t push %%r12\n\t push %%r13\n\t push %%r14\n\t push %%r15\n\t"
        "mov %%rdi, %%r15\n\t"
        "mov %%rsp, %c[saved](%%r15)\n\t"
        "lea 9f(%%rip), %%rax\n\t"
        "mov %%rax, %c[ret](%%r15)\n\t"
        "cmpl $1, %c[mode](%%r15)\n\t"
        "je 2f\n\t"
        "frstor %c[x87](%%r15)\n\t"
        "movq %c[mm]+0(%%r15), %%mm0\n\t  movq %c[mm]+8(%%r15), %%mm1\n\t"
        "movq %c[mm]+16(%%r15), %%mm2\n\t movq %c[mm]+24(%%r15), %%mm3\n\t"
        "movq %c[mm]+32(%%r15), %%mm4\n\t movq %c[mm]+40(%%r15), %%mm5\n\t"
        "movq %c[mm]+48(%%r15), %%mm6\n\t movq %c[mm]+56(%%r15), %%mm7\n\t"
        "jmp 3f\n"
        "2:\n\t"
        "frstor %c[x87](%%r15)\n"
        "3:\n\t"
        "ldmxcsr %c[mxcsr](%%r15)\n\t"
        "movdqu %c[xmm]+0(%%r15), %%xmm0\n\t   movdqu %c[xmm]+16(%%r15), %%xmm1\n\t"
        "movdqu %c[xmm]+32(%%r15), %%xmm2\n\t  movdqu %c[xmm]+48(%%r15), %%xmm3\n\t"
        "movdqu %c[xmm]+64(%%r15), %%xmm4\n\t  movdqu %c[xmm]+80(%%r15), %%xmm5\n\t"
        "movdqu %c[xmm]+96(%%r15), %%xmm6\n\t  movdqu %c[xmm]+112(%%r15), %%xmm7\n\t"
        "mov %c[gpr]+0(%%r15), %%rax\n\t  mov %c[gpr]+8(%%r15), %%rcx\n\t"
        "mov %c[gpr]+16(%%r15), %%rdx\n\t mov %c[gpr]+24(%%r15), %%rbx\n\t"
        "mov %c[gpr]+40(%%r15), %%rbp\n\t mov %c[gpr]+48(%%r15), %%rsi\n\t"
        "mov %c[gpr]+56(%%r15), %%rdi\n\t"
        "mov %c[gpr]+32(%%r15), %%rsp\n\t"
        "jmp *%c[code](%%r15)\n"
        "9:\n\t"
        "mov %%rax, %c[gpr]+0(%%r15)\n\t  mov %%rcx, %c[gpr]+8(%%r15)\n\t"
        "mov %%rdx, %c[gpr]+16(%%r15)\n\t mov %%rbx, %c[gpr]+24(%%r15)\n\t"
        "mov %%rsp, %c[gpr]+32(%%r15)\n\t mov %%rbp, %c[gpr]+40(%%r15)\n\t"
        "mov %%rsi, %c[gpr]+48(%%r15)\n\t mov %%rdi, %c[gpr]+56(%%r15)\n\t"
        "mov %c[saved](%%r15), %%rsp\n\t"
        /* The x87 state first: an MMX instruction resets TOP, and fnsave does
         * not change register contents, so the MMX values survive it. */
        "fnsave %c[x87out](%%r15)\n\t"
        "movq %%mm0, %c[mm]+0(%%r15)\n\t  movq %%mm1, %c[mm]+8(%%r15)\n\t"
        "movq %%mm2, %c[mm]+16(%%r15)\n\t movq %%mm3, %c[mm]+24(%%r15)\n\t"
        "movq %%mm4, %c[mm]+32(%%r15)\n\t movq %%mm5, %c[mm]+40(%%r15)\n\t"
        "movq %%mm6, %c[mm]+48(%%r15)\n\t movq %%mm7, %c[mm]+56(%%r15)\n\t"
        "movdqu %%xmm0, %c[xmm]+0(%%r15)\n\t   movdqu %%xmm1, %c[xmm]+16(%%r15)\n\t"
        "movdqu %%xmm2, %c[xmm]+32(%%r15)\n\t  movdqu %%xmm3, %c[xmm]+48(%%r15)\n\t"
        "movdqu %%xmm4, %c[xmm]+64(%%r15)\n\t  movdqu %%xmm5, %c[xmm]+80(%%r15)\n\t"
        "movdqu %%xmm6, %c[xmm]+96(%%r15)\n\t  movdqu %%xmm7, %c[xmm]+112(%%r15)\n\t"
        "stmxcsr %c[mxcsrout](%%r15)\n\t"
        "emms\n\t"
        "pop %%r15\n\t pop %%r14\n\t pop %%r13\n\t pop %%r12\n\t pop %%rbp\n\t pop %%rbx\n\t"
        :
        : "D"(n), [saved] "i"(offsetof(Native, saved_rsp)), [ret] "i"(offsetof(Native, ret)),
          [mode] "i"(offsetof(Native, mode)), [x87] "i"(offsetof(Native, x87)),
          [mm] "i"(offsetof(Native, mm)), [mxcsr] "i"(offsetof(Native, mxcsr)),
          [xmm] "i"(offsetof(Native, xmm)), [gpr] "i"(offsetof(Native, gpr)),
          [code] "i"(offsetof(Native, code)), [x87out] "i"(offsetof(Native, x87_out)),
          [mxcsrout] "i"(offsetof(Native, mxcsr_out))
        : "rax", "rcx", "rdx", "rsi", "memory", "cc", "xmm0", "xmm1", "xmm2", "xmm3", "xmm4",
          "xmm5", "xmm6", "xmm7");
}

/* ------------------------------------------------------------ guest memory */

bool in_window(uint32_t a, uint32_t n) {
    return a >= kWin && a - kWin <= kWinSize && kWinSize - (a - kWin) >= n;
}

bool in_aux(uint32_t a, uint32_t n) {
    return a >= kAux && a - kAux <= kAuxSize && kAuxSize - (a - kAux) >= n;
}

uint8_t *guest_ptr(uint32_t a, uint32_t n) {
    if (in_window(a, n)) return g_win + (a - kWin);
    if (in_aux(a, n)) return g_aux + (a - kAux);
    return nullptr;
}

}  // namespace

extern "C" int sa2_peek(uint32_t a, uint32_t n, void *o) {
    uint8_t *p = guest_ptr(a, n);
    if (!p) return SA2_E_RANGE;
    std::memcpy(o, p, n);
    return SA2_OK;
}

extern "C" int sa2_poke(uint32_t a, uint32_t n, const void *i) {
    uint8_t *p = guest_ptr(a, n);
    if (!p) return SA2_E_RANGE;
    std::memcpy(p, i, n);
    return SA2_OK;
}

namespace {

std::vector<uint8_t> unhex(const std::string &h) {
    std::vector<uint8_t> out;
    for (size_t i = 0; i + 1 < h.size(); i += 2) out.push_back(static_cast<uint8_t>(std::stoul(h.substr(i, 2), nullptr, 16)));
    return out;
}

std::string hex(const void *p, size_t n) {
    static const char *d = "0123456789abcdef";
    std::string s;
    for (size_t i = 0; i < n; i++) {
        const uint8_t b = static_cast<const uint8_t *>(p)[i];
        s += d[b >> 4];
        s += d[b & 15];
    }
    return s;
}

void fill_window(const std::string &pokes, const std::vector<uint8_t> &insn) {
    for (uint32_t i = 0; i < kWinSize; i++) g_win[i] = static_cast<uint8_t>((i * 131u + (i >> 9)) & 0xFFu);
    for (uint32_t i = 0; i < kAuxSize; i++) g_aux[i] = static_cast<uint8_t>((i * 29u + 7u) & 0xFFu);
    size_t at = 0;
    while (at < pokes.size()) {
        size_t comma = pokes.find(',', at);
        std::string item = pokes.substr(at, comma == std::string::npos ? std::string::npos : comma - at);
        size_t colon = item.find(':');
        if (colon != std::string::npos) {
            const uint32_t a = static_cast<uint32_t>(std::stoul(item.substr(0, colon), nullptr, 16));
            std::vector<uint8_t> b = unhex(item.substr(colon + 1));
            if (uint8_t *p = guest_ptr(a, static_cast<uint32_t>(b.size()))) std::memcpy(p, b.data(), b.size());
        }
        if (comma == std::string::npos) break;
        at = comma + 1;
    }
    std::memcpy(g_win + (kCode - kWin), insn.data(), insn.size());
}

std::string changes(const std::vector<uint8_t> &before) {
    std::string s;
    uint32_t i = 0;
    while (i < kWinSize) {
        if (g_win[i] == before[i]) { i++; continue; }
        uint32_t j = i;
        while (j < kWinSize && g_win[j] != before[j]) j++;
        char head[16];
        std::snprintf(head, sizeof head, "%08x:", kWin + i);
        s += (s.empty() ? "" : ",") + std::string(head) + hex(g_win + i, j - i);
        i = j;
    }
    return s.empty() ? "-" : s;
}

int rc_mode(uint16_t cw) {
    switch (cw & 0x0C00u) {
    case 0x0000u: return FE_TONEAREST;
    case 0x0400u: return FE_DOWNWARD;
    case 0x0800u: return FE_UPWARD;
    default:      return FE_TOWARDZERO;
    }
}

/* Where lifted code keeps the x87 environment: byte offsets into the FPU
 * union, read from the lifted IR by tools/s4/emulate_check.py and passed in
 * SA2_FX_OFFSETS. The model is written and read through them, not through the
 * emulator's field names, so a layout the emulator gets wrong is a layout this
 * disagrees with -- the first version of both used the FSAVE view and agreed. */
struct Fx { uint32_t cwd, swd, ftw, fop, ip, cs, dp, ds, mxcsr; };
Fx g_fx;
bool g_fx_ok;

bool parse_fx(const char *v) {
    if (!v) return false;
    int got = std::sscanf(v, "cwd=%u,swd=%u,ftw=%u,fop=%u,ip=%u,cs=%u,dp=%u,ds=%u,mxcsr=%u",
                          &g_fx.cwd, &g_fx.swd, &g_fx.ftw, &g_fx.fop, &g_fx.ip, &g_fx.cs,
                          &g_fx.dp, &g_fx.ds, &g_fx.mxcsr);
    return got == 9;
}

uint8_t *fxb(State *s) { return reinterpret_cast<uint8_t *>(&s->x87); }
template <typename T> void put(State *s, uint32_t off, T v) { std::memcpy(fxb(s) + off, &v, sizeof v); }
template <typename T> T get(State *s, uint32_t off) { T v; std::memcpy(&v, fxb(s) + off, sizeof v); return v; }

/* remill's model of the x87 state from an FSAVE image, as lifted code leaves
 * it: the condition codes and exception flags live in `sw` and the copies of
 * them in the status word are stale, and the rounding the host is using is
 * the real one while the control word's copy of it is stale. Seeding the
 * stale copies wrong makes an emulator that reads them wrong fail. */
void model_from_image(State *s, const uint8_t img[108], uint32_t mxcsr) {
    uint16_t cw, sw, tw, cs, op, ds;
    uint32_t ip, dp;
    std::memcpy(&cw, img + 0, 2);
    std::memcpy(&sw, img + 4, 2);
    std::memcpy(&tw, img + 8, 2);
    std::memcpy(&ip, img + 12, 4);
    std::memcpy(&cs, img + 16, 2);
    std::memcpy(&op, img + 18, 2);
    std::memcpy(&dp, img + 20, 4);
    std::memcpy(&ds, img + 24, 2);
    uint8_t ab = 0;
    for (int k = 0; k < 8; k++)
        if (((tw >> (2 * k)) & 3u) != 3u) ab = static_cast<uint8_t>(ab | (1u << k));
    /* Stale RC, and bit 6 clear: lifted code holds the word as loaded (the CRT's
     * `fldcw 0x023F`), and the hardware reads bit 6 as 1 whatever was loaded, so
     * an emulator that reports the stored bit fails here. */
    put<uint16_t>(s, g_fx.cwd, static_cast<uint16_t>((cw ^ 0x0C00u) & ~0x0040u));
    put<uint16_t>(s, g_fx.swd, static_cast<uint16_t>(sw ^ 0x473Fu));            /* stale flags */
    put<uint8_t>(s, g_fx.ftw, ab);
    put<uint16_t>(s, g_fx.fop, static_cast<uint16_t>(op & 0x07FFu));
    put<uint32_t>(s, g_fx.ip, ip);
    put<uint16_t>(s, g_fx.cs, cs);
    put<uint32_t>(s, g_fx.dp, dp);
    put<uint16_t>(s, g_fx.ds, ds);
    put<uint32_t>(s, g_fx.mxcsr, mxcsr);
    FPUStatusFlags &f = s->sw;
    f.c0 = (sw >> 8) & 1u; f.c1 = (sw >> 9) & 1u; f.c2 = (sw >> 10) & 1u; f.c3 = (sw >> 14) & 1u;
    f.pe = (sw >> 5) & 1u; f.ue = (sw >> 4) & 1u; f.oe = (sw >> 3) & 1u; f.ze = (sw >> 2) & 1u;
    f.de = (sw >> 1) & 1u; f.ie = sw & 1u;
    for (int k = 0; k < 8; k++) std::memcpy(&s->st.elems[k].val, img + 28 + 10 * k, 10);
    std::fesetround(rc_mode(cw));
}

/* The model read back as an FSAVE image, the way the lifted FNSTCW and FNSTSW
 * read it; the tag word from the abridged byte (not compared: the hardware
 * derives its own). */
void image_from_model(State *s, uint8_t img[108]) {
    const FPUStatusFlags &f = s->sw;
    const int mode = std::fegetround();
    const uint16_t rc = mode == FE_DOWNWARD ? 0x0400u : mode == FE_UPWARD ? 0x0800u
                      : mode == FE_TOWARDZERO ? 0x0C00u : 0x0000u;
    /* As the lifted FNSTCW reports it after tools/spike/pc24pass: bit 6 set. */
    const uint16_t cw = static_cast<uint16_t>((get<uint16_t>(s, g_fx.cwd) & 0xF3FFu) | 0x0040u | rc);
    const uint16_t sw = static_cast<uint16_t>((get<uint16_t>(s, g_fx.swd) & 0xB8C0u)
        | ((f.c0 & 1u) << 8) | ((f.c1 & 1u) << 9) | ((f.c2 & 1u) << 10) | ((f.c3 & 1u) << 14)
        | ((f.pe & 1u) << 5) | ((f.ue & 1u) << 4) | ((f.oe & 1u) << 3) | ((f.ze & 1u) << 2)
        | ((f.de & 1u) << 1) | (f.ie & 1u));
    const uint8_t ab = get<uint8_t>(s, g_fx.ftw);
    uint16_t tw = 0;
    for (int k = 0; k < 8; k++)
        if (!((ab >> k) & 1u)) tw = static_cast<uint16_t>(tw | (3u << (2 * k)));
    const uint16_t ones = 0xFFFFu, cs = get<uint16_t>(s, g_fx.cs), ds = get<uint16_t>(s, g_fx.ds);
    const uint16_t op = static_cast<uint16_t>(get<uint16_t>(s, g_fx.fop) & 0x07FFu);
    const uint32_t ip = get<uint32_t>(s, g_fx.ip), dp = get<uint32_t>(s, g_fx.dp);
    std::memcpy(img + 0, &cw, 2);  std::memcpy(img + 2, &ones, 2);
    std::memcpy(img + 4, &sw, 2);  std::memcpy(img + 6, &ones, 2);
    std::memcpy(img + 8, &tw, 2);  std::memcpy(img + 10, &ones, 2);
    std::memcpy(img + 12, &ip, 4); std::memcpy(img + 16, &cs, 2); std::memcpy(img + 18, &op, 2);
    std::memcpy(img + 20, &dp, 4); std::memcpy(img + 24, &ds, 2); std::memcpy(img + 26, &ones, 2);
    for (int k = 0; k < 8; k++) std::memcpy(img + 28 + 10 * k, &s->st.elems[k].val, 10);
}

/* The emulated side: a State from the context, sa2_emulate, the state read
 * back. */
std::string emulated(const Ctx &c, const std::vector<uint8_t> &before) {
    State *s = static_cast<State *>(std::aligned_alloc(64, (sizeof(State) + 63) & ~size_t(63)));
    std::memset(s, 0, sizeof(State));
    Reg *r[8] = {&s->gpr.rax, &s->gpr.rcx, &s->gpr.rdx, &s->gpr.rbx,
                 &s->gpr.rsp, &s->gpr.rbp, &s->gpr.rsi, &s->gpr.rdi};
    for (int k = 0; k < 8; k++) r[k]->dword = c.gpr[k];
    for (int k = 0; k < 8; k++) std::memcpy(&s->mmx.elems[k].val, &c.mm[k], 8);
    for (int k = 0; k < 8; k++) std::memcpy(&s->vec[k].xmm, c.xmm[k], 16);
    /* The game's FS and GS are not zero, so neither is the model's: a prefix
     * that picks the wrong base misses. */
    s->addr.fs_base.dword = kWin + 0x00020000u;
    s->addr.gs_base.dword = kWin + 0x00030000u;
    model_from_image(s, c.x87, c.mxcsr);
    _mm_setcsr(c.mxcsr);
    s->gpr.rip.dword = kCode;
    uint32_t next = 0, fault = 0;
    char why[200];
    const int st = sa2_emulate(s, &next, &fault, why, sizeof why);
    std::string out = "emu " + std::to_string(st) + " ";
    char nf[16];
    std::snprintf(nf, sizeof nf, "%08x", st == SA2_EMU_OK ? next : fault);
    out += nf;
    Ctx after = c;
    for (int k = 0; k < 8; k++) after.gpr[k] = r[k]->dword;
    for (int k = 0; k < 8; k++) std::memcpy(&after.mm[k], &s->mmx.elems[k].val, 8);
    for (int k = 0; k < 8; k++) std::memcpy(after.xmm[k], &s->vec[k].xmm, 16);
    image_from_model(s, after.x87);
    /* In the model, MXCSR is the FXSAVE field lifted code's LDMXCSR writes; no
     * emulated instruction may change it. */
    after.mxcsr = get<uint32_t>(s, g_fx.mxcsr);
    std::free(s);
    out += " " + hex(&after, sizeof after) + " " + changes(before);
    if (st != SA2_EMU_OK) {
        for (char *q = why; *q; q++) if (*q == ' ') *q = '_';
        out += " why=" + std::string(why);
    }
    return out;
}

std::string native(const Ctx &c, const std::vector<uint8_t> &insn, int mode, uint8_t *code) {
    Native n{};
    for (int k = 0; k < 8; k++) n.gpr[k] = c.gpr[k];
    std::memcpy(n.mm, c.mm, sizeof n.mm);
    std::memcpy(n.xmm, c.xmm, sizeof n.xmm);
    std::memcpy(n.x87, c.x87, 108);
    n.mxcsr = c.mxcsr;
    n.mode = static_cast<uint32_t>(mode);
    std::memcpy(code, insn.data(), insn.size());
    const int32_t disp = static_cast<int32_t>(offsetof(Native, ret));
    uint8_t *p = code + insn.size();
    *p++ = 0x41; *p++ = 0xFF; *p++ = 0xA7;          /* jmp qword [r15 + disp32] */
    std::memcpy(p, &disp, 4);
    n.code = reinterpret_cast<uint64_t>(code);
    const std::vector<uint8_t> before(g_win, g_win + kWinSize);
    run_native(&n);
    Ctx after = c;
    for (int k = 0; k < 8; k++) after.gpr[k] = static_cast<uint32_t>(n.gpr[k]);
    std::memcpy(after.mm, n.mm, sizeof n.mm);
    std::memcpy(after.xmm, n.xmm, sizeof n.xmm);
    std::memcpy(after.x87, n.x87_out, 108);
    after.mxcsr = n.mxcsr_out;
    return "native " + hex(&after, sizeof after) + " " + changes(before);
}

}  // namespace

int main() {
    void *w = mmap(reinterpret_cast<void *>(static_cast<uintptr_t>(kWin)), kWinSize,
                   PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (w == MAP_FAILED || w != reinterpret_cast<void *>(static_cast<uintptr_t>(kWin))) {
        std::fprintf(stderr, "cannot map the guest window at %#x\n", kWin);
        return 2;
    }
    g_win = static_cast<uint8_t *>(w);
    g_fx_ok = parse_fx(std::getenv("SA2_FX_OFFSETS"));
    if (!g_fx_ok) {
        std::fprintf(stderr, "SA2_FX_OFFSETS is missing or malformed; tools/s4/emulate_check.py "
                             "reads it from the lifted IR\n");
        return 2;
    }
    void *x = mmap(reinterpret_cast<void *>(static_cast<uintptr_t>(kAux)), kAuxSize,
                   PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    if (x == MAP_FAILED || x != reinterpret_cast<void *>(static_cast<uintptr_t>(kAux))) {
        std::fprintf(stderr, "cannot map the image-address region at %#x\n", kAux);
        return 2;
    }
    g_aux = static_cast<uint8_t *>(x);
    uint8_t *code = static_cast<uint8_t *>(mmap(nullptr, 4096, PROT_READ | PROT_WRITE | PROT_EXEC,
                                                MAP_PRIVATE | MAP_ANONYMOUS, -1, 0));
    if (code == MAP_FAILED) {
        std::fprintf(stderr, "cannot map an executable page\n");
        return 2;
    }
    char line[8192];
    while (std::fgets(line, sizeof line, stdin)) {
        char mode[4] = {0}, ih[64] = {0}, nh[64] = {0}, ch[1024] = {0}, pk[4096] = {0};
        const int got = std::sscanf(line, "%3s %63s %63s %1023s %4095s", mode, ih, nh, ch, pk);
        if (got < 4) continue;
        const std::vector<uint8_t> insn = unhex(ih), cb = unhex(ch);
        const std::vector<uint8_t> host = std::strcmp(nh, "-") ? unhex(nh) : insn;
        if (cb.size() != sizeof(Ctx) || insn.empty() || insn.size() > 15) {
            std::printf("bad case\n");
            continue;
        }
        Ctx c;
        std::memcpy(&c, cb.data(), sizeof c);
        const std::string pokes = got >= 5 && std::strcmp(pk, "-") ? pk : "";
        const int m = mode[0] == 'x' ? 1 : 0;
        /* A refusal, or a case only the emulator can run (an FS or GS prefix,
         * which the 64-bit host resolves through its own TLS), is not run
         * natively. */
        std::string nat = "native -";
        if (mode[0] != 'r' && mode[0] != 'e') {
            fill_window(pokes, insn);
            nat = native(c, host, m, code);
        }
        fill_window(pokes, insn);
        const std::vector<uint8_t> before(g_win, g_win + kWinSize);
        const std::string emu = emulated(c, before);
        std::fesetround(FE_TONEAREST);
        _mm_setcsr(0x1F80u);
        std::printf("%s | %s\n", nat.c_str(), emu.c_str());
        std::fflush(stdout);
    }
    return 0;
}
