// S2 patch: a generic remill fixture harness for one lifted function.
//
// The S1 harness (tools/spike/fixtures/remill_harness.cpp, read-only here) knows
// the ten spike functions by name: it has a marshaller per function and a fixed
// control word of 0x007F. The S2 patch work needs to drive functions that are not
// among the ten (hand-encoded x87 probes, tools/s2/patch/probes.py, and the CRT
// square root body the game's every sqrt goes through) and needs the control word
// to be a column of the fixture, not a constant. So this harness is driven
// entirely by the vector file:
//
//   header "SA2S2V1\0" u32 va, rows, in_words, out_words, cw, stack_args,
//                      reg_eax, reg_ecx, reg_edx, reg_esi, reg_edi, flags
//   row    in_words u32
//
// Setup per row: State is zeroed; State.x87.fxsave.cwd is set to `cw` verbatim
// (precision-control bits included: that is the column); the guest stack gets the
// sentinel return address and then `stack_args` of the row's leading words pushed
// right to left, so in[0] lands at [esp+4]; every remaining word is written to the
// input arena A_IN at its own index; each register takes a REG_* descriptor.
//
// Output per row, fixed prefix then the arena:
//   0 status   (shapes.h: 0 returned, 1 left the lifted bytes, 2 fault, 4 timeout)
//   1 exit_pc
//   2 eax
//   3,4,5 st(0) as an x86_fp80: mantissa low, mantissa high, sign+exponent
//   6 State.x87.fxsave.cwd after the row (what a guest fldcw left there)
//   7.. out_words words read from the output arena A_OUT, or from the input
//       arena when the header's flags carry FLAG_OUT_IN_ARENA (a function that
//       writes its result back through the pointer it was given).
//
// Header flags: FLAG_ST0_FROM_ROW loads st(0) with the double in the row's first
// two words before the call, for a function entered with a value already on the
// x87 stack (the CRT's square-root body is called that way).
//
// The lifted function's symbol is a build-time macro (-DSUB=sub_300000) so the
// same source drives any function; callee stubs are generated per function by the
// driver (tools/s2/patch/stubs.py) from the IR's own declarations.
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <csetjmp>
#include <csignal>
#include <unistd.h>
#include "remill/Arch/X86/Runtime/State.h"
extern "C" {
#include "guest.h"
#include "shapes.h"
}

#define A_IN  0x03400000u
#define A_OUT_S2 0x03500000u

// Register descriptors (see probes.py / the driver).
#define REG_ZERO   0xF0000000u
#define REG_A_IN   0xF0000001u
#define REG_A_OUT  0xF0000002u
#define REG_IN_WORD_BASE 0xF1000000u    // | index: the row's word at that index
#define REG_A_IN_OFF_BASE 0xF2000000u   // | byte offset: A_IN + offset

#define FLAG_ST0_FROM_ROW  1u   // st(0) = the double in row words 0 and 1
#define FLAG_OUT_IN_ARENA  2u   // read the output words from A_IN, not A_OUT

struct Memory;
typedef uint32_t gaddr;
typedef Memory *(*lifted_fn)(State *, gaddr, Memory *);

extern "C" Memory *SUB(State *, gaddr, Memory *);

static sigjmp_buf trap_jb;
static State *watched_state;
static volatile uint32_t trap_status, trap_pc;
static volatile int in_lifted_code;

[[noreturn]] static void s2_trap(uint32_t status, uint32_t pc) {
    trap_status = status; trap_pc = pc;
    if (!in_lifted_code) { fprintf(stderr, "fault outside lifted code (status %u at %#x)\n", status, pc); _exit(3); }
    in_lifted_code = 0;
    siglongjmp(trap_jb, 1);
}
static void on_fault(int, siginfo_t *si, void *) { s2_trap(2, (uint32_t)(uintptr_t)si->si_addr); }
static void on_alarm(int) { s2_trap(4, watched_state ? watched_state->gpr.rip.dword : 0); }

extern "C" {
uint8_t  __remill_read_memory_8 (Memory *, gaddr a) { return *gp8(a); }
uint16_t __remill_read_memory_16(Memory *, gaddr a) { uint16_t v; memcpy(&v, gp8(a), 2); return v; }
uint32_t __remill_read_memory_32(Memory *, gaddr a) { uint32_t v; memcpy(&v, gp8(a), 4); return v; }
uint64_t __remill_read_memory_64(Memory *, gaddr a) { uint64_t v; memcpy(&v, gp8(a), 8); return v; }
float    __remill_read_memory_f32(Memory *, gaddr a) { float v; memcpy(&v, gp8(a), 4); return v; }
double   __remill_read_memory_f64(Memory *, gaddr a) { double v; memcpy(&v, gp8(a), 8); return v; }
// remill declares the 80-bit accessors as `Memory *(Memory *, addr_t, float80_t &)`:
// the value travels through a 16-byte aligned buffer, not in a register, so these
// take a pointer. Declaring them `long double` by value silently mis-ABIs every
// `fld`/`fstp` of a tbyte operand (measured: the store wrote the unrounded input).
Memory *__remill_read_memory_f80(Memory *m, gaddr a, void *out) { memcpy(out, gp8(a), 10); return m; }
Memory *__remill_write_memory_8 (Memory *m, gaddr a, uint8_t v)  { *gp8(a) = v; return m; }
Memory *__remill_write_memory_16(Memory *m, gaddr a, uint16_t v) { memcpy(gp8(a), &v, 2); return m; }
Memory *__remill_write_memory_32(Memory *m, gaddr a, uint32_t v) { memcpy(gp8(a), &v, 4); return m; }
Memory *__remill_write_memory_64(Memory *m, gaddr a, uint64_t v) { memcpy(gp8(a), &v, 8); return m; }
Memory *__remill_write_memory_f32(Memory *m, gaddr a, float v)   { memcpy(gp8(a), &v, 4); return m; }
Memory *__remill_write_memory_f64(Memory *m, gaddr a, double v)  { memcpy(gp8(a), &v, 8); return m; }
Memory *__remill_write_memory_f80(Memory *m, gaddr a, const void *in) { memcpy(gp8(a), in, 10); return m; }
bool __remill_flag_computation_zero(bool r, ...) { return r; }
bool __remill_flag_computation_sign(bool r, ...) { return r; }
bool __remill_flag_computation_carry(bool r, ...) { return r; }
bool __remill_flag_computation_overflow(bool r, ...) { return r; }
bool __remill_compare_eq(bool r) { return r; }
bool __remill_compare_neq(bool r) { return r; }
bool __remill_compare_sgt(bool r) { return r; }
bool __remill_compare_sge(bool r) { return r; }
bool __remill_compare_slt(bool r) { return r; }
bool __remill_compare_sle(bool r) { return r; }
bool __remill_compare_ugt(bool r) { return r; }
bool __remill_compare_uge(bool r) { return r; }
bool __remill_compare_ult(bool r) { return r; }
bool __remill_compare_ule(bool r) { return r; }
uint32_t __remill_fpu_exception_test_and_clear(uint32_t, uint32_t) { return 0; }
uint8_t __remill_undefined_8() { return 0; }
uint16_t __remill_undefined_16() { return 0; }
uint32_t __remill_undefined_32() { return 0; }
uint64_t __remill_undefined_64() { return 0; }
Memory *__remill_function_return(State *, gaddr, Memory *m) { return m; }
Memory *__remill_missing_block(State *, gaddr pc, Memory *) { s2_trap(1, pc); }
Memory *__remill_error(State *, gaddr pc, Memory *) { s2_trap(2, pc); }
Memory *__remill_jump(State *, gaddr pc, Memory *) { s2_trap(1, pc); }
Memory *__remill_function_call(State *, gaddr pc, Memory *) { s2_trap(1, pc); }

// The generated stub file (stubs.cpp) calls this for every declared callee the
// driver did not decide to serve natively.
void s2_callee_trap(uint32_t va) { s2_trap(1, va); }
void s2_pop_return(State *s) { s->gpr.rsp.dword += 4; }
}

static long double st_get(State *s, int i) { long double v = 0; memcpy(&v, &s->st.elems[i].val, 10); return v; }

struct Header {
    char magic[8];
    uint32_t va, rows, in_words, out_words, cw, stack_args;
    uint32_t reg_eax, reg_ecx, reg_edx, reg_esi, reg_edi, flags;
};

static uint32_t reg_value(uint32_t desc, const uint32_t *in, uint32_t in_words) {
    if (desc == REG_ZERO) return 0;
    if (desc == REG_A_IN) return A_IN;
    if (desc == REG_A_OUT) return A_OUT_S2;
    if ((desc & 0xFF000000u) == REG_IN_WORD_BASE) {
        uint32_t i = desc & 0x00FFFFFFu;
        return i < in_words ? in[i] : 0;
    }
    if ((desc & 0xFF000000u) == REG_A_IN_OFF_BASE) return A_IN + (desc & 0x00FFFFFFu);
    return desc;
}

int main(int argc, char **argv) {
    if (argc != 4) { fprintf(stderr, "usage: %s image.bin vectors.bin out.bin\n", argv[0]); return 2; }
    if (guest_map(argv[1]) != 0) return 1;
    FILE *f = fopen(argv[2], "rb");
    if (!f) { perror(argv[2]); return 1; }
    Header h;
    if (fread(&h, sizeof h, 1, f) != 1 || memcmp(h.magic, "SA2S2V1", 8) != 0) { fprintf(stderr, "bad vector file\n"); return 1; }
    size_t nin = (size_t)h.rows * h.in_words;
    uint32_t *in = (uint32_t *)calloc(nin ? nin : 1, 4);
    if (fread(in, 4, nin, f) != nin) { fprintf(stderr, "short vector file\n"); return 1; }
    fclose(f);
    const uint32_t PREFIX = 7;
    uint32_t words = PREFIX + h.out_words;
    uint32_t *out = (uint32_t *)calloc((size_t)h.rows * words, 4);

    static uint8_t altstack[1 << 16];
    stack_t ss = {altstack, 0, sizeof altstack};
    sigaltstack(&ss, nullptr);
    struct sigaction sa; memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_fault; sa.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_NODEFER;
    sigaction(SIGSEGV, &sa, nullptr); sigaction(SIGBUS, &sa, nullptr); sigaction(SIGFPE, &sa, nullptr);
    signal(SIGALRM, on_alarm);

    alignas(16) static uint8_t state_buf[sizeof(State)];
    State &state = *reinterpret_cast<State *>(state_buf);
    for (uint32_t r = 0; r < h.rows; r++) {
        const uint32_t *row = in + (size_t)r * h.in_words;
        uint32_t *o = out + (size_t)r * words;
        guest_reset_arenas();
        memset(gp8(A_IN), 0, 0x1000);
        memset(gp8(A_OUT_S2), 0, 0x1000);
        for (uint32_t i = h.stack_args; i < h.in_words; i++) *gp32(A_IN + (i - h.stack_args) * 4) = row[i];
        memset(&state, 0, sizeof state);
        state.x87.fxsave.cwd.flat = (uint16_t)h.cw;
        // The host x87 starts at its own control word; the guest's is State's.
        uint32_t esp = A_STACK_TOP;
        for (uint32_t i = h.stack_args; i-- > 0;) { esp -= 4; *gp32(esp) = row[i]; }
        esp -= 4; *gp32(esp) = SENTINEL_RET;
        state.gpr.rsp.dword = esp;
        state.gpr.rip.dword = h.va;
        state.gpr.rax.dword = reg_value(h.reg_eax, row, h.in_words);
        state.gpr.rcx.dword = reg_value(h.reg_ecx, row, h.in_words);
        state.gpr.rdx.dword = reg_value(h.reg_edx, row, h.in_words);
        state.gpr.rsi.dword = reg_value(h.reg_esi, row, h.in_words);
        state.gpr.rdi.dword = reg_value(h.reg_edi, row, h.in_words);
        if ((h.flags & FLAG_ST0_FROM_ROW) && h.in_words >= 2) {
            uint64_t bits = (uint64_t)row[0] | ((uint64_t)row[1] << 32);
            double d;
            memcpy(&d, &bits, 8);
            long double v = (long double)d;
            memcpy(&state.st.elems[0].val, &v, 10);
        }
        uint32_t status = 0, exit_pc = 0;
        watched_state = &state;
        alarm(2);
        if (sigsetjmp(trap_jb, 1) == 0) {
            in_lifted_code = 1;
            SUB(&state, h.va, nullptr);
            in_lifted_code = 0;
            if (state.gpr.rip.dword != SENTINEL_RET) { status = 1; exit_pc = state.gpr.rip.dword; }
        } else {
            status = trap_status; exit_pc = trap_pc;
        }
        alarm(0);
        o[0] = status; o[1] = exit_pc; o[2] = state.gpr.rax.dword;
        long double st0 = st_get(&state, 0);
        uint8_t raw[10]; memcpy(raw, &st0, 10);
        memcpy(&o[3], raw, 4); memcpy(&o[4], raw + 4, 4);
        o[5] = (uint32_t)(raw[8] | (raw[9] << 8));
        o[6] = state.x87.fxsave.cwd.flat;
        uint32_t out_base = (h.flags & FLAG_OUT_IN_ARENA) ? A_IN : A_OUT_S2;
        for (uint32_t i = 0; i < h.out_words; i++) o[PREFIX + i] = *gp32(out_base + i * 4);
    }
    FILE *g = fopen(argv[3], "wb");
    if (!g) { perror(argv[3]); return 1; }
    uint32_t hdr[3] = {h.va, h.rows, words};
    fwrite("SA2S2O1\0", 1, 8, g);
    fwrite(hdr, 4, 3, g);
    fwrite(out, 4, (size_t)h.rows * words, g);
    fclose(g);
    return 0;
}
