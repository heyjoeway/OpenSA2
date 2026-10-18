/* no-2p-specials: in a 2-player battle neither player earns special attacks, neither by collecting
 * rings nor as the catch-up gift after a miss, so the special-attack slots stay empty
 * (runtime/sa2_tweaks.h: the tweaks and their rules). It is the Mod Loader's Codes.lst code
 * "Disable Special Moves in 2P", made as two wraps, since OpenSA2 never runs the guest code bytes
 * that code patches.
 *
 * TWEAK_WRAPS: sub_44ce10, sub_46aa80
 *
 * Where the specials come from, read from the unpacked image. The special-attack slots are three
 * bytes per player, P1SpecialAttacks at 0x0174AFED and P2SpecialAttacks at 0x0174AFF0 (0x0174AFED
 * + 3 * player), one of them set to 1 for the level of special the player holds; SpecialActivateTimer
 * at 0x0174AFF3 + player is set to 0x3C when a special is given.
 *
 *   0x0044CE10  AddRings (EAX the player, EDX the change). It updates RingCount (0x0174B028 + 2 *
 *               player) and the HUD, calls AddLives (0x0044CB10) only when TwoPlayerMode (the
 *               byte at 0x0174AFDE) is 0, and then, at 0x0044CEBE, `cmp byte [0x0174AFDE],0;
 *               je 0x0044D250` skips the 2P tail. The tail, 0x0044CECB-0x0044D24F, begins
 *               `cmp dword [esi*4 + 0x01DEA6C0],0; je 0x0044D250` (MainCharObj1[player], ESI =
 *               EAX & 1) and otherwise compares the rings before and after with thresholds that
 *               GetHandicapThingMaybe (0x0044CBA0) picks, sets the player's special slot, plays a
 *               voice (0x00443130) and sets the player's SpecialActivateTimer. 0x0044D250 is the
 *               epilogue. Codes.lst writes 90 E9 at 0x0044CEC5, turning the `je` into a `jmp` to
 *               it, so the tail never runs.
 *   0x0046AA80  GiveAssistSpecial (ESI the player): picks a special from the player's character
 *               and the two players' positions and sets the slot and the timer. Three calls, each
 *               reached only when TwoPlayerMode is not 0: 0x0046ACF6 in GamePlayerMissed
 *               (0x0046ABD0), 0x0046BDD9 in BreathCounterP (0x0046BC60) and 0x0046B9C3 in
 *               0x0046B900, each right after the player's rings are taken (AddRings with minus
 *               the count). Codes.lst writes C3 at 0x0046AA80, a `ret` at the entry.
 *
 * The wraps. sub_44ce10: when TwoPlayerMode is not 0, the wrap saves MainCharObj1[EAX & 1], writes
 * 0 there, calls the lifted body and writes the saved pointer back. The tail's first test then
 * takes the `je` to 0x0044D250 that Codes.lst's jump takes. Nothing in AddRings before 0x0044CEC5
 * reads 0x01DEA6C0, the one call before it (AddLives) is not made in 2P, and the path from
 * 0x0044CED3 to the epilogue calls nothing, so no other code runs while the slot reads 0; the
 * ring count and the HUD change as they do with the tweak off. In 1P (TwoPlayerMode 0) the wrap
 * only calls the lifted body. sub_46aa80: the wrap does what a `ret` at the entry does, the return
 * address popped from guest [ESP] into EIP and ESP + 4, and the lifted body is not called; EAX and
 * every other register are left as the caller set them, as the `ret` leaves them
 * (harness/replay/wrap_test_spawn.c's bare_ret is the same return).
 *
 * Off is the lifted code alone: each wrap tests its bit first and otherwise only calls its
 * __real_. 1P play is the same with the tweak on, since neither path is taken there.
 *
 * The first time the tweak acts for a player in each wrap the run says so in a line of its own:
 * "sa2 tweaks: no 2P specials: player 0's ring specials not given" and "sa2 tweaks: no 2P
 * specials: player 1's catch-up special not given". A guest read that fails (TwoPlayerMode, the
 * slot or the guest stack) is said once, and the call is left to the lifted body.
 *
 * Risks. Only 2P balance changes: a player behind, or one who collects rings, gets no special
 * attack. The slots and timers are not saved data, and the wraps write no save data. The slot
 * the AddRings wrap clears holds 0 only for the length of the call, on the game's own thread.
 *
 * One wrap of AddRings. ring-loss-cap also acts on 0x0044CE10, and a function has one wrap (the
 * tools refuse a second by name), so the AddRings half of this tweak is a function of its own,
 * sa2_no_2p_specials_add_rings, which takes the lifted body to call as `real`: it tests its bit
 * first, and otherwise hides MainCharObj1[player] around one call of `real`. This file's
 * __wrap_sub_44ce10 calls it with __real_sub_44ce10. ring-loss-cap's file acts in this wrap (its
 * TWEAK_WRAPS line names it): the wrap hands the call to sa2_ring_loss_cap first when that tweak is
 * on, passing a `real` that goes through sa2_no_2p_specials_add_rings, so ring-loss-cap changes EDX
 * and this tweak hides the slot around the same single call of the lifted body. With only one of
 * the two on, each acts as it does alone.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define NO_2P_TWO_PLAYER_MODE  0x0174AFDEu   /* TwoPlayerMode, a byte */
#define NO_2P_MAIN_CHAR_OBJ1   0x01DEA6C0u   /* MainCharObj1[], a pointer per player */

static int g_no_2p_ring_said[2], g_no_2p_assist_said[2];
static int g_no_2p_mode_said, g_no_2p_slot_said, g_no_2p_stack_said;

void *__real_sub_44ce10(void *state, uint32_t pc, void *memory);
void *__real_sub_46aa80(void *state, uint32_t pc, void *memory);
void *sa2_no_2p_specials_add_rings(void *state, uint32_t pc, void *memory,
                                   void *(*real)(void *, uint32_t, void *));
/* runtime/tweaks/ring-loss-cap.c, which acts in this file's wrap of sub_44ce10. */
void *sa2_ring_loss_cap(void *state, uint32_t pc, void *memory,
                        void *(*real)(void *, uint32_t, void *));

/* AddRings: the 2P tail skipped, by MainCharObj1[player] reading 0 for the length of the call of
 * `real`, the lifted body (or a wrap's way to it). Called by the wrap below, and by a wrap of
 * 0x0044CE10 another tweak's file shares with this one. */
void *sa2_no_2p_specials_add_rings(void *state, uint32_t pc, void *memory,
                                   void *(*real)(void *, uint32_t, void *)) {
    if (!sa2_tweak_on(SA2_TWEAK_NO_2P_SPECIALS)) return real(state, pc, memory);
    uint8_t two_player = 0;
    if (sa2_peek(NO_2P_TWO_PLAYER_MODE, 1, &two_player) != SA2_OK) {
        if (!g_no_2p_mode_said) {
            g_no_2p_mode_said = 1;
            fprintf(stderr, "sa2 tweaks: no 2P specials: TwoPlayerMode at 0x%08X does not read, so "
                    "ring specials are left to the game\n", NO_2P_TWO_PLAYER_MODE);
        }
        return real(state, pc, memory);
    }
    if (two_player == 0) return real(state, pc, memory);
    const uint32_t player = sa2_rt_get_gpr(state, SA2_EAX) & 1u;
    const uint32_t slot = NO_2P_MAIN_CHAR_OBJ1 + 4u * player;
    uint32_t obj = 0;
    const uint32_t zero = 0;
    if (sa2_peek(slot, 4, &obj) != SA2_OK || (obj != 0 && sa2_poke(slot, 4, &zero) != SA2_OK)) {
        if (!g_no_2p_slot_said) {
            g_no_2p_slot_said = 1;
            fprintf(stderr, "sa2 tweaks: no 2P specials: MainCharObj1 at 0x%08X does not read or "
                    "write, so ring specials are left to the game\n", slot);
        }
        return real(state, pc, memory);
    }
    if (obj == 0) return real(state, pc, memory);  /* the tail skips itself */
    void *ret = real(state, pc, memory);
    if (sa2_poke(slot, 4, &obj) != SA2_OK)
        fprintf(stderr, "sa2 tweaks: no 2P specials: MainCharObj1 at 0x%08X could not be put back "
                "to 0x%08X\n", slot, obj);
    if (!g_no_2p_ring_said[player]) {
        g_no_2p_ring_said[player] = 1;
        fprintf(stderr, "sa2 tweaks: no 2P specials: player %u's ring specials not given\n", player);
    }
    return ret;
}

/* The lifted body as ring-loss-cap's function reaches it: through this tweak's AddRings half. */
static void *add_rings_no_2p(void *state, uint32_t pc, void *memory) {
    return sa2_no_2p_specials_add_rings(state, pc, memory, __real_sub_44ce10);
}

__attribute__((used, retain))
void *__wrap_sub_44ce10(void *state, uint32_t pc, void *memory) {
    if (sa2_tweak_on(SA2_TWEAK_RING_LOSS_CAP)) return sa2_ring_loss_cap(state, pc, memory, add_rings_no_2p);
    if (!sa2_tweak_on(SA2_TWEAK_NO_2P_SPECIALS)) return __real_sub_44ce10(state, pc, memory);
    return sa2_no_2p_specials_add_rings(state, pc, memory, __real_sub_44ce10);
}

/* GiveAssistSpecial: a `ret` at the entry. */
__attribute__((used, retain))
void *__wrap_sub_46aa80(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_NO_2P_SPECIALS)) return __real_sub_46aa80(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t back = 0;
    if (sa2_peek(esp, 4, &back) != SA2_OK) {
        if (!g_no_2p_stack_said) {
            g_no_2p_stack_said = 1;
            fprintf(stderr, "sa2 tweaks: no 2P specials: the guest stack at 0x%08X does not read, so "
                    "the catch-up special is left to the game\n", esp);
        }
        return __real_sub_46aa80(state, pc, memory);
    }
    sa2_rt_set_gpr(state, SA2_EIP, back);
    sa2_rt_set_gpr(state, SA2_ESP, esp + 4u);
    const uint32_t player = sa2_rt_get_gpr(state, SA2_ESI) & 1u;
    if (!g_no_2p_assist_said[player]) {
        g_no_2p_assist_said[player] = 1;
        fprintf(stderr, "sa2 tweaks: no 2P specials: player %u's catch-up special not given\n", player);
    }
    return memory;
}
