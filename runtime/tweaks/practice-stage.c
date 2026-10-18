/* practice-stage: the game boots straight into one action stage, with no logos, title or menus,
 * and never saves in that run. SA2_PRACTICE=level[,character[,mission]] chooses the stage; unset,
 * it is City Escape (runtime/sa2_tweaks.h: the tweaks and their rules). It is the Mod Loader's test
 * spawn (SA2ModLoader testspawn.cpp, `--level`, `--character`, `--mission`), done with wraps, since
 * OpenSA2 never runs a patched code byte; harness/replay/wrap_test_spawn.c, the stage replays'
 * test spawn, already did most of it, and its parts now live here, shared.
 *
 * TWEAK_WRAPS: sub_77dd10, sub_434515, sub_4395c0, sub_4436a0, sub_426760
 *
 * The setting. SA2_PRACTICE is read once, the first time a wrap asks, and only when SA2_TWEAKS
 * names practice-stage (so never in a replay, nor in the launcher's selftest, which drops
 * SA2_TWEAKS). Its words, split at commas, upper or lower case, spaces and punctuation ignored in a
 * name; a word with a digit in it is a number, and must be only digits (spaces around them aside),
 * so "-1", "1.5" or "1-3" is refused by name, never read as 1, 15 or 13:
 *
 *   level      the level ID (13) or its name (cityescape, "City Escape"). Only the 30 action
 *              stages of the game's stage select table (StageSelectLevels, 0x00C75078, 33 rows of
 *              {level, character, column, row}; its rows 70, the kart race, and 90, Chao World,
 *              are left out) are taken. Bosses, two-player stages, kart races, Chao World and the
 *              test levels rely on state the menus set and the test spawn skips (the Mod Loader
 *              has the same limit), and levels 0 and 1 the game itself turns into 90 at
 *              0x0043C983 (in 0x0043C970), the Mod Loader's 13-byte NOP; each is refused by name.
 *   character  0 Sonic, 1 Shadow, 4 Knuckles, 5 Rouge, 6 Tails (in the Cyclone, MechTails),
 *              7 Eggman (in the Egg Walker, MechEggman), or the name. Left out, the stage's own,
 *              from the same table. 2 and 3 (Tails and Eggman on foot) and the alternate
 *              characters (Amy, Metal Sonic, Chao Walkers, Tikal, Chaos, which need the Mod
 *              Loader's LoadSonic-to-LoadAmy jumps) are refused by name.
 *   mission    1 to 5 (MissionNum, the byte at 0x0174AFE3, gets mission - 1); left out, 1. Green
 *              Hill (31) takes mission 1 only: it has no hard-mode SET file (set0031_hd_s.bin),
 *              where every other stage of the table has one.
 *
 * A setting that does not parse turns the tweak off for the run, by name. The one line the run
 * prints: "sa2 tweaks: practice stage: City Escape (level 13), Sonic (0), mission 1; the logos,
 * title and menus are skipped and this run never saves", or "... SA2_PRACTICE=... refused: <why>;
 * the game starts as usual".
 *
 * Where, read from the unpacked image.
 *
 *   sub_77dd10   WinMain's data-DLL loader, where the Mod Loader's init runs. After the lifted
 *                body, as wrap_test_spawn.c did it: CurrentLevel (int16 at 0x01934B70) set,
 *                LoadTipsTexs (0x00427EF0, ESI = the TextLanguage byte at 0x0174AFD1) called,
 *                LoadMenuButtonsTex done as the loader does it (its path string, built from
 *                [0x0174B5FC] and the language, given to LoadPNG, 0x0041D490, ECX = the path, and
 *                the result stored in MenuButtonImage, 0x01A55868), ChaoSaveIndexThing
 *                (0x0173D06C) = 0, CurrentCharacter (0x01934B80) set; and here, MissionNum. The
 *                two calls go through a sentinel return address on the guest stack, every
 *                register put back after; a return anywhere else stops the run by name.
 *                StartLevel's setup (0x00439610, from GameMode 4's arm 0x004341C1) zeroes
 *                MissionNum only in two-player play and for levels above 0x3C (but 0x46) or the
 *                boss levels 0x13, 0x14, 0x1D, 0x21 and 0x2A, none of them taken here.
 *   sub_434515   GameModeHandler's case 13 (the arm 0x00434515 of 0x00434160, the title's load,
 *                reached through the jump table at 0x00434BD4). On the boot's first visit it
 *                stores `mov [GameMode],15h` (0x00434594, the immediate at 0x0043459A that the Mod
 *                Loader makes 4), then NextGameMode = 13 and the visit's other stores, and
 *                returns through the handler's epilogue (0x00434213); nothing after the store
 *                reads GameMode (0x0174B040). The first time GameMode is 0x15 after the arm, the
 *                wrap writes 4 (StartLevel). Later visits, after the stage, are left alone: the
 *                practice is the boot's start, and a return to the title is the game's own. The
 *                arm, not the handler: the handler is skip-intro's wrap, and the stage replays'
 *                own rule for it stays where it is (wrap_test_spawn.c, or the hook skip-intro's
 *                wrap calls). With skip-intro on as well, its wrap sees GameMode 4, not 0x15,
 *                and does nothing.
 *   sub_4395c0   ResetSeedAndCharacter, called once from GameMode 0's arm (0x00434194) at the
 *                boot, after the data-DLL loader: with [0x01748B94] = 1 and a pending character
 *                at [0x01749564] (not -2) it takes that one; otherwise it stores 0 to
 *                CurrentCharacter at 0x004395F3 (the Mod Loader's 10-byte NOP), then calls srand
 *                (0x007A89C6) and returns; nothing after the store reads CurrentCharacter. The
 *                image starts with [0x01748B94] = 1 and [0x01749564] = -2, the second path. On
 *                its first call the wrap reads both before the lifted body and, when the body took
 *                the second path, writes the chosen character back. The first path, a pending
 *                character, is left alone and said.
 *   sub_4436a0   ProbablySavesSaveFile (called from the goal, the results' score and time, the
 *   sub_426760   options menu, the event executor and GameStateHandler) and WriteSaveFileThing
 *                (from WriteTaskWithWaiting, 0x00456820, and the Chao World's FExec_1 and
 *                FExec_2): `ret` at the entry, the Mod Loader's "prevent the game to save", for
 *                the whole run, with the function's success status in EAX: 1 for
 *                ProbablySavesSaveFile (its `mov eax,1` at 0x0044414C; 0, at 0x00444139, is a
 *                failure) and 0 for WriteSaveFileThing (its `xor eax,eax` at 0x00426847; -4, at
 *                0x00426824, is a failure). Their callers check it: 0x0066DCB8 and 0x0066E973
 *                (`test eax,eax`, and on 0 ChaoSaveIndexThing = -2 and [0x0174B01C] = 0xFE, a
 *                save error), 0x0045684F and 0x00456D5A (non-zero frees the task's buffer and
 *                sets [0x0174AFD4] = 0xFF), and the Chao World's FExec. A bare `ret`, the Mod
 *                Loader's, leaves the caller's EAX, so a skipped save could be reported as a
 *                failed one: at 0x0066E973 EAX is always 0, at 0x0045684F it is the task
 *                pointer. The test spawn never loads the player's save, so a save written in the
 *                run would overwrite it with an empty game: in a practice run the save on disk
 *                is never written, and returning success writes nothing either. The first
 *                skipped save is said. The stage replays' test spawn keeps the bare `ret`, so a
 *                stage replay runs as before.
 *
 * The stage replays' test spawn (tools/s4/stage_run.py, Metal Harbor with Sonic) asks for the
 * same start through sa2_harness_test_spawn, which harness/replay/wrap_test_spawn.c defines (from
 * SA2_TEST_SPAWN_LEVEL and SA2_TEST_SPAWN_CHARACTER) and this file defines as a weak "no" for every
 * other link. For it the data-DLL loader's wrap and the two save wraps do what wrap_test_spawn.c's
 * own wraps did, with no line printed; the arm and ResetSeedAndCharacter wraps, the mission and the
 * setting's checks are the tweak's alone, so a stage replay runs as before.
 *
 * Off is identical: with the tweak off (and no stage replay's hook), each wrap asks sa2_tweak_on
 * and the weak hook, then only calls `__real_`; nothing is read, written or printed.
 *
 * Risks. A practice run starts with no save loaded, as the Mod Loader's does: the stage plays as a
 * new game's would, with no upgrades. The stage select plays with the upgrades in the loaded save,
 * so a stage whose route needs an upgrade picked up in an earlier stage (Eggman's Jet Engine,
 * Sonic's Light Shoes or Bounce Bracelet, say) may not be finishable in a practice run; an upgrade
 * the stage itself holds, such as Metal Harbor's light dash, can still be picked up, and no save
 * is written. No practice run has been played to a later stage's goal. What follows the stage
 * (results, menus, Chao World) runs on that empty game, unsaved. Mission 1 of Green Forest (3),
 * White Jungle (4) and Security Hall (8) plays with the story's countdown, 8, 10 and 5 minutes:
 * setMissionTimeLimits (0x00451F00) gives it at 0x00452066 to 0x004520A7 when MissionNum is 0 and
 * the byte at 0x0174AFC2 is 0, and the test spawn never sets that byte, as with the Mod Loader's.
 * A stage-select run has no countdown there; when it runs out, the run ends. The chosen character
 * in another character's stage plays it as the Mod Loader's test spawn does: objects, goals and
 * the HUD are the stage's own, so a hunting stage with Sonic has no pieces to find a use for. The
 * boot reaches the stage far sooner, so a boot's frame counts change; replays ignore tweaks.
 * Cannon's Core (34) starts its first section, as the stage select's row does. Practice runs are
 * not speedrun runs.
 */
#include <ctype.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_trap.h"
#include "sa2_tweaks.h"

#define PRACTICE_CURRENT_LEVEL      0x01934B70u   /* int16 */
#define PRACTICE_CURRENT_CHARACTER  0x01934B80u
#define PRACTICE_CHAO_SAVE_INDEX    0x0173D06Cu
#define PRACTICE_TEXT_LANGUAGE      0x0174AFD1u   /* byte */
#define PRACTICE_MISSION            0x0174AFE3u   /* byte, MissionNum */
#define PRACTICE_PAD_OR_KEYBOARD    0x0174B5FCu
#define PRACTICE_GAME_MODE          0x0174B040u
#define PRACTICE_MENU_BUTTON_IMAGE  0x01A55868u
#define PRACTICE_RESET_FLAG         0x01748B94u   /* ResetSeedAndCharacter's first test */
#define PRACTICE_PENDING_CHARACTER  0x01749564u   /* -2: none */
#define PRACTICE_LOAD_TIPS          0x00427EF0u   /* LoadTipsTexs */
#define PRACTICE_LOAD_PNG           0x0041D490u   /* LoadPNG */

#define MODE_START_LEVEL  4u
#define MODE_LOAD_INTRO   0x15u
#define NO_PENDING        0xFFFFFFFEu

void *__real_sub_77dd10(void *state, uint32_t pc, void *memory);
void *__real_sub_434515(void *state, uint32_t pc, void *memory);
void *__real_sub_4395c0(void *state, uint32_t pc, void *memory);
void *__real_sub_4436a0(void *state, uint32_t pc, void *memory);
void *__real_sub_426760(void *state, uint32_t pc, void *memory);
void *sub_427ef0(void *state, uint32_t pc, void *memory);
void *sub_41d490(void *state, uint32_t pc, void *memory);

/* The stage replays' test spawn (harness/replay/wrap_test_spawn.c) defines this: 1 and the level
 * and character when the run asks for a test spawn. Elsewhere, no. */
__attribute__((weak)) int sa2_harness_test_spawn(int *level, uint32_t *character) {
    (void)level;
    (void)character;
    return 0;
}

/* The stage select's action stages (StageSelectLevels, 0x00C75078, without 70 and 90), in level
 * order, with each row's character. */
static const struct { uint8_t level, character; const char *name; } PRACTICE_LEVELS[] = {
    {3, 0, "Green Forest"},   {4, 1, "White Jungle"},   {5, 4, "Pumpkin Hill"},
    {6, 1, "Sky Rail"},       {7, 4, "Aquatic Mine"},   {8, 5, "Security Hall"},
    {9, 6, "Prison Lane"},    {10, 0, "Metal Harbor"},  {11, 7, "Iron Gate"},
    {12, 7, "Weapons Bed"},   {13, 0, "City Escape"},   {14, 1, "Radical Highway"},
    {16, 4, "Wild Canyon"},   {17, 6, "Mission Street"}, {18, 5, "Dry Lagoon"},
    {21, 7, "Sand Ocean"},    {22, 0, "Crazy Gadget"},  {23, 6, "Hidden Base"},
    {24, 6, "Eternal Engine"}, {25, 4, "Death Chamber"}, {26, 5, "Egg Quarters"},
    {27, 7, "Lost Colony"},   {28, 0, "Pyramid Cave"},  {30, 0, "Final Rush"},
    {31, 0, "Green Hill"},    {32, 4, "Meteor Herd"},   {34, 0, "Cannon's Core"},
    {40, 1, "Final Chase"},   {43, 7, "Cosmic Wall"},   {44, 5, "Mad Space"},
};
#define PRACTICE_DEFAULT_LEVEL 13u
#define PRACTICE_GREEN_HILL    31u

/* The six base characters, and the names each answers to. */
static const struct { uint8_t id; const char *name, *alias; } PRACTICE_CHARACTERS[] = {
    {0, "Sonic", "sonic"},    {1, "Shadow", "shadow"},  {4, "Knuckles", "knuckles"},
    {5, "Rouge", "rouge"},    {6, "Tails", "mechtails"}, {7, "Eggman", "mecheggman"},
};

enum { SPAWN_UNREAD, SPAWN_OFF, SPAWN_PRACTICE, SPAWN_HARNESS };
static struct {
    int kind, level, mission;   /* mission: 0-4, MissionNum; -1 leaves it (the harness) */
    uint32_t character;
} g_spawn;
static int g_practice_title_done, g_practice_reset_done, g_practice_save_said, g_practice_poke_said;

/* The word with every character but letters and digits dropped, in lower case. */
static void squash(const char *from, size_t len, char *to, size_t n) {
    size_t k = 0;
    for (size_t i = 0; i < len && k + 1 < n; i++)
        if (isalnum((unsigned char)from[i])) to[k++] = (char)tolower((unsigned char)from[i]);
    to[k] = 0;
}

enum { WORD_NAME, WORD_NUMBER, WORD_BAD_NUMBER };

/* A word as typed (p, len): a name when it has no digit; a number when, spaces at both ends aside,
 * it is only digits (its value in *value, held at 9999 above that); otherwise a bad number ("-1",
 * "1.5", "1-3"), which is refused by name, never squashed into another number. The word, trimmed,
 * in *tp and *tlen. */
static int word_kind(const char *p, size_t len, long *value, const char **tp, int *tlen) {
    while (len && isspace((unsigned char)*p)) p++, len--;
    while (len && isspace((unsigned char)p[len - 1])) len--;
    *tp = p;
    *tlen = len > 200 ? 200 : (int)len;
    int digits = 0, others = 0;
    long v = 0;
    for (size_t i = 0; i < len; i++) {
        if (isdigit((unsigned char)p[i])) {
            digits = 1;
            v = v * 10 + (p[i] - '0');
            if (v > 9999) v = 9999;
        } else {
            others = 1;
        }
    }
    if (!digits) return WORD_NAME;
    if (others) return WORD_BAD_NUMBER;
    *value = v;
    return WORD_NUMBER;
}

static int level_row(unsigned level) {
    for (unsigned i = 0; i < sizeof PRACTICE_LEVELS / sizeof PRACTICE_LEVELS[0]; i++)
        if (PRACTICE_LEVELS[i].level == level) return (int)i;
    return -1;
}

static int character_row(unsigned id) {
    for (unsigned i = 0; i < sizeof PRACTICE_CHARACTERS / sizeof PRACTICE_CHARACTERS[0]; i++)
        if (PRACTICE_CHARACTERS[i].id == id) return (int)i;
    return -1;
}

/* SA2_PRACTICE's words into g_spawn; 0 and the reason in `why` when it does not parse. */
static int practice_parse(const char *value, char *why, size_t n) {
    char word[3][48];
    int kind[3];
    long number[3] = {0, 0, 0};
    const char *raw[3];
    int rawlen[3];
    unsigned nwords = 0;
    const char *p = value ? value : "";
    for (;;) {
        const char *comma = strchr(p, ',');
        const size_t len = comma ? (size_t)(comma - p) : strlen(p);
        if (nwords == 3) {
            snprintf(why, n, "more than three words (level, character, mission)");
            return 0;
        }
        kind[nwords] = word_kind(p, len, &number[nwords], &raw[nwords], &rawlen[nwords]);
        squash(p, len, word[nwords++], sizeof word[0]);
        if (!comma) break;
        p = comma + 1;
    }
    static const char *const WHAT[3] = {"level", "character", "mission"};
    for (unsigned i = 0; i < nwords; i++) {
        if (kind[i] == WORD_BAD_NUMBER) {
            snprintf(why, n, "%s \"%.*s\" is not a whole number", WHAT[i], rawlen[i], raw[i]);
            return 0;
        }
        if (rawlen[i] && !word[i][0]) {   /* only punctuation: neither left out nor a name */
            snprintf(why, n, "%s \"%.*s\" is not a name or a number", WHAT[i], rawlen[i], raw[i]);
            return 0;
        }
    }
    int row;
    if (!word[0][0]) {
        row = level_row(PRACTICE_DEFAULT_LEVEL);
    } else if (kind[0] == WORD_NUMBER) {
        const long level = number[0];
        row = level_row((unsigned)(level > 255 ? 255 : level));
        if (row < 0 && level < 2) {
            snprintf(why, n, "level %.*s is a test level, which the game turns into Chao World",
                     rawlen[0], raw[0]);
            return 0;
        }
        if (row < 0) {
            snprintf(why, n, "level %.*s is not one of the stage select's 30 action stages (bosses, "
                     "two-player stages, kart races, Chao World and the test levels need what the "
                     "menus set)", rawlen[0], raw[0]);
            return 0;
        }
    } else {
        row = -1;
        for (unsigned i = 0; row < 0 && i < sizeof PRACTICE_LEVELS / sizeof PRACTICE_LEVELS[0]; i++) {
            char name[48];
            squash(PRACTICE_LEVELS[i].name, strlen(PRACTICE_LEVELS[i].name), name, sizeof name);
            if (strcmp(name, word[0]) == 0) row = (int)i;
        }
        if (row < 0) {
            snprintf(why, n, "\"%s\" is not the name of one of the stage select's 30 action stages",
                     word[0]);
            return 0;
        }
    }
    g_spawn.level = PRACTICE_LEVELS[row].level;
    g_spawn.character = PRACTICE_LEVELS[row].character;
    g_spawn.mission = 0;
    if (nwords > 1 && word[1][0]) {
        int c = -1;
        if (kind[1] == WORD_NUMBER) {
            const long id = number[1];
            c = character_row((unsigned)(id > 255 ? 255 : id));
            if (c < 0) {
                snprintf(why, n, "character %.*s is not one of the six (0 Sonic, 1 Shadow, 4 Knuckles, "
                         "5 Rouge, 6 Tails, 7 Eggman); on-foot Tails and Eggman and the alternate "
                         "characters are not taken", rawlen[1], raw[1]);
                return 0;
            }
        } else {
            for (unsigned i = 0; c < 0 && i < sizeof PRACTICE_CHARACTERS / sizeof PRACTICE_CHARACTERS[0]; i++) {
                char name[16];
                squash(PRACTICE_CHARACTERS[i].name, strlen(PRACTICE_CHARACTERS[i].name), name, sizeof name);
                if (strcmp(name, word[1]) == 0 || strcmp(PRACTICE_CHARACTERS[i].alias, word[1]) == 0)
                    c = (int)i;
            }
            if (c < 0) {
                snprintf(why, n, "\"%s\" is not one of the six characters (sonic, shadow, knuckles, "
                         "rouge, tails, eggman)", word[1]);
                return 0;
            }
        }
        g_spawn.character = PRACTICE_CHARACTERS[c].id;
    }
    if (nwords > 2 && word[2][0]) {
        const long mission = kind[2] == WORD_NUMBER ? number[2] : 0;
        if (mission < 1 || mission > 5) {
            snprintf(why, n, "mission \"%.*s\" is not 1 to 5", rawlen[2], raw[2]);
            return 0;
        }
        if (g_spawn.level == PRACTICE_GREEN_HILL && mission != 1) {
            snprintf(why, n, "Green Hill has mission 1 only");
            return 0;
        }
        g_spawn.mission = (int)mission - 1;
    }
    return 1;
}

static int spawn(void) {
    if (g_spawn.kind != SPAWN_UNREAD) return g_spawn.kind;
    g_spawn.kind = SPAWN_OFF;
    if (sa2_tweak_on(SA2_TWEAK_PRACTICE_STAGE)) {
        const char *value = getenv("SA2_PRACTICE");
        char why[200];
        if (practice_parse(value, why, sizeof why)) {
            g_spawn.kind = SPAWN_PRACTICE;
            const int row = level_row((unsigned)g_spawn.level);
            fprintf(stderr, "sa2 tweaks: practice stage: %s (level %d), %s (%u), mission %d%s; the "
                    "logos, title and menus are skipped and this run never saves\n",
                    PRACTICE_LEVELS[row].name, g_spawn.level,
                    PRACTICE_CHARACTERS[character_row(g_spawn.character)].name, g_spawn.character,
                    g_spawn.mission + 1, value && *value ? "" : " (SA2_PRACTICE is not set)");
        } else {
            fprintf(stderr, "sa2 tweaks: practice stage: SA2_PRACTICE=%s refused: %s; the game starts "
                    "as usual\n", value, why);
        }
    }
    int level = -1;
    uint32_t character = 0;
    if (g_spawn.kind == SPAWN_OFF && sa2_harness_test_spawn(&level, &character)) {
        g_spawn.kind = SPAWN_HARNESS;
        g_spawn.level = level;
        g_spawn.character = character;
        g_spawn.mission = -1;
    }
    return g_spawn.kind;
}

static uint32_t peek32(uint32_t va) {
    uint32_t v = 0;
    sa2_peek(va, 4, &v);
    return v;
}

static void poke(uint32_t va, uint32_t n, const void *v, const char *what) {
    if (sa2_poke(va, n, v) != SA2_OK && !g_practice_poke_said++)
        fprintf(stderr, "sa2 tweaks: practice stage: %s at 0x%08X does not write, so the start is "
                "not the one chosen\n", what, va);
}

typedef void *(*practice_fn)(void *state, uint32_t pc, void *memory);

/* A lifted function called the way `call` would, from inside a wrap: a sentinel return address
 * pushed on the guest stack, the function run to its `ret`, and every register but EAX (the
 * result) put back. A return anywhere but the sentinel stops the run. */
static void *guest_call(void *s, void *m, practice_fn fn, uint32_t va, uint32_t esp) {
    uint32_t saved[SA2_GPR_COUNT];
    for (int i = 0; i < SA2_GPR_COUNT; ++i) saved[i] = sa2_rt_get_gpr(s, i);
    const uint32_t sentinel = SA2_CALL_SENTINEL;
    esp -= 4u;
    sa2_poke(esp, 4, &sentinel);
    sa2_rt_set_gpr(s, SA2_ESP, esp);
    sa2_rt_set_gpr(s, SA2_EIP, va);
    m = fn(s, va, m);
    const uint32_t eip_after = sa2_rt_get_gpr(s, SA2_EIP), esp_after = sa2_rt_get_gpr(s, SA2_ESP);
    if (eip_after != sentinel || esp_after != esp + 4u) {
        sa2_trap_note("test spawn: %#010x returned to %#010x with esp %#010x, not to the "
                      "sentinel with esp %#010x", va, eip_after, esp_after, esp + 4u);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, eip_after);
    }
    const uint32_t eax = sa2_rt_get_gpr(s, SA2_EAX);
    for (int i = 0; i < SA2_GPR_COUNT; ++i) sa2_rt_set_gpr(s, i, saved[i]);
    sa2_rt_set_gpr(s, SA2_EAX, eax);
    return m;
}

__attribute__((used, retain))
void *__wrap_sub_77dd10(void *s, uint32_t pc, void *m) {
    const int kind = spawn();
    if (kind == SPAWN_OFF) return __real_sub_77dd10(s, pc, m);
    m = __real_sub_77dd10(s, pc, m);
    const uint32_t eax = sa2_rt_get_gpr(s, SA2_EAX);   /* the data DLL loader's result */
    const uint16_t level = (uint16_t)g_spawn.level;
    poke(PRACTICE_CURRENT_LEVEL, 2, &level, "CurrentLevel");

    uint8_t lang = 0;
    sa2_peek(PRACTICE_TEXT_LANGUAGE, 1, &lang);
    const uint32_t esi = sa2_rt_get_gpr(s, SA2_ESI);
    sa2_rt_set_gpr(s, SA2_ESI, lang);
    m = guest_call(s, m, sub_427ef0, PRACTICE_LOAD_TIPS, sa2_rt_get_gpr(s, SA2_ESP));
    sa2_rt_set_gpr(s, SA2_ESI, esi);

    /* LoadMenuButtonsTex: the loader's sprintf, into a buffer below the stack. */
    char path[40];
    snprintf(path, sizeof path, peek32(PRACTICE_PAD_OR_KEYBOARD)
             ? "\\SOC\\menuButton%d\\menu_button_%d.png"
             : "\\SOC\\menuButtonK%d\\menu_button_%d.png", lang, lang);
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    const uint32_t buf = (esp - 0x100u) & ~0xFu;
    sa2_poke(buf, (uint32_t)strlen(path) + 1u, path);
    const uint32_t ecx = sa2_rt_get_gpr(s, SA2_ECX);
    sa2_rt_set_gpr(s, SA2_ECX, buf);
    m = guest_call(s, m, sub_41d490, PRACTICE_LOAD_PNG, buf - 0x10u);
    const uint32_t image = sa2_rt_get_gpr(s, SA2_EAX);
    poke(PRACTICE_MENU_BUTTON_IMAGE, 4, &image, "MenuButtonImage");
    sa2_rt_set_gpr(s, SA2_ECX, ecx);

    const uint32_t zero = 0;
    poke(PRACTICE_CHAO_SAVE_INDEX, 4, &zero, "ChaoSaveIndexThing");
    poke(PRACTICE_CURRENT_CHARACTER, 4, &g_spawn.character, "CurrentCharacter");
    if (g_spawn.mission >= 0) {
        const uint8_t mission = (uint8_t)g_spawn.mission;
        poke(PRACTICE_MISSION, 1, &mission, "MissionNum");
    }
    sa2_rt_set_gpr(s, SA2_EAX, eax);
    return m;
}

__attribute__((used, retain))
void *__wrap_sub_434515(void *s, uint32_t pc, void *m) {
    if (spawn() != SPAWN_PRACTICE || g_practice_title_done) return __real_sub_434515(s, pc, m);
    m = __real_sub_434515(s, pc, m);
    g_practice_title_done = 1;
    const uint32_t mode = peek32(PRACTICE_GAME_MODE);
    if (mode == MODE_LOAD_INTRO) {
        const uint32_t start_level = MODE_START_LEVEL;
        poke(PRACTICE_GAME_MODE, 4, &start_level, "GameMode");
    } else {
        fprintf(stderr, "sa2 tweaks: practice stage: the title's first load chose game mode %u, not "
                "the intro (0x15), so the title follows and the stage does not start\n", mode);
    }
    return m;
}

__attribute__((used, retain))
void *__wrap_sub_4395c0(void *s, uint32_t pc, void *m) {
    if (spawn() != SPAWN_PRACTICE || g_practice_reset_done) return __real_sub_4395c0(s, pc, m);
    g_practice_reset_done = 1;
    uint32_t flag = 0, pending = 0;
    const int read = sa2_peek(PRACTICE_RESET_FLAG, 4, &flag) == SA2_OK &&
                     sa2_peek(PRACTICE_PENDING_CHARACTER, 4, &pending) == SA2_OK;
    m = __real_sub_4395c0(s, pc, m);
    if (!read) {
        fprintf(stderr, "sa2 tweaks: practice stage: ResetSeedAndCharacter's state at 0x%08X does not "
                "read, so the character is the one it sets\n", PRACTICE_RESET_FLAG);
    } else if (flag == 1u && pending != NO_PENDING) {
        fprintf(stderr, "sa2 tweaks: practice stage: ResetSeedAndCharacter took the pending character "
                "%u, which is left alone\n", pending);
    } else if (peek32(PRACTICE_CURRENT_CHARACTER) != g_spawn.character) {
        poke(PRACTICE_CURRENT_CHARACTER, 4, &g_spawn.character, "CurrentCharacter");
    }
    return m;
}

/* `ret` at the entry: the return address popped into EIP. In a practice run EAX is the function's
 * success status, `ok_eax`, so the caller takes its saved path; the stage replays' test spawn keeps
 * the caller's EAX, as it always has. */
static void *bare_ret(void *s, void *m, uint32_t va, uint32_t ok_eax) {
    if (g_spawn.kind == SPAWN_PRACTICE) {
        if (!g_practice_save_said++)
            fprintf(stderr, "sa2 tweaks: practice stage: a save skipped (0x%08X); this run writes no "
                    "save\n", va);
        sa2_rt_set_gpr(s, SA2_EAX, ok_eax);
    }
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    sa2_rt_set_gpr(s, SA2_EIP, peek32(esp));
    sa2_rt_set_gpr(s, SA2_ESP, esp + 4u);
    return m;
}

__attribute__((used, retain))
void *__wrap_sub_4436a0(void *s, uint32_t pc, void *m) {
    if (spawn() == SPAWN_OFF) return __real_sub_4436a0(s, pc, m);
    return bare_ret(s, m, 0x004436A0u, 1u);   /* saved: 0x0044414C */
}

__attribute__((used, retain))
void *__wrap_sub_426760(void *s, uint32_t pc, void *m) {
    if (spawn() == SPAWN_OFF) return __real_sub_426760(s, pc, m);
    return bare_ret(s, m, 0x00426760u, 0u);   /* written: 0x00426847 */
}
