/* forgiving-restarts: a restart from the pause menu keeps the rings and the life it would cost, and
 * a miss keeps the score; the timer still starts again, and a miss still costs the rings and a life
 * (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_43c370, sub_43c940
 *
 * Where a restart resets them, read from the unpacked image. The counts: RingCount, two int16 at
 * 0x0174B028 (player 1, player 2); Life_Count, two int16 at 0x0174B024; ScoreP1, an int32 at
 * 0x0174B050. The HUD block at [0x01A5A230] keeps a copy of each: the lives at + 8 and + 0xA, the
 * rings at + 0xC and + 0xE, the score at + 0x18. A restart sets each count back to its backup, the
 * rings to 0x0174B02C and the score to 0x0174B054 (both 0 in a stage begun from the menu; a stage
 * reached from another, as in Cannon's Core, carries in what the player had), at three sites:
 *
 *   0x0043D7A0  when the flag 0x01A558A0 is 0: 0x0044CA50 (the rings and their HUD copies; 50 in
 *               level 0x42), then the score and its HUD copy; then, for each player, 0x0046BE30,
 *               which takes a life when the player's Powerups has Dead (0x4000), and InitPlayer
 *               (0x0043DA40).
 *   0x0043C7C0  when 0x01A558A0 is 0: the same rings and score.
 *   InitPlayer  a player put back at the level's start (no checkpoint), not in a two-player game,
 *               when 0x01A558A0 is not 1: the same; at a checkpoint (0x0043E3F0), AddRings takes the
 *               rings to 0.
 *
 * GameStateHandler (0x0043A780) reaches them through two functions, by the game state:
 *
 *   state 0x0D, the pause menu's Restart, confirmed (0x0043B4B1 and 0x0043B4BE set it; every
 *               level but Cannon's Core's parts, and only with a life left, when the menu has six
 *               lines): sets Dead on player 1 (0x0043BAFA), so the restart costs a life, then calls
 *               RestartStageTeardown (0x0043C370, return address 0x0043BB03), which calls
 *               0x0043D7A0, then RestartInitStageRuntime (0x0043C940, return address 0x0043BB0D),
 *               which calls 0x0043C7C0 and 0x0043D7A0 again. The stage begins again at its start.
 *   state 9,    a miss with a life left (GamePlayerMissed and BreathCounterP set Dead and ask for it
 *               with 0x01A558A4 = 2, which the in-game state reads at 0x0043AB31 and turns into
 *               state 9 at 0x0043AC8B or 0x0043ACA1; with no life left it is 0x0E, the game over):
 *               sets state 5 and calls
 *               RestartStageTeardown (return address 0x0043BA96), and the player is put back at the
 *               last checkpoint.
 *
 * RestartStageTeardown's other caller (state 0x0C, return address 0x0043BB84) and Cannon's Core's
 * restart (state 0x0B, 0x0043C420) are left as the game has them, and so is a two-player game
 * (0x0174AFDE not 0), where a restart keeping one side's rings would change a battle.
 *
 * The wraps. RestartStageTeardown's wrap reads the return address at guest [ESP] on entry (the
 * lifted CALL has pushed it). From the pause menu's call it reads the rings, the lives and their HUD
 * copies; RestartInitStageRuntime's wrap, called next from the same state (it has no other caller),
 * writes them back after its body, the last of the three resets, per player and only where the
 * reset left fewer: the rings as they were (a reset that gives more, as Final Hazard's 50 in level
 * 0x42 does, is kept), and each count of lives that came back lower. From a miss's call it reads
 * the score and its HUD copy, and writes them back after the body when the body changed them. The
 * timer, the checkpoint and everything else the restart does is the game's own.
 *
 * Off is identical: the first line of each wrap asks sa2_tweak_on and, when the tweak is off (or a
 * replay is set), only calls `__real_` and returns; nothing is read or written.
 *
 * The save. The rings and the score are not in the save as such; a stage's result is: the score
 * kept through a miss reaches the results screen (AwardRankForScore, 0x0044F670, ranks it), so a
 * rank and a best score earned with the tweak on are saved as the game saves any, and rings kept
 * through a restart count for a ring mission and for the rings the save totals. The life is a life
 * the player had (as infinite-lives keeps it); the save writer (0x004438A3) copies the count held.
 * Repeated pause restarts can stack rings beyond one run's count (the stage's rings come back each
 * time), and through AddRings's 1-ups (0x0044CE10, one each time a hundred is crossed) lives too;
 * both reach the save, and the Android note says so.
 *
 * Risks. Scores, ranks and missions change, which is the point: a result with the tweak on is an
 * assisted one, and the first kept score says so in the log. A return address not one of the two
 * means a restart the reading did not cover, and the tweak leaves it alone, said once with the
 * address. Rings kept at 100 or more on a 100-ring mission ([0x0174AFE3] == 1, one-player,
 * [0x0174B002] == 0) in a level at or below 0x3C, or in level 0x46, meet the check at
 * 0x0043CF28..0x0043CF4F, which calls 0x0043E6D0 and clears the stage as the game does when the
 * player collects 100 rings there; rings carried over through restarts count toward it. If a count
 * does not read, the tweak leaves that restart to the game, said once.
 *
 * Each kept count is said, the first 16 of each kind: "sa2 tweaks: forgiving restarts: the pause
 * menu's restart kept 37 rings (not 0) and 4 lives (not 3)" (in Final Hazard, "left the game's 50
 * rings (more than the 5 before) and 4 lives (not 3)"), "sa2 tweaks: forgiving restarts: the
 * miss kept the score at 1200 (not 0); a result this run is an assisted one".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define FR_LIVES      0x0174B024u   /* Life_Count[2], int16 each */
#define FR_RINGS      0x0174B028u   /* RingCount[2], int16 each */
#define FR_SCORE      0x0174B050u   /* ScoreP1, int32 */
#define FR_TWO_PLAYER 0x0174AFDEu   /* byte: a two-player game when not 0 */
#define FR_HUD        0x01A5A230u   /* the HUD block: + 8 lives, + 0xC rings, + 0x18 score */

#define FR_PAUSE_TEARDOWN_RET 0x0043BB03u   /* state 0x0D's call of 0x0043C370 */
#define FR_PAUSE_INIT_RET     0x0043BB0Du   /* state 0x0D's call of 0x0043C940 */
#define FR_MISS_TEARDOWN_RET  0x0043BA96u   /* state 9's call of 0x0043C370 */
#define FR_SAID_MAX 16u

/* What the pause menu's restart read before the teardown, for the init's wrap to write back. */
static struct {
    int pending;
    int16_t rings[2], lives[2], hud_rings[2], hud_lives[2];
    uint32_t hud;
    int hud_ok;
} g_fr_pause;

static unsigned g_fr_pause_said, g_fr_miss_said;
static int g_fr_stack_said, g_fr_other_said, g_fr_2p_said, g_fr_unread_said, g_fr_init_said;

static int fr_caller(void *state, const char *wrap, uint32_t *site) {
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    if (sa2_peek(esp, 4, site) == SA2_OK) return 1;
    if (!g_fr_stack_said++)
        fprintf(stderr, "sa2 tweaks: forgiving restarts: the guest stack at 0x%08X does not read in "
                "%s, so the caller is unknown and the restart is left to the game\n", esp, wrap);
    return 0;
}

static void fr_unread(const char *what, uint32_t at) {
    if (!g_fr_unread_said++)
        fprintf(stderr, "sa2 tweaks: forgiving restarts: %s at 0x%08X does not read, so the "
                "restart is left to the game\n", what, at);
}

/* 1 in a one-player game; a two-player game (or a flag that does not read) is said once. */
static int fr_one_player(void) {
    uint8_t two = 0;
    if (sa2_peek(FR_TWO_PLAYER, 1, &two) != SA2_OK) {
        fr_unread("the two-player flag", FR_TWO_PLAYER);
        return 0;
    }
    if (two && !g_fr_2p_said++)
        fprintf(stderr, "sa2 tweaks: forgiving restarts: a two-player game's restart is left to the "
                "game\n");
    return !two;
}

static int fr_hud(uint32_t *hud) {
    return sa2_peek(FR_HUD, 4, hud) == SA2_OK && *hud != 0;
}

static void fr_pause_read(void) {
    g_fr_pause.pending = 0;
    if (!fr_one_player()) return;
    if (sa2_peek(FR_RINGS, 4, g_fr_pause.rings) != SA2_OK) { fr_unread("the rings", FR_RINGS); return; }
    if (sa2_peek(FR_LIVES, 4, g_fr_pause.lives) != SA2_OK) { fr_unread("the lives", FR_LIVES); return; }
    g_fr_pause.hud_ok = fr_hud(&g_fr_pause.hud) &&
                        sa2_peek(g_fr_pause.hud + 8, 4, g_fr_pause.hud_lives) == SA2_OK &&
                        sa2_peek(g_fr_pause.hud + 0xC, 4, g_fr_pause.hud_rings) == SA2_OK;
    g_fr_pause.pending = 1;
}

static void fr_pause_write(void) {
    int16_t rings[2], lives[2];
    if (sa2_peek(FR_RINGS, 4, rings) != SA2_OK || sa2_peek(FR_LIVES, 4, lives) != SA2_OK) {
        fr_unread("the rings or the lives", FR_RINGS);
        return;
    }
    /* Per player, each count goes back only when the reset left fewer: a reset that gives more (Final
     * Hazard's 50 rings, a larger carried-in backup) is the game's and stays. */
    int keep_rings[2], keep_lives[2], any = 0;
    for (unsigned p = 0; p < 2; p++) {
        keep_rings[p] = rings[p] < g_fr_pause.rings[p];
        keep_lives[p] = lives[p] < g_fr_pause.lives[p];
        any |= keep_rings[p] | keep_lives[p];
    }
    if (!any) return;
    for (unsigned p = 0; p < 2; p++) {
        if (keep_rings[p]) {
            sa2_poke(FR_RINGS + 2 * p, 2, &g_fr_pause.rings[p]);
            if (g_fr_pause.hud_ok) sa2_poke(g_fr_pause.hud + 0xC + 2 * p, 2, &g_fr_pause.hud_rings[p]);
        }
        if (keep_lives[p]) {
            sa2_poke(FR_LIVES + 2 * p, 2, &g_fr_pause.lives[p]);
            if (g_fr_pause.hud_ok) sa2_poke(g_fr_pause.hud + 8 + 2 * p, 2, &g_fr_pause.hud_lives[p]);
        }
    }
    if (g_fr_pause_said < FR_SAID_MAX) {
        g_fr_pause_said++;
        /* Player 1's counts as held now: "kept N (not M)" for one written back, the game's own
         * otherwise. */
        char ring_said[96], life_said[48];
        if (keep_rings[0])
            snprintf(ring_said, sizeof ring_said, "kept %d rings (not %d)", g_fr_pause.rings[0],
                     rings[0]);
        else if (rings[0] > g_fr_pause.rings[0])
            snprintf(ring_said, sizeof ring_said, "left the game's %d rings (more than the %d before)",
                     rings[0], g_fr_pause.rings[0]);
        else
            snprintf(ring_said, sizeof ring_said, "kept %d rings", rings[0]);
        if (keep_lives[0])
            snprintf(life_said, sizeof life_said, "%d lives (not %d)", g_fr_pause.lives[0], lives[0]);
        else
            snprintf(life_said, sizeof life_said, "%d lives", lives[0]);
        fprintf(stderr, "sa2 tweaks: forgiving restarts: the pause menu's restart %s and %s%s\n",
                ring_said, life_said,
                g_fr_pause.hud_ok ? "" : "; the HUD's copies did not read, so the HUD may show the "
                "game's counts until they change");
    }
}

/* The miss's restart: the score and its HUD copy as they were before the teardown. */
static void *fr_miss(void *state, uint32_t pc, void *memory, void *(*real)(void *, uint32_t, void *)) {
    if (!fr_one_player()) return real(state, pc, memory);
    int32_t score, shown = 0, now;
    if (sa2_peek(FR_SCORE, 4, &score) != SA2_OK) {
        fr_unread("the score", FR_SCORE);
        return real(state, pc, memory);
    }
    uint32_t hud;
    const int hud_ok = fr_hud(&hud) && sa2_peek(hud + 0x18, 4, &shown) == SA2_OK;
    void *ret = real(state, pc, memory);
    if (sa2_peek(FR_SCORE, 4, &now) != SA2_OK || now == score) return ret;
    sa2_poke(FR_SCORE, 4, &score);
    if (hud_ok) sa2_poke(hud + 0x18, 4, &shown);
    if (g_fr_miss_said < FR_SAID_MAX) {
        g_fr_miss_said++;
        fprintf(stderr, "sa2 tweaks: forgiving restarts: the miss kept the score at %d (not %d); a "
                "result this run is an assisted one%s\n", score, now,
                hud_ok ? "" : "; the HUD's copy did not read, so the HUD may show the game's score "
                "until it changes");
    }
    return ret;
}

void *__real_sub_43c370(void *state, uint32_t pc, void *memory);
void *__real_sub_43c940(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_43c370(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_FORGIVING_RESTARTS)) return __real_sub_43c370(state, pc, memory);
    g_fr_pause.pending = 0;
    uint32_t site;
    if (!fr_caller(state, "RestartStageTeardown (0x0043C370)", &site))
        return __real_sub_43c370(state, pc, memory);
    if (site == FR_MISS_TEARDOWN_RET) return fr_miss(state, pc, memory, __real_sub_43c370);
    if (site == FR_PAUSE_TEARDOWN_RET) fr_pause_read();
    else if (!g_fr_other_said++)
        fprintf(stderr, "sa2 tweaks: forgiving restarts: RestartStageTeardown called from 0x%08X (not "
                "the pause menu's restart or a miss) is left to the game\n", site);
    return __real_sub_43c370(state, pc, memory);
}

__attribute__((used, retain))
void *__wrap_sub_43c940(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_FORGIVING_RESTARTS)) return __real_sub_43c940(state, pc, memory);
    const int pending = g_fr_pause.pending;
    g_fr_pause.pending = 0;
    uint32_t site = 0;
    if (!pending || !fr_caller(state, "RestartInitStageRuntime (0x0043C940)", &site))
        return __real_sub_43c940(state, pc, memory);
    void *ret = __real_sub_43c940(state, pc, memory);
    if (site == FR_PAUSE_INIT_RET) fr_pause_write();
    else if (!g_fr_init_said++)
        fprintf(stderr, "sa2 tweaks: forgiving restarts: RestartInitStageRuntime called from 0x%08X "
                "after the pause menu's teardown, so that restart's rings and life are left to the "
                "game\n", site);
    return ret;
}
