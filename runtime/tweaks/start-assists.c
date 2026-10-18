/* start-assists: player 1 plays every level with a magnetic barrier, invincibility and all of the
 * character's own upgrades, held each frame (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_44c1a0
 *
 * Where the three live, read from the unpacked image. SetPhysicsParamsAndGiveUpgrades (0x004599C0),
 * which every character loader calls, stores the character's task in MainCharacter[player]
 * (0x01DEA6E0, at 0x00459A1C) and the task's +0x40 in MainCharObj2[player] (0x01DE9600, at
 * 0x00459A31): the CharObj2Base. Its CharID is the byte at +1 and its CharID2 (which character it
 * really is) the byte at +3; the delete path 0x00459E10 (from Sonic_Delete, Knuckles_Delete and the
 * other characters' delete functions) zeroes both slots again, so MainCharacter[0] is 0 outside a
 * level and in a kart race, and otherwise a live character.
 *
 *   Powerups, the word at +0x10. AwardMagneticBarrier (0x0046E180, from ItemBoxItem_MagneticBarrier)
 *   clears 0x0001 and sets 0x0002 (0x0046E1C5); AwardBarrier (0x0046E2E0, jumped to from the
 *   Barrier box at 0x006C8A4E with no test of its own) the reverse (0x0046E321-0x0046E32D);
 *   AwardInvincibility (0x0046E440) sets 0x8000 (0x0046E48C). Each award first tests its own bit
 *   and returns at once when it is set: 0x0002 at 0x0046E1A6, 0x0001 at 0x0046E306, 0x8000 at
 *   0x0046E46B. Otherwise it spawns the task that draws the effect: the normal barrier
 *   (0x00753210, main 0x007533B0), TBarrier (0x00753280, main 0x007533E0, which deletes itself
 *   once bit 0x0002 is clear) and Muteki (0x007532F0, main 0x00754150, which holds 0x8000 for
 *   0x4EC frames, 21 seconds, then clears it and deletes itself). AwardInvincibility also starts the invincibility
 *   music (0x00443480 with "invncibl.adx", 0x0046E495), and each award plays a voice line
 *   (0x00443130).
 *   Upgrades, the dword at +0x24. The loader ORs bits into it from the "Got" bytes
 *   0x01DEB300-0x01DEB31C (the save's copy: 0x200 bytes copied from the save buffer at 0x01DEF837
 *   at 0x00445185, and back at 0x0044371B) through a jump table on CharID (0x00459B43): Sonic 0x3F
 *   (0x00459B4A), Shadow 0xF0000 (0x00459BAE), Mech Tails 0x3C0 (0x00459BF7), Mech Eggman
 *   0x1F00000 (0x00459C3D), Knuckles 0x7C00 (0x00459C96), Rouge 0x1E000000 (0x00459CE8), Tails and
 *   Eggman on foot nothing. The bit 0x8000 is Super Sonic's. Nothing writes a Got byte from
 *   Upgrades (an upgrade's pickup sets its byte to 1, 0x006D87F9), so a bit set here is never
 *   saved. What the bits reach is: an upgrade pedestal decides it is taken from its Got byte alone
 *   (its load, 0x006D8540, at 0x006D8649) and never reads Upgrades, so one gated behind an
 *   upgrade the player has not found yet (a Light Dash line, a Bounce spot, a Mystic Melody path,
 *   a digging spot) is still there, and touching it writes its Got byte (0x006D87F9), which the
 *   next save keeps (0x0044371B) after the tweak is off.
 *
 * The tweak, after the lifted body of 0x0044C1A0 each frame. That function (81 bytes, unnamed) is
 * called only by GameLoop (0x00433EE0), at 0x00433FF0, on GameLoop's straight-line path after
 * GameModeHandler (0x00434160) has run the frame's tasks; it computes a frame-time float into
 * 0x0174B09C and returns it on the x87 stack, which GameLoop pops (0x00433FF5) and the wrap leaves
 * alone. (0x0044C260, its twin at 0x00433FF7, is the hook rival-upgrades wraps; one function can
 * be wrapped once.) With player 1's CharObj2Base read as [[0x01DEA6E0]+0x40], both non-zero:
 *
 *   Powerups |= 0x8003: invincibility, the magnetic barrier, which pulls rings in, and the plain
 *   barrier. The plain barrier's bit is there only so that its box returns at once like the other
 *   two (AwardBarrier tests 0x0001 alone, so with 0x8002 it would spawn the bubble, play the
 *   voice line and store (pw & ~2) | 1, and the wrap would put 0x0002 back after that frame's
 *   tasks: 0x8003 again, with a bubble drawn). The game itself sets the two barrier bits one at
 *   a time; 0x8003 is the value an 0x8002 tweak reached and held after any Barrier box, so holding
 *   it from the start adds no value of Powerups, only takes the bubble task away.
 *   Upgrades |= the character's own bits above, by CharID2 when CharID agrees (a real character,
 *   not one playing another's code).
 *
 * A bit already set is not written again, so a write happens on the first frame after the character
 * is loaded (each restart loads it anew) and after anything clears a bit. The writes are to the
 * character's heap object, never to the Got bytes or the save buffer. Anything collected or cleared
 * with the tweak on is saved as usual: times, ranks, emblems, Lost Chao and the upgrades picked up
 * early (above). The Android note says so.
 *
 * Left alone on purpose, each said once: the upgrades of Amy, Metal Sonic, Tikal, Chaos, the Chao
 * Walkers and the Super forms, which have no set of their own (CharID2 8 to 16; Sonic's or
 * Shadow's bits on Amy or Metal Sonic would draw the Flame Ring's somersault aura, 0x007599D0, the
 * crash Codes.lst stubs out, and a Walker's would reach the bazooka code it patches at 0x0075D914);
 * they still get the barrier and invincibility. Two-player mode (TwoPlayerMode, 0x0174AFDE, is
 * set): one player held invincible would decide a race or a fight. Player 2 is never touched.
 *
 * Off is the lifted code alone: the wrap tests the tweak's bit first and otherwise only calls
 * `__real_sub_44c1a0`. Risks, all with the tweak on: no effect's task is spawned, so no barrier
 * bubble and no invincibility sparkle is drawn, no music or voice line plays, and
 * the three item boxes do nothing (each award returns at once when its bit is set, which for the
 * plain Barrier box is why 0x0001 is held); invincibility does not save the player from a pit,
 * drowning or being crushed; the upgrades open Lost Chao, upgrade-gated emblems and upgrade
 * pedestals early, and those results are saved.
 *
 * The first frame each character is met in a level (the first 32 such pairs), the run says what
 * the tweak did: "sa2 tweaks: start assists: Sonic in level 13: Powerups 0x0000 -> 0x8003,
 * Upgrades 0x00000000 -> 0x0000003F", or for a character without upgrades of its own "... Amy in
 * level 13: Powerups 0x0000 -> 0x8003, upgrades left alone (Amy has none of its own)".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define ASSIST_MAIN_CHARACTER 0x01DEA6E0u   /* MainCharacter[0], player 1's task */
#define ASSIST_TASK_DATA2     0x40u         /* task -> CharObj2Base */
#define ASSIST_CURRENT_LEVEL  0x01934B70u   /* int16 */
#define ASSIST_TWO_PLAYER     0x0174AFDEu   /* byte */
#define ASSIST_CP_POWERUPS    0x10u         /* u16 Powerups */
#define ASSIST_CP_UPGRADES    0x24u         /* u32 Upgrades */
#define ASSIST_POWERUPS       0x8003u       /* invincibility | magnetic barrier | barrier */

#define ASSIST_SAY "sa2 tweaks: start assists: "

/* By CharID2: the character's name and its own upgrade bits (the loader's, 0x00459B43). */
static const struct {
    const char *name;
    uint32_t upgrades;
} ASSIST_CHARS[] = {
    {"Sonic", 0x0000003Fu},       {"Shadow", 0x000F0000u},      {"Tails", 0},
    {"Eggman", 0},                {"Knuckles", 0x00007C00u},    {"Rouge", 0x1E000000u},
    {"Mech Tails", 0x000003C0u},  {"Mech Eggman", 0x01F00000u}, {"Amy", 0},
    {"Super Sonic", 0},           {"Super Shadow", 0},          {"character 11", 0},
    {"Metal Sonic", 0},           {"Chao Walker", 0},           {"Dark Chao Walker", 0},
    {"Tikal", 0},                 {"Chaos", 0},
};
#define ASSIST_NCHARS (sizeof ASSIST_CHARS / sizeof ASSIST_CHARS[0])

static uint32_t g_assist_met[32];
static unsigned g_assist_nmet;
static int g_assist_unread_said, g_assist_two_player_said;

static void assist_unread(const char *what, uint32_t addr) {
    if (!g_assist_unread_said++)
        fprintf(stderr, ASSIST_SAY "%s at 0x%08X does not read or write, so this frame is left alone\n",
                what, addr);
}

/* Whether this (character, level) pair is new (and adds it); 0 once the list is full. */
static int assist_first(uint32_t key) {
    for (unsigned i = 0; i < g_assist_nmet; i++)
        if (g_assist_met[i] == key) return 0;
    if (g_assist_nmet == sizeof g_assist_met / sizeof g_assist_met[0]) return 0;
    g_assist_met[g_assist_nmet++] = key;
    return 1;
}

static void assist_frame(void) {
    uint32_t task = 0, cp = 0;
    if (sa2_peek(ASSIST_MAIN_CHARACTER, 4, &task) != SA2_OK) {
        assist_unread("MainCharacter[0]", ASSIST_MAIN_CHARACTER);
        return;
    }
    if (task == 0) return;                      /* no level, no player 1 */
    if (sa2_peek(task + ASSIST_TASK_DATA2, 4, &cp) != SA2_OK) {
        assist_unread("player 1's task", task + ASSIST_TASK_DATA2);
        return;
    }
    if (cp == 0) return;
    uint8_t two_player = 0;
    if (sa2_peek(ASSIST_TWO_PLAYER, 1, &two_player) != SA2_OK) {
        assist_unread("TwoPlayerMode", ASSIST_TWO_PLAYER);
        return;
    }
    if (two_player) {
        if (!g_assist_two_player_said++)
            fprintf(stderr, ASSIST_SAY "two-player mode is left alone\n");
        return;
    }
    uint8_t head[4];
    uint16_t pw = 0;
    uint32_t up = 0;
    if (sa2_peek(cp, 4, head) != SA2_OK || sa2_peek(cp + ASSIST_CP_POWERUPS, 2, &pw) != SA2_OK ||
        sa2_peek(cp + ASSIST_CP_UPGRADES, 4, &up) != SA2_OK) {
        assist_unread("player 1's CharObj2Base", cp);
        return;
    }
    const unsigned id = head[1], id2 = head[3];
    const uint32_t own = id2 < ASSIST_NCHARS && id == id2 ? ASSIST_CHARS[id2].upgrades : 0;
    const uint16_t pw_now = (uint16_t)(pw | ASSIST_POWERUPS);
    const uint32_t up_now = up | own;
    if (pw_now != pw && sa2_poke(cp + ASSIST_CP_POWERUPS, 2, &pw_now) != SA2_OK) {
        assist_unread("player 1's Powerups", cp + ASSIST_CP_POWERUPS);
        return;
    }
    if (up_now != up && sa2_poke(cp + ASSIST_CP_UPGRADES, 4, &up_now) != SA2_OK) {
        assist_unread("player 1's Upgrades", cp + ASSIST_CP_UPGRADES);
        return;
    }
    int16_t level = -1;
    if (sa2_peek(ASSIST_CURRENT_LEVEL, 2, &level) != SA2_OK) level = -1;
    if (!assist_first(id2 << 16 | (uint16_t)level)) return;
    char name[24];
    if (id2 < ASSIST_NCHARS) snprintf(name, sizeof name, "%s", ASSIST_CHARS[id2].name);
    else snprintf(name, sizeof name, "character %u", id2);
    if (own)
        fprintf(stderr, ASSIST_SAY "%s in level %d: Powerups 0x%04X -> 0x%04X, Upgrades 0x%08X -> "
                "0x%08X\n", name, level, pw, pw_now, up, up_now);
    else
        fprintf(stderr, ASSIST_SAY "%s in level %d: Powerups 0x%04X -> 0x%04X, upgrades left alone "
                "(%s has none of its own)\n", name, level, pw, pw_now, name);
}

void *__real_sub_44c1a0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_44c1a0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_START_ASSISTS)) return __real_sub_44c1a0(state, pc, memory);
    void *ret = __real_sub_44c1a0(state, pc, memory);
    assist_frame();
    return ret;
}
