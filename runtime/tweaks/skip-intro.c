/* skip-intro: the game starts at the title screen. The boot's company logos and intro (game mode
 * 0x15, LoadIntro, then 0x16) and the opening cutscene the title then plays at once (event 360)
 * are skipped; the title's idle opening, and everything after the title, are left as they are
 * (runtime/sa2_tweaks.h: the tweaks and their rules). It is the Mod Loader's "Skip Intro"
 * (SA2ModLoader dllmain.cpp: the imm32 at 0x0043459A made 13, the call at 0x00434778 hooked, and
 * LoadTipsTexs called), done with one wrap of the game mode handler, since OpenSA2 never runs a
 * patched code byte.
 *
 * TWEAK_WRAPS: sub_434160
 *
 * Where, read from the unpacked image. 0x00434160 is GameModeHandler: it switches on GameMode
 * (0x0174B040) through the jump table at 0x00434BD4 and returns through 0x00434213 (EAX = 0).
 *
 *   Case 13 (0x00434515), the title's load. On a visit with [0x0174B019] set (1 in the image's
 *   data), [0x01A559E8] = 0 and the visit counter [0x0174B090] a multiple of 3 (the first visit,
 *   then every third), and [0x01AEE2F8] != 1, it stores `mov [GameMode],15h` at 0x00434594 and
 *   NextGameMode (0x0174B044) = 13 at 0x0043459E, then counter + 1, [0x01AEE2F8] = 0,
 *   [0x0174B048] = 13, [0x0174B078] = 1, [0x0174B006] = 0, and returns; nothing between the store
 *   and the return reads GameMode. Only 0x00434594 and 0x004353EC (0x004353D0, a demo's end)
 *   store 0x15 to GameMode. The wrap reads GameMode before the call; when it was 13 and is 0x15
 *   after, it writes 13: the Mod Loader's patched immediate, every other store of the block
 *   unchanged. The next visit (counter 1) takes the title path, 0x004345C5: GameMode 14.
 *
 *   Case 14 (0x004346DF), the title. The title menu's result + 2 indexes the table at 0x00434C60;
 *   result 1 is the arm 0x00434764, which calls 0x00454CC0, 0x00441A00, 0x0044C320, 0x0042F990
 *   and 0x00434CD0 (the call at 0x00434778 the Mod Loader hooks), then, unless [0x0174B01B] is
 *   set, GameMode = NextGameMode (0x0043479A-0x004347A0) and returns. The first title entry of a
 *   boot plays the opening at once: case 13's first visit sets [0x0174B017] = 0xFF (0x00434533),
 *   the title's module load turns that into [0x01D7B9D0] = 1 (0x006707B0), and Menu_TitleScreen
 *   (0x00671BA0) then goes straight to its idle state, whose entry 0 in the table at 0x00C76648
 *   is -1, "the opening": 0x0067E690 sets the story type byte [0x01DEB320] = 4 and NextGameMode =
 *   12 (LoadStory). Type 4 selects IntroStoryEntries (0x0173A5E0, event 360) in 0x004586A0
 *   (jump table 0x004588A8, case 4 = 0x0045871B). When GameMode was 14 before the call, is 12
 *   after, the type is 4, and this is the first time the title has left for a story in the run,
 *   the wrap writes GameMode = 13 and NextGameMode = 13: the state the Mod Loader's hook leaves
 *   (it makes NextGameMode 13 before the copy). Case 13 (counter 2) then loads the title again,
 *   which now waits for the player: [0x0174B017] is 0 by then. The Mod Loader changes the first
 *   12 of any kind; the type test keeps a player's Story start, which is never type 4, from
 *   being turned back. The title's later idle openings (entry 4 of the same table, after
 *   1200-frame waits) are left alone, as the Mod Loader leaves them, and said once.
 *
 *   LoadTipsTexs (0x00427EF0, ESI = the TextLanguage byte at 0x0174AFD1) loads the loading
 *   screen's tips textures and sets [0x0093BC5A] = 1. Its one caller is execSegalogo's call at
 *   0x0079234C, in the logos the skip removes; without it the first loading screen's text path
 *   (0x00428010) indexes the empty vector at 0x01AF1110 and ends in the CRT's bounds failure
 *   (0x007A7E65). So on the first logos skip the wrap calls it, as the Mod Loader does at its
 *   start and harness/replay/wrap_test_spawn.c after the data DLL loader: a sentinel return
 *   address pushed on the guest stack, the lifted body run to its `ret`, and every register put
 *   back. A return anywhere but the sentinel stops the run by name (SA2_TRAP_CONTROL_LEFT). It
 *   is called whatever other tweak is on: a tweak that skips the loading screen leaves the
 *   textures unused, and the vanilla game loads them too.
 *
 * The test spawn. The stage replays' harness (tools/s4/stage_run.py) used to wrap this function
 * itself; a function is wrapped once, so the wrap is the runtime's here, and the harness's rule
 * (13 to 0x15 becomes 13 to 4, the Mod Loader's --level start) is sa2_harness_gamemode_after,
 * which harness/replay/wrap_test_spawn.c defines and this file defines as a weak no-op for every
 * other link. It runs first, whether the tweak is on or off, with GameMode before and after the
 * lifted body, as the harness's own wrap saw them; the tweak then reads GameMode again.
 *
 * Off is identical: with the tweak off the wrap reads GameMode twice, calls the lifted body and
 * the hook (a no-op outside a stage replay's link), and writes nothing.
 *
 * Risks. The boot reaches the title about 3100 frames sooner, so a boot's frame counts change;
 * replays ignore tweaks. The save is untouched: no save data is read or written here. Before
 * the title, the skip leaves out what the logos and the opening load and do besides
 * LoadTipsTexs, as the Mod Loader does. The lines the run prints: "sa2 tweaks: intro skipped:
 * the logos and intro skipped; the loading tips' textures loaded in their place", "... the
 * opening cutscene (story type 4) skipped; the title screen follows", and once each, "... the
 * logos skipped again on a return to the title" and "... the title's idle opening plays (only
 * the boot's is skipped)".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_trap.h"
#include "sa2_tweaks.h"

#define SKIP_INTRO_GAME_MODE      0x0174B040u
#define SKIP_INTRO_NEXT_GAME_MODE 0x0174B044u
#define SKIP_INTRO_STORY_TYPE     0x01DEB320u   /* byte; 4 = IntroStoryEntries, the opening */
#define SKIP_INTRO_TEXT_LANGUAGE  0x0174AFD1u   /* byte; LoadTipsTexs takes it in ESI */
#define SKIP_INTRO_LOAD_TIPS      0x00427EF0u   /* LoadTipsTexs */

#define MODE_STORY        12u
#define MODE_TITLE_LOAD   13u
#define MODE_TITLE        14u
#define MODE_LOAD_INTRO   0x15u
#define STORY_TYPE_INTRO  4u

void *__real_sub_434160(void *state, uint32_t pc, void *memory);
void *sub_427ef0(void *state, uint32_t pc, void *memory);

/* The stage replays' test spawn (harness/replay/wrap_test_spawn.c) defines this; elsewhere it
 * does nothing. */
__attribute__((weak)) void sa2_harness_gamemode_after(uint32_t before, uint32_t after) {
    (void)before;
    (void)after;
}

static int g_skip_intro_logos, g_skip_intro_title_left, g_skip_intro_idle_said;

static uint32_t game_mode(void) {
    uint32_t v = 0;
    sa2_peek(SKIP_INTRO_GAME_MODE, 4, &v);
    return v;
}

/* LoadTipsTexs from inside the wrap: harness/replay/wrap_test_spawn.c's guest_call. */
static void *load_tips_texs(void *s, void *m) {
    uint32_t saved[SA2_GPR_COUNT];
    for (int i = 0; i < SA2_GPR_COUNT; ++i) saved[i] = sa2_rt_get_gpr(s, i);
    uint8_t lang = 0;
    sa2_peek(SKIP_INTRO_TEXT_LANGUAGE, 1, &lang);
    const uint32_t sentinel = SA2_CALL_SENTINEL;
    const uint32_t esp = saved[SA2_ESP] - 4u;
    sa2_poke(esp, 4, &sentinel);
    sa2_rt_set_gpr(s, SA2_ESI, lang);
    sa2_rt_set_gpr(s, SA2_ESP, esp);
    sa2_rt_set_gpr(s, SA2_EIP, SKIP_INTRO_LOAD_TIPS);
    m = sub_427ef0(s, SKIP_INTRO_LOAD_TIPS, m);
    const uint32_t eip_after = sa2_rt_get_gpr(s, SA2_EIP), esp_after = sa2_rt_get_gpr(s, SA2_ESP);
    if (eip_after != sentinel || esp_after != esp + 4u) {
        sa2_trap_note("sa2 tweaks: intro skipped: LoadTipsTexs (%#010x) returned to %#010x with esp "
                      "%#010x, not to the sentinel with esp %#010x", SKIP_INTRO_LOAD_TIPS, eip_after,
                      esp_after, esp + 4u);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, eip_after);
    }
    for (int i = 0; i < SA2_GPR_COUNT; ++i) sa2_rt_set_gpr(s, i, saved[i]);
    return m;
}

__attribute__((used, retain))
void *__wrap_sub_434160(void *state, uint32_t pc, void *memory) {
    const uint32_t before = game_mode();
    memory = __real_sub_434160(state, pc, memory);
    sa2_harness_gamemode_after(before, game_mode());
    if (!sa2_tweak_on(SA2_TWEAK_SKIP_INTRO)) return memory;
    const uint32_t after = game_mode();
    if (before == MODE_TITLE_LOAD && after == MODE_LOAD_INTRO) {
        const uint32_t title_load = MODE_TITLE_LOAD;
        sa2_poke(SKIP_INTRO_GAME_MODE, 4, &title_load);
        if (g_skip_intro_logos++ == 0) {
            memory = load_tips_texs(state, memory);
            fprintf(stderr, "sa2 tweaks: intro skipped: the logos and intro skipped; the loading "
                    "tips' textures loaded in their place\n");
        } else if (g_skip_intro_logos == 2) {
            fprintf(stderr, "sa2 tweaks: intro skipped: the logos skipped again on a return to the "
                    "title\n");
        }
    } else if (before == MODE_TITLE && after == MODE_STORY) {
        uint8_t type = 0;
        const int read = sa2_peek(SKIP_INTRO_STORY_TYPE, 1, &type) == SA2_OK;
        if (g_skip_intro_title_left++ == 0 && read && type == STORY_TYPE_INTRO) {
            const uint32_t title_load = MODE_TITLE_LOAD;
            sa2_poke(SKIP_INTRO_GAME_MODE, 4, &title_load);
            sa2_poke(SKIP_INTRO_NEXT_GAME_MODE, 4, &title_load);
            fprintf(stderr, "sa2 tweaks: intro skipped: the opening cutscene (story type 4) skipped; "
                    "the title screen follows\n");
        } else if (!read) {
            fprintf(stderr, "sa2 tweaks: intro skipped: the story type at 0x%08X does not read, so "
                    "the title's start of a story is left alone\n", SKIP_INTRO_STORY_TYPE);
        } else if (type == STORY_TYPE_INTRO && !g_skip_intro_idle_said++) {
            fprintf(stderr, "sa2 tweaks: intro skipped: the title's idle opening plays (only the "
                    "boot's is skipped)\n");
        }
    }
    return memory;
}
