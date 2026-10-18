/* physics-preset: Sonic and Shadow move with Metal Sonic's physics numbers from 2-player mode, or,
 * with SA2_PHYSICS_PRESET set, any character takes any other's (runtime/sa2_tweaks.h: the tweaks
 * and their rules).
 *
 * TWEAK_WRAPS: sub_4599c0
 *
 * Where the numbers come from, read from the unpacked image. PhysicsArray, 0x017391E8 in .dataa
 * (initialised data), holds 19 entries of 0x84 bytes, each a PhysicsData (SA2Structs.h: the int
 * HangTime, then 32 floats, FloorGrip to CenterHeight), indexed by the Characters enum (Sonic 0,
 * Shadow 1, Tails 2, Eggman 3, Knuckles 4, Rouge 5, Mech Tails 6, Mech Eggman 7, Amy 8, Super Sonic
 * 9, Super Shadow 10, B 11, Metal Sonic 12, Chao Walker 13, Dark Chao Walker 14, Tikal 15, Chaos
 * 16; 17 and 18 are copies of 12). SetPhysicsParamsAndGiveUpgrades (0x004599C0), which every
 * character loader calls (LoadSonic at 0x00716EEC, LoadShadow 0x0071744C, LoadMetalSonic
 * 0x007176FF, the others and the two Super forms, 16 sites), copies entry [CharID2] (the byte at
 * CharObj2Base+3, 0x00459A38) into CharObj2Base+0xC0 with `rep movsd`, 0x21 dwords, at 0x00459A6E.
 * After the copy it applies its own exceptions over it: 0x00459910 (called at 0x00459A75) sets the
 * float at +0x138 (Weight, the entry's +0x78) again from the table's entry [CharID] (the byte at
 * CharObj2Base+1; 0x00459950, 0x00459967), or for some characters in levels 30-53 to the constant
 * at 0x00A3EB9C (0x0045993D). CharID is not CharID2 for six characters (the loaders' byte stores):
 * Amy 0/8 (0x00717186, 0x0071718A), Metal Sonic 1/12 (0x007176E6), Tikal 4/15 (0x0072895B), Chaos
 * 5/16 (0x00728C00), Chao Walker 6/13 (0x007411A7) and Dark Chao Walker 7/14 (0x00741387), so each
 * of them takes its Weight from Sonic's, Shadow's, Knuckles', Rouge's, Mech Tails' and Mech
 * Eggman's entry; the other loaders store one value in both. The jump table at 0x00459A90 then
 * sets +0x110 and +0x114 from constants for some characters in
 * levels 30, 32, 40, 43, 44, 50 and 53, so those keep their special values whatever the table
 * holds. Nothing in the image writes the table; it is read directly in three more places, each at
 * play, after a character exists: 0x00464D25-0x00464DD8 (entry [n]+0x3C and +0x58),
 * 0x00465235-0x00466C8C (compares a character's +0x134 with Sonic's +0x74 for CharID 0 and with
 * Shadow's for CharID 1) and 0x00717A75-0x00717AD9, which sets CharObj2Base+0xC4, +0x130, +0x134
 * and +0x140 from Sonic's +0x04, +0x70, +0x74 and +0x80 (FloorGrip, Radius, Height and
 * CenterHeight; one branch times a constant). That code is in 0x00717840, the main function of the
 * four characters that run Sonic's code: Sonic, Amy, Shadow and Metal Sonic, whose loaders install
 * it at 0x00716E36, 0x007170EC, 0x00717396 and 0x0071764C.
 *
 * The table as the image has it: Sonic, Shadow, Super Sonic, Super Shadow and B are the same 132
 * bytes, and so are Knuckles and Rouge, and Mech Tails and Mech Eggman, so a swap between those is
 * no change (the run says so). Metal Sonic (12) differs from Sonic in 7 values: JumpSpeed 1.66 ->
 * 2.52, RunAccel 0.05 -> 0.2, AirAccel 0.031 -> 0.05, RunDecel -0.06 -> -0.01, RunBrake -0.18 ->
 * -0.02, AirBrake -0.17 -> -0.03 and GroundFriction -0.1 -> -0.4: a higher jump, four times the
 * acceleration on the ground, and weaker brakes.
 *
 * The tweak, in the wrap of 0x004599C0: on its first call in the process, before the lifted body,
 * it writes the preset into the table, so the first character loaded, and every one after, copies
 * the changed entry, and the direct readers above see it too. The table is written once; nothing
 * restores it, so every later load, restart and stage keeps it.
 *
 *   SA2_PHYSICS_PRESET unset or empty: the preset "metal-sonic", which is
 *       sonic=metal-sonic,shadow=metal-sonic.
 *   SA2_PHYSICS_PRESET=<to>=<from>[,<to>=<from>...]: each <to> character's entry becomes the
 *       <from> character's as the image has it (so shadow=sonic,sonic=shadow trades the two), with
 *       the names sonic, shadow, tails, eggman, knuckles, rouge, mech-tails, mech-eggman, amy,
 *       super-sonic, super-shadow, b, metal-sonic, chao-walker, dark-chao-walker, tikal and chaos.
 *       SA2_PHYSICS_PRESET=metal-sonic names the default.
 *
 * It is a setting of the tweak and does nothing alone: without SA2_TWEAKS=physics-preset, or in a
 * replay, the wrap only calls the lifted body and the variable is never read.
 *
 * By name, never silently: on the first call the run says, per character the preset names, what
 * changes: "sa2 tweaks: physics preset: Shadow takes Metal Sonic's physics (7 of 33 values: JumpSpeed
 * 1.66 -> 2.52, ...)", or "... Shadow takes Sonic's physics, the same numbers, so nothing changes".
 * The two shared reads above are said by name too, after that line, when a preset reaches them:
 * a change to Sonic's FloorGrip, Radius, Height or CenterHeight also reaches Shadow, Amy and Metal
 * Sonic, and a change to an entry's Weight reaches the characters whose CharID is that entry
 * ("... Amy also takes Sonic's new Weight 0.08 -> 0.2 (0x00459910 reloads it from entry [CharID],
 * Sonic's)"). When the changed character is one of the six, its Weight is not in the list, and a
 * line says the Weight it gets; when it is Shadow, Amy or Metal Sonic, a line names those of the
 * four values that 0x00717A75 sets back to Sonic's entry's.
 * A value of SA2_PHYSICS_PRESET that is no preset (an unknown name, a <to> named twice, a piece
 * without '=') is named in a line and the table is left alone; so is a table that does not read or
 * write.
 *
 * Off is the lifted code alone: the wrap tests the tweak's bit first and otherwise only calls
 * `__real_sub_4599c0`. Risks: it changes how the characters move, so stages tuned for the original
 * jump and speed can play differently (Metal Sonic's higher jump reaches places the stage did not
 * plan for); in 2-player mode both players share the table. Known limit: the shared reads make a
 * custom preset reach characters it does not name, and leave some of a named character's values
 * unused. The direct reads at 0x00717A75 take Sonic's entry for Shadow, Amy and Metal Sonic, so a
 * change to Sonic's FloorGrip, Radius, Height or CenterHeight reaches all three, and a preset that
 * gives one of the three other values than Sonic's for those four has them set back there. Weight
 * comes from entry [CharID], so a change to Sonic's, Shadow's, Knuckles', Rouge's, Mech Tails' or
 * Mech Eggman's Weight also reaches Amy, Metal Sonic, Tikal, Chaos, Chao Walker or Dark Chao Walker,
 * and a preset naming one of those six as <to> does not change its Weight. The run names each of
 * these. The default is not touched by either: Metal Sonic's FloorGrip, Radius, Height, Weight and
 * CenterHeight equal Sonic's and Shadow's (2, 4, 10, 0.08, 5.4). Behaviour outside the table (rail
 * exits, the characters' own code) is not touched. No save data is written: the table is not in
 * the save.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define PHYSICS_TABLE   0x017391E8u    /* PhysicsArray */
#define PHYSICS_ENTRY   0x84u          /* sizeof(PhysicsData), 33 dwords */
#define PHYSICS_ENTRIES 19u
#define PHYSICS_DWORDS  33u
#define PHYSICS_NAMED   17u            /* the Characters enum's names; 17 and 18 have none */

static const char *const PHYSICS_NAMES[PHYSICS_NAMED] = {
    "sonic", "shadow", "tails", "eggman", "knuckles", "rouge", "mech-tails", "mech-eggman", "amy",
    "super-sonic", "super-shadow", "b", "metal-sonic", "chao-walker", "dark-chao-walker", "tikal", "chaos"};
static const char *const PHYSICS_SAID[PHYSICS_NAMED] = {
    "Sonic", "Shadow", "Tails", "Eggman", "Knuckles", "Rouge", "Mech Tails", "Mech Eggman", "Amy",
    "Super Sonic", "Super Shadow", "B", "Metal Sonic", "Chao Walker", "Dark Chao Walker", "Tikal", "Chaos"};
/* PhysicsData's fields (SA2Structs.h), dword by dword; the first is an int. */
static const char *const PHYSICS_FIELDS[PHYSICS_DWORDS] = {
    "HangTime", "FloorGrip", "SpeedCapH", "SpeedCapV", "SpeedMaxH", "PushSpeedMax", "JumpSpeed",
    "NoControlSpeed", "SlideSpeed", "JogSpeed", "RunSpeed", "RushSpeed", "KnockbackSpeed", "DashSpeed",
    "JumpAdd", "RunAccel", "AirAccel", "RunDecel", "RunBrake", "AirBrake", "AirDecel", "AirResist",
    "AirResistV", "AirResistH", "GroundFriction", "GroundFrictionH", "FrictionCap", "RatBound", "Radius",
    "Height", "Weight", "CameraY", "CenterHeight"};

/* The shared reads (the header): Weight is reloaded from entry [CharID], which is not the
 * character's own entry for these six; and 0x00717A75 sets four values from Sonic's entry for the
 * characters that run Sonic's code. */
#define PHYSICS_WEIGHT 30u
static const unsigned char PHYSICS_CHARID[PHYSICS_NAMED] = {0, 1, 2, 3, 4, 5, 6, 7, 0, 9, 10, 11, 1, 6, 7, 4, 5};
static const unsigned char PHYSICS_SONIC_FIELDS[] = {1, 28, 29, 32};   /* FloorGrip, Radius, Height, CenterHeight */
#define PHYSICS_SONIC_CODE(c) ((c) == 1 || (c) == 8 || (c) == 12)    /* Shadow, Amy, Metal Sonic */

#define PHYSICS_DEFAULT "metal-sonic"
#define PHYSICS_DEFAULT_SWAPS "sonic=metal-sonic,shadow=metal-sonic"

static int g_physics_done;

static int physics_character(const char *s, size_t n) {
    for (unsigned i = 0; i < PHYSICS_NAMED; i++)
        if (strlen(PHYSICS_NAMES[i]) == n && memcmp(PHYSICS_NAMES[i], s, n) == 0) return (int)i;
    return -1;
}

/* from[to] = the entry `to` takes, or -1; 0 with `why` filled when the value is no preset. */
static int physics_parse(const char *value, int from[PHYSICS_NAMED], char *why, size_t n) {
    for (unsigned i = 0; i < PHYSICS_NAMED; i++) from[i] = -1;
    if (strcmp(value, PHYSICS_DEFAULT) == 0) value = PHYSICS_DEFAULT_SWAPS;
    int pairs = 0;
    for (const char *p = value; *p;) {
        const char *end = strchr(p, ',');
        if (!end) end = p + strlen(p);
        const char *s = p, *e = end;
        while (s < e && (*s == ' ' || *s == '\t')) s++;
        while (e > s && (e[-1] == ' ' || e[-1] == '\t')) e--;
        if (s < e) {
            const char *eq = memchr(s, '=', (size_t)(e - s));
            if (!eq) {
                snprintf(why, n, "\"%.*s\" is no <to>=<from> pair", (int)(e - s), s);
                return 0;
            }
            const int to = physics_character(s, (size_t)(eq - s));
            const int src = physics_character(eq + 1, (size_t)(e - eq - 1));
            if (to < 0 || src < 0) {
                const char *bad = to < 0 ? s : eq + 1;
                const size_t len = to < 0 ? (size_t)(eq - s) : (size_t)(e - eq - 1);
                snprintf(why, n, "no character is named \"%.*s\"", (int)len, bad);
                return 0;
            }
            if (from[to] >= 0) {
                snprintf(why, n, "%s is given physics twice", PHYSICS_NAMES[to]);
                return 0;
            }
            from[to] = src;
            pairs++;
        }
        p = *end ? end + 1 : end;
    }
    if (!pairs) snprintf(why, n, "it names no <to>=<from> pair");
    return pairs > 0;
}

static void physics_value(char *out, size_t n, unsigned field, uint32_t bits) {
    if (field == 0) {
        snprintf(out, n, "%d", (int32_t)bits);
        return;
    }
    float f;
    memcpy(&f, &bits, sizeof f);
    snprintf(out, n, "%g", (double)f);
}

/* Appends "<field> <a> -> <b>" (or "<field> <b>" when a is NULL) to a list being built. */
static void physics_list(char *line, size_t n, size_t *at, unsigned *count, unsigned field,
                         const uint32_t *a, uint32_t b) {
    char va[32], vb[32];
    physics_value(vb, sizeof vb, field, b);
    if (a) physics_value(va, sizeof va, field, *a);
    if (*at < n)
        *at += (size_t)snprintf(line + *at, n - *at, "%s%s %s%s%s", *count ? ", " : "", PHYSICS_FIELDS[field],
                                a ? va : "", a ? " -> " : "", vb);
    ++*count;
}

/* The lines for the shared reads, after `to`'s entry was written with `src` over `old`. AFTER(e) is
 * what entry e holds once the whole preset is written. */
static void physics_shared(const uint32_t (*table)[PHYSICS_DWORDS], const int from[PHYSICS_NAMED],
                           unsigned to, const uint32_t *old, const uint32_t *src) {
#define AFTER(e) (table[from[e] >= 0 ? (unsigned)from[e] : (e)])
    char line[512];
    size_t at = 0;
    unsigned n = 0;
    /* Others that read this entry's Weight: the characters whose CharID is `to`. */
    if (old[PHYSICS_WEIGHT] != src[PHYSICS_WEIGHT]) {
        for (unsigned c = 0; c < PHYSICS_NAMED; c++)
            if (c != to && PHYSICS_CHARID[c] == to && at < sizeof line)
                at += (size_t)snprintf(line + at, sizeof line - at, "%s%s", n++ ? " and " : "", PHYSICS_SAID[c]);
        if (n) {
            char a[32], b[32];
            physics_value(a, sizeof a, PHYSICS_WEIGHT, old[PHYSICS_WEIGHT]);
            physics_value(b, sizeof b, PHYSICS_WEIGHT, src[PHYSICS_WEIGHT]);
            fprintf(stderr, "sa2 tweaks: physics preset: %s also take%s %s's new Weight %s -> %s (0x00459910 "
                    "reloads it from entry [CharID], %s's)\n", line, n > 1 ? "" : "s", PHYSICS_SAID[to], a, b,
                    PHYSICS_SAID[to]);
        }
    }
    /* Others that read Sonic's four values at 0x00717A75. */
    if (to == 0) {
        at = 0, n = 0;
        for (unsigned i = 0; i < sizeof PHYSICS_SONIC_FIELDS; i++) {
            const unsigned f = PHYSICS_SONIC_FIELDS[i];
            if (old[f] != src[f]) physics_list(line, sizeof line, &at, &n, f, &old[f], src[f]);
        }
        if (n)
            fprintf(stderr, "sa2 tweaks: physics preset: Shadow, Amy and Metal Sonic also take Sonic's new %s "
                    "(the Sonic code they share sets them from Sonic's entry at 0x00717A75)\n", line);
    }
    /* This character's own values that a shared read sets from another entry. */
    if (PHYSICS_CHARID[to] != to && AFTER(PHYSICS_CHARID[to])[PHYSICS_WEIGHT] != src[PHYSICS_WEIGHT]) {
        char kept[32], given[32];
        physics_value(kept, sizeof kept, PHYSICS_WEIGHT, AFTER(PHYSICS_CHARID[to])[PHYSICS_WEIGHT]);
        physics_value(given, sizeof given, PHYSICS_WEIGHT, src[PHYSICS_WEIGHT]);
        fprintf(stderr, "sa2 tweaks: physics preset: %s's Weight is %s, %s's, not %s's %s: 0x00459910 reloads "
                "it from entry [CharID]\n", PHYSICS_SAID[to], kept, PHYSICS_SAID[PHYSICS_CHARID[to]],
                PHYSICS_SAID[from[to]], given);
    }
    if (PHYSICS_SONIC_CODE(to)) {
        at = 0, n = 0;
        for (unsigned i = 0; i < sizeof PHYSICS_SONIC_FIELDS; i++) {
            const unsigned f = PHYSICS_SONIC_FIELDS[i];
            if (src[f] != AFTER(0)[f]) physics_list(line, sizeof line, &at, &n, f, NULL, AFTER(0)[f]);
        }
        if (n)
            fprintf(stderr, "sa2 tweaks: physics preset: at 0x00717A75 the Sonic code that %s runs sets back "
                    "Sonic's %s\n", PHYSICS_SAID[to], line);
    }
#undef AFTER
}

static void physics_apply(void) {
    const char *env = getenv("SA2_PHYSICS_PRESET");
    const char *value = env && *env ? env : PHYSICS_DEFAULT;
    int from[PHYSICS_NAMED];
    char why[160];
    if (!physics_parse(value, from, why, sizeof why)) {
        fprintf(stderr, "sa2 tweaks: physics preset: SA2_PHYSICS_PRESET=%s is no preset (%s; it takes "
                "metal-sonic or <to>=<from> pairs), so the physics are left alone\n", value, why);
        return;
    }
    static uint32_t table[PHYSICS_ENTRIES][PHYSICS_DWORDS];
    if (sa2_peek(PHYSICS_TABLE, sizeof table, table) != SA2_OK) {
        fprintf(stderr, "sa2 tweaks: physics preset: the physics table at 0x%08X does not read, so the "
                "physics are left alone\n", PHYSICS_TABLE);
        return;
    }
    for (unsigned to = 0; to < PHYSICS_NAMED; to++) {
        if (from[to] < 0) continue;
        const uint32_t *old = table[to], *src = table[from[to]];
        /* For the six whose CharID is another character, 0x00459910 reloads Weight from that entry, so
         * it is left out of the list; physics_shared names the Weight they get. */
        const unsigned charid = PHYSICS_CHARID[to];
        const int own_weight = charid == to;
        char line[2048];
        size_t at = 0;
        unsigned changed = 0, differ = 0;
        for (unsigned f = 0; f < PHYSICS_DWORDS; f++) {
            if (old[f] == src[f]) continue;
            differ++;
            if (f == PHYSICS_WEIGHT && !own_weight) continue;
            physics_list(line, sizeof line, &at, &changed, f, &old[f], src[f]);
        }
        if (!differ) {
            fprintf(stderr, "sa2 tweaks: physics preset: %s takes %s's physics, the same numbers, so "
                    "nothing changes\n", PHYSICS_SAID[to], PHYSICS_SAID[from[to]]);
            continue;
        }
        if (!changed) {
            fprintf(stderr, "sa2 tweaks: physics preset: %s takes %s's physics; the one value that differs "
                    "is Weight, which %s takes from %s's entry (0x00459910 reloads it from entry [CharID]), "
                    "so nothing changes\n", PHYSICS_SAID[to], PHYSICS_SAID[from[to]], PHYSICS_SAID[to],
                    PHYSICS_SAID[charid]);
            continue;
        }
        if (sa2_poke(PHYSICS_TABLE + to * PHYSICS_ENTRY, PHYSICS_ENTRY, src) != SA2_OK) {
            fprintf(stderr, "sa2 tweaks: physics preset: %s's entry at 0x%08X does not write, so %s keeps "
                    "its physics\n", PHYSICS_SAID[to], PHYSICS_TABLE + to * PHYSICS_ENTRY, PHYSICS_SAID[to]);
            continue;
        }
        fprintf(stderr, "sa2 tweaks: physics preset: %s takes %s's physics (%u of %u values: %s)\n",
                PHYSICS_SAID[to], PHYSICS_SAID[from[to]], changed, PHYSICS_DWORDS, line);
        physics_shared((const uint32_t (*)[PHYSICS_DWORDS])table, from, to, old, src);
    }
}

void *__real_sub_4599c0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_4599c0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_PHYSICS_PRESET)) return __real_sub_4599c0(state, pc, memory);
    if (!g_physics_done) {
        g_physics_done = 1;
        physics_apply();
    }
    return __real_sub_4599c0(state, pc, memory);
}
