/* The continuation after an emulated instruction, through the real runtime.
 *
 * remill ends a trace at an instruction it cannot lift: it calls
 * __remill_sync_hyper_call(256) with `gpr.rip` at the instruction and then
 * tail-calls __remill_error at the next one. runtime/remill_runtime.cpp runs
 * runtime/sa2_emulate.cpp in the hypercall and continues at the next
 * instruction in __remill_error. This drives that path with two fake lifted
 * functions shaped like remill's output and prints what happened, for
 * Documentation/scripts/tests/test_emulate.py:
 *
 *   - with no function-table row after the instruction: CONTROL_LEFT there,
 *     and the pending continuation consumed, so a later bare __remill_error
 *     at the same address is a plain FAULT;
 *   - with a row: a loop of N emulated instructions, each continuing into the
 *     next, whose frame address must not move -- a continuation that was a
 *     call rather than a tail call would add frames per instruction;
 *   - built without sa2_emulate.cpp (the -DNO_EMULATOR variant): a FAULT at the
 *     instruction, naming the missing emulator.
 *
 * First written by the review of the slice that added the emulator
 * (scratchpad cont_test.cpp); kept here so the continuation has a test.
 *
 *   continuation_probe [N]     one JSON line
 */
#include <csetjmp>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include <sys/mman.h>

#include "remill/Arch/X86/Runtime/State.h"

extern "C" {
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_trap.h"
}

struct Memory;
extern "C" Memory *__remill_sync_hyper_call(State *, Memory *, uint32_t);
extern "C" Memory *__remill_error(State *, uint32_t, Memory *);

/* The guest-memory surface remill_runtime.cpp reads, over one mapping. */
extern "C" {
sa2_guest_t sa2_guest;
uint8_t *sa2_guest_fast_base;          /* the intrinsics' fast test (guest_memory.h): set below */
int sa2_guest_poison = 0;
uint64_t sa2_guest_poison_reads = 0;
uint32_t sa2_guest_poison_first = 0;
void sa2_guest_note_write(uint32_t, uint32_t) {}
int sa2_guest_is_poison(uint32_t, uint32_t) { return 0; }
const char *sa2_guest_region_of(uint32_t) { return "test"; }
int sa2_peek(uint32_t a, uint32_t n, void *o) {
    uint8_t *p = sa2_gp(a, n);
    if (!p || a + n > 0x01000000u) return SA2_E_RANGE;
    std::memcpy(o, p, n);
    return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *i) {
    uint8_t *p = sa2_gp(a, n);
    if (!p || a + n > 0x01000000u) return SA2_E_RANGE;
    std::memcpy(p, i, n);
    return SA2_OK;
}
}

namespace {

const uint32_t kSite = 0x00401000u, kNext = 0x00401003u;   /* 0F DC C1: paddusb mm0, mm1 */
uint64_t g_iters, g_n;
uintptr_t g_sp_min, g_sp_max;

}  // namespace

/* After the instruction: counts, measures its own frame, and loops back to the
 * instruction the way a codec loop does. */
extern "C" __attribute__((noinline)) Memory *sub_after(State *s, uint32_t, Memory *m) {
    const uintptr_t sp = reinterpret_cast<uintptr_t>(__builtin_frame_address(0));
    if (!g_sp_min || sp < g_sp_min) g_sp_min = sp;
    if (sp > g_sp_max) g_sp_max = sp;
    if (++g_iters >= g_n) return m;
    s->gpr.rip.dword = kSite;
    m = __remill_sync_hyper_call(s, m, 256);
    [[clang::musttail]] return __remill_error(s, kNext, m);
}

/* The trace remill emits for a block ending at the instruction. */
extern "C" __attribute__((noinline)) Memory *sub_before(State *s, uint32_t, Memory *m) {
    s->gpr.rip.dword = kSite;
    m = __remill_sync_hyper_call(s, m, 256);
    [[clang::musttail]] return __remill_error(s, kNext, m);
}

extern "C" Memory *sub_bare(State *s, uint32_t, Memory *m) {
    return __remill_error(s, kNext, m);
}

namespace {

int run(State *s, sa2_lifted_fn fn, uint32_t pc) {
    sa2_trap_clear();
    if (sigsetjmp(*sa2_trap_jmpbuf(), 1) == 0) {
        sa2_trap_window(1);
        fn(s, pc, nullptr);
        sa2_trap_window(0);
        return 0;
    }
    return static_cast<int>(sa2_trap_last()->status);
}

void jstr(const char *s) {
    std::putchar('"');
    for (; *s; s++) {
        if (*s == '"' || *s == '\\') std::putchar('\\');
        std::putchar(*s);
    }
    std::putchar('"');
}

}  // namespace

int main(int argc, char **argv) {
    g_n = argc > 1 ? std::strtoull(argv[1], nullptr, 0) : 100000;
    void *b = mmap(nullptr, 0x01000000u, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    sa2_guest.base = static_cast<uint8_t *>(b);
    sa2_guest.size = 0x01000000u;
    sa2_guest.ready = 1;
    sa2_guest_fast_base = sa2_guest.base;   /* mapped, and the poison tier off */
    const uint8_t insn[3] = {0x0F, 0xDC, 0xC1};
    sa2_poke(kSite, 3, insn);
    sa2_trap_install_handlers();
    sa2_ft_init(64);
    State *s = static_cast<State *>(sa2_rt_state_alloc());
    const uint64_t one = 0x0101010101010101ull;
    std::memcpy(&s->mmx.elems[1].val, &one, 8);

    const int st0 = run(s, reinterpret_cast<sa2_lifted_fn>(sub_before), kSite);
    const uint32_t pc0 = sa2_trap_last()->exit_pc;
    char note0[512];
    std::snprintf(note0, sizeof note0, "%s", sa2_trap_last()->note);
    const int st1 = run(s, reinterpret_cast<sa2_lifted_fn>(sub_bare), kNext);
    const uint32_t pc1 = sa2_trap_last()->exit_pc;
    std::printf("{\"no_row\": {\"status\": %d, \"exit_pc\": %u, \"note\": ", st0, pc0);
    jstr(note0);
    std::printf("}, \"bare_after\": {\"status\": %d, \"exit_pc\": %u}", st1, pc1);

#ifndef NO_EMULATOR
    sa2_ft_add(kNext, reinterpret_cast<sa2_lifted_fn>(sub_after), "blk_after", SA2_FN_LIFTED);
    std::memset(&s->mmx.elems[0].val, 0, 8);
    const uint64_t before_emulated = sa2_rt_emulated(), before_continued = sa2_rt_emulated_continued();
    g_iters = 0;
    const int st2 = run(s, reinterpret_cast<sa2_lifted_fn>(sub_before), kSite);
    uint64_t mm0;
    std::memcpy(&mm0, &s->mmx.elems[0].val, 8);
    std::printf(", \"loop\": {\"status\": %d, \"iterations\": %llu, \"emulated\": %llu, "
                "\"continued\": %llu, \"mm0\": \"%016llx\", \"frame_spread\": %llu}",
                st2, static_cast<unsigned long long>(g_iters),
                static_cast<unsigned long long>(sa2_rt_emulated() - before_emulated),
                static_cast<unsigned long long>(sa2_rt_emulated_continued() - before_continued),
                static_cast<unsigned long long>(mm0),
                static_cast<unsigned long long>(g_sp_max - g_sp_min));
#endif
    std::printf("}\n");
    return 0;
}
