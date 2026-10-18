/* 2p-layout-in-1p: a one-player stage loads the game's leftover two-player object layout (its
 * rings, item boxes, enemies and other set pieces) on the stages where that layout fits the stage,
 * as the Mod Loader's Codes.lst code "Always Load 2P Layout" (MainMemory: writenop 488FAB 6) does
 * on every stage. Hard Mode (the fifth mission) keeps its own layout, and so do the two stages whose
 * two-player files do not fit, Pumpkin Hill (5) and Iron Gate (11) (runtime/sa2_tweaks.h: the
 * tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_488f60
 *
 * Where the layout is chosen, read from the unpacked image. 0x00488F60, LoadStageSETFile (the Mod
 * Loader's SA2Functions.h), builds the stage's two SET file names from ECX, a base name, or
 * "set%04d" with CurrentLevel ([0x01934B70], a short) when ECX is 0, and hands them to LoadSETFile
 * (0x00488DD0, ECX = the _s name, [ESP+4] = the _u name), which loads the objects. Between them it
 * reads two bytes, TwoPlayerMode ([0x0174AFDE]) and MissionNum ([0x0174AFE3]):
 *
 *   0x00488F9F  cmp byte [0x0174AFDE],0 / 0x00488FAB je 0x00489036. Non-zero (a two-player game):
 *               "%s_2p_s.bin" (0x008B978C) and "%s_2p_u.bin" (0x008B97A4), each tried with
 *               0x00428CD0 and, when that file is missing, replaced by "%s_s.bin" (0x008B9798) or
 *               "%s_u.bin" (0x008B97B0) (the checks at 0x00488FD8 and 0x0048901E).
 *   0x00489036  zero: cmp byte [0x0174AFE3],4. Mission 5 (MissionNum 4, Hard Mode): "%s_hd_s.bin"
 *               (0x008B97BC) and "%s_hd_u.bin" (0x008B97C8) with the same fallback; any other
 *               mission: "%s_s.bin" and "%s_u.bin".
 *
 * The Codes.lst code removes the je, so every one-player stage takes the two-player names, Hard
 * Mode included. The wrap does what a one-player game with TwoPlayerMode set does inside this one
 * call: when [0x0174AFDE] reads 0, [0x0174AFE3] is not 4, ECX is 0 and CurrentLevel is a stage
 * the table below lets through, it writes 1 to [0x0174AFDE], calls the lifted body, and writes the
 * 0 back before it returns. Nothing else in the call reads the byte: LoadStageSETFile reads it only
 * at 0x00488F9F, and its callees (0x00428CD0, LoadSETFile, _sprintf 0x007A7C6D, the stack cookie
 * check 0x007A597F, and every function they call directly, 160 in all) never reference
 * 0x0174AFDE. The game sees TwoPlayerMode 0 again before the stage's first frame, so only the file
 * names change. The 51 callers call it directly, one lifted function per object, and nothing takes
 * its address, so every call passes through the wrap. 50 of them are stage loaders that set ECX to
 * 0; the other, 0x0054B190, passes a base name it cuts from a file name (the Chao gardens',
 * "set_chao_hero.bin" from 0x0054D251), which CurrentLevel does not name, so the wrap leaves a call
 * with ECX non-zero to the game. A stage that has a two-player _u file and no _s file loads its
 * one-player _s file, as the game's own fallback does: Metal Harbor (set0010) has set0010_2p_u.bin
 * and no set0010_2p_s.bin, so it loads set0010_s.bin (361 objects) and set0010_2p_u.bin (196
 * objects, against set0010_u.bin's 296).
 *
 * Which stages, from the installation's SET files and the object list each stage's loader passes
 * to 0x00487E40 (a push of the list's head just before the call) [private: read from the game's
 * files]. The spawner (0x00488462, 0x004884ED) masks a record's id with 0x7FFF, shifts it left by 4
 * and adds the list's entries pointer, and nothing compares the id with the list's count, so an id
 * at or past the count reads past the list. Of the 15 one-player stages with two-player files, 13
 * have every id inside the list and keep a GOALRING (the table's NULL rows): 3, 4, 6, 8, 10, 13,
 * 14, 25, 26, 30, 32, 40 and 44. Two do not, and keep their own layout:
 *   Iron Gate (11)     set0011_2p_s.bin (there is no set0011_2p_u.bin) holds 112 records, 38 of them
 *                      id 92, against a 92-entry list (head 0x00BEDEB0, entries 0x00BED8F0). Entry
 *                      92 is the head itself, whose Function word is 0x008C4904, a string in
 *                      .rdata: a player near those records (about x +/-200, z +/-110) would load
 *                      an object whose main routine is data, a trap here and a crash in the
 *                      original. The mix has no GOALRING, SAVEPOINT or ELV either. The file was laid
 *                      out against another list (versus Iron Gate is level 58).
 *   Pumpkin Hill (5)   set0005_2P_s.bin and set0005_2P_u.bin fit the list but place no GOALRING,
 *                      which the stage's own files do.
 * The versus stages (15, 39, 41, 45 to 59) load only in a two-player game, which the wrap leaves
 * alone; any other stage has no two-player files and loads its own either way.
 * Documentation/scripts/tests/test_tweak_2p_layout_in_1p.py reads the installation and the image
 * and holds the table to them.
 *
 * Hard Mode is left alone on purpose, where the Codes.lst code is not: a player who picks the
 * fifth mission asks for its layout, and with the je gone it would load the two-player one. A
 * two-player game is left alone too: it loads the two-player layout already.
 *
 * Off is the lifted code alone: the wrap tests sa2_tweak_on first and only calls __real_. The save
 * is not touched by the wrap: TwoPlayerMode is not saved, and it is back to 0 when the call
 * returns. A stage cleared on the two-player layout counts as cleared, with its rank and emblem, as
 * the Codes.lst code's would; an upgrade or Chao key the layout leaves out is not collected, so it
 * is not saved.
 *
 * Risks: the two-player layouts were built for a race between two players and leave out much of
 * what a story mission uses [private: object types counted from the game's files]. Every table
 * stage but 10 and 40 loses its LEVUPDAI (the upgrade pedestal); 3, 6, 8, 13, 14, 25, 26, 30, 32
 * and 44 lose CONTCHAO (the Chao key box); many lose MINIMAL (small animals), CHAOPIPE, E GOLD and
 * KNUDAI; 4, 13, 14, 25, 26 and 44 lose SWITCH objects, City Escape (13) its TRUCK, and Death
 * Chamber (25) its WARP. Metal Harbor loses only CWALL and SOLIDBOX, Final Chase (40) E GOLD,
 * KDASIBA, MSGER and RING_CIRCLE. So a mission may find fewer rings, a different route, or an
 * object it counts on missing, and may not be finishable. A guest read or write that fails leaves
 * the call to the lifted body with the game's own layout, and says so once.
 *
 * The run says what it did, once per stage (the first 16 stages, at most): "sa2 tweaks: 2p layout:
 * stage 10 loads its two-player object files where it has them", "... stage 11 keeps its own
 * layout: <why>", "... stage 9 has no two-player layout checked for one player, so it loads its
 * own", "... stage 10, mission 5 keeps its Hard Mode layout", or, once each, "... a two-player game
 * loads its two-player layout already" and "... a SET file loaded by name (ECX ...), not by stage
 * number, keeps its own layout".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define TWO_PLAYER_MODE 0x0174AFDEu   /* TwoPlayerMode, a byte */
#define MISSION_NUM     0x0174AFE3u   /* MissionNum, a byte; 4 is mission 5, Hard Mode */
#define CURRENT_LEVEL   0x01934B70u   /* CurrentLevel, a short */
#define HARD_MODE       4u

/* The one-player stages with two-player SET files (the header's "Which stages"). why NULL: the
 * stage loads them; else why it keeps its own layout. */
static const struct {
    int16_t level;
    const char *why;
} k_2p_stages[] = {
    {3, NULL}, {4, NULL},
    {5, "its two-player files place no goal ring"},
    {6, NULL}, {8, NULL}, {10, NULL},
    {11, "its two-player file is not for this stage (object ids past the stage's object list, "
         "no goal ring)"},
    {13, NULL}, {14, NULL}, {25, NULL}, {26, NULL}, {30, NULL}, {32, NULL}, {40, NULL}, {44, NULL},
};

static int16_t g_2p_layout_named[16];
static unsigned g_2p_layout_nnamed;
static int g_2p_layout_versus_said, g_2p_layout_read_said, g_2p_layout_write_said,
    g_2p_layout_level_said, g_2p_layout_byname_said;

/* Whether this stage's line (hard: the Hard Mode one) is still to say; marks it said. Hard Mode's
 * stages are kept apart by their sign bit flipped, so a stage can have both lines. */
static int first_time(int16_t level, int hard) {
    const int16_t key = hard ? (int16_t)~level : level;
    for (unsigned i = 0; i < g_2p_layout_nnamed; i++)
        if (g_2p_layout_named[i] == key) return 0;
    if (g_2p_layout_nnamed == sizeof g_2p_layout_named / sizeof g_2p_layout_named[0]) return 0;
    g_2p_layout_named[g_2p_layout_nnamed++] = key;
    return 1;
}

void *__real_sub_488f60(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_488f60(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_2P_LAYOUT_IN_1P)) return __real_sub_488f60(state, pc, memory);
    uint8_t two = 0, mission = 0;
    int16_t level = -1;
    if (sa2_peek(TWO_PLAYER_MODE, 1, &two) != SA2_OK || sa2_peek(MISSION_NUM, 1, &mission) != SA2_OK) {
        if (!g_2p_layout_read_said++)
            fprintf(stderr, "sa2 tweaks: 2p layout: TwoPlayerMode (0x%08X) or MissionNum (0x%08X) "
                    "does not read, so the stage loads its own layout\n", TWO_PLAYER_MODE, MISSION_NUM);
        return __real_sub_488f60(state, pc, memory);
    }
    const int level_read = sa2_peek(CURRENT_LEVEL, 2, &level) == SA2_OK;
    if (!level_read) level = -1;
    if (two != 0) {
        if (!g_2p_layout_versus_said++)
            fprintf(stderr, "sa2 tweaks: 2p layout: a two-player game loads its two-player layout "
                    "already\n");
        return __real_sub_488f60(state, pc, memory);
    }
    if (mission == HARD_MODE) {
        if (first_time(level, 1))
            fprintf(stderr, "sa2 tweaks: 2p layout: stage %d, mission 5 keeps its Hard Mode layout\n",
                    level);
        return __real_sub_488f60(state, pc, memory);
    }
    const uint32_t name = sa2_rt_get_gpr(state, SA2_ECX);
    if (name != 0) {
        if (!g_2p_layout_byname_said++)
            fprintf(stderr, "sa2 tweaks: 2p layout: a SET file loaded by name (ECX 0x%08X), not by "
                    "stage number, keeps its own layout\n", name);
        return __real_sub_488f60(state, pc, memory);
    }
    if (!level_read) {
        if (!g_2p_layout_level_said++)
            fprintf(stderr, "sa2 tweaks: 2p layout: CurrentLevel (0x%08X) does not read, so the "
                    "stage loads its own layout\n", CURRENT_LEVEL);
        return __real_sub_488f60(state, pc, memory);
    }
    int listed = 0;
    const char *why = NULL;
    for (size_t i = 0; i < sizeof k_2p_stages / sizeof k_2p_stages[0]; i++)
        if (k_2p_stages[i].level == level) listed = 1, why = k_2p_stages[i].why;
    if (!listed) {
        if (first_time(level, 0))
            fprintf(stderr, "sa2 tweaks: 2p layout: stage %d has no two-player layout checked for "
                    "one player, so it loads its own\n", level);
        return __real_sub_488f60(state, pc, memory);
    }
    if (why != NULL) {
        if (first_time(level, 0))
            fprintf(stderr, "sa2 tweaks: 2p layout: stage %d keeps its own layout: %s\n", level, why);
        return __real_sub_488f60(state, pc, memory);
    }
    const uint8_t one = 1;
    if (sa2_poke(TWO_PLAYER_MODE, 1, &one) != SA2_OK) {
        if (!g_2p_layout_write_said++)
            fprintf(stderr, "sa2 tweaks: 2p layout: TwoPlayerMode (0x%08X) does not write, so the "
                    "stage loads its own layout\n", TWO_PLAYER_MODE);
        return __real_sub_488f60(state, pc, memory);
    }
    void *ret = __real_sub_488f60(state, pc, memory);
    if (sa2_poke(TWO_PLAYER_MODE, 1, &two) != SA2_OK)
        fprintf(stderr, "sa2 tweaks: 2p layout: TwoPlayerMode (0x%08X) did not take its 0 back after "
                "stage %d's layout loaded\n", TWO_PLAYER_MODE, level);
    if (first_time(level, 0))
        fprintf(stderr, "sa2 tweaks: 2p layout: stage %d loads its two-player object files where it "
                "has them\n", level);
    return ret;
}
