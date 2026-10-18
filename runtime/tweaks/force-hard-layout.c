/* force-hard-layout: a one-player stage loads its Hard Mode (mission 5) object layout, its rings,
 * item boxes, enemies and other set pieces, whichever mission the player picked, as the Mod Loader's
 * Codes.lst code "Always Use Hard Mode Layout" (MainMemory: writenop 0048903D 2) does; the mission
 * itself, its goal, its rank and what the save records stay the picked mission's. A stage whose
 * Hard Mode layout leaves out an upgrade the save does not have yet keeps its own layout until the
 * player has that upgrade (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_488f60
 *
 * Where the layout is chosen, read from the unpacked image. 0x00488F60, LoadStageSETFile (the Mod
 * Loader's SA2Functions.h), builds the stage's two SET file names from ECX, a base name, or
 * "set%04d" (0x008B9784) with CurrentLevel ([0x01934B70], a short) when ECX is 0, and hands them
 * to LoadSETFile (0x00488DD0, ECX = the _s name, [ESP+4] = the _u name), which loads the objects.
 * Between the two it reads two bytes, each once:
 *
 *   0x00488F9F  cmp byte [0x0174AFDE],0 (TwoPlayerMode) / 0x00488FAB je 0x00489036: a two-player
 *               game takes the "%s_2p_s.bin" and "%s_2p_u.bin" names.
 *   0x00489036  cmp byte [0x0174AFE3],4 (MissionNum; 4 is mission 5) / 0x0048903D jne 0x0048908B.
 *               On 4: "%s_hd_s.bin" (0x008B97BC) and "%s_hd_u.bin" (0x008B97C8), each tried with
 *               the file loader 0x00428CD0 and, when that file is missing, replaced by
 *               "%s_s.bin" (0x008B9798) or "%s_u.bin" (0x008B97B0) (0x00489066, and 0x0048901E
 *               through the jmp at 0x00489086). Otherwise "%s_s.bin" and "%s_u.bin".
 *
 * The Codes.lst code removes the jne at 0x0048903D. The wrap does what a one-player game with
 * MissionNum 4 does inside this one call: when TwoPlayerMode reads 0, MissionNum is not 4 and ECX
 * is 0, it writes 4 to [0x0174AFE3], calls the lifted body, and writes the mission the byte held
 * back before it returns. Nothing else in the call reads the byte: LoadStageSETFile reads it only
 * at 0x00489036, and none of the 268 functions its direct calls reach (_sprintf 0x007A7C6D, the
 * file loader 0x00428CD0, LoadSETFile 0x00488DD0, the stack cookie check 0x007A597F and theirs)
 * references 0x0174AFE3; LoadSETFile's one indirect call is the allocator ([0x01D19CAC]). The
 * game sees its own mission again before the stage's first frame, so the mission's goal, its
 * timer, the rank (AwardRankForScore) and the save are the picked mission's; only the file names
 * change. Its 51 call sites call it directly and nothing takes its address, so every load passes
 * through the wrap. 50 are stage loaders (the Init of each level header), each with ECX set to 0;
 * the other, in 0x0054B190, passes a base name it cuts from a file name (a Chao garden's,
 * "set_chao_hero.bin"), and the wrap leaves a call with ECX non-zero to the game. A stage with no Hard Mode files (a boss, a kart race) loads its
 * own files through the game's fallback, as the Codes.lst code's would.
 *
 * Which stages keep their own layout, from the installation's SET files and the object list each
 * stage's loader passes to 0x00487E40 [private: read from the game's files]. The upgrade pedestal,
 * LEVUPDAI (main routine 0x006D8540), takes its upgrade from the record: the integer part of its
 * Scale.x, low byte, modulo 28, picks a 36-byte row at 0x00B35390, whose word at +0x0C is the
 * upgrade's byte in the save's upgrade flags at 0x01DEB300 (0x006D8649 tests it: non-zero, the
 * upgrade is had). Ten stages' Hard Mode layouts place no pedestal where their own place one:
 *   Green Forest (3)  Sonic's Ancient Light      0x01DEB301   Weapons Bed (12)  Eggman's Large Cannon    0x01DEB315
 *   White Jungle (4)  Shadow's Air Shoes         0x01DEB310   Wild Canyon (16)  Knuckles' Mystic Melody  0x01DEB30E
 *   Sky Rail (6)      Shadow's Ancient Light     0x01DEB311   Dry Lagoon (18)   Rouge's Mystic Melody    0x01DEB31C
 *   Metal Harbor (10) Sonic's Light Shoes        0x01DEB300   Crazy Gadget (22) Sonic's Flame Ring       0x01DEB303
 *   Pyramid Cave (28) Sonic's Bounce Bracelet    0x01DEB304   Meteor Herd (32)  Knuckles' Sunglasses     0x01DEB30B
 * On these the wrap reads the upgrade's byte first: 0, and the stage keeps its own layout, so the
 * upgrade can still be found (story mode needs several of them to go on); non-zero, the stage
 * loads its Hard Mode layout. This is the story-progress risk that "Fixed Forced Hard Mode" (En
 * Passant) answers for the Codes.lst code. Every other stage's Hard Mode layout keeps the pedestal
 * its own layout has, with the same upgrade. Documentation/scripts/tests/test_tweak_force_hard_layout.py
 * reads the installation and the image and holds the table to them.
 *
 * Mission 5 is left alone (it loads this layout already), and so is a two-player game (it loads
 * the two-player layout, ahead of MissionNum).
 *
 * Off is the lifted code alone: the wrap tests sa2_tweak_on first and only calls __real_. The save
 * is not touched by the wrap: MissionNum is not saved data, and it holds the picked mission again
 * when the call returns. A mission cleared on the Hard Mode layout counts as that mission, with
 * its rank and emblem, in the save, as the Codes.lst code's would.
 *
 * Risks [private: object types counted from the game's files]: each Hard Mode layout was built for
 * mission 5's goal, so a mission 2 to 4 (100 rings, a lost Chao, a time limit) may find fewer
 * rings, no lost Chao goal, or a different route. Most Hard Mode layouts hold one GOALRING where
 * their own hold two; Meteor Herd's and Mad Space's hold none (their missions end on the last
 * emerald piece) and lose BIG THE CAT, and Meteor Herd its three Chao key boxes. Wild Canyon's
 * leaves out its eight EMERALD_F records and keeps its EMERALD records, so the pieces a hunting
 * mission places may come from other spots. A guest read or write that fails leaves the call to
 * the lifted body with the stage's own layout, and says so once.
 *
 * One wrap of LoadStageSETFile. 2p-layout-in-1p acts on 0x00488F60 too, and a function has one
 * wrap (the tools refuse a second by name), so this file's __wrap_sub_488f60 is the one, and
 * 2p-layout-in-1p.c acts in it (its TWEAK_WRAPS line names it). This tweak's work is a function of
 * its own, sa2_force_hard_layout_load_set, which takes the lifted body to call as `real` and tests
 * its bit first. When this tweak is on, the wrap calls it with a `real` that goes through
 * 2p-layout-in-1p's function, sa2_2p_layout_in_1p_load_set (which only calls the body unless that
 * tweak is on too): MissionNum then reads 4 inside, which 2p-layout-in-1p leaves to the Hard Mode
 * layout, and on a stage this tweak keeps on its own layout 2p-layout-in-1p acts as it does alone.
 * When this tweak is off, the wrap calls 2p-layout-in-1p's function with __real_sub_488f60, and
 * with both off only __real_sub_488f60 runs.
 *
 * The run says what it did, once per stage (the first 16 stages, at most): "sa2 tweaks: hard
 * layout: stage 13 loads its Hard Mode object files where it has them", "... stage 10 keeps its
 * own layout: its Hard Mode layout has no upgrade pedestal, and this save does not have Sonic's
 * Light Shoes yet", or "... stage 10 is mission 5, which loads its Hard Mode layout already"; and,
 * once each, "... a two-player game keeps its two-player layout" and "... a SET file loaded by
 * name (ECX ...), not by stage number, keeps its own layout".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define HARD_LAYOUT_TWO_PLAYER_MODE 0x0174AFDEu   /* TwoPlayerMode, a byte */
#define HARD_LAYOUT_MISSION_NUM     0x0174AFE3u   /* MissionNum, a byte; 4 is mission 5 */
#define HARD_LAYOUT_CURRENT_LEVEL   0x01934B70u   /* CurrentLevel, a short */
#define HARD_LAYOUT_UPGRADES        0x01DEB300u   /* the save's upgrade flags, a byte each */
#define HARD_LAYOUT_MISSION_5       4u

/* The stages whose Hard Mode layout places no upgrade pedestal where their own does, and the
 * upgrade's byte past HARD_LAYOUT_UPGRADES (the header's table). */
static const struct {
    int16_t level;
    uint8_t flag;
    const char *upgrade;
} k_hard_layout_lost_upgrade[] = {
    {3, 0x01, "Sonic's Ancient Light"},   {4, 0x10, "Shadow's Air Shoes"},
    {6, 0x11, "Shadow's Ancient Light"},  {10, 0x00, "Sonic's Light Shoes"},
    {12, 0x15, "Eggman's Large Cannon"},  {16, 0x0E, "Knuckles' Mystic Melody"},
    {18, 0x1C, "Rouge's Mystic Melody"},  {22, 0x03, "Sonic's Flame Ring"},
    {28, 0x04, "Sonic's Bounce Bracelet"}, {32, 0x0B, "Knuckles' Sunglasses"},
};

static int16_t g_hard_layout_named[16];
static unsigned g_hard_layout_nnamed;
static int g_hard_layout_versus_said, g_hard_layout_read_said, g_hard_layout_write_said,
    g_hard_layout_level_said, g_hard_layout_byname_said, g_hard_layout_upgrade_said;

/* Whether this stage's line (kind 0: loaded or kept, 1: mission 5) is still to say; marks it said.
 * Mission 5's lines are kept apart by the stage's bits flipped, so a stage can have both. */
static int hard_layout_first_time(int16_t level, int kind) {
    const int16_t key = kind ? (int16_t)~level : level;
    for (unsigned i = 0; i < g_hard_layout_nnamed; i++)
        if (g_hard_layout_named[i] == key) return 0;
    if (g_hard_layout_nnamed == sizeof g_hard_layout_named / sizeof g_hard_layout_named[0]) return 0;
    g_hard_layout_named[g_hard_layout_nnamed++] = key;
    return 1;
}

void *__real_sub_488f60(void *state, uint32_t pc, void *memory);
void *sa2_force_hard_layout_load_set(void *state, uint32_t pc, void *memory,
                                     void *(*real)(void *, uint32_t, void *));
/* runtime/tweaks/2p-layout-in-1p.c, which acts in this file's wrap of sub_488f60. */
void *sa2_2p_layout_in_1p_load_set(void *state, uint32_t pc, void *memory,
                                   void *(*real)(void *, uint32_t, void *));

/* LoadStageSETFile with MissionNum reading 4 for the length of one call of `real`, the lifted body
 * (or 2p-layout-in-1p's way to it). Called by the wrap below. */
void *sa2_force_hard_layout_load_set(void *state, uint32_t pc, void *memory,
                                     void *(*real)(void *, uint32_t, void *)) {
    if (!sa2_tweak_on(SA2_TWEAK_FORCE_HARD_LAYOUT)) return real(state, pc, memory);
    uint8_t two = 0, mission = 0;
    if (sa2_peek(HARD_LAYOUT_TWO_PLAYER_MODE, 1, &two) != SA2_OK ||
        sa2_peek(HARD_LAYOUT_MISSION_NUM, 1, &mission) != SA2_OK) {
        if (!g_hard_layout_read_said++)
            fprintf(stderr, "sa2 tweaks: hard layout: TwoPlayerMode (0x%08X) or MissionNum (0x%08X) "
                    "does not read, so the stage loads its own layout\n", HARD_LAYOUT_TWO_PLAYER_MODE,
                    HARD_LAYOUT_MISSION_NUM);
        return real(state, pc, memory);
    }
    if (two != 0) {
        if (!g_hard_layout_versus_said++)
            fprintf(stderr, "sa2 tweaks: hard layout: a two-player game keeps its two-player layout\n");
        return real(state, pc, memory);
    }
    const uint32_t name = sa2_rt_get_gpr(state, SA2_ECX);
    if (name != 0) {
        if (!g_hard_layout_byname_said++)
            fprintf(stderr, "sa2 tweaks: hard layout: a SET file loaded by name (ECX 0x%08X), not by "
                    "stage number, keeps its own layout\n", name);
        return real(state, pc, memory);
    }
    int16_t level = -1;
    if (sa2_peek(HARD_LAYOUT_CURRENT_LEVEL, 2, &level) != SA2_OK) {
        if (!g_hard_layout_level_said++)
            fprintf(stderr, "sa2 tweaks: hard layout: CurrentLevel (0x%08X) does not read, so the "
                    "stage loads its own layout\n", HARD_LAYOUT_CURRENT_LEVEL);
        return real(state, pc, memory);
    }
    if (mission == HARD_LAYOUT_MISSION_5) {
        if (hard_layout_first_time(level, 1))
            fprintf(stderr, "sa2 tweaks: hard layout: stage %d is mission 5, which loads its Hard Mode "
                    "layout already\n", level);
        return real(state, pc, memory);
    }
    for (size_t i = 0; i < sizeof k_hard_layout_lost_upgrade / sizeof k_hard_layout_lost_upgrade[0]; i++) {
        if (k_hard_layout_lost_upgrade[i].level != level) continue;
        const uint32_t at = HARD_LAYOUT_UPGRADES + k_hard_layout_lost_upgrade[i].flag;
        uint8_t had = 0;
        if (sa2_peek(at, 1, &had) != SA2_OK) {
            if (!g_hard_layout_upgrade_said++)
                fprintf(stderr, "sa2 tweaks: hard layout: the save's upgrade flag at 0x%08X does not "
                        "read, so stage %d keeps its own layout\n", at, level);
            return real(state, pc, memory);
        }
        if (had == 0) {
            if (hard_layout_first_time(level, 0))
                fprintf(stderr, "sa2 tweaks: hard layout: stage %d keeps its own layout: its Hard Mode "
                        "layout has no upgrade pedestal, and this save does not have %s yet\n", level,
                        k_hard_layout_lost_upgrade[i].upgrade);
            return real(state, pc, memory);
        }
    }
    const uint8_t hard = HARD_LAYOUT_MISSION_5;
    if (sa2_poke(HARD_LAYOUT_MISSION_NUM, 1, &hard) != SA2_OK) {
        if (!g_hard_layout_write_said++)
            fprintf(stderr, "sa2 tweaks: hard layout: MissionNum (0x%08X) does not write, so the stage "
                    "loads its own layout\n", HARD_LAYOUT_MISSION_NUM);
        return real(state, pc, memory);
    }
    void *ret = real(state, pc, memory);
    if (sa2_poke(HARD_LAYOUT_MISSION_NUM, 1, &mission) != SA2_OK)
        fprintf(stderr, "sa2 tweaks: hard layout: MissionNum (0x%08X) did not take mission %u back "
                "after stage %d's layout loaded\n", HARD_LAYOUT_MISSION_NUM, mission + 1u, level);
    if (hard_layout_first_time(level, 0))
        fprintf(stderr, "sa2 tweaks: hard layout: stage %d loads its Hard Mode object files where it "
                "has them\n", level);
    return ret;
}

/* The lifted body as this tweak's function reaches it: through 2p-layout-in-1p's half. */
static void *load_set_2p(void *state, uint32_t pc, void *memory) {
    return sa2_2p_layout_in_1p_load_set(state, pc, memory, __real_sub_488f60);
}

__attribute__((used, retain))
void *__wrap_sub_488f60(void *state, uint32_t pc, void *memory) {
    if (sa2_tweak_on(SA2_TWEAK_FORCE_HARD_LAYOUT))
        return sa2_force_hard_layout_load_set(state, pc, memory, load_set_2p);
    if (!sa2_tweak_on(SA2_TWEAK_2P_LAYOUT_IN_1P)) return __real_sub_488f60(state, pc, memory);
    return sa2_2p_layout_in_1p_load_set(state, pc, memory, __real_sub_488f60);
}
