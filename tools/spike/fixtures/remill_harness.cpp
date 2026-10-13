// remill fixture harness (tools/spike/fixtures). Links one lifted function's
// IR (plus the in-ten callees it calls) with this stub runtime and runs a
// vector file through it (vectors.h, shapes.h, marshal.h).
//
// The State layout comes from remill's own header (remill/Arch/X86/Runtime/
// State.h, v5.0.7). Everything is built for x86_64-linux-musl: the IR was
// produced on an x86-64 host and hardcodes host sizes (memset of a 16-byte
// long-double union), so compiled for i386, where x86_fp80 occupies 12
// bytes, every x87 operand copy was clobbered and each float store wrote 0;
// the 64-bit build is the configuration remill's own tests use. Memory
// intrinsics dereference guest addresses directly because the PE image is
// mapped at its own VA (guest.h; the ELF is linked at 0x10000000 to keep
// 0x400000..0x4000000 free). Flag/compare intrinsics are identities, as
// remill's runtime defines them. __remill_missing_block and the callees
// outside the ten that no IR provides trap: the row is recorded as status 1
// with the exit address. Three callees are native stubs that behave as the
// game's do: _CIsqrt (0x8290a0) executes fsqrt under the guest control word,
// memset (0x7acde0), and 0x459260 (direction -> two angles) which answers from
// the native row the vector carries. The x87 control word is set to 0x007F
// (design 2.3) before every row; remill's semantics carry it but do not
// consult it, which is what the fixture measures.
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
#include "marshal.h"
#include "shapes.h"
#include "vectors.h"
}

struct Memory;
// The IR passes 32-bit guest addresses (remill's addr_t is 64-bit when its
// headers are compiled for the x86-64 host), so every intrinsic takes uint32_t.
typedef uint32_t gaddr;
typedef Memory *(*lifted_fn)(State *, gaddr, Memory *);

static sigjmp_buf trap_jb;
static State *watched_state;
static int debug_level;
static volatile uint32_t trap_status, trap_pc;
static volatile int in_lifted_code;   // a fault while collecting outputs must not re-enter the row
static call_t current;
static long double st_get(State *s, int i) {
    long double v = 0;
    memcpy(&v, &s->st.elems[i].val, 10);
    return v;
}
static void st_set(State *s, int i, long double v) { memcpy(&s->st.elems[i].val, &v, 10); }

[[noreturn]] static void trap(uint32_t status, uint32_t pc) {
    trap_status = status; trap_pc = pc;
    if (debug_level) {
        State *s = watched_state;
        fprintf(stderr, "  trap status %u pc %#x in_lifted_code %d c0 %d c2 %d c3 %d ax %#x pf %d\n   st:", status, pc, in_lifted_code,
                s->sw.c0, s->sw.c2, s->sw.c3, s->gpr.rax.word, s->aflag.pf);
        for (int i = 0; i < 8; i++) fprintf(stderr, " %.9Lg", st_get(s, i));
        fprintf(stderr, "\n");
    }
    if (!in_lifted_code) {
        fprintf(stderr, "fault outside lifted code (status %u at %#x); giving up\n", status, pc);
        _exit(3);
    }
    in_lifted_code = 0;
    siglongjmp(trap_jb, 1);
}

static void on_fault(int, siginfo_t *si, void *) {
    trap(2, (uint32_t)(uintptr_t)si->si_addr);
}
static void on_alarm(int) {   // a row that runs for seconds is looping: status 4 at the block PC
    trap(4, watched_state ? watched_state->gpr.rip.dword : 0);
}

static unsigned debug_calls;
static void dbg(const char *what, uint32_t a, double v = 0) {   // SA2_FIXTURE_DEBUG=2: trace the first memory accesses per row
    if (debug_level >= 2 && debug_calls++ < 400)
        fprintf(stderr, "  %s %#x = %.9g at pc %#x esp %#x\n", what, a, v, watched_state ? watched_state->gpr.rip.dword : 0,
                watched_state ? watched_state->gpr.rsp.dword : 0);
}


extern "C" {
// ---- memory ----
uint8_t  __remill_read_memory_8 (Memory *, gaddr a) { return *gp8(a); }
uint16_t __remill_read_memory_16(Memory *, gaddr a) { uint16_t v; memcpy(&v, gp8(a), 2); return v; }
uint32_t __remill_read_memory_32(Memory *, gaddr a) { uint32_t v; memcpy(&v, gp8(a), 4); dbg("r32", a, v); return v; }
uint64_t __remill_read_memory_64(Memory *, gaddr a) { uint64_t v; memcpy(&v, gp8(a), 8); return v; }
float    __remill_read_memory_f32(Memory *, gaddr a) { float v; memcpy(&v, gp8(a), 4); dbg("rf32", a, v); return v; }
double   __remill_read_memory_f64(Memory *, gaddr a) { double v; memcpy(&v, gp8(a), 8); return v; }
Memory *__remill_write_memory_8 (Memory *m, gaddr a, uint8_t v)  { *gp8(a) = v; return m; }
Memory *__remill_write_memory_16(Memory *m, gaddr a, uint16_t v) { memcpy(gp8(a), &v, 2); return m; }
Memory *__remill_write_memory_32(Memory *m, gaddr a, uint32_t v) { memcpy(gp8(a), &v, 4); dbg("w32", a, v); return m; }
Memory *__remill_write_memory_64(Memory *m, gaddr a, uint64_t v) { memcpy(gp8(a), &v, 8); return m; }
Memory *__remill_write_memory_f32(Memory *m, gaddr a, float v)   { memcpy(gp8(a), &v, 4); dbg("wf32", a, v); return m; }
Memory *__remill_write_memory_f64(Memory *m, gaddr a, double v)  { memcpy(gp8(a), &v, 8); return m; }
// ---- flags, compares, fpu ----
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
uint32_t __remill_fpu_exception_test_and_clear(uint32_t, uint32_t) {
    if (debug_level >= 3 && watched_state && debug_calls++ < 400) {   // x87 trace: st0..st2, C0..C3, AX
        State *s = watched_state;
        fprintf(stderr, "  x87 pc %#x st0 %.9Lg st1 %.9Lg st2 %.9Lg c0 %d c1 %d c2 %d c3 %d ax %#x\n", s->gpr.rip.dword,
                st_get(s, 0), st_get(s, 1), st_get(s, 2), s->sw.c0, s->sw.c1, s->sw.c2, s->sw.c3, s->gpr.rax.word);
    }
    return 0;
}
uint8_t __remill_undefined_8() { return 0; }
uint16_t __remill_undefined_16() { return 0; }
uint32_t __remill_undefined_32() { return 0; }
uint64_t __remill_undefined_64() { return 0; }
// ---- control flow ----
Memory *__remill_function_return(State *, gaddr, Memory *m) { return m; }
Memory *__remill_missing_block(State *, gaddr pc, Memory *) { trap(1, pc); }
Memory *__remill_error(State *, gaddr pc, Memory *) { trap(2, pc); }
Memory *__remill_jump(State *, gaddr pc, Memory *) { trap(1, pc); }
Memory *__remill_function_call(State *, gaddr pc, Memory *) { trap(1, pc); }

// ---- callees outside the ten: native stubs ----
static void pop_return(State *s) { s->gpr.rsp.dword += 4; }

Memory *sub_8290a0(State *s, gaddr, Memory *m) {   // CRT _CIsqrt
    long double v = st_get(s, 0), r;
    uint16_t host_cw, guest_cw = s->x87.fxsave.cwd.flat;
    __asm__ volatile("fnstcw %0" : "=m"(host_cw));
    __asm__ volatile("fldcw %0" : : "m"(guest_cw));
    __asm__ volatile("fsqrt" : "=t"(r) : "0"(v));
    __asm__ volatile("fldcw %0" : : "m"(host_cw));
    st_set(s, 0, r);
    pop_return(s);
    return m;
}
Memory *sub_7acde0(State *s, gaddr, Memory *m) {   // CRT memset(dst, val, n)
    uint32_t esp = s->gpr.rsp.dword;
    uint32_t dst = *gp32(esp + 4), val = *gp32(esp + 8), n = *gp32(esp + 12);
    memset(gp8(dst), (int)val, n);
    s->gpr.rax.dword = dst;
    pop_return(s);
    return m;
}
Memory *sub_459260(State *s, gaddr pc, Memory *m) {  // direction -> angles, answered from the native row
    uint32_t esp = s->gpr.rsp.dword;
    uint32_t x = *gp32(esp + 4), y = *gp32(esp + 8), z = *gp32(esp + 12);
    if (x != current.dir[0] || y != current.dir[1] || z != current.dir[2]) trap(1, pc);
    *gp32(s->gpr.rdi.dword) = current.angle1;
    *gp32(s->gpr.rsi.dword) = current.angle2;
    s->gpr.rax.dword = current.angle2;
    pop_return(s);
    return m;
}
// Ninja helpers and the polygon builder: lifted IR when linked (weak), else a trap.
#define UNLIFTED(name, va) Memory *name(State *, gaddr, Memory *) __attribute__((weak)); \
    Memory *name(State *, gaddr, Memory *) { trap(1, va); }
UNLIFTED(sub_427470, 0x427470u)
UNLIFTED(sub_4274e0, 0x4274e0u)
UNLIFTED(sub_4275b0, 0x4275b0u)
UNLIFTED(sub_427680, 0x427680u)
UNLIFTED(sub_427750, 0x427750u)
UNLIFTED(sub_48b610, 0x48b610u)
// The ten (weak: a binary links the ones it needs).
Memory *sub_4273b0(State *, gaddr, Memory *) __attribute__((weak));
Memory *sub_426cc0(State *, gaddr, Memory *) __attribute__((weak));
Memory *sub_48b9f0(State *, gaddr, Memory *) __attribute__((weak));
Memory *sub_446960(State *, gaddr, Memory *) __attribute__((weak));
Memory *sub_4469e0(State *, gaddr, Memory *) __attribute__((weak));
Memory *sub_77f3a0(State *, gaddr, Memory *) __attribute__((weak));
Memory *sub_77fc30(State *, gaddr, Memory *) __attribute__((weak));
Memory *sub_468e70(State *, gaddr, Memory *) __attribute__((weak));
Memory *sub_468fa0(State *, gaddr, Memory *) __attribute__((weak));
Memory *sub_44b210(State *, gaddr, Memory *) __attribute__((weak));
// remill names the recursive self-call of 0x48B9F0 "sub_48b9f0.1".
Memory *sub_48b9f0_recursive(State *s, gaddr pc, Memory *m) __asm__("sub_48b9f0.1");
Memory *sub_48b9f0_recursive(State *s, gaddr pc, Memory *m) { return sub_48b9f0 ? sub_48b9f0(s, pc, m) : (trap(1, pc), m); }
}

static lifted_fn lookup(uint32_t va) {
    switch (va) {
    case FN_CALC_VECTOR: return sub_4273b0;   case FN_CALC_POINT: return sub_426cc0;
    case FN_OBJECT_TREE: return sub_48b9f0;   case FN_CLAMP_YAW: return sub_446960;
    case FN_VECTOR_ANGLE: return sub_4469e0;  case FN_UNIT_VECTOR: return sub_77f3a0;
    case FN_LENGTH: return sub_77fc30;        case FN_ROTATE_ENTITY: return sub_468e70;
    case FN_TRANSFORM_DIR: return sub_468fa0; case FN_PUSH_IDENTITY: return sub_44b210;
    }
    return nullptr;
}

static uint32_t float_word(long double v) { float f = (float)v; uint32_t w; memcpy(&w, &f, 4); return w; }

int main(int argc, char **argv) {
    if (argc != 4) { fprintf(stderr, "usage: %s image.bin vectors.bin out.bin\n", argv[0]); return 2; }
    if (guest_map(argv[1]) != 0) return 1;
    vectors_t v;
    if (vectors_read(argv[2], &v) != 0) return 1;
    const shape_t *sh = shape_for(v.va);
    if (!sh || sh->in_words != v.in_words || sh->out_words != v.out_words) {
        fprintf(stderr, "vector shape mismatch for %#x\n", v.va); return 1;
    }
    lifted_fn fn = lookup(v.va);
    static uint8_t altstack[1 << 16];
    stack_t ss = {altstack, 0, sizeof altstack};
    sigaltstack(&ss, nullptr);
    struct sigaction sa; memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_fault; sa.sa_flags = SA_SIGINFO | SA_ONSTACK | SA_NODEFER;
    sigaction(SIGSEGV, &sa, nullptr); sigaction(SIGBUS, &sa, nullptr); sigaction(SIGFPE, &sa, nullptr);
    signal(SIGALRM, on_alarm);

    alignas(16) static uint8_t state_buf[sizeof(State)];
    State &state = *reinterpret_cast<State *>(state_buf);
    uint32_t words = 2 + v.out_words;
    for (uint32_t r = 0; r < v.rows; r++) {
        uint32_t *out = v.out + (size_t)r * words;
        if (!fn) { out[0] = 3; out[1] = 0; continue; }
        marshal_in(v.va, v.in + (size_t)r * v.in_words, &current);
        memset(&state, 0, sizeof state);
        state.x87.fxsave.cwd.flat = 0x007F;
        state.gpr.rax.dword = current.eax; state.gpr.rcx.dword = current.ecx; state.gpr.rdx.dword = current.edx;
        state.gpr.rsi.dword = current.esi; state.gpr.rdi.dword = current.edi;
        *gp32(current.esp0) = SENTINEL_RET;
        state.gpr.rsp.dword = current.esp0;
        state.gpr.rip.dword = v.va;
        uint32_t status = 0, exit_pc = 0;
        watched_state = &state;
        debug_level = getenv("SA2_FIXTURE_DEBUG") ? atoi(getenv("SA2_FIXTURE_DEBUG")) : 0;
        debug_calls = 0;
        if (debug_level) fprintf(stderr, "row %u: start eip %#x esp %#x\n", r, v.va, current.esp0);
        alarm(2);
        if (sigsetjmp(trap_jb, 1) == 0) {
            in_lifted_code = 1;
            fn(&state, v.va, nullptr);
            in_lifted_code = 0;
            if (state.gpr.rip.dword != SENTINEL_RET) { status = 1; exit_pc = state.gpr.rip.dword; }
        } else {
            status = trap_status; exit_pc = trap_pc;
            if (debug_level) fprintf(stderr, "  landed after trap\n");
        }
        alarm(0);
        out[0] = status; out[1] = exit_pc;
        marshal_out(v.va, &current, status, state.gpr.rax.dword, float_word(st_get(&state, 0)), out + 2);
    }
    return vectors_write(argv[3], &v);
}
