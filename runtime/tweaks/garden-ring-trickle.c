/* garden-ring-trickle: in Chao World, one ring is added to the player's ring total (the rings the
 * Black Market spends) every 60 frames, about one a second, up to the game's own cap of 9,999,999
 * (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_530850
 *
 * Where it acts, read from the unpacked image. TotalRings is the int32 at 0x0174B05C (.data). The
 * game adds a stage's rings to it at 0x0044F6E3, 0x004505E5 and 0x004506CE, and each clamps it with
 * `cmp eax,98967Fh; jbe` and a store of 9,999,999 (0x0044F6E8, 0x004505EA, 0x004506D3); the Black
 * Market's setter 0x005A6B30 clamps to the same cap (0x005A6B3D). The save writer copies it into the
 * save block (0x004438BC into 0x01DEC630) and the loaders copy it back (0x00445289, 0x0044673B).
 *
 * The frame hook. ALW_Control_Main (0x00530850, cdecl, the task at [ESP+4]) is the main of the
 * "ALW_Control" task, which ALW_Control (0x00530B80) creates, one at a time (it frees the last one
 * it made first, 0x01A0F94C), from every Chao World area's setup: the lobby (AL_LobbyMaster
 * 0x0054C2A0), the Hero, Dark and Neutral gardens (0x0054D210, 0x0054B730, 0x0054C7B0), the
 * kindergarten (its main at 0x0054E3C0 calls it at 0x0054E4E7; the Black Market is the
 * kindergarten's, ..\stg_kinder\alg_kinder_bl.c), the stadium, the race, karate, the entrance and
 * the transporter's area (0x0054E010, 0x0052D1C0, 0x0052D990, 0x0054DAD0, 0x0054BCE0). The task
 * list runs it once a frame while Chao World plays, and not while the game is paused. It is
 * reached only through the function table (ALW_Control stores it with `mov edi,530850h` at
 * 0x00530BA3 for the task loader 0x0046F680), so every call goes through the wrap. GameStateHandler
 * (0x0043A780), the frame hook the spec first named, is fast-stage-load's wrap; the game loop and
 * GameModeHandler are the harness's and skip-intro's.
 *
 * Before ALW_Control_Main's body, when CurrentLevel (the int16 at 0x01934B70) is 90 (Chao World),
 * the wrap counts the frame; every 60th it adds one ring to TotalRings, clamped to 9,999,999. A
 * total at the cap or above is left alone.
 *
 * The ring counter on screen. The Black Market and the transporter show the total through a task
 * of their own (FTaskExec 0x005A6860, made by 0x005A6AC0, its pointer at 0x01AEDBFC while it is up,
 * cleared by 0x005A6B10 and the Black Market's 0x0058B480). It copies TotalRings once when it
 * starts (0x005A6A85) into its own total (0x01A2787C) and the value it shows (0x01A27880), and
 * rolls the value shown toward its total frame by frame. A purchase or a sale does not reread
 * TotalRings: it computes the new total from the counter's (0x0058AFFA `mov eax,[1A2787Ch]; sub
 * eax,[ecx]`, 0x0058B42C, and the transporter's prize at 0x0057DF43) and stores it in both through
 * 0x005A6B30, while the check that the player can pay reads TotalRings (0x0058AC55). So while the
 * counter is up the wrap moves the counter's total with TotalRings, as 0x005A6B30 does: the new
 * total at 0x01A2787C and its seven digits at 0x01A27884 (least significant first, 0xFF for a
 * leading blank, as 0x005A6520 writes them); the counter then rolls up to it, and what the player
 * pays comes out of the total with the trickled rings in it. 0x005A6B30's sound is not played. If
 * the counter is up and its total is not TotalRings (no game code leaves them apart), the counter
 * is left alone and the ring goes to TotalRings only, said once. The pointer can outlive its task:
 * an area change frees the task list without clearing 0x01AEDBFC. The wrap then writes only the
 * dead counter's three globals, never the task, and 0x005A6AC0 clears and refills them (memset of
 * 0x01A27870, then a copy of TotalRings) before any counter made later shows them.
 *
 * Off is identical: the wrap tests its bit first and otherwise only calls __real_sub_530850.
 *
 * Risks. The rings are real: TotalRings is saved with the game whenever the game saves, so the
 * trickled rings stay in the save and the Black Market's economy is assisted, as the Android note
 * says. ALW_Control_Main running outside Chao World (CurrentLevel not 90) adds nothing and is said
 * once. A guest read or write that fails adds nothing that frame and is said once.
 *
 * What is said: the first ring added, "sa2 tweaks: garden ring trickle: ring total 1200 -> 1201
 * (one ring every 60 frames in Chao World)", and once each: the counter on screen first followed,
 * the total at the cap, a counter whose total is not TotalRings, a frame outside Chao World, a
 * read or a write that fails.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define TRICKLE_TOTAL_RINGS    0x0174B05Cu  /* TotalRings, int32 */
#define TRICKLE_CURRENT_LEVEL  0x01934B70u  /* CurrentLevel, int16 */
#define TRICKLE_COUNTER_TASK   0x01AEDBFCu  /* the ring counter's task while it is up, or 0 */
#define TRICKLE_COUNTER_TOTAL  0x01A2787Cu  /* the counter's total, int32 */
#define TRICKLE_COUNTER_DIGITS 0x01A27884u  /* its 7 digits, least significant first */
#define TRICKLE_CHAO_WORLD     90           /* LevelIDs_ChaoWorld */
#define TRICKLE_CAP            9999999u     /* 0x98967F, every writer's cap */
#define TRICKLE_EVERY          60u          /* frames a ring */
#define TRICKLE_RINGS          1u           /* rings each time */

static unsigned g_trickle_frames;
static int g_trickle_first_said, g_trickle_counter_said, g_trickle_cap_said, g_trickle_apart_said;
static int g_trickle_level_said, g_trickle_read_said, g_trickle_write_said;

void *__real_sub_530850(void *state, uint32_t pc, void *memory);

static int trickle_read(uint32_t addr, uint32_t n, void *out, const char *what) {
    if (sa2_peek(addr, n, out) == SA2_OK) return 1;
    if (!g_trickle_read_said++)
        fprintf(stderr, "sa2 tweaks: garden ring trickle: %s at 0x%08X does not read, so no ring is "
                "added\n", what, addr);
    return 0;
}

static int trickle_write(uint32_t addr, uint32_t n, const void *in, const char *what) {
    if (sa2_poke(addr, n, in) == SA2_OK) return 1;
    if (!g_trickle_write_said++)
        fprintf(stderr, "sa2 tweaks: garden ring trickle: %s at 0x%08X does not write, so no ring is "
                "added\n", what, addr);
    return 0;
}

/* The counter's digits for `v`, as 0x005A6520 writes them. */
static void trickle_digits(uint32_t v, uint8_t d[7]) {
    for (int i = 0; i < 7; i++) {
        d[i] = v == 0 ? 0xFFu : (uint8_t)(v % 10u);
        v /= 10u;
    }
    if (d[0] == 0xFFu) d[0] = 0;
}

static void trickle(void) {
    int16_t level;
    if (!trickle_read(TRICKLE_CURRENT_LEVEL, 2, &level, "CurrentLevel")) return;
    if (level != TRICKLE_CHAO_WORLD) {
        if (!g_trickle_level_said++)
            fprintf(stderr, "sa2 tweaks: garden ring trickle: ALW_Control runs in level %d, outside "
                    "Chao World, so no ring is added there\n", level);
        return;
    }
    if (++g_trickle_frames < TRICKLE_EVERY) return;
    uint32_t total, task;
    if (!trickle_read(TRICKLE_TOTAL_RINGS, 4, &total, "TotalRings")) return;
    if (total >= TRICKLE_CAP) {
        if (!g_trickle_cap_said++)
            fprintf(stderr, "sa2 tweaks: garden ring trickle: the ring total is %u, the game's cap of "
                    "%u, so no ring is added\n", total, TRICKLE_CAP);
        return;
    }
    if (!trickle_read(TRICKLE_COUNTER_TASK, 4, &task, "the ring counter's task")) return;
    int follow = 0;
    if (task != 0) {
        uint32_t shown;
        if (!trickle_read(TRICKLE_COUNTER_TOTAL, 4, &shown, "the ring counter's total")) return;
        follow = shown == total;
        if (!follow && !g_trickle_apart_said++)
            fprintf(stderr, "sa2 tweaks: garden ring trickle: the ring counter (task 0x%08X) holds %u, "
                    "not the ring total %u, so it is left alone and the rings go to the total only\n",
                    task, shown, total);
    }
    const uint32_t next = TRICKLE_CAP - total < TRICKLE_RINGS ? TRICKLE_CAP : total + TRICKLE_RINGS;
    if (!trickle_write(TRICKLE_TOTAL_RINGS, 4, &next, "TotalRings")) return;
    g_trickle_frames = 0;
    if (follow) {
        uint8_t d[7];
        trickle_digits(next, d);
        if (!trickle_write(TRICKLE_COUNTER_TOTAL, 4, &next, "the ring counter's total") ||
            !trickle_write(TRICKLE_COUNTER_DIGITS, 7, d, "the ring counter's digit string")) return;
        if (!g_trickle_counter_said++)
            fprintf(stderr, "sa2 tweaks: garden ring trickle: the ring counter on screen follows the "
                    "total (%u)\n", next);
    }
    if (!g_trickle_first_said++)
        fprintf(stderr, "sa2 tweaks: garden ring trickle: ring total %u -> %u (one ring every %u frames "
                "in Chao World)\n", total, next, TRICKLE_EVERY);
}

__attribute__((used, retain))
void *__wrap_sub_530850(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_GARDEN_RING_TRICKLE)) return __real_sub_530850(state, pc, memory);
    trickle();
    return __real_sub_530850(state, pc, memory);
}
