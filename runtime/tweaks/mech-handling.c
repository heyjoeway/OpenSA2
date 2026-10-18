/* mech-handling: the walkers (Tails' Cyclone and Eggman's Egg Walker, and the two Chao Walkers of
 * 2-player mode) speed up faster, lose less speed when the stick is let go, and reach a higher top
 * speed (runtime/sa2_tweaks.h: the tweaks and their rules). The numbers are OpenSA2's own starting
 * values, kept small on purpose and still to be tuned by play.
 *
 * TWEAK_WRAPS: none (it acts in physics-preset.c's wrap of sub_4599c0)
 *
 * Where the numbers are, read from the unpacked image. PhysicsArray, 0x017391E8 in .dataa, holds 19
 * PhysicsData entries of 0x84 bytes indexed by the Characters enum (runtime/tweaks/physics-preset.c
 * has the whole reading). The walkers are entries 6 (Mech Tails, 0x01739500), 7 (Mech Eggman,
 * 0x01739584), 13 (Chao Walker, 0x0173989C) and 14 (Dark Chao Walker, 0x01739920). All four
 * loaders, Mech Eggman's 0x00740C50, Mech Tails' 0x00740EB0, the Chao Walker's 0x00741110 and the
 * Dark Chao Walker's 0x007412F0, install MechEggman_Main, 0x00741530 (`mov edi,0x00741530` at
 * 0x00740C84, 0x00740EE4, 0x00741119 and 0x007412F9), and call SetPhysicsParamsAndGiveUpgrades, 0x004599C0 (0x00740D32,
 * 0x00740F92, 0x007411BE, 0x0074139E), which copies entry [CharID2] into CharObj2Base+0xC0 with
 * `rep movsd` at 0x00459A6E. MechEggman_Main calls the shared ground movement 0x0045B610 at
 * 0x00741F95 and 0x00742178, and the air movement 0x0045D770 at 0x0074250E and six more sites. The
 * five values this tweak writes, as the copy places them:
 *
 *   SpeedMaxH  entry +0x10, cp+0xD0   above it the ground code adds drag, (speed - SpeedMaxH) times
 *                                     cp+0x114 (0x0045B90D); also read by the mech code itself at
 *                                     0x00742100 and 0x0074274D
 *   RushSpeed  entry +0x2C, cp+0xEC   the top of the ground code's acceleration bands (0x0045BB7A);
 *                                     the mech code reads it at 0x007417E4
 *   RunAccel   entry +0x3C, cp+0xFC   the ground acceleration factor (0x0045BABC and five more)
 *   AirAccel   entry +0x40, cp+0x100  the air acceleration (0x0045D8C7, in 0x0045D770)
 *   RunDecel   entry +0x44, cp+0x104  the slowdown with the stick let go (0x0045B9CE, 0x0045B9EA)
 *
 * The image's values and the ones this tweak writes (each a factor of the image's, so the four keep
 * their ratios to one another: RunAccel and AirAccel x1.6, RunDecel x0.6, SpeedMaxH x1.25, RushSpeed
 * 1.8 -> 2):
 *
 *                     SpeedMaxH     RushSpeed  RunAccel      AirAccel         RunDecel
 *   Mech Tails (6)    1 -> 1.25     1.8 -> 2   0.1 -> 0.16   0.031 -> 0.0496  -0.1 -> -0.06
 *   Mech Eggman (7)   1 -> 1.25     1.8 -> 2   0.1 -> 0.16   0.031 -> 0.0496  -0.1 -> -0.06
 *   Chao Walker (13)  1.4 -> 1.75   1.8 -> 2   0.2 -> 0.32   0.046 -> 0.0736  -0.1 -> -0.06
 *   Dark Chao W. (14) 0.7 -> 0.875  1.8 -> 2   0.07 -> 0.112 0.025 -> 0.04    -0.1 -> -0.06
 *
 * Left alone: the other 28 values of each entry. After the copy, the jump table at 0x00459A90 sets
 * cp+0x110 and cp+0x114 (AirDecel and AirResist, the entry's +0x50 and +0x54) from constants in
 * levels 30, 32, 40, 43, 44, 50 and 53; this tweak writes neither, so those stay the game's. Weight
 * (+0x78), which 0x00459910 reloads from entry [CharID], is not written either. One direct reader
 * of the table touches these fields: 0x00464D25-0x00464DDE sets cp+0xFC again from entry
 * [CharID2]+0x3C (RunAccel, sometimes times a constant), so it reads this tweak's RunAccel too.
 *
 * What the player feels is not 1.6 times the acceleration. The ground code uses RunAccel as one
 * factor among its acceleration bands, the drag above SpeedMaxH and friction, so the player-facing
 * text names the 1.6 as a factor on the acceleration value. From a standing start only the first
 * tick differs (speed 0.16 against 0.1); after it the speed gains 0.0675 a tick with the tweak on or
 * off. The gap grows as the walker nears its top speed. Measured in the lifted build: Iron Gate
 * x1.09 in distance at +15 frames and x1.31 in speed at +60, Prison Lane x1.18 in distance at +30
 * [private: stage3 ig-off/ig-on, pl-off/pl-on].
 *
 * The tweak: physics-preset.c's wrap of 0x004599C0 is the one wrap of that function (the tools refuse
 * a second), and on its first call in the process, when this tweak is on, it calls
 * sa2_mech_handling_apply before the lifted body, after the physics preset when that tweak is on
 * too. The function reads each of the four entries, writes the five values one dword at a time, and
 * reads each back. The table is written once; nothing restores it, so every later load, restart and
 * stage keeps it, as with physics-preset.
 *
 * With physics-preset on too: the preset is written first, then these five values, so a preset that
 * names a walker as <to> keeps its other values and loses these five, and the run says which values
 * of the preset's were replaced. A preset whose <from> is a walker copies the walker's numbers as the
 * image has them (the preset reads the table before this tweak writes it); physics-preset.c says so.
 *
 * By name, never silently: on the first call the run says, per walker, what changed: "sa2 tweaks:
 * mech handling: Mech Tails (entry 6 at 0x01739500): SpeedMaxH 1 -> 1.25, RushSpeed 1.8 -> 2, ...".
 * An entry that does not read, or a value that does not write or reads back otherwise, is named and
 * that walker keeps the values it had (the ones already written are put back).
 *
 * Off is the lifted code alone: physics-preset.c's wrap calls the lifted body and returns unless one
 * of the two tweaks is on, and calls this file only when this one is; the function's own first test,
 * sa2_tweak_on, returns at once otherwise.
 *
 * Risks: it changes how the walkers move, so the mech stages play differently: a faster walker can
 * run past lock-on targets or reach timed scripting early, and the values need playtests. In
 * 2-player mode both players share the table. Not touched: speed lost when turning or hovering,
 * knockback, and air steering, which are the walker's own code (MechEggman_RunActions 0x00742C10,
 * FreeMovement_MechEggman 0x00749020), not the table. No save data is written: the table is not in
 * the save.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define MECH_TABLE   0x017391E8u    /* PhysicsArray */
#define MECH_ENTRY   0x84u          /* sizeof(PhysicsData) */
#define MECH_FIELDS  5u
#define MECH_WALKERS 4u

/* The fields written, as dword indices into PhysicsData, and their names (SA2Structs.h). */
static const unsigned char MECH_FIELD[MECH_FIELDS] = {4, 11, 15, 16, 17};
static const char *const MECH_FIELD_NAME[MECH_FIELDS] = {"SpeedMaxH", "RushSpeed", "RunAccel", "AirAccel",
                                                         "RunDecel"};

/* Per walker: its entry, its name, the image's five values and the five this tweak writes. */
static const struct {
    unsigned entry;
    const char *said;
    float image[MECH_FIELDS], to[MECH_FIELDS];
} MECH_ROWS[MECH_WALKERS] = {
    {6, "Mech Tails", {1.0f, 1.8f, 0.1f, 0.031f, -0.1f}, {1.25f, 2.0f, 0.16f, 0.0496f, -0.06f}},
    {7, "Mech Eggman", {1.0f, 1.8f, 0.1f, 0.031f, -0.1f}, {1.25f, 2.0f, 0.16f, 0.0496f, -0.06f}},
    {13, "Chao Walker", {1.4f, 1.8f, 0.2f, 0.046f, -0.1f}, {1.75f, 2.0f, 0.32f, 0.0736f, -0.06f}},
    {14, "Dark Chao Walker", {0.7f, 1.8f, 0.07f, 0.025f, -0.1f}, {0.875f, 2.0f, 0.112f, 0.04f, -0.06f}},
};

static uint32_t mech_bits(float f) {
    uint32_t u;
    memcpy(&u, &f, sizeof u);
    return u;
}

static float mech_float(uint32_t u) {
    float f;
    memcpy(&f, &u, sizeof f);
    return f;
}

/* Appends "<sep><text>" to a list being built. */
static void mech_list(char *line, size_t n, size_t *at, unsigned *count, const char *text) {
    if (*at < n) *at += (size_t)snprintf(line + *at, n - *at, "%s%s", *count ? ", " : "", text);
    ++*count;
}

/* Puts back the first `upto` fields of row `r` that were written over `old`. */
static void mech_restore(unsigned r, const uint32_t *old, const int *written, unsigned upto) {
    for (unsigned i = 0; i < upto; i++) {
        if (!written[i]) continue;
        (void)sa2_poke(MECH_TABLE + MECH_ROWS[r].entry * MECH_ENTRY + MECH_FIELD[i] * 4u, 4, &old[i]);
    }
}

static void mech_row(unsigned r) {
    const char *who = MECH_ROWS[r].said;
    const uint32_t base = MECH_TABLE + MECH_ROWS[r].entry * MECH_ENTRY;
    uint32_t entry[MECH_ENTRY / 4];
    if (sa2_peek(base, sizeof entry, entry) != SA2_OK) {
        fprintf(stderr, "sa2 tweaks: mech handling: %s's entry at 0x%08X does not read, so %s keeps its "
                "handling\n", who, base, who);
        return;
    }
    uint32_t old[MECH_FIELDS];
    int written[MECH_FIELDS] = {0};
    char line[512], preset[256];
    size_t at = 0, pat = 0;
    unsigned changed = 0, replaced = 0;
    for (unsigned i = 0; i < MECH_FIELDS; i++) {
        const uint32_t addr = base + MECH_FIELD[i] * 4u;
        const uint32_t want = mech_bits(MECH_ROWS[r].to[i]);
        old[i] = entry[MECH_FIELD[i]];
        if (old[i] == want) continue;
        uint32_t back = ~want;
        if (sa2_poke(addr, 4, &want) != SA2_OK || sa2_peek(addr, 4, &back) != SA2_OK || back != want) {
            fprintf(stderr, "sa2 tweaks: mech handling: %s's %s at 0x%08X does not write, so %s keeps its "
                    "handling\n", who, MECH_FIELD_NAME[i], addr, who);
            mech_restore(r, old, written, i + 1);
            return;
        }
        written[i] = 1;
        char item[96];
        snprintf(item, sizeof item, "%s %g -> %g", MECH_FIELD_NAME[i], (double)mech_float(old[i]),
                 (double)MECH_ROWS[r].to[i]);
        mech_list(line, sizeof line, &at, &changed, item);
        if (old[i] != mech_bits(MECH_ROWS[r].image[i])) {
            snprintf(item, sizeof item, "%s %g", MECH_FIELD_NAME[i], (double)mech_float(old[i]));
            mech_list(preset, sizeof preset, &pat, &replaced, item);
        }
    }
    if (!changed) {
        fprintf(stderr, "sa2 tweaks: mech handling: %s has the mech handling values already, so nothing "
                "changes\n", who);
        return;
    }
    fprintf(stderr, "sa2 tweaks: mech handling: %s (entry %u at 0x%08X): %s\n", who, MECH_ROWS[r].entry, base,
            line);
    if (replaced)
        fprintf(stderr, "sa2 tweaks: mech handling: %s's %s from the physics preset %s replaced (mech "
                "handling is written after the preset)\n", who, preset, replaced > 1 ? "are" : "is");
}

/* Called once, on the first call of physics-preset.c's wrap of sub_4599c0, before the lifted body. */
void sa2_mech_handling_apply(void);

void sa2_mech_handling_apply(void) {
    if (!sa2_tweak_on(SA2_TWEAK_MECH_HANDLING)) return;
    for (unsigned r = 0; r < MECH_WALKERS; r++) mech_row(r);
}
