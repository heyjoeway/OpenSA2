/* pause-no-confirm: Restart and Exit in the pause menu act at once. The game opens a Yes/No box
 * over the pause menu for each, with No highlighted; with the tweak on, the box answers Yes by
 * itself on its first frame, so one press of A on Restart or Exit is enough (runtime/sa2_tweaks.h:
 * the tweaks and their rules).
 *
 * TWEAK_WRAPS: none (it acts in fast-stage-load.c's wrap of sub_43a780)
 *
 * Where the box lives, read from the unpacked image. 0x0043A780 is GameStateHandler, once a frame
 * from GameModeHandler (runtime/tweaks/fast-stage-load.c has its reading). It switches on GameState
 * (the int16 at 0x01934BE0; `cmp eax,32h` at 0x0043A79F) through the jump table at 0x0043C1B8, whose
 * entry 17 is 0x0043AF52: the pause. That arm calls PauseMenu_Main (0x0043D310) and then, unless
 * [0x0174AFD5] is nonzero or the byte [0x0174B007] is 4 or more (both to 0x0043B5E0, the Chao World
 * side, which reads no box), tests the three dwords of the box:
 *
 *   0x01A558BC  the Exit box, open when 1 (`cmp [1A558BCh],ebp` at 0x0043AF97, ebp = 1).
 *   0x01A558B8  the Restart box, open when 1 (0x0043AFA3).
 *   0x01A558C0  the box's answer (the Mod Loader's MenuButtons_SomethingWasPressed): 1 Yes, 0 No.
 *
 * With neither box open the arm reads the menu: ControllerPointers[0] (0x01DEFB60, a PDS_PERIPHERAL
 * pointer) ->press (+0x10) & 0x0C, A or Start, selects PauseSelection (the byte at 0x01933EB1) in a
 * menu of PauseOptionCount (0x01933EB2) items. Selection 1 of 6 stores 1 at 0x01A558B8 and 0 at
 * 0x01A558C0 (0x0043B17A, 0x0043B180): the Restart box. Selection 4 of 5, or 5 of 6, stores 1 at
 * 0x01A558BC and 0 at 0x01A558C0 (0x0043B1B0, 0x0043B1B6): the Exit box. Both then leave through
 * the shared tail (0x0043C0B1); the press that opened a box answers nothing. The entry to the pause
 * (0x0043AE18-0x0043AE24, in state 16's arm) zeroes all three, and nothing else in the image
 * writes 0x01A558B8 or 0x01A558BC.
 *
 * With a box open, the next frames: PauseMenu_Main reads MenuButtons_Pressed (0x01DEFAB0, and
 * 0x01DEFAB4 for player 2 in a two-player game), not the press word: Left (0x40) stores 1 at
 * 0x01A558C0 (0x0043D45F), Right (0x80) stores 0 (0x0043D431), each with a menu sound, and then
 * draws the menu and the box (0x00440AD0, which reads all three words at 0x00440B1D-0x00440BE5).
 * Then the arm (0x0043B2D3 for Exit, 0x0043B417 for Restart) tests player 1's press & 0x0C
 * (0x0043B2E0, 0x0043B424), and in a two-player game player 2's ([0x01DEFB64]+0x10, 0x0043B2FE,
 * 0x0043B442), closes the box (0x0043B308, 0x0043B44C) and, when 0x01A558C0 is 1, runs the Yes
 * path:
 *
 *   Exit (0x0043B31A): in Chao World (CurrentLevel, the int16 at 0x01934B70, = 90) GameState 0x1D
 *     or 0x0C; elsewhere 0x0043D510, then GameState 0x15 (0x0043B3B7).
 *   Restart (0x0043B45E): GameState 0x0D, or 0x0B for levels 34, 35, 37 and 38 (the byte table
 *     0x0043C2D0 indexed by level - 16, the jumps at 0x0043C2C4).
 *
 * B (press & 0x02) closes either box with nothing done (0x0043B3F9, 0x0043B523).
 *
 * The tweak, in the one wrap of 0x0043A780 (fast-stage-load.c's; the tools refuse a second): the
 * wrap calls sa2_pause_no_confirm first when this tweak is on, with fast-stage-load's own function
 * as `real` (which only calls the lifted body unless that tweak is on too; it acts on frames that
 * begin in state 0, and this tweak on frames that begin in 17, so each sees the other's frames
 * unchanged). On a frame that begins with GameState 17, it notes both boxes, calls `real`, and when
 * a box went from 0 to 1 in that call, it is pending. On the next call, if GameState is still 17
 * and that box is still 1, it stores 1 at 0x01A558C0, ORs A (0x04) into player 1's press word,
 * calls `real`, and puts the press word back as it was. The game runs its own Yes path; nothing of
 * the box is done here. The box shows for that one frame, with Yes highlighted. Any other frame,
 * or a pending box that is gone, is the lifted body alone.
 *
 * Off is identical: fast-stage-load.c's wrap does not call this file unless this tweak's bit is
 * on, and this function's own first test calls `real` and returns.
 *
 * Saves: nothing written by the tweak. Exit and Restart do what the game's Yes does, saving
 * included.
 *
 * Risks. A stray A on Restart or Exit acts at once, so the tweak is opt-in. The A it adds lasts
 * one call of GameStateHandler: whatever reads player 1's press later in that frame sees the
 * player's own press, where a real A would still be there. Right pressed on the box's one frame
 * answers No, as it would with the box; B on that frame does not cancel (the A is tested first).
 * That frame is about 16 ms after the A, and MenuButtons_Pressed is a press, not a hold: a Right
 * held from before, or pressed with the A, lands before the box exists. So a player has no way to
 * back out once A is pressed on Restart or Exit, and the player-facing text must not offer one.
 * In a two-player game only player 1's press is given the A; the game takes either player's. A
 * guest word that does not read or write leaves the box to the player and is said once.
 *
 * Each answer is said, for the first 16: "sa2 tweaks: pause without confirm: the Exit box answered
 * Yes (GameState 17 -> 21, answer 1)".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define PNC_GAME_STATE   0x01934BE0u  /* GameState, int16 */
#define PNC_PAUSED       17           /* the pause arm, 0x0043AF52 */
#define PNC_RESTART_BOX  0x01A558B8u  /* dword, 1 = open */
#define PNC_EXIT_BOX     0x01A558BCu  /* dword, 1 = open */
#define PNC_ANSWER       0x01A558C0u  /* dword, 1 = Yes */
#define PNC_CONTROLLER0  0x01DEFB60u  /* ControllerPointers[0], a PDS_PERIPHERAL pointer */
#define PNC_PRESS        0x10u        /* PDS_PERIPHERAL.press */
#define PNC_BUTTON_A     0x04u
#define PNC_SAID         16           /* answers said, at most */

static int g_pnc_pending;             /* a box opened in the last call */
static unsigned g_pnc_answers;
static int g_pnc_read_said, g_pnc_pad_said, g_pnc_write_said, g_pnc_left_said;

/* Called by fast-stage-load.c's wrap of sub_43a780, with that tweak's function as `real`. */
void *sa2_pause_no_confirm(void *state, uint32_t pc, void *memory, void *(*real)(void *, uint32_t, void *));

static int pnc_read(uint16_t *game_state, uint32_t *restart, uint32_t *exit_box) {
    if (sa2_peek(PNC_GAME_STATE, 2, game_state) == SA2_OK &&
        sa2_peek(PNC_RESTART_BOX, 4, restart) == SA2_OK &&
        sa2_peek(PNC_EXIT_BOX, 4, exit_box) == SA2_OK)
        return 1;
    if (!g_pnc_read_said++)
        fprintf(stderr, "sa2 tweaks: pause without confirm: GameState or the box words at 0x%08X do "
                "not read, so the box is left to the player\n", PNC_RESTART_BOX);
    return 0;
}

/* The pending box's frame: Yes chosen and A pressed for one call of `real`. */
static void *pnc_answer(void *state, uint32_t pc, void *memory, void *(*real)(void *, uint32_t, void *),
                        uint32_t box_word) {
    const char *box = box_word == PNC_EXIT_BOX ? "Exit" : "Restart";
    uint32_t pad = 0, press = 0, answer = 0;
    if (sa2_peek(PNC_CONTROLLER0, 4, &pad) != SA2_OK || pad == 0 ||
        sa2_peek(pad + PNC_PRESS, 4, &press) != SA2_OK) {
        if (!g_pnc_pad_said++)
            fprintf(stderr, "sa2 tweaks: pause without confirm: player 1's controller (0x%08X -> "
                    "0x%08X) does not read, so the %s box is left to the player\n",
                    PNC_CONTROLLER0, pad, box);
        return real(state, pc, memory);
    }
    const uint32_t yes = 1, pressed = press | PNC_BUTTON_A;
    if (sa2_peek(PNC_ANSWER, 4, &answer) != SA2_OK || sa2_poke(PNC_ANSWER, 4, &yes) != SA2_OK ||
        sa2_poke(pad + PNC_PRESS, 4, &pressed) != SA2_OK) {
        (void)sa2_poke(PNC_ANSWER, 4, &answer);
        if (!g_pnc_write_said++)
            fprintf(stderr, "sa2 tweaks: pause without confirm: the answer at 0x%08X or the press "
                    "word at 0x%08X does not write, so the %s box is left to the player\n",
                    PNC_ANSWER, pad + PNC_PRESS, box);
        return real(state, pc, memory);
    }
    void *ret = real(state, pc, memory);
    if (sa2_poke(pad + PNC_PRESS, 4, &press) != SA2_OK)
        fprintf(stderr, "sa2 tweaks: pause without confirm: the press word at 0x%08X could not be "
                "put back to 0x%08X\n", pad + PNC_PRESS, press);
    uint16_t after = PNC_PAUSED;
    uint32_t now = 0, open = 0;
    (void)sa2_peek(PNC_GAME_STATE, 2, &after);
    (void)sa2_peek(PNC_ANSWER, 4, &now);
    (void)sa2_peek(box_word, 4, &open);
    if (after == PNC_PAUSED) {
        if (!g_pnc_left_said++)
            fprintf(stderr, open == 1 ? "sa2 tweaks: pause without confirm: the %s box is still open "
                    "after its frame, so it is left to the player\n"
                    : "sa2 tweaks: pause without confirm: the %s box was answered No on its frame "
                    "(Right was pressed), so the game stays paused\n", box);
    } else if (++g_pnc_answers <= PNC_SAID) {
        fprintf(stderr, "sa2 tweaks: pause without confirm: the %s box answered Yes (GameState %u -> "
                "%u, answer %u)%s\n", box, PNC_PAUSED, after, now,
                g_pnc_answers == PNC_SAID ? "; later answers are not said" : "");
    }
    return ret;
}

void *sa2_pause_no_confirm(void *state, uint32_t pc, void *memory, void *(*real)(void *, uint32_t, void *)) {
    if (!sa2_tweak_on(SA2_TWEAK_PAUSE_NO_CONFIRM)) return real(state, pc, memory);
    const int pending = g_pnc_pending;
    g_pnc_pending = 0;
    uint16_t before = 0;
    uint32_t restart = 0, exit_box = 0;
    if (!pnc_read(&before, &restart, &exit_box) || before != PNC_PAUSED) return real(state, pc, memory);
    /* The arm tests the Exit box first (0x0043AF97). */
    if (pending && (exit_box == 1 || restart == 1))
        return pnc_answer(state, pc, memory, real, exit_box == 1 ? PNC_EXIT_BOX : PNC_RESTART_BOX);
    void *ret = real(state, pc, memory);
    uint16_t after = 0;
    uint32_t restart_after = 0, exit_after = 0;
    if (!pnc_read(&after, &restart_after, &exit_after)) return ret;
    g_pnc_pending = after == PNC_PAUSED &&
                    ((restart != 1 && restart_after == 1) || (exit_box != 1 && exit_after == 1));
    return ret;
}
