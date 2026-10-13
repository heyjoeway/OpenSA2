/* The instructions remill cannot lift, executed by the runtime. See sa2_emulate.h. */
#include "sa2_emulate.h"

#include <cfenv>
#include <cstdarg>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "remill/Arch/X86/Runtime/State.h"

extern "C" {
#include "guest_memory.h"
#include "remill_runtime.h"     /* sa2_rt_get_st80 / sa2_rt_set_st80: the register's x87 bytes */
}
/* Weak, as remill_runtime.cpp takes sa2_emulate: tools/s4/emulate_check.py's probe links this
 * file without the remill runtime, and there a register slot is the x87's ten bytes. In the
 * game they are remill_runtime.cpp's, which convert in tools/s8/f80_double.py's double mode. */
#pragma weak sa2_rt_get_st80
#pragma weak sa2_rt_set_st80

namespace {

enum Kind : uint32_t { PADDUSB, PADDUSW, CVTPS2PI, FNSTENV, FLDENV, FNSAVE, FRSTOR, PSUBSW,
                       PSUBSW_XMM, KINDS };
const char *const kKindName[KINDS] = {"paddusb", "paddusw", "cvtps2pi", "fnstenv", "fldenv",
                                      "fnsave", "frstor", "psubsw", "psubsw xmm"};
uint64_t g_count[KINDS];

struct Decoded {
    Kind     kind;
    uint32_t len;
    bool     mem;       /* the r/m operand is memory */
    uint32_t reg;       /* ModRM.reg: the destination register */
    uint32_t rm;        /* ModRM.rm, when a register */
    uint32_t ea;        /* the effective address, segment base included, when memory */
};

struct Ctx {
    State   *s;
    uint32_t fault;
    char    *why;
    size_t   cap;
};

void say(Ctx &c, const char *fmt, ...) {
    if (!c.why || !c.cap) return;
    va_list ap;
    va_start(ap, fmt);
    std::vsnprintf(c.why, c.cap, fmt, ap);
    va_end(ap);
}

uint32_t gpr(const State *s, uint32_t i) {
    switch (i & 7u) {
    case 0: return s->gpr.rax.dword;
    case 1: return s->gpr.rcx.dword;
    case 2: return s->gpr.rdx.dword;
    case 3: return s->gpr.rbx.dword;
    case 4: return s->gpr.rsp.dword;
    case 5: return s->gpr.rbp.dword;
    case 6: return s->gpr.rsi.dword;
    default: return s->gpr.rdi.dword;
    }
}

uint32_t seg_base(const State *s, uint8_t seg) {
    switch (seg) {
    case 0x26: return s->addr.es_base.dword;
    case 0x2E: return s->addr.cs_base.dword;
    case 0x36: return s->addr.ss_base.dword;
    case 0x64: return s->addr.fs_base.dword;
    case 0x65: return s->addr.gs_base.dword;
    default:   return s->addr.ds_base.dword;
    }
}

/* The first byte of [va, va + n) that is not guest memory, which is the
 * address the hardware's fault names: a probe by peeking, which changes
 * nothing. */
uint32_t first_unmapped(uint32_t va, uint32_t n) {
    uint8_t b;
    for (uint32_t i = 0; i < n; i++)
        if (sa2_peek(va + i, 1, &b) != SA2_OK) return va + i;
    return va;
}

bool rd(Ctx &c, uint32_t va, uint32_t n, void *dst) {
    if (sa2_peek(va, n, dst) == SA2_OK) return true;
    c.fault = first_unmapped(va, n);
    return false;
}

/* All or nothing, as the host CPU stores an operand that crosses into an
 * unmapped page: nothing is written, and the fault names the first byte that
 * is not there. */
bool wr(Ctx &c, uint32_t va, uint32_t n, const void *src) {
    if (sa2_poke(va, n, src) == SA2_OK) return true;
    c.fault = first_unmapped(va, n);
    return false;
}

/* Decode the instruction at `pc`: prefixes, the opcode, ModRM, SIB and
 * displacement, 32-bit addressing. Only the instructions sa2_emulate.h lists
 * are accepted; anything else is named and refused. */
int decode(Ctx &c, uint32_t pc, Decoded &d) {
    uint8_t b[15];
    uint32_t have = 0;
    while (have < sizeof b && sa2_peek(pc + have, 1, &b[have]) == SA2_OK) have++;
    if (!have) { c.fault = pc; return SA2_EMU_FAULT; }
    uint32_t i = 0;
    bool opsize = false;
    uint8_t seg = 0;
    for (; i < have; i++) {
        const uint8_t p = b[i];
        if (p == 0x66) opsize = true;
        else if (p == 0x26 || p == 0x2E || p == 0x36 || p == 0x3E || p == 0x64 || p == 0x65) seg = p;
        else if (p == 0x67 || p == 0xF0 || p == 0xF2 || p == 0xF3) {
            say(c, "prefix %02x on the instruction at %#010x, which no emulated form takes", p, pc);
            return SA2_EMU_UNSUPPORTED;
        } else break;
    }
    auto need = [&](uint32_t n) { return i + n <= have; };
    if (!need(2)) { c.fault = pc + have; return SA2_EMU_FAULT; }
    bool x87 = false;
    if (b[i] == 0x0F) {
        switch (b[i + 1]) {
        case 0xDC: d.kind = PADDUSB; break;
        case 0xDD: d.kind = PADDUSW; break;
        case 0x2D: d.kind = CVTPS2PI; break;
        case 0xE9: d.kind = opsize ? PSUBSW_XMM : PSUBSW; break;
        default:
            say(c, "0F %02x at %#010x is not one of the emulated instructions", b[i + 1], pc);
            return SA2_EMU_UNSUPPORTED;
        }
        if (opsize && d.kind != PSUBSW_XMM) {
            /* 66 makes these the SSE2 xmm forms (and 0F 2D cvtpd2pi), which
             * remill lifts itself: S3 found only the MMX forms refused. */
            say(c, "66 0F %02x at %#010x: the xmm form, which remill lifts; not emulated",
                b[i + 1], pc);
            return SA2_EMU_UNSUPPORTED;
        }
        i += 2;
    } else if (b[i] == 0xD9 || b[i] == 0xDD) {
        const uint8_t op = b[i], m = b[i + 1];
        const uint32_t r = (m >> 3) & 7u;
        if ((m >> 6) == 3 || (r != 4 && r != 6)) {
            say(c, "%02x %02x at %#010x is not one of the emulated instructions", op, m, pc);
            return SA2_EMU_UNSUPPORTED;
        }
        if (opsize) {
            say(c, "66 %02x /%u at %#010x: the 16-bit environment layout is not emulated", op, r, pc);
            return SA2_EMU_UNSUPPORTED;
        }
        d.kind = op == 0xD9 ? (r == 6 ? FNSTENV : FLDENV) : (r == 6 ? FNSAVE : FRSTOR);
        x87 = true;
        i += 1;
    } else {
        say(c, "opcode %02x at %#010x is not one of the emulated instructions", b[i], pc);
        return SA2_EMU_UNSUPPORTED;
    }
    if (!need(1)) { c.fault = pc + have; return SA2_EMU_FAULT; }
    const uint8_t modrm = b[i++];
    const uint32_t mod = modrm >> 6;
    d.reg = (modrm >> 3) & 7u;
    d.rm = modrm & 7u;
    d.mem = mod != 3;
    d.ea = 0;
    if (d.mem) {
        bool stack = false;
        uint32_t ea = 0;
        if (d.rm == 4) {
            if (!need(1)) { c.fault = pc + have; return SA2_EMU_FAULT; }
            const uint8_t sib = b[i++];
            const uint32_t scale = sib >> 6, index = (sib >> 3) & 7u, base = sib & 7u;
            if (index != 4) ea += gpr(c.s, index) << scale;
            if (base == 5 && mod == 0) {
                if (!need(4)) { c.fault = pc + have; return SA2_EMU_FAULT; }
                uint32_t disp;
                std::memcpy(&disp, &b[i], 4);
                i += 4;
                ea += disp;
            } else {
                ea += gpr(c.s, base);
                stack = base == 4 || base == 5;
            }
        } else if (d.rm == 5 && mod == 0) {
            if (!need(4)) { c.fault = pc + have; return SA2_EMU_FAULT; }
            uint32_t disp;
            std::memcpy(&disp, &b[i], 4);
            i += 4;
            ea = disp;
        } else {
            ea = gpr(c.s, d.rm);
            stack = d.rm == 5;
        }
        if (mod == 1) {
            if (!need(1)) { c.fault = pc + have; return SA2_EMU_FAULT; }
            ea += static_cast<uint32_t>(static_cast<int32_t>(static_cast<int8_t>(b[i++])));
        } else if (mod == 2) {
            if (!need(4)) { c.fault = pc + have; return SA2_EMU_FAULT; }
            uint32_t disp;
            std::memcpy(&disp, &b[i], 4);
            i += 4;
            ea += disp;
        }
        d.ea = ea + seg_base(c.s, seg ? seg : (stack ? 0x36 : 0x3E));
    } else if (x87) {
        say(c, "x87 environment instruction with a register operand at %#010x", pc);
        return SA2_EMU_UNSUPPORTED;
    }
    d.len = i;
    return SA2_EMU_OK;
}

/* ------------------------------------------------------- MMX and SSE2 forms */

uint8_t *mm(State *s, uint32_t r) { return reinterpret_cast<uint8_t *>(&s->mmx.elems[r & 7u].val); }
uint8_t *xmm(State *s, uint32_t r) { return reinterpret_cast<uint8_t *>(&s->vec[r & 7u].xmm); }

int saturating_add(Ctx &c, const Decoded &d) {
    const uint32_t n = 8u;
    uint8_t src[8], *dst = mm(c.s, d.reg);
    if (d.mem) {
        if (!rd(c, d.ea, n, src)) return SA2_EMU_FAULT;
    } else {
        std::memcpy(src, mm(c.s, d.rm), n);
    }
    if (d.kind == PADDUSB) {
        for (uint32_t k = 0; k < n; k++) {
            const uint32_t v = static_cast<uint32_t>(dst[k]) + src[k];
            dst[k] = static_cast<uint8_t>(v > 0xFFu ? 0xFFu : v);
        }
    } else {
        for (uint32_t k = 0; k < n; k += 2) {
            uint16_t a, b;
            std::memcpy(&a, dst + k, 2);
            std::memcpy(&b, src + k, 2);
            const uint32_t v = static_cast<uint32_t>(a) + b;
            const uint16_t r = static_cast<uint16_t>(v > 0xFFFFu ? 0xFFFFu : v);
            std::memcpy(dst + k, &r, 2);
        }
    }
    return SA2_EMU_OK;
}

/* psubsw, both forms: eight or four signed words subtracted with signed
 * saturation. Portable C, so the runtime builds where there is no SSE2: until
 * S7 this was the host's own instruction (`_mm_subs_epi16`), and the two are
 * compared over every pair of words (harness/emulate_exhaustive_probe.cpp, run
 * by Documentation/scripts/tests/test_runtime_port.py). The xmm form's
 * m128 must be 16-byte aligned; the hardware raises #GP otherwise, and that is
 * refused by name rather than executed. */
int16_t subs_word(int16_t a, int16_t b) {
    const int32_t v = static_cast<int32_t>(a) - static_cast<int32_t>(b);
    return static_cast<int16_t>(v > 32767 ? 32767 : v < -32768 ? -32768 : v);
}

int saturating_sub_words(Ctx &c, const Decoded &d) {
    const bool x = d.kind == PSUBSW_XMM;
    const uint32_t n = x ? 16u : 8u;
    uint8_t a[16] = {}, b[16] = {};
    uint8_t *dst = x ? xmm(c.s, d.reg) : mm(c.s, d.reg);
    if (d.mem) {
        if (x && (d.ea & 15u)) {
            say(c, "psubsw xmm at %#010x: its m128 operand %#010x is not 16-byte aligned, "
                   "where the hardware raises #GP", c.s->gpr.rip.dword, d.ea);
            return SA2_EMU_UNSUPPORTED;
        }
        if (!rd(c, d.ea, n, b)) return SA2_EMU_FAULT;
    } else {
        std::memcpy(b, x ? xmm(c.s, d.rm) : mm(c.s, d.rm), n);
    }
    std::memcpy(a, dst, n);
    for (uint32_t k = 0; k < n; k += 2) {
        int16_t u, v;
        std::memcpy(&u, a + k, 2);
        std::memcpy(&v, b + k, 2);
        const int16_t r = subs_word(u, v);
        std::memcpy(dst + k, &r, 2);
    }
    return SA2_EMU_OK;
}

/* cvtps2pi's conversion of one single: rounded to an integer by the rounding
 * control `rc` (x87 encoding: 0 nearest even, 1 down, 2 up, 3 toward zero), and
 * the integer indefinite 0x80000000 for a NaN, an infinity, or a result outside
 * int32 -- the masked invalid response. Integers alone, so it does not depend on
 * the host having SSE or on its libm; until S7 this was the host's own
 * instruction (`_mm_cvtps_epi32`), and the two are compared over every single
 * in every rounding mode (harness/emulate_exhaustive_probe.cpp, run by
 * Documentation/scripts/tests/test_runtime_port.py). */
uint32_t cvt_single(uint32_t bits, int rc) {
    const uint32_t indefinite = 0x80000000u;
    const bool neg = (bits >> 31) != 0;
    const int e = static_cast<int>((bits >> 23) & 0xFFu);
    const uint32_t frac = bits & 0x7FFFFFu;
    if (e == 0xFF) return indefinite;
    if (e == 0 && frac == 0) return 0u;
    /* |value| = sig * 2^(exp2), sig < 2^24 */
    const uint64_t sig = e ? (frac | 0x800000u) : frac;
    const int exp2 = (e ? e : 1) - 150;
    uint64_t q;
    if (exp2 >= 0) {
        if (exp2 > 8) return indefinite;        /* |value| >= 2^32 */
        q = sig << exp2;
    } else {
        const int sh = -exp2;                   /* 1 .. 149 */
        uint64_t rbits, half;
        bool sticky = false;
        if (sh < 64) {
            q = sig >> sh;
            rbits = sig & ((1ull << sh) - 1);
            half = 1ull << (sh - 1);
        } else {                                /* sig < 2^24, so below half of one */
            q = 0;
            rbits = 0;
            half = 1;
            sticky = true;
        }
        const bool inexact = rbits || sticky;
        bool away;
        switch (rc & 3) {
        case 0:  away = rbits > half || (rbits == half && (q & 1)); break;
        case 1:  away = neg && inexact; break;
        case 2:  away = !neg && inexact; break;
        default: away = false; break;
        }
        if (away) q++;
    }
    if (neg ? q > 0x80000000ull : q > 0x7FFFFFFFull) return indefinite;
    return neg ? static_cast<uint32_t>(0u - static_cast<uint32_t>(q)) : static_cast<uint32_t>(q);
}

uint16_t rc_bits(int mode);

/* The rounding is the host's current mode, which is where remill's FLDCW leaves
 * the guest's, and is the mode `_mm_cvtps_epi32` used: on x86-64 musl's
 * fegetround reads it from MXCSR. */
int cvtps2pi(Ctx &c, const Decoded &d) {
    uint32_t f[2] = {0, 0};
    if (d.mem) {
        if (!rd(c, d.ea, 8, f)) return SA2_EMU_FAULT;
    } else {
        std::memcpy(f, xmm(c.s, d.rm), 8);
    }
    const int rc = rc_bits(std::fegetround()) >> 10;
    const uint32_t out[2] = {cvt_single(f[0], rc), cvt_single(f[1], rc)};
    std::memcpy(mm(c.s, d.reg), out, 8);
    return SA2_EMU_OK;
}

/* -------------------------------------------------------- x87 environment */

/* The rounding-control bits of a control word, and the host mode they name;
 * the same mapping as sa2_rt_round_from_cw in remill_runtime.cpp. */
int round_from_cw(uint16_t cw) {
    switch (cw & 0x0C00u) {
    case 0x0000u: return FE_TONEAREST;
    case 0x0400u: return FE_DOWNWARD;
    case 0x0800u: return FE_UPWARD;
    default:      return FE_TOWARDZERO;
    }
}

uint16_t rc_bits(int mode) {
    switch (mode) {
    case FE_DOWNWARD:   return 0x0400u;
    case FE_UPWARD:     return 0x0800u;
    case FE_TOWARDZERO: return 0x0C00u;
    default:            return 0x0000u;
    }
}

/* remill's lifted code keeps the x87 environment in the FXSAVE view of the
 * FPU union: the IR types the union's storage as FpuFXSAVE, its FNSTSW and
 * every FLD and FSTP reach the status word at +2, and its LDMXCSR stores MXCSR
 * at +24. The FSAVE view of the same bytes puts the status word at +4 and the
 * data segment at +24, so writing through it moved TOP out of remill's sight
 * and wrote an environment's FDS over MXCSR -- which is what the review of the
 * first version of this file found, and why every access below is fxsave32 and
 * these offsets are asserted. */
static_assert(__builtin_offsetof(FpuFXSAVE, cwd) == 0, "FXSAVE control word");
static_assert(__builtin_offsetof(FpuFXSAVE, swd) == 2, "FXSAVE status word");
static_assert(__builtin_offsetof(FpuFXSAVE, ftw) == 4, "FXSAVE abridged tag byte");
static_assert(__builtin_offsetof(FpuFXSAVE, fop) == 6, "FXSAVE opcode");
static_assert(__builtin_offsetof(FpuFXSAVE, ip) == 8, "FXSAVE instruction pointer");
static_assert(__builtin_offsetof(FpuFXSAVE, cs) == 12, "FXSAVE code segment");
static_assert(__builtin_offsetof(FpuFXSAVE, dp) == 16, "FXSAVE operand pointer");
static_assert(__builtin_offsetof(FpuFXSAVE, ds) == 20, "FXSAVE data segment");
static_assert(__builtin_offsetof(FpuFXSAVE, mxcsr) == 24, "FXSAVE MXCSR, never written here");

decltype(State::x87.fxsave32) &fx(State *s) { return s->x87.fxsave32; }
const decltype(State::x87.fxsave32) &fx(const State *s) { return s->x87.fxsave32; }

/* The control word as the lifted FNSTCW reports it once the PC24 pass has
 * kept its precision bits: the stored word with the rounding bits the host
 * is using. */
uint16_t cw_view(const State *s) {
    /* Bit 6 is reserved and the hardware reads it as 1 whatever was loaded
     * (`fldcw 0x023F` then `fnstcw` gives 0x027F); remill's FNSTCW gets the same
     * bit from tools/spike/pc24pass (2026-09-23). */
    return static_cast<uint16_t>((fx(s).cwd.flat & 0xF3FFu) | 0x0040u
                                 | rc_bits(std::fegetround()));
}

/* The status word as remill's FNSTSW builds it: condition codes and exception
 * flags from `sw`, stack-fault, error-summary, TOP and busy from `swd`. */
uint16_t sw_view(const State *s) {
    const FPUStatusFlags &f = s->sw;
    return static_cast<uint16_t>((fx(s).swd.flat & 0xB8C0u)
        | ((f.c0 & 1u) << 8) | ((f.c1 & 1u) << 9) | ((f.c2 & 1u) << 10) | ((f.c3 & 1u) << 14)
        | ((f.pe & 1u) << 5) | ((f.ue & 1u) << 4) | ((f.oe & 1u) << 3) | ((f.ze & 1u) << 2)
        | ((f.de & 1u) << 1) | (f.ie & 1u));
}

void set_sw(State *s, uint16_t sw) {
    FPUStatusFlags &f = s->sw;
    fx(s).swd.flat = sw;
    f.c0 = (sw >> 8) & 1u;
    f.c1 = (sw >> 9) & 1u;
    f.c2 = (sw >> 10) & 1u;
    f.c3 = (sw >> 14) & 1u;
    f.pe = (sw >> 5) & 1u;
    f.ue = (sw >> 4) & 1u;
    f.oe = (sw >> 3) & 1u;
    f.ze = (sw >> 2) & 1u;
    f.de = (sw >> 1) & 1u;
    f.ie = sw & 1u;
}

/* FXSAVE keeps only an abridged tag, one bit per physical register (1 in
 * use), and lifted code never writes it. The full tag word FSTENV stores has
 * two bits a register: 11 for empty and, here, 00 for any register in use --
 * the hardware would say 01 for a zero and 10 for a special value, which
 * nothing keeps. Loading folds it back: anything but 11 is in use. */
uint16_t full_tags(uint8_t abridged) {
    uint16_t tw = 0;
    for (uint32_t k = 0; k < 8; k++)
        if (!((abridged >> k) & 1u)) tw = static_cast<uint16_t>(tw | (3u << (2 * k)));
    return tw;
}

uint8_t abridged_tags(uint16_t tw) {
    uint8_t ab = 0;
    for (uint32_t k = 0; k < 8; k++)
        if (((tw >> (2 * k)) & 3u) != 3u) ab = static_cast<uint8_t>(ab | (1u << k));
    return ab;
}

/* A new TOP over the same physical registers. remill keeps the stack as
 * logical ST(i) in `st` and TOP in `swd`, and moves both on every push and pop
 * (its FLD shifts `st` and decrements the TOP field), so when an instruction
 * changes TOP and not the registers -- fldenv, and fnsave's FNINIT -- the
 * logical view turns: ST(i) becomes what was ST(i + new - old). */
void retop(State *s, uint32_t top) {
    const uint32_t old = (fx(s).swd.flat >> 11) & 7u;
    const uint32_t d = (top - old) & 7u;
    if (!d) return;
    uint8_t was[8][10];
    for (uint32_t k = 0; k < 8; k++) std::memcpy(was[k], &s->st.elems[k].val, 10);
    for (uint32_t k = 0; k < 8; k++) std::memcpy(&s->st.elems[k].val, was[(k + d) & 7u], 10);
}

void set_cw(State *s, uint16_t cw) {
    fx(s).cwd.flat = cw;
    std::fesetround(round_from_cw(cw));      /* what remill's FLDCW does */
}

/* The 28-byte protected-mode environment, the reserved halves written as the
 * hardware writes them (0xFFFF; observed on the host). */
void env_image(const State *s, uint8_t env[28]) {
    auto put16 = [&](uint32_t at, uint16_t v) { std::memcpy(env + at, &v, 2); };
    auto put32 = [&](uint32_t at, uint32_t v) { std::memcpy(env + at, &v, 4); };
    const auto &f = fx(s);
    put16(0, cw_view(s));
    put16(2, 0xFFFFu);
    put16(4, sw_view(s));
    put16(6, 0xFFFFu);
    put16(8, full_tags(f.ftw.flat));
    put16(10, 0xFFFFu);
    put32(12, f.ip);
    put16(16, f.cs.flat);
    put16(18, static_cast<uint16_t>(f.fop & 0x07FFu));
    put32(20, f.dp);
    put16(24, f.ds.flat);
    put16(26, 0xFFFFu);
}

/* fldenv's half: the registers stay where they are, so a new TOP turns the
 * logical stack (`turn`); frstor loads the registers after, in ST order, and
 * does not. MXCSR, beside these fields in FXSAVE, is not the environment's
 * and is not touched. */
void load_env(State *s, const uint8_t env[28], bool turn) {
    auto get16 = [&](uint32_t at) { uint16_t v; std::memcpy(&v, env + at, 2); return v; };
    auto get32 = [&](uint32_t at) { uint32_t v; std::memcpy(&v, env + at, 4); return v; };
    auto &f = fx(s);
    set_cw(s, get16(0));
    if (turn) retop(s, (get16(4) >> 11) & 7u);
    set_sw(s, get16(4));
    f.ftw.flat = abridged_tags(get16(8));
    f.ip = get32(12);
    f.cs.flat = get16(16);
    f.fop = static_cast<uint16_t>(get16(18) & 0x07FFu);
    f.dp = get32(20);
    f.ds.flat = get16(24);
}

int x87_env(Ctx &c, const Decoded &d) {
    State *s = c.s;
    switch (d.kind) {
    case FNSTENV: {
        uint8_t env[28];
        env_image(s, env);
        if (!wr(c, d.ea, sizeof env, env)) return SA2_EMU_FAULT;
        fx(s).cwd.flat |= 0x003Fu;           /* FNSTENV then masks every exception */
        return SA2_EMU_OK;
    }
    case FLDENV: {
        uint8_t env[28];
        if (!rd(c, d.ea, sizeof env, env)) return SA2_EMU_FAULT;
        load_env(s, env, true);
        return SA2_EMU_OK;
    }
    case FNSAVE: {
        uint8_t img[108];
        env_image(s, img);
        for (uint32_t k = 0; k < 8; k++) {     /* the x87's bytes, in either register mode */
            if (&sa2_rt_get_st80) {
                const sa2_f80 v = sa2_rt_get_st80(s, static_cast<int>(k));
                std::memcpy(img + 28 + 10 * k, v.b, 10);
            } else {
                std::memcpy(img + 28 + 10 * k, &s->st.elems[k].val, 10);
            }
        }
        if (!wr(c, d.ea, sizeof img, img)) return SA2_EMU_FAULT;
        /* and then FNINIT: TOP to 0 over the same registers, every register
         * empty, the pointers cleared; MXCSR is SSE state and stays */
        auto &f = fx(s);
        set_cw(s, 0x037Fu);
        retop(s, 0);
        set_sw(s, 0);
        f.ftw.flat = 0;
        f.fop = 0;
        f.ip = 0;
        f.cs.flat = 0;
        f.dp = 0;
        f.ds.flat = 0;
        return SA2_EMU_OK;
    }
    case FRSTOR: {
        uint8_t img[108];
        if (!rd(c, d.ea, sizeof img, img)) return SA2_EMU_FAULT;
        load_env(s, img, false);
        for (uint32_t k = 0; k < 8; k++) {
            if (&sa2_rt_set_st80) {
                sa2_f80 v;
                std::memset(&v, 0, sizeof v);
                std::memcpy(v.b, img + 28 + 10 * k, 10);
                sa2_rt_set_st80(s, static_cast<int>(k), v);
            } else {
                std::memcpy(&s->st.elems[k].val, img + 28 + 10 * k, 10);
            }
        }
        return SA2_EMU_OK;
    }
    default:
        return SA2_EMU_UNSUPPORTED;
    }
}

}  // namespace

extern "C" int sa2_emulate(void *state, uint32_t *next_pc, uint32_t *fault_va, char *why,
                           size_t why_cap) {
    Ctx c{static_cast<State *>(state), 0, why, why_cap};
    if (why && why_cap) why[0] = 0;
    const uint32_t pc = c.s->gpr.rip.dword;
    Decoded d{};
    int st = decode(c, pc, d);
    if (st == SA2_EMU_OK) {
        switch (d.kind) {
        case PADDUSB: case PADDUSW: st = saturating_add(c, d); break;
        case CVTPS2PI:              st = cvtps2pi(c, d); break;
        case PSUBSW: case PSUBSW_XMM: st = saturating_sub_words(c, d); break;
        default:                    st = x87_env(c, d); break;
        }
    }
    if (st == SA2_EMU_FAULT) {
        if (fault_va) *fault_va = c.fault;
        if (why && why_cap && !why[0])
            say(c, "%s at %#010x: guest %#010x cannot be read or written",
                d.len ? kKindName[d.kind] : "the instruction", pc, c.fault);
        return st;
    }
    if (st != SA2_EMU_OK) return st;
    g_count[d.kind]++;
    if (next_pc) *next_pc = pc + d.len;
    return SA2_EMU_OK;
}

extern "C" uint32_t sa2_emulate_kinds(void) { return KINDS; }
extern "C" const char *sa2_emulate_kind_name(uint32_t i) { return i < KINDS ? kKindName[i] : nullptr; }
extern "C" uint64_t sa2_emulate_kind_count(uint32_t i) { return i < KINDS ? g_count[i] : 0; }
