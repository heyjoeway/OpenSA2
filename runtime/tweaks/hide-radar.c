/* hide-radar: in a treasure hunt (Knuckles, Rouge, and the two-player hunts), the emerald radar at
 * the bottom of the screen and the '!' it shows over the player on top of a piece are not drawn,
 * and the radar's beep is not played, so the pieces are found by eye and by the hint monitors alone
 * (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_73a6f0, sub_437260
 *
 * Where the radar comes from, read from the unpacked image. The setup 0x0073AA70 makes the radar's
 * task with LoadObject (0x0046F680, main 0x00739570), keeps its data at [0x01AF014C], and sets the
 * task's delete routine (+0x18, 0x0073A970) and a display routine (+0x28, 0x0073A6F0, written at
 * 0x0073AB4F, its only reference in the image). The task's main and its display split the work:
 *
 *   0x0073A6F0  the display. It draws and writes nothing the game reads: `cmp byte [0x0174AFCB],0`
 *               and three more switches ([0x0174B002], [0x0174B009], [0x0174AFF9]) send it straight
 *               to its pops and plain `ret` (0x0073A961), and otherwise it sets the texture list
 *               (0x00426420), two fields of the sprite state at [0x02670544], draws an icon per
 *               piece (0x00739FE0, whose only other stores are its own vertex scratch at 0x00A170xx
 *               and 0x01AF015C-0x01AF0164, read nowhere else) and, when the tracked piece is within
 *               30.0 (the double at 0x00905090), the red '!' over the player (0x0073A450, a quad
 *               through 0x0077F6B0, seen in a probe run with a piece's radar point moved onto
 *               Knuckles). One player's radar is 0x0073A790-0x0073A843, two players' from
 *               0x0073A848; both are skipped. It reads no stack argument, and its early exit
 *               changes no register but the flags (EBX, EBP, ESI, EDI and ESP are restored, EAX,
 *               ECX and EDX untouched), so the RET alone (EIP = [ESP], ESP + 4) leaves its caller
 *               what the game leaves it whenever one of those switches is off.
 *   0x00739570  the main, left to run: it moves the hunt along (the piece records at [0x01AF014C],
 *               their distance bands at +0x0A and +0x0B that the hint code reads at 0x0044CCC7, the
 *               voice lines 0x0506/0x0507 through 0x00443130) and plays the beep, sound 0x1009, by
 *               calling 0x00437260 (the sound effect player: ESI the sound, three cdecl arguments,
 *               plain `ret`) at a rate set by the tracked piece's distance and the blink clock
 *               [0x0174B03C]. Five calls, all in its arm 0x00739A65 and each `mov esi,1009h` just
 *               before: two players' at 0x00739CDF, 0x00739D11 and 0x00739D35, one player's at
 *               0x00739DF2 and 0x00739E0D (return addresses 0x00739CE4, 0x00739D16, 0x00739D3A,
 *               0x00739DF7, 0x00739E12). After each the main pops the three arguments and reads none
 *               of EAX, ECX or EDX, and ESI is 0x1009 whether or not the call ran.
 *
 * The display's wrap is the RET alone, every time: the lifted dispatcher reaches it through the
 * function table, which -wrap reaches (tools/s4/link_s4.py). The sound player's wrap is the RET
 * alone when the return address at guest [ESP] is one of the five and ESI is 0x1009, and the lifted
 * body otherwise, so every other sound in the game plays.
 *
 * Off is the lifted code alone: each wrap's first statement tests the tweak's bit and otherwise
 * calls its __real_ and returns, touching no register and no guest byte.
 *
 * What it does not change: the hunt itself, the hint monitors, the characters' voice lines about a
 * piece being near, title cards and the save, which the tweak never writes. Left
 * out on purpose: 0x0174AFCB, the switch the display tests first (Codes.lst's "Disable HUD:
 * Prompts/Radar"), would hide the radar and its '!' as well but also stops the Eggman Quarters guards
 * (0x0069117B, 0x00697468) and the title cards (0x0043CD9B, 0x0043D17C), so it is gameplay, not the
 * radar; and the beep is not that byte's, so the display's own switches cannot silence it.
 *
 * Risks: a hunt is harder with it on, so a time made with it is not comparable with one made
 * without. A radar-all-pieces run with this on shows nothing of that tweak: its wrap of 0x00739FE0
 * is reached only from this display, and its wrap of the distance (0x0077FBD0) passes every call
 * through while this tweak is on. If a build moved the beep's calls, the return addresses would
 * no longer match and the beep would play: the run names each site the first time it is silenced,
 * so a missing line shows a moved site. When the guest stack does not read, the call goes through
 * (the radar drawn, the sound played) and the run says so once.
 *
 * Its lines, each said once: "sa2 tweaks: radar hidden: radar not drawn (display 0x0073A6F0
 * skipped)", and "sa2 tweaks: radar hidden: radar beep not played (sound 0x1009, call at
 * 0x00739DF2)" the first time each of the five calls is skipped. A call from one of the five sites
 * with ESI other than 0x1009 plays, and is said once.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define RADAR_BEEP_SOUND 0x1009u

/* The radar main's calls of the sound player that play the beep: the call's address and the
 * return address the lifted CALL pushes (the header). */
static const struct {
    uint32_t call, ret;
} RADAR_BEEP[] = {
    {0x00739CDFu, 0x00739CE4u}, {0x00739D11u, 0x00739D16u}, {0x00739D35u, 0x00739D3Au},
    {0x00739DF2u, 0x00739DF7u}, {0x00739E0Du, 0x00739E12u},
};
#define RADAR_BEEP_SITES (sizeof RADAR_BEEP / sizeof RADAR_BEEP[0])

static int g_radar_display_said, g_radar_stack_said, g_radar_beep_said[RADAR_BEEP_SITES];
static int g_radar_sound_said[RADAR_BEEP_SITES];

/* The return address the lifted CALL pushed, at guest [ESP]; 0 (and said once) when it does not
 * read, so the call goes through. */
static uint32_t radar_site(void *state, const char *what) {
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0;
    if (sa2_peek(esp, 4, &site) == SA2_OK) return site;
    if (!g_radar_stack_said++)
        fprintf(stderr, "sa2 tweaks: radar hidden: the guest stack at 0x%08X does not read, so %s "
                "goes through\n", esp, what);
    return 0;
}

/* The RET alone: EIP = the return address, ESP + 4, every other register as the caller left it. */
static void *radar_skip(void *state, uint32_t site, void *memory) {
    sa2_rt_set_gpr(state, SA2_ESP, sa2_rt_get_gpr(state, SA2_ESP) + 4u);
    sa2_rt_set_gpr(state, SA2_EIP, site);
    return memory;
}

void *__real_sub_73a6f0(void *state, uint32_t pc, void *memory);
void *__real_sub_437260(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_73a6f0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_HIDE_RADAR)) return __real_sub_73a6f0(state, pc, memory);
    const uint32_t site = radar_site(state, "the radar's display");
    if (!site) return __real_sub_73a6f0(state, pc, memory);
    if (!g_radar_display_said++)
        fprintf(stderr, "sa2 tweaks: radar hidden: radar not drawn (display 0x0073A6F0 skipped)\n");
    return radar_skip(state, site, memory);
}

__attribute__((used, retain))
void *__wrap_sub_437260(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_HIDE_RADAR)) return __real_sub_437260(state, pc, memory);
    const uint32_t site = radar_site(state, "the sound");
    unsigned i = 0;
    while (i < RADAR_BEEP_SITES && RADAR_BEEP[i].ret != site) i++;
    if (i == RADAR_BEEP_SITES) return __real_sub_437260(state, pc, memory);
    const uint32_t sound = sa2_rt_get_gpr(state, SA2_ESI);
    if (sound != RADAR_BEEP_SOUND) {
        if (!g_radar_sound_said[i]++)
            fprintf(stderr, "sa2 tweaks: radar hidden: the call at 0x%08X plays sound 0x%X, not the "
                    "radar beep 0x%X, so it plays\n", RADAR_BEEP[i].call, sound, RADAR_BEEP_SOUND);
        return __real_sub_437260(state, pc, memory);
    }
    if (!g_radar_beep_said[i]++)
        fprintf(stderr, "sa2 tweaks: radar hidden: radar beep not played (sound 0x%X, call at "
                "0x%08X)\n", RADAR_BEEP_SOUND, RADAR_BEEP[i].call);
    return radar_skip(state, site, memory);
}
