/* no-lives-bonus: reaching 100 rings (and 200, 300 and so on) no longer gives an extra life, and
 * the 1-up jingle for it is not played; 1-up item boxes and the game's other lives still count
 * (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_44cb10
 *
 * Where the bonus comes from, read from the unpacked image. AddLives (0x0044CB10, EAX = the player,
 * BX = how many lives) plays sound 0x1008, the 1-up jingle, when BX > 0 (0x0044CB23), adds BX to
 * that player's Life_Count (0x0174B024 + 2p), clamps it to 0..99 and stores it in the HUD's copy at
 * [[0x01A5A230] + 8 + 2p], then returns with a bare RET (no stack arguments). It has three callers,
 * each linked against it directly:
 *
 *   0x0044CEA6  AddRings (0x0044CE10), after it has added EDX to the player's rings at 0x0174B028
 *               + 2p and clamped them to 0..999: when the ring count's hundreds went up and the 2P
 *               flag (0x0174AFDE) is 0, BX = the hundreds gained and EAX = the player. Return
 *               address 0x0044CEAB, which only reloads the rings into the HUD and, with the 2P
 *               flag 0, goes to the epilogue at 0x0044D250; no register AddLives leaves is read.
 *               This is Codes.lst's "writenop 0x0044CEA6 5".
 *   0x0048933C  MinimalCounterExecutor (0x00489240), return address 0x00489341; left alone.
 *   0x00737B13  GiveDiggableItem (0x00737820), return address 0x00737B18; left alone.
 *
 * The 1-up item boxes (0x006DB910, 0x006C97F0, 0x006C8970) add to Life_Count themselves and do not
 * come here; they are unchanged.
 *
 * The wrap reads the return address at guest [ESP] on entry (the lifted CALL has pushed it). When it
 * is 0x0044CEAB, the wrap does what the RET at the end of AddLives does and nothing else: EIP = that
 * address, ESP + 4. AddRings then goes on as it does with the call removed: no jingle, no life, the
 * HUD's lives unchanged. Any other caller gets the lifted body.
 *
 * The save. The tweak writes nothing; the save writer (0x004436A0) copies the count the player has,
 * which is only short of the lives the ring bonus would have given.
 *
 * Off is identical: the first line of the wrap asks sa2_tweak_on and, when the tweak is off (or a
 * replay is set), only calls `__real_` and returns; nothing is read.
 *
 * Risks. Skipping the body leaves EAX and EDX as AddRings had them, where the call would have left
 * them changed; the code after 0x0044CEAB writes EAX and ECX before reading them, then returns
 * through 0x0044D250 without reading EDX, EBX or the flags, so nothing the game does next sees the
 * difference. If the guest stack does not read, the caller is unknown and the life is given as usual,
 * said once.
 *
 * The run says what the tweak did, once each: "sa2 tweaks: ring 1-ups off: the extra life for 100
 * rings was not given (player 1 has 104 rings and stays at 4 lives)", and, the first time another
 * caller gives a life, "sa2 tweaks: ring 1-ups off: the life from GiveDiggableItem (0x00737B18) is
 * not the ring count's, so it is given".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define LIVES_BONUS_RINGS_RET 0x0044CEABu   /* after AddRings' call at 0x0044CEA6 */
#define LIVES_BONUS_LIVES     0x0174B024u   /* Life_Count[2], int16 each */
#define LIVES_BONUS_RINGS     0x0174B028u   /* RingCount[2], int16 each */

/* AddLives' other callers, by return address, which keep their lives. */
static const struct {
    uint32_t ret;
    const char *name;
} LIVES_BONUS_OTHERS[] = {
    {0x00489341u, "MinimalCounterExecutor"},
    {0x00737B18u, "GiveDiggableItem"},
};
#define LIVES_BONUS_NOTHERS (sizeof LIVES_BONUS_OTHERS / sizeof LIVES_BONUS_OTHERS[0])

static int g_lives_bonus_said, g_lives_bonus_stack_said, g_lives_bonus_unknown_said;
static int g_lives_bonus_other_said[LIVES_BONUS_NOTHERS];

static void lives_bonus_other(uint32_t site) {
    for (unsigned i = 0; i < LIVES_BONUS_NOTHERS; i++) {
        if (LIVES_BONUS_OTHERS[i].ret != site) continue;
        if (!g_lives_bonus_other_said[i]++)
            fprintf(stderr, "sa2 tweaks: ring 1-ups off: the life from %s (0x%08X) is not the ring "
                    "count's, so it is given\n", LIVES_BONUS_OTHERS[i].name, site);
        return;
    }
    if (!g_lives_bonus_unknown_said++)
        fprintf(stderr, "sa2 tweaks: ring 1-ups off: AddLives was called from 0x%08X, which is not "
                "the ring count's call, so the life is given\n", site);
}

void *__real_sub_44cb10(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_44cb10(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_NO_LIVES_BONUS)) return __real_sub_44cb10(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0;
    if (sa2_peek(esp, 4, &site) != SA2_OK) {
        if (!g_lives_bonus_stack_said++)
            fprintf(stderr, "sa2 tweaks: ring 1-ups off: the guest stack at 0x%08X does not read, so "
                    "the caller is unknown and the life is given\n", esp);
        return __real_sub_44cb10(state, pc, memory);
    }
    if (site != LIVES_BONUS_RINGS_RET) {
        lives_bonus_other(site);
        return __real_sub_44cb10(state, pc, memory);
    }
    if (!g_lives_bonus_said++) {
        const uint32_t p = sa2_rt_get_gpr(state, SA2_EAX) & 1u;
        int16_t rings = 0, lives = 0;
        if (sa2_peek(LIVES_BONUS_RINGS + 2 * p, 2, &rings) == SA2_OK &&
            sa2_peek(LIVES_BONUS_LIVES + 2 * p, 2, &lives) == SA2_OK)
            fprintf(stderr, "sa2 tweaks: ring 1-ups off: the extra life for 100 rings was not given "
                    "(player %u has %d rings and stays at %d lives)\n", p + 1, rings, lives);
        else
            fprintf(stderr, "sa2 tweaks: ring 1-ups off: the extra life for 100 rings was not given\n");
    }
    sa2_rt_set_gpr(state, SA2_EIP, site);   /* AddLives' RET, and nothing before it */
    sa2_rt_set_gpr(state, SA2_ESP, esp + 4u);
    return memory;
}
