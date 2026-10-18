/* all-upgrades: player 1 has every upgrade from the start of each level, as Codes.lst's "Have All
 * Upgrades" code gives them, held each frame (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: none (it acts in start-assists.c's wrap of sub_44c1a0)
 *
 * Where the upgrades live, read from the unpacked image. SetPhysicsParamsAndGiveUpgrades
 * (0x004599C0), which every character loader calls, stores the character's task in
 * MainCharacter[player] (0x01DEA6E0, at 0x00459A1C) and the task's +0x40 in MainCharObj2[player]
 * (0x01DE9600, at 0x00459A31): the CharObj2Base. Its CharID is the byte at +1 and its CharID2
 * (which character it really is) the byte at +3. Upgrades is the dword at +0x24: the loader ORs a
 * bit into it for each "Got" byte that is set (0x01DEB300-0x01DEB31C, the save's copy) through a
 * jump table on CharID (0x00459B43, `cmp eax,7; ja` at 0x00459B3A). The six sets are Sonic 0x3F,
 * Mech Tails 0x3C0, Knuckles 0x7C00, Shadow 0xF0000, Mech Eggman 0x1F00000 and Rouge 0x1E000000
 * (the ORs from 0x00459B53 to 0x00459D21); together they are 0x1FFF7FFF, the value of Codes.lst's
 * `or32 p01DEA6E0|40|24 0x1FFF7FFF`. The bit 0x8000, which that value leaves out, is Super
 * Sonic's.
 *
 * The tweak, after the lifted body of 0x0044C1A0 each frame. start-assists.c wraps that function
 * (called only by GameLoop, at 0x00433FF0, after GameModeHandler has run the frame's tasks; it
 * returns a frame-time float on the x87 stack, which the wrap leaves alone), and one function can
 * be wrapped once, so its wrap calls sa2_all_upgrades_frame after the lifted body when this tweak
 * is on, after start-assists' own work when both are on. With player 1's CharObj2Base read as
 * [[0x01DEA6E0]+0x40], both non-zero (MainCharacter[0] is 0 outside a level and in a kart race),
 * and TwoPlayerMode (the byte at 0x0174AFDE) 0:
 *
 *   Upgrades |= 0x1FFF7FFF, when CharID == CharID2 and CharID2 <= 7: Sonic, Shadow, Tails and
 *   Eggman on foot, Knuckles, Rouge, Mech Tails and Mech Eggman, each playing its own code;
 *   except in the Sonic vs Shadow fights, levels 19 and 42 (CurrentLevel, the int16 at
 *   0x01934B70, read before the write), where Sonic gets only his own 0x3F and Shadow only his
 *   own 0xF0000 (the models below say why).
 *
 * A bit already set is not written again, so a write happens on the first frame after the character
 * is loaded (each restart loads it anew) and after anything clears a bit. The write is to the
 * character's heap object, never to the Got bytes or the save buffer: the only stores into
 * 0x01DEB300-0x01DEB31C are an upgrade pedestal's pickup (0x006D87F9) and a run of constant stores
 * at 0x0051AED8, and nothing copies Upgrades into them, so a bit set here is never saved. What the
 * bits reach is, as with start-assists: an upgrade pedestal decides it is taken from its Got byte
 * alone (0x006D8649), so a pedestal behind an upgrade the player has not found (a Light Dash line,
 * a Bounce spot, a Mystic Melody path) can be reached and touched, which writes its Got byte, and
 * the next save keeps it; so do times, ranks, emblems and Lost Chao. The Android note says so.
 *
 * Left alone on purpose, each said once: Amy (CharID 0, CharID2 8), Metal Sonic (1/12), the Chao
 * Walkers (6/13, 7/14), Tikal, Chaos and the Super forms, which play another character's code.
 * They are the characters behind the crashes Codes.lst patches for its cheat: Metal Sonic's bounce
 * pose (the data at 0x00A1A9C8; metal-bounce-fix covers it), the Fire Somersault aura on Amy and
 * Metal Sonic (DrawFireSomersaultAura 0x007599D0 and 0x00757810, which no tweak covers) and a
 * Walker's cannon shot (0x0075D914, the CharID read at 0x0075D912; walker-cannon-fix covers it).
 * Giving them upgrades would need all three fixes first. Two-player mode: one player with every
 * upgrade would decide a fight. Player 2 is never touched. The practice-stage tweak spawns only
 * the six main characters, so the rule holds on every one-player route.
 *
 * Off is the lifted code alone: unless start-assists or this tweak is on, the wrap only calls
 * `__real_sub_44c1a0`, and this function's own first test is sa2_tweak_on(SA2_TWEAK_ALL_UPGRADES).
 *
 * Risks, all with the tweak on: upgrade-gated paths, Lost Chao, emblems and upgrade pedestals open
 * early, and those results are saved. A character holds the other characters' bits too, as with the
 * Codes.lst cheat. The models: each loader sets its own display (Knuckles' 0x0072EF20 at
 * 0x007283DA, Rouge's 0x00730970 at 0x007287F7, hide-upgrade-models.c lists the displays and their
 * tests). Knuckles', Rouge's and the walkers' displays test only their own character's bits. One
 * display, Sonic_Display (0x00720090), serves both Sonic and Shadow and tests the bits alone, never
 * CharID: 0x10 at 0x007208C1 draws the model slot at 0x01DE96F4, 0x4 at 0x007209C0 draws
 * 0x01DE96FC, 0x1 at 0x007209EA draws 0x01DE9694 and 0x01DE968C, 0x10000 at 0x00720A34 draws
 * Shadow's Air Shoes from 0x01DE989C and 0x01DE9894, and `test al,8` at 0x007207DA picks Sonic's
 * Flame Ring model whenever 0x8 is set. In a stage of one hedgehog the other's slots hold no model,
 * so nothing more is drawn. In the Sonic vs Shadow fights, levels 19 and 42, both hedgehogs' slots
 * are loaded, and with every bit Shadow would be drawn with Sonic's bracelet, gloves, Light Shoes
 * and Flame Ring in Shadow's textures and Sonic with Shadow's Air Shoes (the case Codes.lst's Hide
 * Bounce Bracelet Fix is for). So in those two levels each gets only his own set: Shadow's
 * 0x40000 without 0x8 picks his own Flame Ring, and Sonic's set holds no 0x10000. rival-upgrades
 * leaves Shadow alone in the same two levels for the same reason, and with both tweaks on Shadow
 * still gets only his own bits there. The walkers' hit points (CharObj2Base+0x48) are the game's own: InitMechHP (0x007414D0)
 * gives 12.0 when CharID is 7 and Upgrades has the Protective Armor (0x800000, tested at
 * 0x007414FF) and 10.0 otherwise, CharID 6 always 10.0; AddHP refills either walker to 12.0 with
 * that bit (0x0046F4E1), 10.0 without. The loader's InitMechHP (0x00740E96 for Mech Eggman) runs
 * before this tweak's first write, so it sees only the saved bits, but the walker's own per-frame
 * routine calls InitMechHP again (0x0066381E) when FrameCountIngame (0x0174B03C) is 0 and on
 * game-state-7 frames (the tests from 0x006637FC), when the bit is held. So Mech Eggman without the
 * saved armor holds 10.0 through the loading states and 12.0 from game state 7, before the player
 * has control, and after a restart; Mech Tails starts at 10.0 and rings refill him to 12.0. The
 * tweak never writes the hit points. [private: measured with practice-stage runs and a hero13 save,
 * Iron Gate as Mech Eggman 10.0 to 12.0 at the state 2 to 7 step and Prison Lane as Mech Tails
 * 10.0; the first hit took Mech Eggman from 12 to 9 with the tweak and from 10 to 7 without.] That
 * a bit meant for another character does nothing harmful on each of the eight is the community's
 * record of the cheat, not checked here character by character [unverified]. Keep it off for runs.
 *
 * The first frame each character is met in a level (the first 32 such pairs), the run says what
 * the tweak did: "sa2 tweaks: all upgrades: Sonic in level 10: Upgrades 0x00000000 -> 0x1FFF7FFF",
 * in a Sonic vs Shadow fight "... Shadow in level 42: Upgrades 0x00000000 -> 0x000F0000 (own
 * upgrades only: the Sonic vs Shadow fight)", or for a character left alone "... Amy in level 13:
 * left alone (it plays Sonic's code)". A guest read that fails is said once, and that frame is left
 * to the game; CurrentLevel is one of those reads, so the bits never cross in the two fights.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define ALLUP_MAIN_CHARACTER 0x01DEA6E0u   /* MainCharacter[0], player 1's task */
#define ALLUP_TASK_DATA2     0x40u         /* task -> CharObj2Base */
#define ALLUP_CURRENT_LEVEL  0x01934B70u   /* int16 */
#define ALLUP_TWO_PLAYER     0x0174AFDEu   /* byte */
#define ALLUP_CP_UPGRADES    0x24u         /* u32 Upgrades */
#define ALLUP_UPGRADES       0x1FFF7FFFu   /* every character's set, Super Sonic's 0x8000 left out */
#define ALLUP_SONIC_SET      0x0000003Fu   /* Sonic's own, alone in the Sonic vs Shadow fights */
#define ALLUP_SHADOW_SET     0x000F0000u   /* Shadow's own, likewise */
#define ALLUP_LAST_OWN       7u            /* the last CharID2 that plays its own code: Mech Eggman */

#define ALLUP_SAY "sa2 tweaks: all upgrades: "

/* By CharID and CharID2: the character's name. */
static const char *const ALLUP_NAMES[] = {
    "Sonic", "Shadow", "Tails", "Eggman", "Knuckles", "Rouge", "Mech Tails", "Mech Eggman", "Amy",
    "Super Sonic", "Super Shadow", "character 11", "Metal Sonic", "Chao Walker", "Dark Chao Walker",
    "Tikal", "Chaos",
};
#define ALLUP_NNAMES (sizeof ALLUP_NAMES / sizeof ALLUP_NAMES[0])

static uint32_t g_allup_met[32];
static unsigned g_allup_nmet;
static int g_allup_unread_said, g_allup_two_player_said;

static void allup_name(char *out, size_t n, unsigned id) {
    if (id < ALLUP_NNAMES) snprintf(out, n, "%s", ALLUP_NAMES[id]);
    else snprintf(out, n, "character %u", id);
}

static void allup_unread(const char *what, uint32_t addr) {
    if (!g_allup_unread_said++)
        fprintf(stderr, ALLUP_SAY "%s at 0x%08X does not read or write, so this frame is left alone\n",
                what, addr);
}

/* Whether this (character, level) pair is new (and adds it); 0 once the list is full. */
static int allup_first(uint32_t key) {
    for (unsigned i = 0; i < g_allup_nmet; i++)
        if (g_allup_met[i] == key) return 0;
    if (g_allup_nmet == sizeof g_allup_met / sizeof g_allup_met[0]) return 0;
    g_allup_met[g_allup_nmet++] = key;
    return 1;
}

/* Called by start-assists.c's wrap of sub_44c1a0 after the lifted body. */
void sa2_all_upgrades_frame(void);

void sa2_all_upgrades_frame(void) {
    if (!sa2_tweak_on(SA2_TWEAK_ALL_UPGRADES)) return;
    uint32_t task = 0, cp = 0;
    if (sa2_peek(ALLUP_MAIN_CHARACTER, 4, &task) != SA2_OK) {
        allup_unread("MainCharacter[0]", ALLUP_MAIN_CHARACTER);
        return;
    }
    if (task == 0) return;                      /* no level, or a kart race */
    if (sa2_peek(task + ALLUP_TASK_DATA2, 4, &cp) != SA2_OK) {
        allup_unread("player 1's task", task + ALLUP_TASK_DATA2);
        return;
    }
    if (cp == 0) return;
    uint8_t two_player = 0;
    if (sa2_peek(ALLUP_TWO_PLAYER, 1, &two_player) != SA2_OK) {
        allup_unread("TwoPlayerMode", ALLUP_TWO_PLAYER);
        return;
    }
    if (two_player) {
        if (!g_allup_two_player_said++)
            fprintf(stderr, ALLUP_SAY "two-player mode is left alone\n");
        return;
    }
    uint8_t head[4];
    uint32_t up = 0;
    if (sa2_peek(cp, 4, head) != SA2_OK || sa2_peek(cp + ALLUP_CP_UPGRADES, 4, &up) != SA2_OK) {
        allup_unread("player 1's CharObj2Base", cp);
        return;
    }
    int16_t level = -1;
    if (sa2_peek(ALLUP_CURRENT_LEVEL, 2, &level) != SA2_OK) {
        allup_unread("CurrentLevel", ALLUP_CURRENT_LEVEL);
        return;
    }
    const unsigned id = head[1], id2 = head[3];
    const int own = id == id2 && id2 <= ALLUP_LAST_OWN;
    /* Sonic_Display draws by the bits alone, and both hedgehogs' models are loaded here. */
    const int fight = (level == 19 || level == 42) && own && id <= 1;
    const uint32_t give = !fight ? ALLUP_UPGRADES : id == 0 ? ALLUP_SONIC_SET : ALLUP_SHADOW_SET;
    const uint32_t up_now = own ? up | give : up;
    if (up_now != up && sa2_poke(cp + ALLUP_CP_UPGRADES, 4, &up_now) != SA2_OK) {
        allup_unread("player 1's Upgrades", cp + ALLUP_CP_UPGRADES);
        return;
    }
    if (!allup_first(id << 24 | id2 << 16 | (uint16_t)level)) return;
    char name[24], code[24];
    allup_name(name, sizeof name, id2);
    allup_name(code, sizeof code, id);
    if (fight)
        fprintf(stderr, ALLUP_SAY "%s in level %d: Upgrades 0x%08X -> 0x%08X (own upgrades only: the "
                "Sonic vs Shadow fight)\n", name, level, up, up_now);
    else if (own)
        fprintf(stderr, ALLUP_SAY "%s in level %d: Upgrades 0x%08X -> 0x%08X\n", name, level, up, up_now);
    else if (id != id2)
        fprintf(stderr, ALLUP_SAY "%s in level %d: left alone (it plays %s's code)\n", name, level, code);
    else
        fprintf(stderr, ALLUP_SAY "%s in level %d: left alone (it has no upgrades of its own)\n", name,
                level);
}
