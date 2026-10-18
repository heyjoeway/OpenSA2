/* infinite-lives: a miss costs no life, so the lives never run out and no game over comes; a 1-up
 * still adds one, and the save keeps the count the player has (runtime/sa2_tweaks.h: the tweaks
 * and their rules).
 *
 * TWEAK_WRAPS: sub_46be30, sub_43d7a0
 *
 * Where a life is lost, read from the unpacked image. The lives are Life_Count, two signed 16-bit
 * counts at 0x0174B024 (player 1) and 0x0174B026 (player 2), and the HUD keeps a copy of each at
 * [[0x01A5A230] + 8] and + 0xA. Every store to 0x0174B024 in .text was read; two of them subtract:
 *
 *   0x0046BEC5  in 0x0046BE30 (EAX = the player), which runs when a player is to be restarted. When
 *               the player's Powerups (CharObj2Base + 0x10) has Dead (0x4000) and the 2P flag
 *               (0x0174AFDE) is 0, it decrements player 1's count, clamps it to 99, and stores the
 *               result in the HUD's copy. Its callers are 0x0043D7A0 (the restart's reset),
 *               GamePlayerMissed (0x0046ABD0), BreathCounterP (0x0046BC60) and 0x0046B900, so
 *               every death in a one-player stage comes through it.
 *   0x0043D7FD  in 0x0043D7A0 itself, only when CurrentLevel (0x01934B70) is 0x46, the kart race,
 *               and a player slot has no MainCharacter: it decrements that slot's count, clamps it
 *               to 99, and stores it in the HUD's copy.
 *
 * The other stores add (the 1-up boxes 0x006DB910, 0x006C97F0, 0x006C8970; the ring bonus at
 * 0x0044CB31) or set a count outright (0x0044CA90, a new game's 4 each); the tweak leaves them
 * alone. Neither wrapped function stores to the counts or the HUD's copies outside the two sites.
 *
 * Each wrap reads both counts and both HUD copies, calls the lifted body, and for each count that
 * came back lower writes back the count and its HUD copy as they were. The lives then never fall,
 * so the game-over tests in GameStateHandler (0x0043ABCD, 0x0043AE53, 0x0043AE86, 0x0043B076) and
 * CountDownTimerForGameOver (0x00451E2A), which take a count at or below zero as out, never pass
 * for a count that was above zero. The
 * 0x0043D7A0 wrap is outside the 0x0046BE30 one (the lifted call between them goes through the
 * wrap), so what it finds lower is the kart race's decrement alone.
 *
 * The save. The save writer (0x004436A0) copies player 1's count into the save at 0x004438A3 (the
 * save's copy at 0x01DEC61C). Nothing here writes a count the game did not hold before the miss, so
 * the save gets the count the player had when the stage began plus the 1-ups since, never a number
 * the tweak made up (the Codes.lst code, a write of 99 each frame, would save 99).
 *
 * Off is identical: the first line of each wrap asks sa2_tweak_on and, when the tweak is off (or a
 * replay is set), only calls `__real_` and returns; nothing is read or written.
 *
 * Risks. Writing the counts back after the call is the same as skipping the decrement only while
 * the call changes nothing else that depends on the count; neither body does (each stores the new
 * count to the HUD's copy and goes on). A count that came back higher is left as it is, so a 1-up
 * reached from inside the call keeps its life. If a count does not read, the tweak cannot act and
 * the life is lost as usual, said once. The tweak holds the count and never raises it: a player
 * who reaches a miss with 0 lives already (a count kept from a run without the tweak) can still
 * meet the game over, and the first restart that finds 0 lives says so once. Two-player battles show no lives and 0x0046BE30 does not
 * decrement there; the kart race's site decrements both slots, and the tweak holds both.
 *
 * The first life held at each site says so once: "sa2 tweaks: lives held: a life lost at
 * 0x0046BEC5 was given back (lives held at 4)".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define LIVES_COUNTS 0x0174B024u   /* Life_Count[2], int16 each: player 1, player 2 */
#define LIVES_HUD    0x01A5A230u   /* the HUD's counters; + 8 and + 0xA hold the lives' copies */

/* What a wrap read before the call: the two counts, and the HUD's copies when they read. */
struct lives_seen {
    int16_t count[2], shown[2];
    uint32_t hud;
    int hud_ok;
};

enum { SITE_RESTART, SITE_KART, SITE_COUNT };
static const char *const SITE[SITE_COUNT] = {"0x0046BEC5", "0x0043D7FD (the kart race)"};
static int g_lives_site_said[SITE_COUNT];
static int g_lives_unread_said, g_lives_hud_said, g_lives_zero_said;

static int lives_read(struct lives_seen *s) {
    if (sa2_peek(LIVES_COUNTS, sizeof s->count, s->count) != SA2_OK) {
        if (!g_lives_unread_said++)
            fprintf(stderr, "sa2 tweaks: lives held: the lives at 0x%08X do not read, so a miss "
                    "costs a life as usual\n", LIVES_COUNTS);
        return 0;
    }
    s->hud_ok = sa2_peek(LIVES_HUD, 4, &s->hud) == SA2_OK && s->hud != 0 &&
                sa2_peek(s->hud + 8, sizeof s->shown, s->shown) == SA2_OK;
    return 1;
}

/* Each count that came back lower than `before` is written back with its HUD copy. */
static void lives_hold(const struct lives_seen *before, int site) {
    int16_t now[2];
    if (sa2_peek(LIVES_COUNTS, sizeof now, now) != SA2_OK) return;
    for (unsigned p = 0; p < 2; p++) {
        if (now[p] >= before->count[p]) continue;
        sa2_poke(LIVES_COUNTS + 2 * p, 2, &before->count[p]);
        if (before->hud_ok)
            sa2_poke(before->hud + 8 + 2 * p, 2, &before->shown[p]);
        else if (!g_lives_hud_said++)
            fprintf(stderr, "sa2 tweaks: lives held: the HUD's lives at [0x%08X] + 8 did not read, "
                    "so the HUD may show one life fewer than the count\n", LIVES_HUD);
        if (!g_lives_site_said[site]++)
            fprintf(stderr, "sa2 tweaks: lives held: a life lost at %s was given back (lives held "
                    "at %d)\n", SITE[site], before->count[p]);
    }
}

void *__real_sub_46be30(void *state, uint32_t pc, void *memory);
void *__real_sub_43d7a0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_46be30(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_INFINITE_LIVES)) return __real_sub_46be30(state, pc, memory);
    struct lives_seen before;
    if (!lives_read(&before)) return __real_sub_46be30(state, pc, memory);
    if (before.count[0] <= 0 && !g_lives_zero_said++)
        fprintf(stderr, "sa2 tweaks: lives held: player 1 has %d lives, which the game takes as out; "
                "the tweak keeps the count and does not raise it, so a miss now can still end in a "
                "game over\n", before.count[0]);
    void *ret = __real_sub_46be30(state, pc, memory);
    lives_hold(&before, SITE_RESTART);
    return ret;
}

__attribute__((used, retain))
void *__wrap_sub_43d7a0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_INFINITE_LIVES)) return __real_sub_43d7a0(state, pc, memory);
    struct lives_seen before;
    if (!lives_read(&before)) return __real_sub_43d7a0(state, pc, memory);
    void *ret = __real_sub_43d7a0(state, pc, memory);
    lives_hold(&before, SITE_KART);
    return ret;
}
