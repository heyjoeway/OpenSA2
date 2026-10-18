/* hide-upgrade-models: the upgrades a character has found (Sonic's Bounce Bracelet and Magic
 * Gloves, Knuckles' Shovel Claws and Sunglasses, Mech Tails' Booster, ...) are not drawn on
 * the character in play; the character looks as it does before it finds them. A walker's guns
 * stay drawn (the Laser Blaster, Mech Tails' Bazooka, Mech Eggman's Large Cannon): its shots start
 * from them, below. The upgrades still work: only the five player display functions see them
 * hidden, and only while they run (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_720090, sub_72ef20, sub_730970, sub_7444f0, sub_747dd0
 *
 * Where the models come from, read from the unpacked image. A player's upgrades are the dword
 * Upgrades at CharObj2Base+0x24, and its task's CharObj2Base is the task's Data2 (+0x40). Each
 * player display function takes the task at [ESP+4] (cdecl), loads Data2 in its prologue, and
 * draws an upgrade's model when its bit is set:
 *
 *   0x00720090  Sonic_Display, for Sonic and Shadow: `mov eax,[ebx+24h]; test eax,40008h` at
 *               0x007207CC (Flame Ring, Sonic 0x8 or Shadow 0x40000), `test byte [ebx+24h],10h`
 *               at 0x007208C1 (Bounce Bracelet), `,4` at 0x007209C0 (Magic Gloves), `,1` at
 *               0x007209EA (Light Shoes, two models), `test dword [ebx+24h],10000h` at 0x00720A34
 *               (Shadow's Air Shoes, two models).
 *   0x0072EF20  Knuckles_Display: 0x800 (Sunglasses) at 0x0072F4CD, 0x2000 (Air Necklace) at
 *               0x0072F528, 0x1400 (Shovel Claw or Hammer Gloves, which pick the hands) at
 *               0x0072F564 and 0x400 and 0x1000 after it.
 *   0x00730970  Rouge_Display: 0x4000000 (Treasure Scope) at 0x00730FEF, 0x8000000 (Iron Boots) at
 *               0x0073104B, 0x2000000 (Pick Nails) at 0x007310B8 and 0x007310FF.
 *   0x007444F0  MechEggman_Display, for Eggman's and Tails' walkers' Eggman shape: 0x400000 (Laser
 *               Blaster: the gun's model, else the plain one) at 0x0074490B, 0x200000 (Large
 *               Cannon) at 0x00744DF3, 0x100000 (Jet Engine) at 0x00744FD1, 0x800000 (Protective
 *               Armor) at 0x00745112.
 *   0x00747DD0  MechTails_Display: 0x100 (Laser Blaster, as above) at 0x0074815F, 0x80 (Bazooka) at
 *               0x0074860E, 0x40 (Booster) at 0x007488CF.
 *
 * Every test of those bits against +0x24 in the image's .text was listed, and the call graph from
 * each player display (direct calls and the function addresses it passes, five deep) was searched
 * for them: these five are the only displays that read them. The other six player displays,
 * Tikal (0x0072FB10), Chaos (0x00731A30), Eggman on foot (0x0073EF20), the Chao walkers
 * (0x00746290, 0x007470D0) and Tails on foot (0x007507D0), read none and are not wrapped. The draw
 * callbacks (SonicDrawCallBack 0x0071EAA0 and the others) read no upgrade bit either.
 *
 * Each wrap reads the task at guest [ESP+4] on entry, its CharObj2Base at [task+0x40] and the
 * Upgrades dword at +0x24. When a model bit (MODELS, below) is set, it writes the dword with the
 * model bits cleared, calls the lifted body, and writes the saved dword back, so every read the
 * body makes during the call sees no upgrade with a model and every read after it sees the
 * player's own. No display writes Upgrades (each body's stores were listed; none goes to +0x24),
 * so the dword after the call is the cleared one, and the saved one goes back as it was. If a body
 * did change it, the wrap keeps the change and adds the cleared bits back, and says so once.
 *
 * A walker's guns are never cleared (UPGRADES_GUNS, 0x00600180), because the walker displays save
 * where its shots start while they draw the gun they picked. After the Laser Blaster test each
 * display walks the gun model it drew (plain or Laser Blaster) and saves the matrix of the node
 * it meets (MechEggman 0x00744B3D, a cmp with [0x01DE9E2C] or the gun's sibling; MechTails
 * 0x00748375, [0x01DE9F9C]) into the player at +0x23C (0x00744BDC, 0x007483F8). After the Large
 * Cannon test (0x00744DF3) and the Bazooka test (0x0074860E) it saves the end node of the cannon
 * or of the plain arm into +0x248 and +0x2E4 (0x00744F0B and 0x00744F2B, 0x00748807 and
 * 0x00748827). Cleared, those bits move the saved points to the plain models', so shots leave
 * from elsewhere: in a run on Mech Eggman's first stage [private] with every upgrade, the tweak
 * clearing the Large Cannon left an iron container standing that the same shots destroy with it
 * drawn, and the score fell behind. The Booster, the Jet Engine and the Protective Armor save
 * nothing into the player; they are hidden.
 *
 * The bits cleared are the Upgrades enum's (SA2Enums.h) model-bearing ones that the five displays
 * test, less the guns: 0x0E953C5D. Never cleared: the guns, Super Sonic 0x8000, the Ancient
 * Lights (0x2, 0x20000) and the Mystic Melodies (0x20, 0x200, 0x4000, 0x80000, 0x1000000,
 * 0x10000000), which have no model. The game's other reads of the bits (the abilities: the bounce, the light
 * dash, the dig, the Flame Ring's fire during the somersault, drawn by 0x00720B50 and 0x00720C10,
 * Sonic's other two displays, which the tweak leaves alone) run outside these calls and see the
 * player's own dword.
 *
 * Off is identical: each wrap tests its bit first and otherwise only calls `__real_` and returns.
 * On, the wrap writes guest memory only between its two writes of the one dword, inside the call,
 * and leaves it as it found it, so the save (which keeps upgrades in its own Got bytes, written by
 * other code) never sees a cleared value.
 *
 * Not covered: cutscenes, which attach upgrade models through their own path
 * (eventAttachUpgradeCheck 0x00601B50, loadEventUpgradeModels 0x00601BB0); in-engine events still
 * show the upgrades a character has. A guest read or write that fails leaves that call as the game
 * has it, and the run says so once, by display.
 *
 * The first time each display draws with upgrades hidden, the run says so once: "sa2 tweaks:
 * upgrade models hidden: Sonic_Display draws without upgrades 0x00000015 (Upgrades 0x00008035)".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define UPGRADES_DATA2   0x40u          /* ObjectMaster.Data2: the CharObj2Base */
#define UPGRADES_FIELD   0x24u          /* CharObj2Base.Upgrades */
#define UPGRADES_GUNS    0x00600180u    /* Laser Blasters, Bazooka, Large Cannon: shots start there */
#define UPGRADES_MODELS  (0x0EF53DDDu & ~UPGRADES_GUNS) /* 0x0E953C5D: the models hidden */

enum { SONIC, KNUCKLES, ROUGE, MECH_EGGMAN, MECH_TAILS, DISPLAYS };
static const char *const DISPLAY_NAME[DISPLAYS] = {
    "Sonic_Display", "Knuckles_Display", "Rouge_Display", "MechEggman_Display", "MechTails_Display"};
static unsigned g_said_hidden, g_said_unread, g_said_unwritten, g_said_changed;

/* Says `line` once per display and kind of case (`said`, a bit per display). */
static void say_once(unsigned *said, int display, const char *line) {
    if (*said & (1u << display)) return;
    *said |= 1u << display;
    fprintf(stderr, "sa2 tweaks: upgrade models hidden: %s %s\n", DISPLAY_NAME[display], line);
}

typedef void *lifted_fn(void *state, uint32_t pc, void *memory);

/* The lifted display with the model bits of its player's Upgrades cleared for the call. */
static void *hidden(int display, lifted_fn *real, void *state, uint32_t pc, void *memory) {
    char line[160];
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t task = 0, cp = 0, saved = 0;
    if (sa2_peek(esp + 4u, 4, &task) != SA2_OK || task == 0 ||
        sa2_peek(task + UPGRADES_DATA2, 4, &cp) != SA2_OK || cp == 0 ||
        sa2_peek(cp + UPGRADES_FIELD, 4, &saved) != SA2_OK) {
        snprintf(line, sizeof line, "draws as the game has it: its player's Upgrades do not read (the "
                 "guest stack at 0x%08X)", esp);
        say_once(&g_said_unread, display, line);
        return real(state, pc, memory);
    }
    if (!(saved & UPGRADES_MODELS)) return real(state, pc, memory);
    const uint32_t at = cp + UPGRADES_FIELD, cleared = saved & ~UPGRADES_MODELS;
    if (sa2_poke(at, 4, &cleared) != SA2_OK) {
        snprintf(line, sizeof line, "draws as the game has it: Upgrades at 0x%08X does not write", at);
        say_once(&g_said_unwritten, display, line);
        return real(state, pc, memory);
    }
    snprintf(line, sizeof line, "draws without upgrades 0x%08X (Upgrades 0x%08X)",
             saved & UPGRADES_MODELS, saved);
    say_once(&g_said_hidden, display, line);
    void *ret = real(state, pc, memory);
    uint32_t now = cleared, back = saved;
    if (sa2_peek(at, 4, &now) == SA2_OK && now != cleared) {
        back = now | (saved & UPGRADES_MODELS);
        snprintf(line, sizeof line, "changed Upgrades while drawing (to 0x%08X); its change is kept and "
                 "the hidden bits put back", now);
        say_once(&g_said_changed, display, line);
    }
    if (sa2_poke(at, 4, &back) != SA2_OK) {
        snprintf(line, sizeof line, "left Upgrades at 0x%08X cleared: it does not write back", at);
        say_once(&g_said_unwritten, display, line);
    }
    return ret;
}

void *__real_sub_720090(void *state, uint32_t pc, void *memory);
void *__real_sub_72ef20(void *state, uint32_t pc, void *memory);
void *__real_sub_730970(void *state, uint32_t pc, void *memory);
void *__real_sub_7444f0(void *state, uint32_t pc, void *memory);
void *__real_sub_747dd0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_720090(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_HIDE_UPGRADE_MODELS)) return __real_sub_720090(state, pc, memory);
    return hidden(SONIC, __real_sub_720090, state, pc, memory);
}

__attribute__((used, retain))
void *__wrap_sub_72ef20(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_HIDE_UPGRADE_MODELS)) return __real_sub_72ef20(state, pc, memory);
    return hidden(KNUCKLES, __real_sub_72ef20, state, pc, memory);
}

__attribute__((used, retain))
void *__wrap_sub_730970(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_HIDE_UPGRADE_MODELS)) return __real_sub_730970(state, pc, memory);
    return hidden(ROUGE, __real_sub_730970, state, pc, memory);
}

__attribute__((used, retain))
void *__wrap_sub_7444f0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_HIDE_UPGRADE_MODELS)) return __real_sub_7444f0(state, pc, memory);
    return hidden(MECH_EGGMAN, __real_sub_7444f0, state, pc, memory);
}

__attribute__((used, retain))
void *__wrap_sub_747dd0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_HIDE_UPGRADE_MODELS)) return __real_sub_747dd0(state, pc, memory);
    return hidden(MECH_TAILS, __real_sub_747dd0, state, pc, memory);
}
