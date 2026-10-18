/* radar-all-pieces: in a treasure hunt (Knuckles, Rouge), the emerald radar shows how near every
 * piece still to find is, not only the first: each of the three icons is drawn in the colour and
 * blink the game gives the piece it tracks (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_739fe0
 *
 * Where the radar comes from, read from the unpacked image. 0x0073A6F0 (the radar's per-frame
 * update) walks the three pieces in one-player play (ESI = slot 0..2, EDI the slot's record, a
 * collected one marked 0xFE at [EDI-4]). For each piece still to find it computes the distance to
 * the player first (0x0077FBD0, called at 0x0073A7B2) and stores it as a float at its [esp+0x14]
 * (0x0073A7B7), then tests EBX, which is 1 for the first piece still to find and 0 after it:
 *
 *   0x0073A7BD  `jne 0x0073A7CE`: the first piece takes the tracked path, which picks the icon by
 *               distance d: d > 500.0 (the double at 0x00904F30) slot+4, else d > 300.0 (the float
 *               at 0x0109E030) slot+0x10, else d > 100.0 (the double at 0x00904E30) slot+0xD, else
 *               slot+0xA, a NaN taking the last; pushes EBP and ESI and calls 0x00739FE0 at
 *               0x0073A811, then beeps (0x0073A450) at d <= 30.0 (0x00905090).
 *   0x0073A7C4  every later piece: `push ebp; push esi; lea ecx,[esi+7]; call 0x00739FE0`, the
 *               icon slot+7 (the plain one, no distance), return address 0x0073A7C9.
 *
 * 0x00739FE0 draws icon ECX for the slot given as its first stack argument ([ESP+4] on entry, the
 * pushed ESI) with the second (EBP, [0x0174B03C]) as the blink clock; the two calls differ only
 * in ECX. The SA2 Mod Loader's Codes.lst code "All emeralds trackable in treasure hunt stages"
 * (TheGag96) writes EB 0F at 0x0073A7BD, turning the `jne` into a `jmp` to the tracked path.
 * OpenSA2 never runs guest .text, so the wrap does what the tracked path would instead: when the
 * return address at guest [ESP] is 0x0073A7C9, it reads the slot (ECX - 7, which must equal
 * [ESP+4]), the piece's distance at [ESP+0x20] (the caller's [esp+0x14] past the two pushes and
 * the return address, written this frame for this piece) and the three bounds from the image,
 * sets ECX as the tracked path would, and calls the lifted body. The lifted caller links
 * sub_739fe0 directly at 0x0073A7C4 (its object leaves the symbol undefined, so -wrap takes it).
 * The tracked call at 0x0073A811 (which the lifted caller reaches through the dispatcher), the
 * two-player path (0x0073A848 on) and every other caller have other return addresses, and the
 * wrap passes them to the lifted body unchanged.
 *
 * Off is the lifted code alone: the wrap's first statement tests the tweak's bit and otherwise
 * calls __real_sub_739fe0 and returns, touching no register and no guest byte.
 *
 * What it does not change: the beep stays with the first piece still to find; the two-player
 * radar; the pieces, the hint monitors and the save, which the tweak never writes (it changes
 * which icon is drawn, nothing the game keeps). Risks: a hunt is easier with it on, so a time made
 * with it is not a fair one; the three blinking icons sit where the game puts its three icons
 * already, with nothing seen to overlap.
 *
 * The first time the tweak changes a slot's icon, the run says so, once per slot: "sa2 tweaks:
 * radar tracks all pieces: slot 2 tracked (icon 0x0F for 0x09, distance 187.4)". A case it cannot
 * act on, a guest read that fails or a slot that does not match, is said once and the plain icon
 * is drawn.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define RADAR_UNTRACKED_RET 0x0073A7C9u   /* after the later pieces' call at 0x0073A7C4 */
#define RADAR_BOUND_500     0x00904F30u   /* double 500.0 */
#define RADAR_BOUND_300     0x0109E030u   /* float 300.0 */
#define RADAR_BOUND_100     0x00904E30u   /* double 100.0 */

static unsigned g_radar_said;             /* bit i: slot i's first change was said */
static int g_radar_read_said, g_radar_slot_said;

static int radar_read(uint32_t addr, uint32_t len, void *out) {
    if (sa2_peek(addr, len, out) == SA2_OK) return 1;
    if (!g_radar_read_said++)
        fprintf(stderr, "sa2 tweaks: radar tracks all pieces: the guest bytes at 0x%08X do not "
                "read, so the later pieces keep the plain icon\n", addr);
    return 0;
}

/* The icon the tracked path (0x0073A7CE-0x0073A80C) gives `slot` at distance d; x87 compares
 * the float and the bounds exactly, as doubles do, and a NaN fails every test. */
static uint32_t radar_icon(uint32_t slot, float d, double b500, float b300, double b100) {
    const double x = d;
    if (x > b500) return slot + 4;
    if (x > (double)b300) return slot + 0x10;
    if (x > b100) return slot + 0xD;
    return slot + 0xA;
}

void *__real_sub_739fe0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_739fe0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_RADAR_ALL_PIECES)) return __real_sub_739fe0(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0, arg = 0;
    float d = 0, b300 = 0;
    double b500 = 0, b100 = 0;
    if (!radar_read(esp, 4, &site) || site != RADAR_UNTRACKED_RET)
        return __real_sub_739fe0(state, pc, memory);
    const uint32_t ecx = sa2_rt_get_gpr(state, SA2_ECX), slot = ecx - 7;
    if (!radar_read(esp + 4, 4, &arg) || !radar_read(esp + 0x20, 4, &d) ||
        !radar_read(RADAR_BOUND_500, 8, &b500) || !radar_read(RADAR_BOUND_300, 4, &b300) ||
        !radar_read(RADAR_BOUND_100, 8, &b100))
        return __real_sub_739fe0(state, pc, memory);
    if (slot > 2 || arg != slot) {
        if (!g_radar_slot_said++)
            fprintf(stderr, "sa2 tweaks: radar tracks all pieces: icon 0x%02X with slot %u on the "
                    "stack is no piece's plain icon, so it is drawn as it is\n", ecx, arg);
        return __real_sub_739fe0(state, pc, memory);
    }
    const uint32_t icon = radar_icon(slot, d, b500, b300, b100);
    sa2_rt_set_gpr(state, SA2_ECX, icon);
    if (!(g_radar_said & (1u << slot))) {
        g_radar_said |= 1u << slot;
        fprintf(stderr, "sa2 tweaks: radar tracks all pieces: slot %u tracked (icon 0x%02X for "
                "0x%02X, distance %.1f)\n", slot, icon, ecx, (double)d);
    }
    return __real_sub_739fe0(state, pc, memory);
}
