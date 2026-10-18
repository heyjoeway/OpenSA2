/* always-recap: the story-so-far recap (the summary screen, "arasuji") plays before every story stage
 * the game has a recap for, not only when a story is resumed from story select. It is the screen
 * the game itself shows on a resume, with the player's own text, voice and music; A or Start skips
 * it as on a resume, and B, which on a resume goes back to story select, skips it too, so the
 * recap never takes the player out of the story (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_4586a0, sub_666600, sub_67e460
 *
 * Where, read from the unpacked image.
 *
 *   The story state. 0x01DEB320 (byte) is the story type: 1 Hero, 2 Dark, 3 Last (4 the opening,
 *   others the demos and boss rushes). 0x01DEB31E (byte) is the index of the story entry to load
 *   next, 0x01DEB31F the one loaded last, and 0x01DEB321 a "first time in this entry" flag. The
 *   entries are 12 bytes (Mod Loader StoryEntry: type, character, level, four events): Hero at
 *   0x0173A148 (48), Dark at 0x0173A370 (44), Last at 0x0173A580 (8); type 0 is events, 1 a
 *   stage, 2 the end, 3 the credits. The saved progress of each story is 0x01DEC60B + type - 1;
 *   0x004436A0 sets it to the entry index + 1 and writes the save after each stage and event, and
 *   the index is then incremented (0x0043ABAD, 0x0043BE56, 0x005FB73F, 0x005FB804, 0x0079315E).
 *
 *   The next-stage path. GameMode (0x0174B040) 12, LoadStory, is the arm 0x004349C1 of the game
 *   mode handler 0x00434160: `call 0x004586A0`, return address 0x004349C6. 0x004586A0 picks the
 *   story's table by the type (jump table 0x004588A8), calls LoadStoryEntry (0x004589D0, fastcall,
 *   EDX = the entry) and copies the index to 0x01DEB31F. LoadStoryEntry sets GameMode 7 for
 *   events (NextGameMode 12, so the next entry follows), 4 or 6 for a stage, 15 for the credits and
 *   2 for the end. Story progression never visits the menus: the recap lives in the menu module.
 *
 *   The resume path. Story select (CurrentMenu 0x01D7BB10 = 2, 0x00670A90), on Continue
 *   (0x00670CC9-0x00670CE0), calls 0x00678D40 (EBX = story - 1, EDI = the saved progress), which
 *   picks the recap: the StorySummaries row (0x00C686C0, 20 bytes: StoryType, StorySequenceID,
 *   BackgroundID, data, count; the list ends at a negative StoryType) of that story with the
 *   largest StorySequenceID not above the progress, into 0x01D1C1B4 and its background into
 *   0x01D1C1B0, and 0x01A4A95C = (progress == 0), "no recap"; then 0x0067E710 (EAX = progress,
 *   ECX = type), which writes the story state (type, index = progress, previous = progress - 1,
 *   the flag = 1, MissionNum 0x0174AFE3 = 0) and keeps the old index in 0x01A4C2DC; then
 *   0x0067E380, which swaps the menu's modules (the table 0x00C77D10-0x00C77E3C) for the recap's;
 *   and CurrentMenu = 5. CurrentMenu 5 is Menu_StorySummary (0x0067E460, called by RunMenus at
 *   0x006662C1), a machine on 0x01D7BB14: 0 starts it (its music, 0x00442CF0), 1 waits for the
 *   fade, 2 waits for a button in MenuButtons_Pressed (0x01DEFAB0): bit 2 (B) goes to 3, bits
 *   0x0C (A, Start), or the recap's own end ([[0x00C77F18] + 0x18]), go to 4, each after the same
 *   stores (its work's two flags cleared, the fade started); 3 waits, swaps the modules back
 *   (0x00670940), restores the index from 0x01A4C2DC and returns to story select (CurrentMenu 2);
 *   4 waits, sets NextGameMode = 12 and returns 1, and the menu hands over to LoadStory.
 *
 *   The menus are entered with GameMode 2 (the arm 0x0043478B makes it 13). Case 13 (0x00434515)
 *   loads the menu module; on that load it clears the story state (type, index, previous = 0, the
 *   flag = 1, at 0x00434686-0x0043469F), wipes the save buffer 0x01DEC600 when 0x01749934 is set,
 *   and with 0x0174B019 set and 0x01A559E8 = 0 goes the title's way: every third time it plays
 *   the intro (GameMode 0x15), else the title's load (0x004345C5). The
 *   module's loader (0x00670670) calls 0x00666600, which initialises every menu module, sets
 *   CurrentMenu = 0 and tail-jumps to 0x00673640, which picks the first menu from 0x01A559E8 (the
 *   menu to return to: 1 at 0x006713EE and 2 at 0x00671655 in the single-player menu, and 0 while
 *   a story started from story select runs, measured) through the table at 0x0067380C, and clears
 *   it. Cases 1 to 8 first call 0x00673840, which copies back every menu's work as 0x00673AE0
 *   saved it when the menus were last left that way (the recap's own work, 0x01D1C1A0, among
 *   them); 0 and any value above 12 take the default, 0x00671B40: the title's modules and music
 *   (title.adx), the help bar off (0x01AEE2FC = 0), CurrentMenu 0, every work as the init left it. Leaving the menus, RunMenus
 *   (0x006663D4) makes Life_Count (0x0174B024, two int16) and the HUD's copies ([[0x01A5A230] +
 *   8]) 4 when the first player's count is below 4, and the module load (0x0067071B) makes 0
 *   lives 4.
 *
 * The wraps.
 *
 *   0x004586A0 (LoadStory). On, before the body: when the type is 1, 2 or 3, the index is above 0,
 *   the entry is events or a stage, and StorySummaries has a row for exactly that story and entry
 *   (the game's own recap points: almost every stage, and a few of the events just before one),
 *   and that recap was not the one the menu has just played (on a resume, 0x0067E460 left for
 *   this very entry; the mark holds for the next LoadStory only, which clears it, so a later visit
 *   to the entry, as from story select's scene list, gets its recap again), the wrap holds the
 *   story state, the lives (and the HUD's copies) and 0x01A559E8, makes 0x01A559E8 13 (so case
 *   13 loads the menus and not the intro or the title, and the chooser takes its default, with no
 *   saved work copied back), sets GameMode = 2, the store LoadStoryEntry makes at a story's end,
 *   and returns as the body's `ret` does (EIP = the return address, ESP + 4) without loading the
 *   entry. It needs
 *   the state a story leaves for the menus: the call from 0x004349C6, GameMode 12, one player,
 *   0x01A559E8 = 0, 1 or 2, 0x0174B01B = 0, 0x01749934 = 0 and 0x0174B005 none of 2, 5 and 6 (the
 *   menu load's other paths); otherwise the entry loads without the recap and the run says why,
 *   once. When the wrap meets LoadStory again after the recap, it checks that the state is the
 *   story and entry it held, puts back the previous index, the flag, MissionNum, the lives (and
 *   the HUD's copies, when the HUD is the same) and 0x01A559E8, and calls the body: the entry
 *   loads as it would have without the recap.
 *
 *   0x00666600 (menu init). After the body, when a recap is held: the chooser has taken its default
 *   (0x01A559E8 was 13); the wrap does what story select's Continue does: 0x00678D40
 *   (EBX = story - 1, EDI = the entry), 0x0067E710 (EAX = the entry, ECX = the type) and
 *   0x0067E380, each called on the live state with a sentinel return address 0x40 bytes below
 *   ESP and every register put back after (a return anywhere else stops the run by name,
 *   SA2_TRAP_CONTROL_LEFT), and CurrentMenu = 5. The menu then runs the recap as on a resume.
 *
 *   0x0067E460 (Menu_StorySummary). After the body: when it returns 1 (the recap is over and
 *   LoadStory is next), the story and entry are noted, so the next LoadStory, and only that one,
 *   does not divert for the recap the menu has just played, on a resume or here. For the recap
 *   the wrap set up, a state 3 (B) the body has just entered becomes 4 (Continue): the stores
 *   before both are the same.
 *
 * Off is identical: each wrap tests the bit first and otherwise only calls `__real_` and returns.
 *
 * Risks. The frames between two story segments change: a menu load, the recap, and the menu's
 * exit, as on a resume, so replays keep it off. The chooser's default asks for the title's music
 * one menu frame before the recap's first state asks for its own (advsng_2.adx). The save is untouched: the menus' path from the
 * recap writes nothing (0x004436A0 has no caller on it), and the lives the menus raise are put
 * back before the next entry loads. The recap shown is the game's own for that point (0x00678D40
 * picks it as on a resume); the stories have none before their first entry. If the menus are
 * entered some other way while a recap is held, or LoadStory meets another story, the held state
 * is dropped and the run says so. The lines the run prints, each
 * prefixed "sa2 tweaks: recap before stages: ": per recap, "Hero story entry 14 (a stage): its recap
 * (summary row 8) plays first" and "the recap is over: Hero story entry 14 loads, with lives 4 and
 * 4 as they were"; once, "B on the recap continues the story here".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_trap.h"
#include "sa2_tweaks.h"

#define RECAP_GAME_MODE      0x0174B040u   /* int32 */
#define RECAP_STORY_TYPE     0x01DEB320u   /* byte: 1 Hero, 2 Dark, 3 Last */
#define RECAP_STORY_INDEX    0x01DEB31Eu   /* byte: the entry to load next */
#define RECAP_STORY_PREV     0x01DEB31Fu   /* byte: the entry loaded last */
#define RECAP_STORY_FIRST    0x01DEB321u   /* byte: first time in this entry */
#define RECAP_MISSION        0x0174AFE3u   /* byte: MissionNum */
#define RECAP_LIVES          0x0174B024u   /* two int16: Life_Count */
#define RECAP_HUD            0x01A5A230u   /* pointer; the lives' copies at +8 */
#define RECAP_MENU_RETURN    0x01A559E8u   /* int32: the menu a story returns to */
#define RECAP_MENU_DEFAULT   13u           /* past the chooser's table: its default */
#define RECAP_CURRENT_MENU   0x01D7BB10u   /* int32 */
#define RECAP_MENU_STATE     0x01D7BB14u   /* int32: the current menu's own state */
#define RECAP_TWO_PLAYER     0x0174AFDEu   /* byte */
#define RECAP_MENU_ALT       0x0174B01Bu   /* byte: the menu load's other path */
#define RECAP_MENU_KIND      0x0174B005u   /* byte: 2, 5 and 6 load the other menu module */
#define RECAP_SAVE_WIPE      0x01749934u   /* int32: the menu load wipes the save buffer when set */
#define RECAP_SUMMARIES      0x00C686C0u   /* StorySummaries, 20-byte rows */
#define RECAP_SUMMARY_ROWS   50u
#define RECAP_LOADSTORY_RET  0x004349C6u   /* after GameMode 12's call at 0x004349C1 */
#define RECAP_PICK_SUMMARY   0x00678D40u   /* EBX = story - 1, EDI = the entry */
#define RECAP_SET_STORY      0x0067E710u   /* EAX = the entry, ECX = the type */
#define RECAP_SUMMARY_MODS   0x0067E380u   /* the recap's menu modules */

#define MODE_TO_MENU     2u
#define MODE_LOAD_STORY  12u
#define MENU_SUMMARY     5u
#define SUMMARY_BACK     3u
#define SUMMARY_GO_ON    4u

static const struct { uint32_t table, count; const char *name; } RECAP_STORIES[3] = {
    {0x0173A148u, 48u, "Hero"}, {0x0173A370u, 44u, "Dark"}, {0x0173A580u, 8u, "Last"}};

enum { RECAP_IDLE, RECAP_HELD, RECAP_IN_MENU, RECAP_OVER };

static struct {
    int phase;
    uint8_t type, index, prev, first, mission;
    int16_t lives[2], hud_lives[2];
    uint32_t hud, menu_return;
    int hud_read;
    int row;
} g_recap;
static int g_recap_done_type = -1, g_recap_done_index = -1;
static int g_recap_said_back, g_recap_said_ret, g_recap_said_state, g_recap_said_read;

void *__real_sub_4586a0(void *state, uint32_t pc, void *memory);
void *__real_sub_666600(void *state, uint32_t pc, void *memory);
void *__real_sub_67e460(void *state, uint32_t pc, void *memory);
void *sub_678d40(void *state, uint32_t pc, void *memory);
void *sub_67e710(void *state, uint32_t pc, void *memory);
void *sub_67e380(void *state, uint32_t pc, void *memory);

static int rd8(uint32_t a, uint8_t *v) { return sa2_peek(a, 1, v) == SA2_OK; }
static int rd32(uint32_t a, uint32_t *v) { return sa2_peek(a, 4, v) == SA2_OK; }
static void wr8(uint32_t a, uint8_t v) { sa2_poke(a, 1, &v); }
static void wr32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }

/* The body's `ret`: EIP = the return address at guest [ESP], ESP + 4. */
static void recap_ret(void *s, uint32_t ret) {
    sa2_rt_set_gpr(s, SA2_EIP, ret);
    sa2_rt_set_gpr(s, SA2_ESP, sa2_rt_get_gpr(s, SA2_ESP) + 4u);
}

/* Call a lifted function on the live state with EAX, ECX, EBX and EDI set, a sentinel return
 * address 0x40 bytes below ESP (sa2_guest_call.h's slack), and every register put back after. */
static void *recap_call(void *s, void *m, void *(*fn)(void *, uint32_t, void *), uint32_t va,
                        uint32_t eax, uint32_t ecx, uint32_t ebx, uint32_t edi) {
    uint32_t saved[SA2_GPR_COUNT];
    for (int i = 0; i < SA2_GPR_COUNT; ++i) saved[i] = sa2_rt_get_gpr(s, i);
    const uint32_t sentinel = SA2_CALL_SENTINEL, base = saved[SA2_ESP] - 0x40u;
    if (sa2_poke(base, 4, &sentinel) != SA2_OK) {
        sa2_trap_note("sa2 tweaks: recap before stages: the guest stack at %#010x does not take the "
                      "return address for %#010x", base, va);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, va);
    }
    sa2_rt_set_gpr(s, SA2_EAX, eax);
    sa2_rt_set_gpr(s, SA2_ECX, ecx);
    sa2_rt_set_gpr(s, SA2_EBX, ebx);
    sa2_rt_set_gpr(s, SA2_EDI, edi);
    sa2_rt_set_gpr(s, SA2_ESP, base);
    sa2_rt_set_gpr(s, SA2_EIP, va);
    m = fn(s, va, m);
    const uint32_t eip = sa2_rt_get_gpr(s, SA2_EIP), esp = sa2_rt_get_gpr(s, SA2_ESP);
    if (eip != sentinel || esp != base + 4u) {
        sa2_trap_note("sa2 tweaks: recap before stages: %#010x returned to %#010x with esp %#010x, "
                      "not to the sentinel with esp %#010x", va, eip, esp, base + 4u);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, eip);
    }
    for (int i = 0; i < SA2_GPR_COUNT; ++i) sa2_rt_set_gpr(s, i, saved[i]);
    return m;
}

/* The StorySummaries row keyed to exactly this story (0 Hero, 1 Dark, 2 Last) and entry, or -1. */
static int recap_row(uint32_t story, uint32_t index) {
    for (uint32_t i = 0; i < RECAP_SUMMARY_ROWS; i++) {
        int32_t row[2];
        if (sa2_peek(RECAP_SUMMARIES + 20u * i, 8, row) != SA2_OK || row[0] < 0) return -1;
        if ((uint32_t)row[0] == story && (uint32_t)row[1] == index) return (int)i;
    }
    return -1;
}

static void recap_drop(const char *why) {
    fprintf(stderr, "sa2 tweaks: recap before stages: %s, so the story state held for %s entry %u "
            "is dropped\n", why, RECAP_STORIES[g_recap.type - 1].name, g_recap.index);
    g_recap.phase = RECAP_IDLE;
}

/* LoadStory after the recap: the state held put back, then the entry loads. */
static void recap_put_back(void) {
    uint8_t type = 0, index = 0;
    if (!rd8(RECAP_STORY_TYPE, &type) || !rd8(RECAP_STORY_INDEX, &index) || type != g_recap.type ||
        index != g_recap.index) {
        recap_drop("LoadStory after the recap meets another story or entry");
        return;
    }
    wr8(RECAP_STORY_PREV, g_recap.prev);
    wr8(RECAP_STORY_FIRST, g_recap.first);
    wr8(RECAP_MISSION, g_recap.mission);
    sa2_poke(RECAP_LIVES, 4, g_recap.lives);
    wr32(RECAP_MENU_RETURN, g_recap.menu_return);
    uint32_t hud = 0;
    const int hud_same = g_recap.hud_read && rd32(RECAP_HUD, &hud) && hud == g_recap.hud && hud &&
                         sa2_poke(hud + 8u, 4, g_recap.hud_lives) == SA2_OK;
    fprintf(stderr, "sa2 tweaks: recap before stages: the recap is over: %s story entry %u loads, "
            "with lives %d and %d as they were%s\n", RECAP_STORIES[type - 1].name, index,
            g_recap.lives[0], g_recap.lives[1], hud_same ? "" : " (the HUD's copies left as the menus made them)");
    g_recap.phase = RECAP_IDLE;
}

/* Whether the state is the one a story leaves for the menus; says once what is not. */
static int recap_menus_ready(uint32_t ret, const char *story, uint32_t index) {
    uint32_t mode = 0, menu_return = 0, wipe = 0;
    uint8_t two = 1, alt = 1, kind = 2;
    const int read = rd32(RECAP_GAME_MODE, &mode) && rd32(RECAP_MENU_RETURN, &menu_return) &&
                     rd32(RECAP_SAVE_WIPE, &wipe) && rd8(RECAP_TWO_PLAYER, &two) &&
                     rd8(RECAP_MENU_ALT, &alt) && rd8(RECAP_MENU_KIND, &kind);
    if (ret != RECAP_LOADSTORY_RET) {
        if (!g_recap_said_ret++)
            fprintf(stderr, "sa2 tweaks: recap before stages: LoadStory called from 0x%08X, not game "
                    "mode 12's call, so %s entry %u loads without its recap\n", ret, story, index);
        return 0;
    }
    if (!read) {
        if (!g_recap_said_read++)
            fprintf(stderr, "sa2 tweaks: recap before stages: the game state does not read, so %s "
                    "entry %u loads without its recap\n", story, index);
        return 0;
    }
    if (mode != MODE_LOAD_STORY || menu_return > 2u || two || alt || wipe ||
        kind == 2u || kind == 5u || kind == 6u) {
        if (!g_recap_said_state++)
            fprintf(stderr, "sa2 tweaks: recap before stages: the game is not in the state a story "
                    "leaves for the menus (GameMode %u, the menu to return to %u, two players %u, "
                    "0x0174B01B %u, 0x01749934 %u, 0x0174B005 %u), so %s entry %u loads without its "
                    "recap\n", mode, menu_return, two, alt, wipe, kind, story, index);
        return 0;
    }
    return 1;
}

__attribute__((used, retain))
void *__wrap_sub_4586a0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_ALWAYS_RECAP)) return __real_sub_4586a0(state, pc, memory);
    /* The menu's mark is for this LoadStory only: a later visit to the same entry (story select's
     * scene list loads it with no Menu_StorySummary in between) gets its recap again. */
    const int done_type = g_recap_done_type, done_index = g_recap_done_index;
    g_recap_done_type = g_recap_done_index = -1;
    if (g_recap.phase != RECAP_IDLE) {
        if (g_recap.phase == RECAP_OVER) recap_put_back();
        else recap_drop("LoadStory came before the recap did");
        return __real_sub_4586a0(state, pc, memory);
    }
    uint8_t type = 0, index = 0, entry_kind = 0xFF;
    if (!rd8(RECAP_STORY_TYPE, &type) || type < 1u || type > 3u || !rd8(RECAP_STORY_INDEX, &index) ||
        index == 0u || index >= RECAP_STORIES[type - 1].count ||
        !rd8(RECAP_STORIES[type - 1].table + 12u * index, &entry_kind) || entry_kind > 1u ||
        (type == done_type && index == done_index))
        return __real_sub_4586a0(state, pc, memory);
    const int row = recap_row(type - 1u, index);
    if (row < 0) return __real_sub_4586a0(state, pc, memory);
    const char *story = RECAP_STORIES[type - 1].name;
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t ret = 0;
    if (!rd32(esp, &ret)) ret = 0;
    if (!recap_menus_ready(ret, story, index)) return __real_sub_4586a0(state, pc, memory);
    g_recap.type = type;
    g_recap.index = index;
    g_recap.row = row;
    rd8(RECAP_STORY_PREV, &g_recap.prev);
    rd8(RECAP_STORY_FIRST, &g_recap.first);
    rd8(RECAP_MISSION, &g_recap.mission);
    sa2_peek(RECAP_LIVES, 4, g_recap.lives);
    rd32(RECAP_MENU_RETURN, &g_recap.menu_return);
    g_recap.hud_read = rd32(RECAP_HUD, &g_recap.hud) && g_recap.hud &&
                       sa2_peek(g_recap.hud + 8u, 4, g_recap.hud_lives) == SA2_OK;
    wr32(RECAP_MENU_RETURN, RECAP_MENU_DEFAULT);
    wr32(RECAP_GAME_MODE, MODE_TO_MENU);
    recap_ret(state, ret);
    g_recap.phase = RECAP_HELD;
    fprintf(stderr, "sa2 tweaks: recap before stages: %s story entry %u (%s): its recap (summary row "
            "%d) plays first\n", story, index, entry_kind ? "a stage" : "events", row);
    return memory;
}

__attribute__((used, retain))
void *__wrap_sub_666600(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_ALWAYS_RECAP)) return __real_sub_666600(state, pc, memory);
    memory = __real_sub_666600(state, pc, memory);
    if (g_recap.phase == RECAP_IN_MENU || g_recap.phase == RECAP_OVER) {
        recap_drop("the menus were entered again before the recap ended");
        return memory;
    }
    if (g_recap.phase != RECAP_HELD) return memory;
    memory = recap_call(state, memory, sub_678d40, RECAP_PICK_SUMMARY, 0, 0, g_recap.type - 1u,
                        g_recap.index);
    memory = recap_call(state, memory, sub_67e710, RECAP_SET_STORY, g_recap.index, g_recap.type, 0, 0);
    memory = recap_call(state, memory, sub_67e380, RECAP_SUMMARY_MODS, 0, 0, 0, 0);
    wr32(RECAP_CURRENT_MENU, MENU_SUMMARY);
    g_recap.phase = RECAP_IN_MENU;
    return memory;
}

__attribute__((used, retain))
void *__wrap_sub_67e460(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_ALWAYS_RECAP)) return __real_sub_67e460(state, pc, memory);
    memory = __real_sub_67e460(state, pc, memory);
    uint32_t menu_state = 0;
    if (g_recap.phase == RECAP_IN_MENU && rd32(RECAP_MENU_STATE, &menu_state) &&
        menu_state == SUMMARY_BACK) {
        wr32(RECAP_MENU_STATE, SUMMARY_GO_ON);
        if (!g_recap_said_back++)
            fprintf(stderr, "sa2 tweaks: recap before stages: B on the recap continues the story here\n");
    }
    if (sa2_rt_get_gpr(state, SA2_EAX) == 1u) {
        uint8_t type = 0, index = 0;
        if (rd8(RECAP_STORY_TYPE, &type) && rd8(RECAP_STORY_INDEX, &index)) {
            g_recap_done_type = type;
            g_recap_done_index = index;
        }
        if (g_recap.phase == RECAP_IN_MENU) g_recap.phase = RECAP_OVER;
    }
    return memory;
}
