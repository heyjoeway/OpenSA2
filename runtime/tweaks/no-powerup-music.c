/* no-powerup-music: the jingles that play when the player breaks a speed-up (speed shoes) or an
 * invincibility item box are not played, so the stage's music keeps playing through the power-up;
 * the power-ups themselves, the character's voice line, and every other jingle are unchanged
 * (runtime/sa2_tweaks.h: the tweaks and their rules). It is the two "Disable High Speed Music" and
 * "Disable Invincibility Music" patches of the Mod Loader's Codes.lst, which NOP the two calls.
 *
 * TWEAK_WRAPS: sub_443480
 *
 * Where the jingles come from, read from the unpacked image. 0x00443480 (PlayJingle, 165 bytes)
 * plays an ADX file once over the stage's song: EBX is the file name (usercall, no stack
 * arguments; plain `ret` at 0x0044351A and 0x00443524). It looks the name up among the songs
 * (0x00443560), and when jingles are on (the byte 0x0174AFE4) allocates a task whose job, at
 * 0x00443320, puts the stage's song back when the jingle ends, then plays the file
 * (PlayMusicOnce, 0x00442EF0). Many places call it; two are the power-ups':
 *
 *   0x0046E072  PlayerSpeedUp (0x0046E000), EBX = 0x008B94FC "speedup.adx"; return address
 *               0x0046E077, then `movsx eax,byte [esi+3]` and a jump table on the character
 *               (0x0046E0E4) to the voice line (0x00443130).
 *   0x0046E495  the invincibility handler (0x0046E440), EBX = 0x008B9518 "invncibl.adx"; return
 *               address 0x0046E49A, then `movsx eax,byte [esi+3]` and the same kind of table
 *               (0x0046E56C) to the voice line.
 *
 * The wrap reads the return address at guest [ESP] on entry (the lifted CALL has pushed it). When
 * it is one of those two, the wrap does what the lifted `ret` does without running the body:
 * EIP = the return address, ESP + 4. That is the Codes.lst NOP's effect: the call is skipped and
 * no register moves. EAX, ECX and EDX keep what the caller had in them, where the body would have
 * changed them; both callers write EAX at once and the paths after them write EDX before reading
 * it and never read ECX, which the calling convention leaves dead. With the jingle, its task is not
 * made, so nothing stops or restores the stage's song: it plays on. Any other caller, and every
 * call when the tweak is off, runs the lifted body as it is (`__real_`). The jingles' switch,
 * 0x0174AFE4, is left alone: it silences every jingle (the 1-up, the goal ring, the bosses').
 *
 * Saves: nothing the save holds is read or written.
 *
 * Risks: none to gameplay. Each handler grants its power-up before the call (PlayerSpeedUp scales
 * the player's speeds at 0x0046E042-0x0046E06C; the invincibility handler sets bit 0x8000 of the
 * player's status word [esi+10h] at 0x0046E48C), and nothing in the jingle feeds back into either.
 * The jingle's own restore of the stage's song is skipped with it, as the NOP skips it.
 *
 * The first time the tweak skips each jingle, the run says so, once per jingle: "sa2 tweaks:
 * powerup-music off: the speed-up jingle (speedup.adx) not played". A guest stack that does not
 * read leaves the call to the lifted body and is said once.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define POWERUP_MUSIC_SPEEDUP_RET 0x0046E077u   /* after PlayerSpeedUp's call at 0x0046E072 */
#define POWERUP_MUSIC_INVINC_RET  0x0046E49Au   /* after the invincibility call at 0x0046E495 */

static int g_powerup_music_speedup_said, g_powerup_music_invinc_said, g_powerup_music_stack_said;

void *__real_sub_443480(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_443480(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_NO_POWERUP_MUSIC)) return __real_sub_443480(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0;
    if (sa2_peek(esp, 4, &site) != SA2_OK) {
        if (!g_powerup_music_stack_said++)
            fprintf(stderr, "sa2 tweaks: powerup-music off: the guest stack at 0x%08X does not read, "
                    "so the caller is unknown and the jingle plays\n", esp);
        return __real_sub_443480(state, pc, memory);
    }
    if (site == POWERUP_MUSIC_SPEEDUP_RET) {
        if (!g_powerup_music_speedup_said++)
            fprintf(stderr, "sa2 tweaks: powerup-music off: the speed-up jingle (speedup.adx) not played\n");
    } else if (site == POWERUP_MUSIC_INVINC_RET) {
        if (!g_powerup_music_invinc_said++)
            fprintf(stderr, "sa2 tweaks: powerup-music off: the invincibility jingle (invncibl.adx) not "
                    "played\n");
    } else {
        return __real_sub_443480(state, pc, memory);
    }
    sa2_rt_set_gpr(state, SA2_EIP, site);       /* the `ret` */
    sa2_rt_set_gpr(state, SA2_ESP, esp + 4u);
    return memory;
}
