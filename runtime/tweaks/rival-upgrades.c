/* rival-upgrades: Shadow, Rouge and Mech Tails share the upgrades their rivals have found. Shadow
 * gets Sonic's Bounce Bracelet and Magic Gloves, Mech Tails gets Eggman's Protective Armor, and
 * Rouge keeps her breath underwater when Knuckles has the Air Necklace, each only once the rival
 * has found that upgrade in the player's save (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_44c260, sub_7414d0
 *
 * Where the upgrades come from, read from the unpacked image. A character's upgrades are the dword
 * Upgrades at CharObj2Base+0x24 (the bits of SA2Enums.h's Upgrades). SetPhysicsParamsAndGiveUpgrades
 * (0x004599C0), which every character loader calls, sets MainCharacter[player] (0x01DEA6E0) to the
 * character's task, whose +0x40 is its CharObj2Base, and ORs bits into Upgrades from the "Got"
 * bytes at 0x01DEB300 (the save's copy, restored by the loader at 0x00445185 and written back by
 * the saver at 0x0044371B) through a jump table on CharID (CharObj2Base+1, 0x00459B43):
 *
 *   Sonic (0)       0x00459B64  [0x01DEB302] SonicMagicGlovesGot      -> 0x4  (Magic Gloves)
 *                   0x00459B7E  [0x01DEB304] SonicBounceBraceletGot   -> 0x10 (Bounce Bracelet)
 *   Shadow (1)      0x00459BAE  his own four bytes only (0x01DEB310-0x01DEB313)
 *   Knuckles (4)    0x00459CC6  [0x01DEB30D] KnucklesAirNecklaceGot   -> 0x2000 (Air Necklace)
 *   Rouge (5)       0x00459CE8  her own four bytes only (0x01DEB319-0x01DEB31C)
 *   Mech Tails (6)  0x00459BF7  Tails's four bytes only (0x01DEB306-0x01DEB309)
 *   Mech Eggman (7) 0x00459C6D  [0x01DEB317] EggmanProtectiveArmorGot -> 0x800000 (Protective Armor)
 *
 * Shadow runs Sonic's code (LoadShadow sets Sonic_Display, 0x007175CC), and the code that uses the
 * two bits does not ask who is playing: Sonic_CheckBounceAttack (0x00725271, reached from the jump's
 * air check at 0x0071AFAC when B or X is pressed, 0x00442444) and the Magic Gloves test in
 * Sonic_ChecksForDamage (0x00719A86). A mech's hit points are the float at CharObj2Base+0x48. AddHP
 * (0x0046F4C0) asks for CharID 6 or 7, then the armor bit (0x0046F4E1): with it, rings refill the
 * mech up to 12.0 (0x008AF738) instead of 10.0 (0x008AE6C0), and the hit point gauge is drawn
 * against that cap (0x0075F8B1). But InitMechHP (0x007414D0), which LoadMechTails (0x007410F6) and
 * the respawn after a miss (0x0046BEF3) call with EAX = the CharObj2Base, gives the armored 12.0
 * only to CharID 7 (0x007414FA) and starts Mech Tails at 10.0, so his gauge would start 5/6 full.
 * MechTails_Display draws no armor model (MechEggman_Display's test at 0x00745112 is its own).
 * Rouge runs Knuckles's code (Knuckles_Main), but the breath check, FUN_00459F30, gives the Air
 * Necklace to Knuckles alone: `cmp byte [cp+1],4; jne 0x00459FB4` at 0x00459F9D, then `test dword
 * [cp+0x24],0x2000` at 0x00459FA7, and on that path 0x0045A081 zeroes UnderwaterTime (the word at
 * CharObj2Base+0x16). Without the necklace,
 * every frame underwater adds 1 to UnderwaterTime (0x0045A025) and, when it reaches exactly 0x474
 * (0x0045A02D), spawns the drowning countdown (0x0046F680 with 0x0046BC60, at 0x0045A043).
 *
 * The tweak, after the lifted body of 0x0044C260 each frame. That function (75 bytes, unnamed) is
 * called only by GameLoop (0x00433EE0), at 0x00433FF7, on GameLoop's straight-line path after
 * GameModeHandler (0x00434160) has run the frame's tasks; it computes a frame-time float and
 * returns it on the x87 stack, which the wrap leaves alone. With player 1's CharObj2Base read as
 * [[0x01DEA6E0]+0x40]:
 *
 *   Shadow      Upgrades |= 0x10 when [0x01DEB304] is set, |= 0x4 when [0x01DEB302] is set.
 *   Mech Tails  Upgrades |= 0x800000 when [0x01DEB317] is set; and after InitMechHP (the wrap of
 *               0x007414D0), for player 1's Mech Tails with [0x01DEB317] set, the hit points are
 *               set to the armored start InitMechHP gives Mech Eggman, the float at 0x008AF738.
 *   Rouge       UnderwaterTime = 0 when [0x01DEB30D] is set, as the necklace path does for
 *               Knuckles: it can never count up to 0x474, so no countdown spawns. Rouge gets no
 *               0x2000 bit, which the breath check would ignore and Knuckles_Display would draw
 *               (0x0072F528).
 *
 * A "Got" byte counts when it is not 0, as the loader's own test does. A bit already set is not
 * written again, so a write happens on the first frame after the character is loaded (each restart
 * loads it anew), and Rouge's word is written only while it counts up underwater. The writes are
 * to the character's heap object, never to the Got bytes or the save (0x01DEC600), so the save
 * keeps what the player found.
 *
 * Left alone on purpose, each said once: a costume (CharID2, CharObj2Base+3, is not the character
 * itself: Metal Sonic plays as CharID 1, and his Bounce Attack has no action data, the crash
 * metal-bounce-fix exists for); two-player mode (TwoPlayerMode, 0x0174AFDE, is set); and Shadow in
 * the Sonic vs Shadow fights, levels 19 and 42 (CurrentLevel, the int16 at 0x01934B70): with
 * Sonic's models loaded beside his, Sonic_Display draws Sonic's bracelet and glove models
 * (0x007208C1 and 0x007209C0, from the model slots 0x01DE96F4 and 0x01DE96FC) on Shadow with
 * Shadow's textures, the case Codes.lst's Hide Bounce Bracelet Fix is for. In his own stages those
 * slots hold no model and nothing is drawn (with the tweak on and off, Radical Highway's frames
 * match until his first bounce). Player 2 is never touched.
 *
 * Off is the lifted code alone: each wrap tests the tweak's bit first and otherwise only calls
 * `__real_` of the function it wraps. Risks: 0x0044C260 is the natural per-frame hook, and another
 * tweak that wants one must share this wrap (tools/s4/tweak_sources.py refuses a second); the
 * Magic Gloves' effect on Shadow is read from the image, not seen in a run.
 *
 * The first frame each rival is met in a level (the first 32 such pairs), the run says what the
 * tweak does there: "sa2 tweaks: rival upgrades shared: Shadow in level 14 gets the Bounce Bracelet
 * and the Magic Gloves (Upgrades 0x00010000 -> 0x00010014)", or "... gets nothing: Sonic has not
 * found the Bounce Bracelet or the Magic Gloves"; and the first time per level Rouge's timer is
 * held: "sa2 tweaks: rival upgrades shared: Rouge's air timer held at 0 in level 18 (it read 1)";
 * and the first armored start per level: "... Mech Tails in level 9 starts with the armored hit
 * points (12), as Mech Eggman does".
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define RIVAL_MAIN_CHARACTER  0x01DEA6E0u   /* MainCharacter[0], player 1's task */
#define RIVAL_TASK_DATA2      0x40u         /* task -> CharObj2Base */
#define RIVAL_CURRENT_LEVEL   0x01934B70u   /* int16 */
#define RIVAL_TWO_PLAYER      0x0174AFDEu   /* byte */
#define RIVAL_GOT_GLOVES      0x01DEB302u   /* SonicMagicGlovesGot */
#define RIVAL_GOT_BRACELET    0x01DEB304u   /* SonicBounceBraceletGot */
#define RIVAL_GOT_NECKLACE    0x01DEB30Du   /* KnucklesAirNecklaceGot */
#define RIVAL_GOT_ARMOR       0x01DEB317u   /* EggmanProtectiveArmorGot */
#define RIVAL_CP_UNDERWATER   0x16u         /* u16 UnderwaterTime */
#define RIVAL_CP_UPGRADES     0x24u         /* u32 Upgrades */
#define RIVAL_CP_MECH_HP      0x48u         /* float, a mech's hit points */
#define RIVAL_ARMORED_HP      0x008AF738u   /* float 12.0, InitMechHP's armored start */

enum { RIVAL_SHADOW = 1, RIVAL_ROUGE = 5, RIVAL_MECH_TAILS = 6 };

#define RIVAL_SAY "sa2 tweaks: rival upgrades shared: "

struct rival_gift {
    uint8_t character;
    uint32_t got, bit;
    const char *upgrade;
};

static const struct rival_gift RIVAL_GIFTS[] = {
    {RIVAL_SHADOW, RIVAL_GOT_BRACELET, 0x10u, "the Bounce Bracelet"},
    {RIVAL_SHADOW, RIVAL_GOT_GLOVES, 0x4u, "the Magic Gloves"},
    {RIVAL_MECH_TAILS, RIVAL_GOT_ARMOR, 0x800000u, "the Protective Armor"},
};

/* The (character, level) pairs already named, and the levels whose Rouge hold, boss skip and
 * costume were said. */
static uint32_t g_rival_met[32];
static unsigned g_rival_nmet;
static uint32_t g_rival_held[16];
static unsigned g_rival_nheld;
static uint32_t g_rival_skips[16];
static unsigned g_rival_nskips;
static uint32_t g_rival_armored[16];
static unsigned g_rival_narmored;
static int g_rival_unread_said, g_rival_two_player_said;

/* Whether `key` is new to the list (and adds it); 0 once the list is full. */
static int rival_first(uint32_t *list, unsigned *n, unsigned cap, uint32_t key) {
    for (unsigned i = 0; i < *n; i++)
        if (list[i] == key) return 0;
    if (*n == cap) return 0;
    list[(*n)++] = key;
    return 1;
}

static const char *rival_name(unsigned character) {
    return character == RIVAL_SHADOW ? "Shadow" : character == RIVAL_ROUGE ? "Rouge" : "Mech Tails";
}

static void rival_unread(const char *what, uint32_t addr) {
    if (!g_rival_unread_said++)
        fprintf(stderr, RIVAL_SAY "%s at 0x%08X does not read, so this frame is left alone\n",
                what, addr);
}

static int rival_got(uint32_t addr, int *got) {
    uint8_t b = 0;
    if (sa2_peek(addr, 1, &b) != SA2_OK) {
        rival_unread("the Got byte", addr);
        return 0;
    }
    *got = b != 0;
    return 1;
}

/* Shadow's or Mech Tails's bits: OR in what the rival has found, and name it the first time. */
static void rival_give(uint32_t cp, unsigned character, int level) {
    uint32_t want = 0;
    const char *have[2], *lack[2];
    unsigned nhave = 0, nlack = 0;
    for (unsigned i = 0; i < sizeof RIVAL_GIFTS / sizeof RIVAL_GIFTS[0]; i++) {
        const struct rival_gift *g = &RIVAL_GIFTS[i];
        if (g->character != character) continue;
        int got;
        if (!rival_got(g->got, &got)) return;
        if (got) {
            want |= g->bit;
            have[nhave++] = g->upgrade;
        } else {
            lack[nlack++] = g->upgrade;
        }
    }
    uint32_t up = 0;
    if (sa2_peek(cp + RIVAL_CP_UPGRADES, 4, &up) != SA2_OK) {
        rival_unread("the character's Upgrades", cp + RIVAL_CP_UPGRADES);
        return;
    }
    const uint32_t now = up | want;
    if (now != up && sa2_poke(cp + RIVAL_CP_UPGRADES, 4, &now) != SA2_OK) {
        rival_unread("the character's Upgrades", cp + RIVAL_CP_UPGRADES);
        return;
    }
    if (!rival_first(g_rival_met, &g_rival_nmet, 32, character << 16 | (uint16_t)level)) return;
    const char *rival = character == RIVAL_SHADOW ? "Sonic" : "Eggman";
    if (nhave)
        fprintf(stderr, RIVAL_SAY "%s in level %d gets %s%s%s (Upgrades 0x%08X -> 0x%08X)\n",
                rival_name(character), level, have[0], nhave > 1 ? " and " : "",
                nhave > 1 ? have[1] : "", up, now);
    else
        fprintf(stderr, RIVAL_SAY "%s in level %d gets nothing: %s has not found %s%s%s\n",
                rival_name(character), level, rival, lack[0], nlack > 1 ? " or " : "",
                nlack > 1 ? lack[1] : "");
}

/* Rouge: hold UnderwaterTime at 0 while Knuckles has the Air Necklace. */
static void rival_breathe(uint32_t cp, int level) {
    int got;
    if (!rival_got(RIVAL_GOT_NECKLACE, &got)) return;
    if (rival_first(g_rival_met, &g_rival_nmet, 32, (uint32_t)RIVAL_ROUGE << 16 | (uint16_t)level)) {
        if (got)
            fprintf(stderr, RIVAL_SAY "Rouge in level %d keeps her breath underwater (Knuckles has "
                    "the Air Necklace)\n", level);
        else
            fprintf(stderr, RIVAL_SAY "Rouge in level %d gets nothing: Knuckles has not found the "
                    "Air Necklace\n", level);
    }
    if (!got) return;
    uint16_t t = 0;
    if (sa2_peek(cp + RIVAL_CP_UNDERWATER, 2, &t) != SA2_OK) {
        rival_unread("Rouge's air timer", cp + RIVAL_CP_UNDERWATER);
        return;
    }
    if (t == 0) return;
    const uint16_t zero = 0;
    if (sa2_poke(cp + RIVAL_CP_UNDERWATER, 2, &zero) != SA2_OK) {
        rival_unread("Rouge's air timer", cp + RIVAL_CP_UNDERWATER);
        return;
    }
    if (rival_first(g_rival_held, &g_rival_nheld, 16, (uint16_t)level))
        fprintf(stderr, RIVAL_SAY "Rouge's air timer held at 0 in level %d (it read %u)\n", level, t);
}

static void rival_frame(void) {
    uint32_t task = 0, cp = 0;
    if (sa2_peek(RIVAL_MAIN_CHARACTER, 4, &task) != SA2_OK) {
        rival_unread("MainCharacter[0]", RIVAL_MAIN_CHARACTER);
        return;
    }
    if (task == 0) return;                      /* no level, no player 1 */
    if (sa2_peek(task + RIVAL_TASK_DATA2, 4, &cp) != SA2_OK) {
        rival_unread("player 1's task", task + RIVAL_TASK_DATA2);
        return;
    }
    if (cp == 0) return;
    uint8_t head[4];
    if (sa2_peek(cp, 4, head) != SA2_OK) {
        rival_unread("player 1's CharObj2Base", cp);
        return;
    }
    const unsigned character = head[1], costume = head[3];
    if (character != RIVAL_SHADOW && character != RIVAL_ROUGE && character != RIVAL_MECH_TAILS) return;
    int16_t level = 0;
    uint8_t two_player = 0;
    if (sa2_peek(RIVAL_CURRENT_LEVEL, 2, &level) != SA2_OK) {
        rival_unread("CurrentLevel", RIVAL_CURRENT_LEVEL);
        return;
    }
    if (sa2_peek(RIVAL_TWO_PLAYER, 1, &two_player) != SA2_OK) {
        rival_unread("TwoPlayerMode", RIVAL_TWO_PLAYER);
        return;
    }
    if (two_player) {
        if (!g_rival_two_player_said++)
            fprintf(stderr, RIVAL_SAY "two-player mode is left alone\n");
        return;
    }
    if (costume != character) {
        if (rival_first(g_rival_skips, &g_rival_nskips, 16, 0x10000u | costume << 8 | character))
            fprintf(stderr, RIVAL_SAY "character %u playing as %s is left alone (a costume, not the "
                    "character)\n", costume, rival_name(character));
        return;
    }
    if (character == RIVAL_SHADOW && (level == 19 || level == 42)) {
        if (rival_first(g_rival_skips, &g_rival_nskips, 16, (uint16_t)level))
            fprintf(stderr, RIVAL_SAY "Shadow in level %d is left alone (Sonic's upgrade models "
                    "would draw on him)\n", level);
        return;
    }
    if (character == RIVAL_ROUGE) rival_breathe(cp, level);
    else rival_give(cp, character, level);
}

/* After InitMechHP: player 1's own Mech Tails, with Eggman's armor found, starts at the armored
 * hit points. Left alone, silently, for everyone else: the per-frame wrap names the cases. */
static void rival_mech_start(uint32_t cp) {
    uint8_t head[4], two_player = 0;
    if (sa2_peek(cp, 4, head) != SA2_OK) {
        rival_unread("the mech's CharObj2Base", cp);
        return;
    }
    if (head[0] != 0 || head[1] != RIVAL_MECH_TAILS || head[3] != RIVAL_MECH_TAILS) return;
    int got;
    int16_t level = 0;
    uint32_t hp = 0;
    if (sa2_peek(RIVAL_TWO_PLAYER, 1, &two_player) != SA2_OK) {
        rival_unread("TwoPlayerMode", RIVAL_TWO_PLAYER);
        return;
    }
    if (two_player || !rival_got(RIVAL_GOT_ARMOR, &got) || !got) return;
    if (sa2_peek(RIVAL_ARMORED_HP, 4, &hp) != SA2_OK) {
        rival_unread("the armored hit points", RIVAL_ARMORED_HP);
        return;
    }
    if (sa2_poke(cp + RIVAL_CP_MECH_HP, 4, &hp) != SA2_OK) {
        rival_unread("the mech's hit points", cp + RIVAL_CP_MECH_HP);
        return;
    }
    if (sa2_peek(RIVAL_CURRENT_LEVEL, 2, &level) != SA2_OK) level = -1;
    float v;
    memcpy(&v, &hp, sizeof v);
    if (rival_first(g_rival_armored, &g_rival_narmored, 16, (uint16_t)level))
        fprintf(stderr, RIVAL_SAY "Mech Tails in level %d starts with the armored hit points (%g), "
                "as Mech Eggman does\n", level, (double)v);
}

void *__real_sub_44c260(void *state, uint32_t pc, void *memory);
void *__real_sub_7414d0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_44c260(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_RIVAL_UPGRADES)) return __real_sub_44c260(state, pc, memory);
    void *ret = __real_sub_44c260(state, pc, memory);
    rival_frame();
    return ret;
}

__attribute__((used, retain))
void *__wrap_sub_7414d0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_RIVAL_UPGRADES)) return __real_sub_7414d0(state, pc, memory);
    const uint32_t cp = sa2_rt_get_gpr(state, SA2_EAX);
    void *ret = __real_sub_7414d0(state, pc, memory);
    rival_mech_start(cp);
    return ret;
}
