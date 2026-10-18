/* draw-distance: stage objects (rings, enemies, springs, boxes, everything a stage's SET file
 * places) load, and stay loaded, at twice the usual distance from the players and the camera, so
 * they appear from further away and pop in less (runtime/sa2_tweaks.h: the tweaks and their rules).
 * Level geometry is left alone: its culling is separate (0x0047CAE0 and each stage's own
 * functions) and this tweak does not touch it.
 *
 * TWEAK_WRAPS: sub_488340, sub_4881f0
 *
 * Where the distance comes from, read from the unpacked image. Every range test on a SET object
 * goes through one of two leaf functions, each "is the point (x, y, z) within r of this
 * position?", answering EAX = 1 when the squared distance is below r^2, else 0:
 *
 *   0x00488340  one player. ECX = the position (three floats), the stack x, y, z, r^2 (cdecl, the
 *               caller pops 16 bytes). It pushes ECX, then reads r^2 at [esp+0x14]; on entry,
 *               under the return address the lifted CALL has pushed, r^2 is at guest ESP + 0x10.
 *               It writes its x, y and z slots (the differences squared) but never r^2.
 *               Four callers, each linked against it directly: 0x00488717 in the 1P SET loader
 *               0x00488630 (r^2 is the object list entry's distance, or 160000.0, 400 squared,
 *               at 0x01613D04), and 0x00488CF7, 0x00488D31 and 0x00488D6F in the clipper
 *               0x00488C80 (an object's clip r^2 against the camera and players 1 and 2; a SET
 *               object's is its load r^2 plus 100.0, which the loader stores, and other
 *               objects, from about 1100 call sites, pass their own).
 *   0x004881F0  two players. EAX = player 1's position, ECX = player 2's or 0, the stack x, y, z,
 *               r^2 again; it opens with `sub esp,0Ch` and reads r^2 at [esp+0x1C], so on entry
 *               it is at guest ESP + 0x10 as well. One caller: 0x004884D5 in the 2P SET loader
 *               0x004883D0.
 *
 * The wrap reads the float at guest ESP + 0x10, writes it back multiplied by 4 (twice the
 * radius), calls the lifted body, and writes the caller's original 4 bytes back, since the slot is
 * the caller's argument area. A product by 4 is exact for every finite float below 2^126; above,
 * it is infinity, and the object is always in range, as it all but was. Loading and clipping are
 * scaled alike, so an object still clips further out than it loads, and does not blink. An object
 * whose list entry says "always load" (flag 2) never asks, and is unchanged.
 *
 * Why off is the lifted code alone: each wrap tests sa2_tweak_on(SA2_TWEAK_DRAW_DISTANCE) first
 * and otherwise only calls `__real_` and returns; it reads and writes nothing.
 *
 * Risks (play only, as every tweak). Objects that load earlier run their logic earlier, which
 * moves random numbers, timing and sound overlap, so a run with the tweak is not a replay's run.
 * More objects are live at once: on a dense stage the object pool can fill, and the loader then
 * skips an object for that frame (0x00488741 returns 0; it is tried again the next frame); each
 * frame costs more, which a slow phone may feel. Objects that have their own draw distance in
 * their display code still use it. The save is never written by this tweak.
 *
 * By name: the first range check each wrap changes says so, once ("sa2 tweaks: draw distance
 * doubled: the first 1P object range check runs at twice the radius (r^2 160000 -> 640000)"), and
 * a guest stack that does not read leaves the check alone and says so once.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define DRAW_DISTANCE_R2_AT   0x10u     /* r^2 on entry: guest ESP + 0x10, in both functions */
#define DRAW_DISTANCE_SCALE   4.0f      /* r^2 times 4: twice the radius */

static int g_draw_distance_said[2], g_draw_distance_stack_said[2];

/* Scales r^2 under the return address at guest ESP, calls `real`, and puts the caller's bytes back.
 * `which` is 0 for the 1P check, 1 for the 2P check. */
static void *draw_distance_call(void *(*real)(void *, uint32_t, void *), int which,
                                void *state, uint32_t pc, void *memory) {
    static const char *const who[2] = {"1P", "2P"};
    const uint32_t at = sa2_rt_get_gpr(state, SA2_ESP) + DRAW_DISTANCE_R2_AT;
    uint32_t orig = 0;
    if (sa2_peek(at, 4, &orig) != SA2_OK) {
        if (!g_draw_distance_stack_said[which]++)
            fprintf(stderr, "sa2 tweaks: draw distance doubled: the guest stack at 0x%08X does not read, "
                    "so this %s object range check keeps its radius\n", at, who[which]);
        return real(state, pc, memory);
    }
    float r2;
    memcpy(&r2, &orig, 4);
    const float scaled = r2 * DRAW_DISTANCE_SCALE;
    if (sa2_poke(at, 4, &scaled) != SA2_OK) {
        if (!g_draw_distance_stack_said[which]++)
            fprintf(stderr, "sa2 tweaks: draw distance doubled: the guest stack at 0x%08X does not take "
                    "a write, so this %s object range check keeps its radius\n", at, who[which]);
        return real(state, pc, memory);
    }
    if (!g_draw_distance_said[which]++)
        fprintf(stderr, "sa2 tweaks: draw distance doubled: the first %s object range check runs at twice "
                "the radius (r^2 %.0f -> %.0f)\n", who[which], (double)r2, (double)scaled);
    void *ret = real(state, pc, memory);
    sa2_poke(at, 4, &orig);
    return ret;
}

void *__real_sub_488340(void *state, uint32_t pc, void *memory);
void *__real_sub_4881f0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_488340(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_DRAW_DISTANCE)) return __real_sub_488340(state, pc, memory);
    return draw_distance_call(__real_sub_488340, 0, state, pc, memory);
}

__attribute__((used, retain))
void *__wrap_sub_4881f0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_DRAW_DISTANCE)) return __real_sub_4881f0(state, pc, memory);
    return draw_distance_call(__real_sub_4881f0, 1, state, pc, memory);
}
