/* Harness level 2, lifted side: the Mod Loader's test spawn, reproduced.
 *
 * Documentation/decisions/2026-09-23-S4-stage-route.md: a stage replay starts
 * natively through the Mod Loader's `--level <name> --character <name>`
 * (SA2ModLoader/testspawn.cpp, TestSpawnCheckArgs), and the lifted build must
 * get the same effects. The lifted build never runs guest code bytes, so the
 * loader's code patches are reproduced by link-time wraps, and its data writes
 * and calls happen where the loader makes them: in its init, which runs when
 * WinMain's data-DLL loader (0x0077DD10) returns.
 *
 *   sub_77dd10  after the real body: CurrentLevel (int16 at 0x01934B70) and
 *   sub_4436a0  CurrentCharacter (0x01934B80) set, ChaoSaveIndexThing
 *   sub_426760  (0x0173D06C) cleared, LoadTipsTexs and LoadMenuButtonsTex done;
 *               and `ret` at the entry of the two save functions (the loader's
 *               "prevent the game to save"). The runtime wraps these three in
 *               every build for the practice-stage tweak
 *               (runtime/tweaks/practice-stage.c), which does the same start, so
 *               the test spawn asks those wraps for it: sa2_harness_test_spawn
 *               below answers with the level and character, and the runtime's
 *               own definition, a weak "no", stands in every other link. For a
 *               test spawn those wraps do what this file's own did, and print
 *               nothing.
 *   sub_434160  the imm32 at 0x0043459A, `mov [GameMode],15h` in case 13, made 4:
 *               when GameMode was 13 on entry and is 0x15 on exit, 4 is written.
 *               Only 0x00434594 (case 13) and 0x004353EC store 0x15 to GameMode,
 *               and nothing between the store and the return reads it. The runtime
 *               wraps sub_434160 in every build (runtime/tweaks/skip-intro.c, the
 *               skip-intro tweak), so this rule is sa2_harness_gamemode_after, which
 *               that wrap calls after the lifted body with GameMode before and after
 *               it; the runtime's own definition is a weak no-op.
 *
 * The two NOP patches (0x0043C983, 0x004395F3) and the LifeIcon hook change
 * nothing for level 10 with Sonic and are not reproduced; the decision record
 * says why.
 *
 * SA2_TEST_SPAWN_LEVEL=<id> turns it on, SA2_TEST_SPAWN_CHARACTER=<id> (default
 * 0) chooses the character. Unset, both hooks change nothing.
 */
#include <stdint.h>
#include <stdlib.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"

#define GAME_MODE          0x0174B040u

static int spawn_level = -2;           /* -2 unread, -1 off */
static uint32_t spawn_character;

static int level(void) {
    if (spawn_level == -2) {
        const char *l = getenv("SA2_TEST_SPAWN_LEVEL");
        const char *c = getenv("SA2_TEST_SPAWN_CHARACTER");
        spawn_level = l && *l ? (int)strtol(l, NULL, 0) : -1;
        spawn_character = c && *c ? (uint32_t)strtoul(c, NULL, 0) : 0u;
    }
    return spawn_level;
}

/* The runtime's practice-stage wraps ask this (runtime/tweaks/practice-stage.c). */
int sa2_harness_test_spawn(int *level_out, uint32_t *character) {
    if (level() < 0) return 0;
    *level_out = spawn_level;
    *character = spawn_character;
    return 1;
}

/* GameModeHandler (sub_434160) has run: GameMode was `before` and is `after`. Called by the
 * runtime's wrap of it (runtime/tweaks/skip-intro.c) in every build that links this file. */
void sa2_harness_gamemode_after(uint32_t before, uint32_t after) {
    if (level() >= 0 && before == 13u && after == 0x15u) {
        const uint32_t start_level = 4u;
        sa2_poke(GAME_MODE, 4, &start_level);
    }
}
