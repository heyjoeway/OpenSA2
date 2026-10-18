/* quiet-omochao: Omochao, the hint robot, does nothing: the ones a stage's object list places and
 * the ones a stage's control object makes for its hints neither appear nor speak, as with the
 * "Disable Omochao" code in SA2 Mod Loader's Codes.lst (runtime/sa2_tweaks.h: the tweaks and their
 * rules).
 *
 * TWEAK_WRAPS: sub_6c0780, sub_6c0a90
 *
 * The Codes.lst entry writes code: C3 (`ret`) at 0x006C0780, and 20 NOPs at 0x006C0A9A. OpenSA2
 * never runs the image's .text, so the two wraps below do what those bytes make the two functions
 * do, read from the unpacked image:
 *
 *   0x006C0780  Omochao_Main, an object's main (cdecl: the ObjectMaster at [ESP+4], the caller pops
 *               it; one plain `ret`, at 0x006C096A). About 60 stages' object lists in .dataa hold
 *               it as a dword (0x00970D50 and 0x00984058 among them), so the object loader makes
 *               the object and the object loop calls it through the function table; 0x006C0A90
 *               names it as the main of the objects it makes (0x006C0AB9, 0x006C0AD3) and calls
 *               it once directly (0x006C0B4B). With the `ret` at its entry the object stays, but
 *               its main never runs: it never sets its display, collision or hint, so nothing is
 *               drawn or said. The wrap returns as that `ret` does: EIP the return address at
 *               guest [ESP], ESP + 4, every other register as the caller left it.
 *   0x006C0A90  the Omochao maker (EAX a mode, 0, 1 or 2; EBX, ECX and two stack arguments the
 *               place and the hint; cdecl, plain `ret`). The NOPs remove its three mode tests,
 *               and every mode falls through to `xor eax,eax; pop edi; pop esi; pop ebp; ret` at
 *               0x006C0AAE: no object made, EAX 0, the same answer a failed allocation gives. The
 *               wrap returns with EAX 0, EIP the return address, ESP + 4 (the pushes it skips are
 *               popped on that path, so EBP, ESI and EDI are the caller's). Its eight callers,
 *               linked to it directly: 0x00499B54 (0x004999C0, the last boss), 0x0049E4C9
 *               (0x0049E2C0, the last boss's player manager), 0x004C82D0 (ControlShadow_Main),
 *               0x00619E3A (ControlShadow_stg19), 0x006274D3 (ControlTWalker_stg20), 0x00649D86
 *               (ControlRouge), 0x00662B62 (ControlTWalker_stg29), 0x007623C4 (0x00762370). Each
 *               pops its two arguments and overwrites EAX, or returns it to a caller that
 *               overwrites it or ignores it (0x0049E2C0 into the object main 0x0049E520;
 *               0x00762370 into 0x0051FD10, 0x005C7A60, 0x005CCB30 (and from it BossBigFootExec,
 *               which loads EAX at 0x007938D3), 0x005D1800 and 0x0060CFF0); each redefines the
 *               flags with an `add esp` before it reads them, so the flags `xor eax,eax` sets
 *               are not written.
 *
 * Neither function is wrapped by the runtime or another tweak, and each is its own lifted object,
 * so every caller (the function table's row, the direct calls, the arms) reaches the wrap.
 *
 * Off is the lifted code alone: each wrap asks sa2_tweak_on first and otherwise only calls
 * `__real_` and returns.
 *
 * Risks. Players who want the hints lose them. The stages' object lists still make each Omochao,
 * so where one would have deleted itself, its idle object stays until the stage ends, as with the
 * Codes.lst code. The control objects still set the byte at 0x0174AFFF they set after asking for
 * an Omochao, as with Codes.lst. Neither function touches the save, so the tweak writes nothing to
 * it. The objects' run differs from a run with the tweak off, so replays ignore it (the play-only
 * rule).
 *
 * The Chao Garden is left alone. All 59 dwords in .dataa that hold 0x006C0780 are object-list
 * rows named "MSGER" (58) or "MSGER_F" (1), the stages' Omochao; the gardens' object lists (Hero
 * 0x0121ADB0, Neutral 0x0121C7F0, Dark 0x0125B3D0) and the Chao race's (0x01353338) hold no
 * Omochao_Main row, and nothing else in the image names either function, so the garden's
 * Omochao is another object.
 *
 * What the run says, once each: the first Omochao main skipped, "sa2 tweaks: omochao quiet: first
 * Omochao skipped (object 0x...; it stays and does nothing)"; and each caller of the maker the first
 * time it asks (the eight above, at most 8 named), "sa2 tweaks: omochao quiet: no Omochao made for
 * the call at 0x00499B54 (mode 1)". A guest stack that does not read leaves the function to the
 * lifted body, and says so once.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

static int g_omochao_main_said, g_omochao_stack_said;
static uint32_t g_omochao_callers[8];
static unsigned g_omochao_ncallers;

/* The `ret` both functions end in: the return address at guest [ESP] into EIP, ESP + 4. 0 when
 * the stack does not read (said once; the caller then runs the lifted body). */
static int omochao_ret(void *state, uint32_t *ret) {
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    if (sa2_peek(esp, 4, ret) != SA2_OK) {
        if (!g_omochao_stack_said++)
            fprintf(stderr, "sa2 tweaks: omochao quiet: the guest stack at 0x%08X does not read, so "
                    "this Omochao is left to the game\n", esp);
        return 0;
    }
    sa2_rt_set_gpr(state, SA2_EIP, *ret);
    sa2_rt_set_gpr(state, SA2_ESP, esp + 4u);
    return 1;
}

void *__real_sub_6c0780(void *state, uint32_t pc, void *memory);
void *__real_sub_6c0a90(void *state, uint32_t pc, void *memory);

/* Omochao_Main: the `ret` Codes.lst writes at its entry. */
__attribute__((used, retain))
void *__wrap_sub_6c0780(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_QUIET_OMOCHAO)) return __real_sub_6c0780(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t object = 0, ret;
    (void)sa2_peek(esp + 4u, 4, &object);
    if (!omochao_ret(state, &ret)) return __real_sub_6c0780(state, pc, memory);
    if (!g_omochao_main_said++)
        fprintf(stderr, "sa2 tweaks: omochao quiet: first Omochao skipped (object 0x%08X; it stays "
                "and does nothing)\n", object);
    return memory;
}

/* The Omochao maker: every mode falls through to `xor eax,eax; ret`, as the NOPs make it. */
__attribute__((used, retain))
void *__wrap_sub_6c0a90(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_QUIET_OMOCHAO)) return __real_sub_6c0a90(state, pc, memory);
    const uint32_t mode = sa2_rt_get_gpr(state, SA2_EAX);
    uint32_t ret;
    if (!omochao_ret(state, &ret)) return __real_sub_6c0a90(state, pc, memory);
    sa2_rt_set_gpr(state, SA2_EAX, 0);
    const uint32_t call = ret - 5u;                  /* each caller's `call rel32` is 5 bytes */
    unsigned i = 0;
    while (i < g_omochao_ncallers && g_omochao_callers[i] != call) i++;
    if (i == g_omochao_ncallers && i < sizeof g_omochao_callers / sizeof g_omochao_callers[0]) {
        g_omochao_callers[g_omochao_ncallers++] = call;
        fprintf(stderr, "sa2 tweaks: omochao quiet: no Omochao made for the call at 0x%08X (mode %u)\n",
                call, mode);
    }
    return memory;
}
