/* RetDec glue (tools/spike/fixtures): compiled with -std=gnu89 -w, this TU
 * includes one RetDec .c output (RETDEC_C, under private/generated) after a
 * prelude that defines the helpers RetDec assumes a runtime for, then
 * exposes retdec_call() for the harness. The RetDec C is not patched: where
 * the game passes arguments in registers, RetDec's function takes none and
 * reads an uninitialised local; the harness still loads the registers before
 * the call so the attempt is recorded as measured, not assumed. */
#include <stdint.h>
#include <string.h>
#include <math.h>
#include "marshal.h"
#include "shapes.h"

static long double fpr_slots[8];
long double __frontend_reg_load_fpr(int i) { return fpr_slots[i & 7]; }
void __frontend_reg_store_fpr(int i, long double v) { fpr_slots[i & 7] = v; }
int llvm_ctpop_i8(char c) { return __builtin_popcount((unsigned char)c); }
int __CIsqrt() { return 0; }
void _memset(void *p, int v, int n) { memset(p, v, n); }
void __asm_rep_movsd_memcpy(char *d, char *s, int n) { memmove(d, s, (size_t)n * 4); }

void retdec_trap(uint32_t pc);
/* Callees RetDec's C names that no TU provides: the other nine (RetDec
 * guessed their signatures from the caller's register use, so their C could
 * not be linked in without patching) and the Ninja helpers outside the ten. */
#define UNLINKED(name, va) int name() { retdec_trap(va); return 0; }
#if FN != 0x004273B0
UNLINKED(function_4273b0, 0x004273B0u)
#endif
#if FN != 0x0044B210
UNLINKED(function_44b210, 0x0044B210u)
#endif
UNLINKED(function_459260, 0x00459260u)
UNLINKED(function_427470, 0x00427470u)
UNLINKED(function_4274e0, 0x004274E0u)
UNLINKED(function_4275b0, 0x004275B0u)
UNLINKED(function_427680, 0x00427680u)
UNLINKED(function_427750, 0x00427750u)
UNLINKED(function_48b610, 0x0048B610u)

#include RETDEC_C

int retdec_call(uint32_t va, const call_t *c) {
    int result = 0;
#if FN == 0x0044B210
    g1 = *(int32_t *)(uintptr_t)G_MATRIX_STACK_TOP;
    g2 = *(int32_t *)(uintptr_t)G_MATRIX_STACK_LIMIT;
#endif
    __asm__ volatile("" : : "a"(c->eax), "c"(c->ecx), "d"(c->edx), "S"(c->esi), "D"(c->edi));
#if FN == 0x004273B0
    result = function_4273b0();
#elif FN == 0x00426CC0
    result = function_426cc0((char)*(uint32_t *)(uintptr_t)(c->esp0 + 4));
#elif FN == 0x00446960
    result = function_446960();
#elif FN == 0x004469E0
    result = function_4469e0();
#elif FN == 0x0077F3A0
    result = function_77f3a0();
#elif FN == 0x0077FC30
    result = function_77fc30();
#elif FN == 0x00468E70
    result = function_468e70();
#elif FN == 0x00468FA0
    result = function_468fa0((int32_t)*(uint32_t *)(uintptr_t)(c->esp0 + 4), (int32_t)*(uint32_t *)(uintptr_t)(c->esp0 + 8));
#elif FN == 0x0044B210
    result = function_44b210();
    *(int32_t *)(uintptr_t)G_MATRIX_STACK_TOP = g1;
#else
#error "no RetDec call shape for FN"
#endif
    (void)va;
    return result;
}
