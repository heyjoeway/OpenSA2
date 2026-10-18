/* metal-bounce-fix: Metal Sonic's bounce attack pose is given a model Metal Sonic has, as the
 * Mod Loader's "Fix Metal Sonic Bounce Attack Crash" code does (runtime/sa2_tweaks.h: the tweaks
 * and their rules).
 *
 * TWEAK_WRAPS: sub_717640
 *
 * What is wrong, read from the unpacked image. MetalSonicAnimList (0x00A1A7E8, in .dataa) is Metal
 * Sonic's table of 249 animation entries of 16 bytes each: AnimNum, ModelNum, mtnmode and
 * NextAnimation (int16 each), then TransitionSpeed and AnimationSpeed (floats). Entry 30, at
 * 0x00A1A9C8, is the bounce attack's ball pose. Its bytes are 1E 00 47 00 03 00 1E 00, then 0.5 and
 * 1.0: animation 30 on model 71. The table was copied from Shadow's (0x00A188C8), whose entry 30 is
 * the same, and model 71 is Shadow's ball model. Metal Sonic's model is 418 (0x01A2), which every
 * other entry from 0 to 144 names, and Metal Sonic's model set holds no model 71. The game reaches
 * entry 30 when Metal Sonic bounces (action 69), which needs the bounce bracelet bit (0x10) in his
 * Upgrades (playerwk+0x24). LoadMetalSonic gives him none in a two-player game; only the "Have All
 * Upgrades" cheat sets it, and then the first bounce crashes.
 *
 * The fix is the Mod Loader code's three writes: byte 0x0C at 0x00A1A9C8 and bytes A2 01 at
 * 0x00A1A9CA. Entry 30 then begins 0C 00 A2 01: animation 12, the ball that entry 12 of Sonic's and
 * Shadow's tables uses, on model 418. Its mtnmode (3), NextAnimation (30) and speeds stay. Byte
 * 0x00A1A9C9 is 00 before and after, so the tweak writes the four bytes 0C 00 A2 01 at once.
 *
 * Where it writes. The table is read only through the pointer that LoadMetalSonic (0x00717640)
 * stores at playerwk+0x198 (`mov dword ptr [ebp+198h],0A1A7E8h` at 0x00717717). That is the only
 * reference to the table in .text. LoadMetalSonic's only caller is LoadShadow (0x00717360, the call
 * at 0x00717382), which calls it in a two-player game when the player picked Metal Sonic
 * (TwoPlayerMode, the byte at 0x0174AFDE, and AltCharacter[player] at 0x0174B015 both nonzero). The
 * wrap writes the entry on the first call of LoadMetalSonic, before the lifted body runs, so no
 * Metal Sonic has read the table yet. Nothing writes the table back, so the write is done once per
 * process. It writes only the executable's data in guest memory, never the save.
 *
 * Why off is identical: with the tweak off the wrap only calls `__real_sub_717640`, and the table
 * keeps the executable's bytes. With it on, nothing changes until a two-player game loads Metal
 * Sonic.
 *
 * By name, never silently. Before writing, the wrap reads the four bytes. When they are the
 * original bytes it writes, reads the entry back and says "sa2 tweaks: metal-bounce fixed:
 * Metal Sonic's bounce pose (0x00A1A9C8) now animation 12 on model 418". When they are something
 * else, or the read fails, it leaves the table alone and says why, with the bytes it found. It says
 * one of these lines on the first load only.
 *
 * Risks. In normal play there is none: without the cheat Metal Sonic never reaches entry 30, so the
 * change shows only with a cheat that gives him the bounce upgrade, which OpenSA2 does not offer.
 * With such a cheat the pose shows Metal Sonic in the ball of animation 12, the Mod Loader code's
 * choice. Checked in a lifted Metal Harbor run with Metal Sonic loaded and the bit set: off, the
 * first bounce entered entry 30 and read guest address 0; on, he bounced five times and the run
 * went on. Without the bit, off and on played the same frames. The unit test is
 * Documentation/scripts/tests/test_tweak_metal_bounce_fix.py.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define METAL_BOUNCE_ENTRY 0x00A1A9C8u   /* MetalSonicAnimList[30]: AnimNum, ModelNum */

static const uint8_t METAL_BOUNCE_WAS[4] = {0x1E, 0x00, 0x47, 0x00};   /* animation 30, model 71 */
static const uint8_t METAL_BOUNCE_NOW[4] = {0x0C, 0x00, 0xA2, 0x01};   /* animation 12, model 418 */

static int g_metal_bounce_tried;

static void metal_bounce_write(void) {
    uint8_t got[4];
    if (sa2_peek(METAL_BOUNCE_ENTRY, 4, got) != SA2_OK) {
        fprintf(stderr, "sa2 tweaks: metal-bounce fixed: Metal Sonic's bounce pose at 0x%08X does "
                "not read, so it is left alone\n", METAL_BOUNCE_ENTRY);
        return;
    }
    if (memcmp(got, METAL_BOUNCE_WAS, 4) != 0) {
        fprintf(stderr, "sa2 tweaks: metal-bounce fixed: Metal Sonic's bounce pose at 0x%08X holds "
                "%02X %02X %02X %02X, not the game's 1E 00 47 00, so it is left alone\n",
                METAL_BOUNCE_ENTRY, got[0], got[1], got[2], got[3]);
        return;
    }
    if (sa2_poke(METAL_BOUNCE_ENTRY, 4, METAL_BOUNCE_NOW) != SA2_OK
        || sa2_peek(METAL_BOUNCE_ENTRY, 4, got) != SA2_OK || memcmp(got, METAL_BOUNCE_NOW, 4) != 0) {
        fprintf(stderr, "sa2 tweaks: metal-bounce fixed: Metal Sonic's bounce pose at 0x%08X could "
                "not be written\n", METAL_BOUNCE_ENTRY);
        return;
    }
    fprintf(stderr, "sa2 tweaks: metal-bounce fixed: Metal Sonic's bounce pose (0x%08X) now "
            "animation 12 on model 418\n", METAL_BOUNCE_ENTRY);
}

void *__real_sub_717640(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_717640(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_METAL_BOUNCE_FIX)) return __real_sub_717640(state, pc, memory);
    if (!g_metal_bounce_tried) {
        g_metal_bounce_tried = 1;
        metal_bounce_write();
    }
    return __real_sub_717640(state, pc, memory);
}
