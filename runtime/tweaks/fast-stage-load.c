/* fast-stage-load: a stage starts as soon as its files are loaded. The game's Loading state, which
 * loads nothing and waits on a screen timer before it moves on, is skipped, as the
 * "Fast Stage Loading Times" code (Codes.lst, value 02) skips it (runtime/sa2_tweaks.h: the tweaks
 * and their rules).
 *
 * TWEAK_WRAPS: sub_43a780
 *
 * Where it acts, read from the unpacked image. 0x0043A780 is GameStateHandler, called once a
 * frame from GameModeHandler (0x00434160, direct calls at 0x00434235 and 0x00434944). It switches
 * on GameState, the int16 at 0x01934BE0, through the jump table at 0x0043C1B8:
 *
 *   state 0 (Inactive, arm 0x0043A7AF) calls 0x004728B0, which loads the stage's files, and when
 *               it returns nonzero stores 1 (`mov ecx,1` at 0x0043A888, `mov [GameState],cx` at
 *               0x0043A88D: the byte the Codes.lst entry rewrites is that mov's imm32, file
 *               offset 0x39C89). It then calls 0x004785A0 (the black fade quad) and leaves through
 *               the shared tail at 0x0043C0B1. The arm's other stores are 3 (0x0043A81E,
 *               0x0043A836 via 0x0043C0AA) and 2 (0x0043A86E, 0x0043A8CD); 0x0043A88D is its only 1.
 *   state 1 (Loading, arm 0x0043A8DF) runs the frame's objects and waits for the word
 *               [0x01A558A4] to be 6, which a screen task stores once its own counter passes 90
 *               frames (0x00472653); then calls 0x0041AF20, clears 0x01A558A4 and stores 2. Every
 *               frame it clears 0x01A557E8, the flag the loading tips (0x00428010) draw under.
 *   state 2 (LoadItems, arm 0x0043A9A1) goes on toward the stage, as from the stores of 2 above.
 *
 * The wrap reads GameState before the call. When it was 0 and the call left it 1, the state 0 arm
 * finished the load and took the 0x0043A88D store, and the wrap writes 2 there, the value the
 * Codes.lst entry makes that mov store: the next frame runs LoadItems instead of Loading. Between
 * the store and the return nothing that reads GameState runs in the frame that matters:
 * 0x004785A0 and its callees (scanned six calls deep) do not touch it, and the tail reads it only
 * after 0x00441A50 returns nonzero, which happens only when 0x00441A50 ran 0x00441890 (the leave
 * the stage path); that path ends the frame with GameState 0x21 whatever it was, so the wrap sees
 * no 1 and leaves it. Every other frame, and every other state, is the lifted body alone.
 *
 * Chao World (CurrentLevel, the int16 at 0x01934B70, = 90) keeps its Loading state: its state 1
 * has a branch of its own (0x0043A8E9: 0x0043A470 and the Chao World's own next states) that the
 * Codes.lst entry also skips and nothing here has checked. The first time, the run says so:
 * "sa2 tweaks: fast stage load: Chao World keeps its Loading state".
 *
 * Off is identical: the wrap tests its bit first and otherwise only calls __real_sub_43a780.
 * Nothing is written to the save: GameState is a frame-to-frame state word.
 *
 * Risks. The loading screen and its tips can be cut short. 0x0041AF20, which the Loading state
 * calls once at its end (a virtual call on the object at 0x01A55764), is not called, and the tips
 * flag 0x01A557E8 is not cleared there (GameModeHandler and 0x00427EF0 write it too), both
 * exactly as the Codes.lst entry leaves them. The faster value 3 of the entry's first version,
 * which lets the player move during a stage's opening mini-cutscene, is not offered. A guest read
 * or write that fails leaves the state alone and says so once.
 *
 * Each skip is said, for the first 32: "sa2 tweaks: fast stage load: level 5's Loading state
 * skipped (load 1)".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define FAST_LOAD_GAME_STATE    0x01934BE0u  /* GameState, int16 */
#define FAST_LOAD_CURRENT_LEVEL 0x01934B70u  /* CurrentLevel, int16 */
#define FAST_LOAD_INACTIVE      0            /* the states the header names */
#define FAST_LOAD_LOADING       1
#define FAST_LOAD_LOAD_ITEMS    2
#define FAST_LOAD_CHAO_WORLD    90
#define FAST_LOAD_SAID          32           /* skips said, at most */

static unsigned g_fast_load_skips;
static int g_fast_load_chao_said, g_fast_load_read_said, g_fast_load_write_said;

void *__real_sub_43a780(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_43a780(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_FAST_STAGE_LOAD)) return __real_sub_43a780(state, pc, memory);
    uint16_t before = 0;
    if (sa2_peek(FAST_LOAD_GAME_STATE, 2, &before) != SA2_OK) {
        if (!g_fast_load_read_said++)
            fprintf(stderr, "sa2 tweaks: fast stage load: GameState at 0x%08X does not read, so the "
                    "Loading state is kept\n", FAST_LOAD_GAME_STATE);
        return __real_sub_43a780(state, pc, memory);
    }
    void *ret = __real_sub_43a780(state, pc, memory);
    if (before != FAST_LOAD_INACTIVE) return ret;
    uint16_t after = 0, level = 0;
    if (sa2_peek(FAST_LOAD_GAME_STATE, 2, &after) != SA2_OK ||
        sa2_peek(FAST_LOAD_CURRENT_LEVEL, 2, &level) != SA2_OK) {
        if (!g_fast_load_read_said++)
            fprintf(stderr, "sa2 tweaks: fast stage load: GameState or CurrentLevel does not read "
                    "after the load, so the Loading state is kept\n");
        return ret;
    }
    if (after != FAST_LOAD_LOADING) return ret;
    if (level == FAST_LOAD_CHAO_WORLD) {
        if (!g_fast_load_chao_said++)
            fprintf(stderr, "sa2 tweaks: fast stage load: Chao World keeps its Loading state\n");
        return ret;
    }
    const uint16_t items = FAST_LOAD_LOAD_ITEMS;
    if (sa2_poke(FAST_LOAD_GAME_STATE, 2, &items) != SA2_OK) {
        if (!g_fast_load_write_said++)
            fprintf(stderr, "sa2 tweaks: fast stage load: GameState at 0x%08X does not write, so the "
                    "Loading state is kept\n", FAST_LOAD_GAME_STATE);
        return ret;
    }
    if (++g_fast_load_skips <= FAST_LOAD_SAID)
        fprintf(stderr, "sa2 tweaks: fast stage load: level %u's Loading state skipped (load %u)%s\n",
                level, g_fast_load_skips,
                g_fast_load_skips == FAST_LOAD_SAID ? "; later skips are not said" : "");
    return ret;
}
