/* radar-all-pieces: in a treasure hunt (Knuckles, Rouge), the emerald radar shows how near every
 * piece still to find is, not only the first: each of the three icons is drawn in the colour and
 * blink the game gives the piece it tracks, the radar's beep keeps the pace of the nearest piece,
 * and the '!' over the player shows on top of any piece (runtime/sa2_tweaks.h: the tweaks and their
 * rules).
 *
 * TWEAK_WRAPS: sub_739fe0, sub_77fbd0
 *
 * The game tracks one piece, the first still to find in slot order, in three places, read from the
 * unpacked image. The radar's data is at [0x01AF014C]: slot i's byte at +0x2C + 16i (0xFE once the
 * piece is found) and its position at +0x30 + 16i. 0x0077FBD0 is the distance, EAX the piece's
 * position and ECX the player's (player 0's at [0x01DEA6C0] + 0x14), the result a float on the x87
 * stack: the squared distance in float, (dy*dy + dx*dx) + dz*dz, 0.0 under 0.025, else its square
 * root.
 *
 *   The icon. 0x0073A6F0 (the radar's display) walks the three slots in one-player play (ESI = slot
 *   0..2, EDI the slot's position), and for each piece still to find stores its distance
 *   (0x0077FBD0, called at 0x0073A7B2) as a float at its [esp+0x14] (0x0073A7B7), then tests EBX,
 *   1 for the first piece still to find and 0 after it:
 *
 *     0x0073A7BD  `jne 0x0073A7CE`: the first piece takes the tracked path, which picks the icon
 *                 by distance d: d > 500.0 (the double at 0x00904F30) slot+4, else d > 300.0 (the
 *                 float at 0x0109E030) slot+0x10, else d > 100.0 (the double at 0x00904E30)
 *                 slot+0xD, else slot+0xA, a NaN taking the last; pushes EBP and ESI and calls
 *                 0x00739FE0 at 0x0073A811 (return address 0x0073A816). Then, still only while EBX
 *                 is 1 (`cmp ebx,1` at 0x0073A819), d < 30.0 (the double at 0x00905090; 30.0
 *                 itself and a NaN do not) calls 0x0073A450 at 0x0073A82F: the '!', a quad drawn
 *                 over the player, with no argument and no sound.
 *     0x0073A7C4  every later piece: `push ebp; push esi; lea ecx,[esi+7]; call 0x00739FE0`, the
 *                 icon slot+7 (the plain one, no distance), return address 0x0073A7C9.
 *
 *   The beep. 0x00739570 (the radar's main) plays it in one-player play in its arm 0x00739A65
 *   (0x00739D42 on): it finds the first slot still to find, takes its distance (0x0077FBD0, called
 *   at 0x00739D74, return address 0x00739D79, EAX = that slot's position) and, while the game is in
 *   play ([0x01934BE0] = 0x10, [0x0174AFF9] and [0x0174B002] clear), calls the sound player
 *   (0x00437260, ESI = 0x1009) when the frame clock [0x0174B03C] is a multiple of 64 at
 *   300 < d <= 500 (0x00739DF2), of 32 at 100 < d <= 300 (0x00739DF2) and of 16 at d <= 100 or a
 *   NaN (0x00739E0D). Past 500 it is silent. At d <= 100 it also says the character's line
 *   about a piece being near (0x0506 Knuckles, 0x0507 Rouge, through 0x00443130) once, keeping a
 *   flag at +4 that the pickup (0x0073B3E0, at 0x0073B61C) clears. That is all the beeping there
 *   is: the display plays no sound, and 0x0073A450 none either.
 *
 * The SA2 Mod Loader's Codes.lst code "All emeralds trackable in treasure hunt stages" (TheGag96)
 * writes EB 0F at 0x0073A7BD, turning the `jne` into a `jmp`: every piece takes the tracked path's
 * icon, and nothing else, since the '!' still waits for EBX = 1 and the beep is the main's. This
 * tweak does that, and gives the beep and the '!' to the later pieces too. OpenSA2 never runs guest
 * .text, so two wraps do it:
 *
 *   sub_739fe0  when the return address at guest [ESP] is 0x0073A7C9, it reads the slot (ECX - 7,
 *               which must equal [ESP+4]), the piece's distance at [ESP+0x20] (the caller's
 *               [esp+0x14] past the two pushes and the return address, written this frame for this
 *               piece) and the bounds from the image, sets ECX as the tracked path would and calls
 *               the lifted body. Then, at d < 30.0, it calls 0x0073A450 (runtime/sa2_guest_call.h:
 *               no argument, a sentinel return address on the guest stack) with EAX, ECX and EDX
 *               put back after, unless this frame's '!' is drawn already: at the return address
 *               0x0073A816, the first piece's own call, the wrap notes whether that piece is
 *               under 30.0 and calls the lifted body unchanged; the first piece always comes
 *               before the later ones in the walk. So the '!' is drawn once a frame, whichever
 *               pieces are under 30.0. The lifted caller links sub_739fe0 directly at 0x0073A7C4
 *               and reaches 0x0073A811 through the dispatcher; -wrap takes both
 *               (tools/s4/link_s4.py).
 *   sub_77fbd0  when the return address at guest [ESP] is 0x00739D79 and EAX is the first piece's
 *               position, it points EAX at the nearest piece still to find and calls the lifted
 *               body, so the main's beep, its pace and the line at 100.0 follow that piece. The
 *               nearest is picked by the squared distance computed in float in the body's order;
 *               a tie, or a NaN, keeps the first piece, which is then passed on unchanged. EAX is
 *               dead after the call: the body leaves its status word in AX, and neither the main
 *               nor the sound player it calls reads the upper half before the main writes all of
 *               it (0x00739E21, 0x00739F8E). The other 301 calls of the distance in the image have
 *               other return addresses and are passed to the lifted body unchanged.
 *
 * Two pieces in range: the beep is the game's one call a frame, at the pace of the nearest piece.
 * The paces nest (16 divides 32 divides 64, on the one clock), so that is every frame on which any
 * piece's own pace would beep, played once; the first piece's beeps all still play. The line at
 * 100.0 keeps its one flag: it is said the first time any piece comes within 100.0 after a pickup.
 *
 * With hide-radar on, this tweak does nothing: hide-radar skips the display, so the first wrap is
 * never reached, and the second passes every call through, so the line at 100.0 stays the first
 * piece's as the game has it. hide-radar's own wrap of the sound player (sub_437260) silences the
 * main's five beep calls, which this tweak leaves where they are.
 *
 * Off is the lifted code alone: each wrap's first statement tests the tweak's bit and otherwise
 * calls its __real_ and returns, touching no register and no guest byte.
 *
 * What it does not change: the two-player radar (0x0073A848 on) and two-player beep (0x00739A72
 * on); the pieces, the hint monitors and the save, which the tweak never writes (it changes which
 * icon is drawn, which position the main's one distance call reads and draws the '!', nothing the
 * game keeps). Risks: a hunt is easier with it on, so a time made with it is not a fair one; the
 * three blinking icons sit where the game puts its three icons already, with nothing seen to
 * overlap.
 *
 * The run says, once per slot, the first time the tweak changes that slot's icon ("sa2 tweaks:
 * radar tracks all pieces: slot 2 tracked (icon 0x0F for 0x09, distance 187.4)"), the first time
 * the beep follows it ("... the beep follows slot 2, nearer than slot 0"), and the first time it
 * draws the '!' for it ("... the '!' shows for slot 2 (distance 21.4)"). A case it cannot act on,
 * a guest read that fails, a slot that does not match or a distance call whose EAX is not the
 * first piece's position, is said once and left as the game has it.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_guest_call.h"
#include "sa2_tweaks.h"

#define RADAR_UNTRACKED_RET 0x0073A7C9u   /* after the later pieces' call at 0x0073A7C4 */
#define RADAR_TRACKED_RET   0x0073A816u   /* after the first piece's call at 0x0073A811 */
#define RADAR_BEEP_DIST_RET 0x00739D79u   /* after the main's one-player distance call at 0x00739D74 */
#define RADAR_BOUND_500     0x00904F30u   /* double 500.0 */
#define RADAR_BOUND_300     0x0109E030u   /* float 300.0 */
#define RADAR_BOUND_100     0x00904E30u   /* double 100.0 */
#define RADAR_BOUND_BANG    0x00905090u   /* double 30.0 */
#define RADAR_BANG          0x0073A450u   /* the '!' over the player */
#define RADAR_DATA          0x01AF014Cu   /* the radar's data: slot i's byte at +0x2C + 16i */
#define RADAR_SLOTS_AT      0x2Cu
#define RADAR_FOUND         0xFEu

static unsigned g_radar_said, g_radar_beep_said, g_radar_bang_said;  /* bit i: said for slot i */
static int g_radar_read_said, g_radar_slot_said, g_radar_dist_said;
static int g_radar_bang;                  /* this frame's '!' is drawn already */

static int radar_read(uint32_t addr, uint32_t len, void *out) {
    if (sa2_peek(addr, len, out) == SA2_OK) return 1;
    if (!g_radar_read_said++)
        fprintf(stderr, "sa2 tweaks: radar tracks all pieces: the guest bytes at 0x%08X do not "
                "read, so the game's radar is left as it is there\n", addr);
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

/* 0x0073A450, as the display calls it at 0x0073A82F: no argument, plain ret. EAX, ECX and EDX are
 * put back after, so the display goes on with what 0x00739FE0 left it. */
static void radar_draw_bang(void *state) {
    const uint32_t eax = sa2_rt_get_gpr(state, SA2_EAX), ecx = sa2_rt_get_gpr(state, SA2_ECX),
                   edx = sa2_rt_get_gpr(state, SA2_EDX);
    sa2_guest_stdcall(state, RADAR_BANG, 0, NULL);
    sa2_rt_set_gpr(state, SA2_EAX, eax);
    sa2_rt_set_gpr(state, SA2_ECX, ecx);
    sa2_rt_set_gpr(state, SA2_EDX, edx);
}

void *__real_sub_739fe0(void *state, uint32_t pc, void *memory);
void *__real_sub_77fbd0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_739fe0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_RADAR_ALL_PIECES)) return __real_sub_739fe0(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0, arg = 0;
    float d = 0, b300 = 0;
    double b500 = 0, b100 = 0, b30 = 0;
    if (!radar_read(esp, 4, &site)) return __real_sub_739fe0(state, pc, memory);
    if (site == RADAR_TRACKED_RET) {
        /* The first piece: the display draws its '!' itself under 30.0. Unread, it counts as
         * drawn, so no second one is. */
        g_radar_bang = !radar_read(esp + 0x20, 4, &d) || !radar_read(RADAR_BOUND_BANG, 8, &b30) ||
                       (double)d < b30;
        return __real_sub_739fe0(state, pc, memory);
    }
    if (site != RADAR_UNTRACKED_RET) return __real_sub_739fe0(state, pc, memory);
    const uint32_t ecx = sa2_rt_get_gpr(state, SA2_ECX), slot = ecx - 7;
    if (!radar_read(esp + 4, 4, &arg) || !radar_read(esp + 0x20, 4, &d) ||
        !radar_read(RADAR_BOUND_500, 8, &b500) || !radar_read(RADAR_BOUND_300, 4, &b300) ||
        !radar_read(RADAR_BOUND_100, 8, &b100) || !radar_read(RADAR_BOUND_BANG, 8, &b30))
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
    memory = __real_sub_739fe0(state, pc, memory);
    if (!g_radar_bang && (double)d < b30) {
        g_radar_bang = 1;
        radar_draw_bang(state);
        if (!(g_radar_bang_said & (1u << slot))) {
            g_radar_bang_said |= 1u << slot;
            fprintf(stderr, "sa2 tweaks: radar tracks all pieces: the '!' shows for slot %u "
                    "(distance %.1f)\n", slot, (double)d);
        }
    }
    return memory;
}

/* The squared distance from the player p to the piece q in float, in 0x0077FBD0's order; one
 * operation a statement, so nothing is fused. */
#pragma STDC FP_CONTRACT OFF
static float radar_d2(const float p[3], const float q[3]) {
    const float dy = p[1] - q[1], dx = p[0] - q[0], dz = p[2] - q[2];
    const float yy = dy * dy, xx = dx * dx, zz = dz * dz;
    const float yx = yy + xx;
    return yx + zz;
}

__attribute__((used, retain))
void *__wrap_sub_77fbd0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_RADAR_ALL_PIECES) || sa2_tweak_on(SA2_TWEAK_HIDE_RADAR))
        return __real_sub_77fbd0(state, pc, memory);
    uint32_t site = 0, data = 0;
    if (!radar_read(sa2_rt_get_gpr(state, SA2_ESP), 4, &site) || site != RADAR_BEEP_DIST_RET)
        return __real_sub_77fbd0(state, pc, memory);
    uint8_t slots[48];                    /* +0x2C..+0x5B: per slot, its byte, 3, its position */
    float player[3], pos[3][3];
    const uint32_t eax = sa2_rt_get_gpr(state, SA2_EAX);
    if (!radar_read(RADAR_DATA, 4, &data) || !radar_read(data + RADAR_SLOTS_AT, sizeof slots, slots) ||
        !radar_read(sa2_rt_get_gpr(state, SA2_ECX), sizeof player, player))
        return __real_sub_77fbd0(state, pc, memory);
    unsigned first = 0;
    while (first < 3 && slots[16 * first] == RADAR_FOUND) first++;
    if (first == 3 || eax != data + RADAR_SLOTS_AT + 4 + 16 * first) {
        if (!g_radar_dist_said++)
            fprintf(stderr, "sa2 tweaks: radar tracks all pieces: the beep's distance call has EAX "
                    "0x%08X, not the first piece's position, so the beep keeps it\n", eax);
        return __real_sub_77fbd0(state, pc, memory);
    }
    unsigned near = first;
    memcpy(pos[first], slots + 16 * first + 4, sizeof pos[first]);
    float best = radar_d2(player, pos[first]);
    for (unsigned i = first + 1; i < 3; i++) {
        if (slots[16 * i] == RADAR_FOUND) continue;
        memcpy(pos[i], slots + 16 * i + 4, sizeof pos[i]);
        const float d2 = radar_d2(player, pos[i]);
        if (d2 < best) {
            best = d2;
            near = i;
        }
    }
    if (near != first) {
        sa2_rt_set_gpr(state, SA2_EAX, data + RADAR_SLOTS_AT + 4 + 16 * near);
        if (!(g_radar_beep_said & (1u << near))) {
            g_radar_beep_said |= 1u << near;
            fprintf(stderr, "sa2 tweaks: radar tracks all pieces: the beep follows slot %u, nearer "
                    "than slot %u\n", near, first);
        }
    }
    return __real_sub_77fbd0(state, pc, memory);
}
