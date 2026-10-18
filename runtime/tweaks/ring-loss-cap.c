/* ring-loss-cap: a hit costs at most 20 rings instead of all of them. The game scatters at most 20
 * rings on a hit, so with the tweak on a hit takes away just the rings it scatters, and the player
 * can pick them all back up, though not for a second 100-ring extra life (runtime/sa2_tweaks.h: the
 * tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_44ce10
 *
 * Where the rings go, read from the unpacked image. 0x006C1AF0 (DropRings, its player number the
 * stack argument, reached through the characters' function pointers) reads the player's ring count,
 * the word at 0x0174B028 + 2 * (player & 1), into EDX, and EBP = min(rings, 20) (0x006C1B1B-
 * 0x006C1B31), the number of rings to scatter. With no rings it calls 0x0046B110 (KillPlayer) and
 * returns, the mechs (character 6 and 7) excepted. Otherwise it negates EDX and calls 0x0044CE10
 * (AddRings: EAX the player, EDX the change, ring count clamped to 0..999) at 0x006C1B3A, return
 * address 0x006C1B3F, so the player loses every ring; then `test ebp,ebp; jle 0x006C1C7B` and the
 * loop 0x006C1B50-0x006C1C75 scatters EBP ring objects, and the hit sound (0x8014) plays at
 * 0x006C1C7B. A player with 150 rings loses 150 and sees 20 of them scattered.
 *
 * The wrap reads the return address at guest [ESP] on entry (the lifted CALL has pushed it). When
 * the call came from DropRings (0x006C1B3F) and the change in EDX is below -20, it sets EDX to -20
 * and calls the lifted body: the player loses 20 rings, the 20 that EBP, untouched, still scatters.
 * A player with 20 rings or fewer loses them all, as without the tweak, and scatters them all; a
 * player with none dies, as without the tweak (DropRings does not call AddRings then). The cap is
 * the scatter count on purpose, so the loss and the scatter stay equal and EBP needs no change.
 * AddRings's 26 other call sites in the image (ring pickups among them) pass straight through after
 * the compare. Codes.lst's "Don't Lose Rings" patches .text (a NOP over the call at 0x006C1B3A and
 * a jump at 0x006C1B41), which OpenSA2 cannot apply because it never runs guest .text; this wrap
 * makes the change at the call's callee, with the loss capped rather than removed.
 *
 * The 100-ring 1-up is given once per hundred, as without the tweak. AddRings itself gives it: from
 * 0x0044CE62 to 0x0044CE93 it compares the old count's hundreds with the new count's, and when the
 * new ones are higher and TwoPlayerMode (the byte at 0x0174AFDE) is 0 it calls 0x0044CB10 (AddLives:
 * the 1-up jingle 0x1008, Life_Count at 0x0174B024 + 2 * player plus BX, clamped to 99, and the
 * HUD's copy) at 0x0044CEA6 with BX = the hundreds gained. Nothing records a hundred already paid;
 * without the tweak none can be paid twice, because a hit takes every ring and the count must climb
 * from 0 again. With the cap, a hit at 100 leaves 80 and picking the 20 back up would cross 100
 * again, a life each time, up to 99, and the save writer copies Life_Count into the save (0x004438A3).
 * So the wrap keeps g_ring_loss_paid[player], the highest hundred a capped hit has dropped the count
 * from (old count / 100 at that hit, the higher of it and the mark kept). On a call from any other
 * site, in a 1P game, when the count would rise into hundreds at or below that mark, the wrap
 * rewrites the call so AddRings sees only the hundreds not yet paid: with `award` the new count's
 * hundreds less the higher of the old count's and the mark, it writes new - 100 * award into the
 * ring count and sets EDX to 100 * award before calling the lifted body, which then stores the same
 * new count and HUD value it would have and calls AddLives for `award` hundreds, or not at all when
 * that is 0 (no jingle, no life). On the 1P path nothing after 0x0044CEAB reads the old count
 * (its copies at [esp+0x20] and [esp+0x24] are dead once AddRings returns), and a held-back call
 * returns the registers AddRings' path without a 1-up returns; the 2P tail at 0x0044CECB, which
 * reads the old count, is not taken in 1P, and the wrap leaves 2P calls alone. The mark goes back to 0 when an AddRings call finds the
 * count at 0, which is where a hit that takes every ring, a death and a stage's start leave it, so a
 * player back at 0 earns every hundred again, as without the tweak.
 *
 * Off is the lifted code alone: the wrap tests its bit first and only calls __real_sub_44ce10.
 *
 * Each of the first 16 hits it caps or leaves alone is said in a line of its own, "sa2 tweaks: ring
 * loss capped at 20: player 0 lost 20 of 150 rings", then one line saying later hits are not
 * listed; each of the first 16 1-ups it holds back likewise, "sa2 tweaks: ring loss capped at 20:
 * player 0 went from 99 to 100 rings; the 1-up for each hundred up to 100 was given before a capped
 * hit, so 0 of 1 lives are given". A guest stack, ring count or TwoPlayerMode that does not read,
 * or a ring count that does not write, leaves the call as the game makes it, each said once.
 *
 * Risks. The player keeps more rings, so stage scores, ranks and the rings a stage gives the Chao
 * are higher than an unassisted run's, and the game saves its results as usual; the Android
 * description says so. Lives are not: the 1-ups stay one per hundred until the count is back at 0.
 * The wrap writes no save data itself; the only guest write is the ring count on a held-back 1-up,
 * which AddRings overwrites with the same new count it stores without the tweak. A hit at 0 rings
 * (a death, or nothing for a mech) is unchanged, and a ring pickup only loses a 1-up it would have
 * paid a second time; a mech's hit with rings is capped as any other. A count set to a new value
 * without passing 0 or a capped hit (none is known in 1P) keeps the mark until the count is 0.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define RING_LOSS_DROP_RINGS_RET 0x006C1B3Fu   /* after DropRings' call at 0x006C1B3A */
#define RING_LOSS_RINGS          0x0174B028u   /* RingCount[2], int16 each */
#define RING_LOSS_TWO_PLAYER     0x0174AFDEu   /* TwoPlayerMode, a byte */
#define RING_LOSS_MAX            999           /* AddRings' clamp (0x0044CE28) */
#define RING_LOSS_CAP            20            /* DropRings' own scatter count (0x006C1B31) */
#define RING_LOSS_LISTED         16            /* the hits, and the held-back 1-ups, the run lists */

static unsigned g_ring_loss_hits, g_ring_loss_held;
static int g_ring_loss_stack_said, g_ring_loss_rings_said, g_ring_loss_write_said;
static int g_ring_loss_mode_said;
static int g_ring_loss_paid[2];   /* the highest hundred a capped hit dropped the count from */

void *__real_sub_44ce10(void *state, uint32_t pc, void *memory);

/* A call from a site other than DropRings', in a 1P game, that would pay a hundred's 1-up a second
 * time: the ring count and EDX are rewritten so AddRings pays only the hundreds not yet paid. */
static void ring_loss_hold_one_ups(void *state, uint32_t player, int old) {
    const int old_h = old / 100;
    if (g_ring_loss_paid[player] <= old_h) return;
    uint8_t two_player = 0;
    if (sa2_peek(RING_LOSS_TWO_PLAYER, 1, &two_player) != SA2_OK) {
        if (!g_ring_loss_mode_said++)
            fprintf(stderr, "sa2 tweaks: ring loss capped at %d: TwoPlayerMode at 0x%08X does not "
                    "read, so a 1-up a capped hit already paid may be paid again\n", RING_LOSS_CAP,
                    RING_LOSS_TWO_PLAYER);
        return;
    }
    if (two_player) return;   /* no 1-ups in 2P, and the 2P tail reads the old count */
    /* AddRings' sum: the old count zero-extended plus EDX, compared as a signed word. */
    const int16_t sum = (int16_t)(uint16_t)((uint32_t)(uint16_t)old + sa2_rt_get_gpr(state, SA2_EDX));
    const int now = sum > RING_LOSS_MAX ? RING_LOSS_MAX : sum < 0 ? 0 : sum;
    const int due = now / 100 - old_h;
    if (due <= 0) return;
    const int award = now / 100 > g_ring_loss_paid[player] ? now / 100 - g_ring_loss_paid[player] : 0;
    const int16_t start = (int16_t)(now - 100 * award);
    if (sa2_poke(RING_LOSS_RINGS + 2 * player, 2, &start) != SA2_OK) {
        if (!g_ring_loss_write_said++)
            fprintf(stderr, "sa2 tweaks: ring loss capped at %d: the ring count at 0x%08X does not "
                    "write, so a 1-up a capped hit already paid may be paid again\n", RING_LOSS_CAP,
                    RING_LOSS_RINGS + 2 * player);
        return;
    }
    sa2_rt_set_gpr(state, SA2_EDX, (uint32_t)(100 * award));
    const unsigned held = g_ring_loss_held;
    if (held <= RING_LOSS_LISTED) g_ring_loss_held++;
    if (held < RING_LOSS_LISTED)
        fprintf(stderr, "sa2 tweaks: ring loss capped at %d: player %u went from %d to %d rings; the "
                "1-up for each hundred up to %d was given before a capped hit, so %d of %d lives are "
                "given\n", RING_LOSS_CAP, player, old, now, 100 * g_ring_loss_paid[player], award, due);
    else if (held == RING_LOSS_LISTED)
        fprintf(stderr, "sa2 tweaks: ring loss capped at %d: %d held-back 1-ups listed; later ones "
                "are not\n", RING_LOSS_CAP, RING_LOSS_LISTED);
}

__attribute__((used, retain))
void *__wrap_sub_44ce10(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_RING_LOSS_CAP)) return __real_sub_44ce10(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0;
    if (sa2_peek(esp, 4, &site) != SA2_OK) {
        if (!g_ring_loss_stack_said++)
            fprintf(stderr, "sa2 tweaks: ring loss capped at %d: the guest stack at 0x%08X does not "
                    "read, so the caller is unknown and the ring change is left alone\n",
                    RING_LOSS_CAP, esp);
        return __real_sub_44ce10(state, pc, memory);
    }
    const uint32_t player = sa2_rt_get_gpr(state, SA2_EAX) & 1u;
    int16_t old = 0;
    if (sa2_peek(RING_LOSS_RINGS + 2 * player, 2, &old) != SA2_OK) {
        if (!g_ring_loss_rings_said++)
            fprintf(stderr, "sa2 tweaks: ring loss capped at %d: the ring count at 0x%08X does not "
                    "read, so the ring change is left alone\n", RING_LOSS_CAP,
                    RING_LOSS_RINGS + 2 * player);
        return __real_sub_44ce10(state, pc, memory);
    }
    if (old == 0) g_ring_loss_paid[player] = 0;
    if (site != RING_LOSS_DROP_RINGS_RET) {
        ring_loss_hold_one_ups(state, player, old);
        return __real_sub_44ce10(state, pc, memory);
    }
    const int32_t change = (int32_t)sa2_rt_get_gpr(state, SA2_EDX);
    const int32_t lost = change < -RING_LOSS_CAP ? RING_LOSS_CAP : -change;
    if (change < -RING_LOSS_CAP) {
        sa2_rt_set_gpr(state, SA2_EDX, (uint32_t)-RING_LOSS_CAP);
        if (old / 100 > g_ring_loss_paid[player]) g_ring_loss_paid[player] = old / 100;
    }
    const unsigned hit = g_ring_loss_hits;
    if (hit <= RING_LOSS_LISTED) g_ring_loss_hits++;
    if (hit < RING_LOSS_LISTED)
        fprintf(stderr, "sa2 tweaks: ring loss capped at %d: player %u lost %d of %d rings\n",
                RING_LOSS_CAP, player, lost, -change);
    else if (hit == RING_LOSS_LISTED)
        fprintf(stderr, "sa2 tweaks: ring loss capped at %d: %d hits listed; later hits are not\n",
                RING_LOSS_CAP, RING_LOSS_LISTED);
    return __real_sub_44ce10(state, pc, memory);
}
