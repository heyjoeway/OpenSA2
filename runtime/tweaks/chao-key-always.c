/* chao-key-always: after a stage cleared in one-player play the game goes to Chao World, as it
 * does when the player has found the stage's Chao Key, without the key being found; after the
 * visit the game carries on as it does after a found key (runtime/sa2_tweaks.h: the tweaks and
 * their rules). The Codes.lst entry "Have Chao Garden Key" does this by writing HaveChaoKey every
 * frame.
 *
 * TWEAK_WRAPS: sub_43c4d0
 *
 * Where it acts, read from the unpacked image. HaveChaoKey is the byte at 0x0174B004 (.data);
 * ChaoKeyTask (0x006E99D0) sets it to 1 when the key is picked up, and the leave-the-stage path
 * of GameStateHandler (0x0043C152) clears it. The one place that turns it into a route is
 * SetNextLevel (0x0043C4D0, no arguments), whose only callers are two arms of GameStateHandler
 * (0x0043A780), each linked against it directly: Ingame (state 16, 0x0043AB74) and state 29
 * (0x0043BDFA), each when the word at 0x01A558A4 is 1, which it is when a stage is cleared and
 * its results are done. SetNextLevel reads TwoPlayerMode (0x0174AFDE):
 *
 *   two-player  it clears HaveChaoKey (0x0043C4EA) before anything reads it.
 *   one-player  0x0043C6BB tests HaveChaoKey. When set, and the stage is not one of Cannon's
 *               Core's later sections (levels 35 to 38) or the byte at 0x0174B003 is 1, it stores
 *               GameState = 11 (Exit_3) and NextLevel (int16 at 0x01934BEC) = 90, Chao World
 *               (0x0043C6DB-0x0043C6FD), and calls 0x00437260. Otherwise it stores GameState = 8
 *               and, in Cannon's Core, the next section's level, calling nothing (0x0043C70C).
 *
 * The wrap runs before SetNextLevel's body. In one-player play, outside Chao World (CurrentLevel,
 * the int16 at 0x01934B70, read by its low byte as the Codes.lst entry reads it, is not 90), with
 * the key not found, it sets HaveChaoKey to 1 and calls the body. When the body took the Chao
 * World route (GameState 11 and NextLevel 90 after it), the key stays held, so what the game does
 * next is what it does with a found key: state 11's white fade (0x0043BB89), the story's step
 * (0x0043AB7E, 0x0043BE0A) and GameModeHandler's choice (0x004343BE) all read the key that a
 * found key leaves. When the body did not take it (a Cannon's Core section before the last), the
 * wrap puts the key back to 0 before anything else runs, so that case is the lifted code alone.
 * A found key, a two-player game and Chao World's own end are left alone.
 *
 * Unlike the Codes.lst entry, which holds the key in every frame of every stage, the key is held
 * only from the routing decision on: the key object in the stage is untouched (ObjectChaoKeyExec,
 * 0x006E9DE0, still sees the key missing and the player can still pick it up), and so is the
 * key's other reader while the stage is played (0x0044E940).
 *
 * Off is identical: the wrap tests its bit first and otherwise only calls __real_sub_43c4d0.
 *
 * Risks. Every stage cleared in one-player play, story or stage select, bosses and kart races
 * included, is followed by a Chao World visit, which some players will not want. The game's save
 * routine (0x004436A0) reads the key: when it saves while the key is held (0x00443FFA), it marks
 * the save the way a found key does (the byte at 0x01DEF829 plus the character's index, inside the
 * save block at 0x01DEC600), so the save can record a Chao Key the player did not find. A guest
 * read or write that fails leaves the key alone and says so once. GameStateHandler, the function
 * the spec first named, is wrapped by fast-stage-load; SetNextLevel is the narrower point.
 *
 * Each case is said, the first 32 times: "sa2 tweaks: chao key always: level 13 cleared without
 * the Chao Key: going to Chao World", or "... level 36: no Chao World route from this section, so
 * the key is put back", and once each: "a two-player game is left alone", "Chao World's own end
 * is left alone", "level 13 cleared with the Chao Key found".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define CHAO_KEY_HAVE           0x0174B004u  /* HaveChaoKey, byte */
#define CHAO_KEY_TWO_PLAYER     0x0174AFDEu  /* TwoPlayerMode, byte */
#define CHAO_KEY_CURRENT_LEVEL  0x01934B70u  /* CurrentLevel, int16; its low byte */
#define CHAO_KEY_NEXT_LEVEL     0x01934BECu  /* NextLevel, int16 */
#define CHAO_KEY_GAME_STATE     0x01934BE0u  /* GameState, int16 */
#define CHAO_KEY_CHAO_WORLD     90           /* LevelIDs_ChaoWorld */
#define CHAO_KEY_EXIT_3         11           /* GameStates_Exit_3, the Chao World route's state */
#define CHAO_KEY_SAID           32           /* routes said, at most */

static unsigned g_chao_key_said;
static int g_chao_key_two_player_said, g_chao_key_chao_world_said, g_chao_key_found_said;
static int g_chao_key_read_said, g_chao_key_write_said;

void *__real_sub_43c4d0(void *state, uint32_t pc, void *memory);

static void chao_key_say(const char *what, unsigned level) {
    if (++g_chao_key_said > CHAO_KEY_SAID) return;
    fprintf(stderr, "sa2 tweaks: chao key always: level %u%s%s\n", level, what,
            g_chao_key_said == CHAO_KEY_SAID ? "; later ones are not said" : "");
}

__attribute__((used, retain))
void *__wrap_sub_43c4d0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_CHAO_KEY_ALWAYS)) return __real_sub_43c4d0(state, pc, memory);
    uint8_t two_player = 0, level = 0, key = 0;
    if (sa2_peek(CHAO_KEY_TWO_PLAYER, 1, &two_player) != SA2_OK ||
        sa2_peek(CHAO_KEY_CURRENT_LEVEL, 1, &level) != SA2_OK ||
        sa2_peek(CHAO_KEY_HAVE, 1, &key) != SA2_OK) {
        if (!g_chao_key_read_said++)
            fprintf(stderr, "sa2 tweaks: chao key always: TwoPlayerMode, CurrentLevel or HaveChaoKey "
                    "does not read, so the key is left alone\n");
        return __real_sub_43c4d0(state, pc, memory);
    }
    if (two_player) {
        if (!g_chao_key_two_player_said++)
            fprintf(stderr, "sa2 tweaks: chao key always: a two-player game is left alone\n");
        return __real_sub_43c4d0(state, pc, memory);
    }
    if (level == CHAO_KEY_CHAO_WORLD) {
        if (!g_chao_key_chao_world_said++)
            fprintf(stderr, "sa2 tweaks: chao key always: Chao World's own end is left alone\n");
        return __real_sub_43c4d0(state, pc, memory);
    }
    if (key) {
        if (!g_chao_key_found_said++)
            fprintf(stderr, "sa2 tweaks: chao key always: level %u cleared with the Chao Key found\n",
                    level);
        return __real_sub_43c4d0(state, pc, memory);
    }
    const uint8_t held = 1, missing = 0;
    if (sa2_poke(CHAO_KEY_HAVE, 1, &held) != SA2_OK) {
        if (!g_chao_key_write_said++)
            fprintf(stderr, "sa2 tweaks: chao key always: HaveChaoKey at 0x%08X does not write, so "
                    "the key is left alone\n", CHAO_KEY_HAVE);
        return __real_sub_43c4d0(state, pc, memory);
    }
    void *ret = __real_sub_43c4d0(state, pc, memory);
    uint16_t game_state = 0, next_level = 0;
    if (sa2_peek(CHAO_KEY_GAME_STATE, 2, &game_state) == SA2_OK &&
        sa2_peek(CHAO_KEY_NEXT_LEVEL, 2, &next_level) == SA2_OK &&
        game_state == CHAO_KEY_EXIT_3 && next_level == CHAO_KEY_CHAO_WORLD) {
        chao_key_say(" cleared without the Chao Key: going to Chao World", level);
        return ret;
    }
    uint8_t now = 0;
    if (sa2_peek(CHAO_KEY_HAVE, 1, &now) == SA2_OK && now == held &&
        sa2_poke(CHAO_KEY_HAVE, 1, &missing) == SA2_OK) {
        chao_key_say(": no Chao World route from this section, so the key is put back", level);
        return ret;
    }
    if (!g_chao_key_write_said++)
        fprintf(stderr, "sa2 tweaks: chao key always: level %u took no Chao World route and "
                "HaveChaoKey could not be put back\n", level);
    return ret;
}
