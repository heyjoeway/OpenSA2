/* remill's x86 State layout, by field name, for the differential harness (cri_dsp_diff.c) to
 * hold runtime/sa2_cri_dsp.c's offsets to (`sa2_cri_dsp_layout`).
 *
 * The lifted code is 32-bit x86, so the FPU's FXSAVE image is `fxsave32` (remill's `fxsave`
 * macro picks by ADDRESS_SIZE_BITS, which defaults to 64 in a host translation unit). */
#include <cstddef>
#include <cstring>

#include "remill/Arch/X86/Runtime/State.h"

#if defined(__clang__)
#pragma clang diagnostic ignored "-Winvalid-offsetof"
#endif

#define F(name, member) {name, offsetof(State, member)}

extern "C" int sa2_remill_offset(const char *field) {
    static const struct { const char *name; size_t offset; } table[] = {
        F("aflag.cf", aflag.cf), F("aflag.pf", aflag.pf), F("aflag.af", aflag.af),
        F("aflag.zf", aflag.zf), F("aflag.sf", aflag.sf), F("aflag.of", aflag.of),
        F("seg.ds", seg.ds), F("seg.cs", seg.cs),
        F("addr.ss_base", addr.ss_base), F("addr.ds_base", addr.ds_base),
        F("gpr.rax", gpr.rax), F("gpr.rcx", gpr.rcx), F("gpr.rdx", gpr.rdx), F("gpr.rsi", gpr.rsi), F("gpr.rdi", gpr.rdi),
        F("gpr.rsp", gpr.rsp), F("gpr.rip", gpr.rip),
        F("st.elems[0].val", st.elems[0].val), F("st.elems[1].val", st.elems[1].val),
        F("sw.c0", sw.c0), F("sw.c2", sw.c2), F("sw.c3", sw.c3), F("sw.de", sw.de),
        F("sw.ie", sw.ie),
        F("x87.fxsave.cwd", x87.fxsave32.cwd), F("x87.fxsave.swd", x87.fxsave32.swd),
        F("x87.fxsave.fop", x87.fxsave32.fop), F("x87.fxsave.ip", x87.fxsave32.ip),
        F("x87.fxsave.cs", x87.fxsave32.cs), F("x87.fxsave.dp", x87.fxsave32.dp),
        F("x87.fxsave.ds", x87.fxsave32.ds),
    };
    for (const auto &e : table)
        if (!std::strcmp(e.name, field)) return static_cast<int>(e.offset);
    return -1;
}

extern "C" unsigned sa2_remill_state_size(void) { return sizeof(State); }
