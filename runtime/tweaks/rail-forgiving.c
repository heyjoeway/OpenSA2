/* rail-forgiving: a character who loses balance on a grind rail wobbles and stays on, and is never
 * thrown off by the balance check: Sonic, Shadow, Amy and Metal Sonic, who share the grind code
 * (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_725f30
 *
 * Where the fall comes from, read from the unpacked image. Sonic_Main (0x00717840, the task main
 * that LoadSonic, LoadShadow and the loaders at 0x007170E0 and 0x00717640 install: the references
 * at 0x00716E37, 0x00717397, 0x007170ED and 0x0071764D) does two things each frame that matter here:
 *
 *   0x00717913  calls 0x00719920, the action checks (`push edi; push edi; push esi; push ebx`:
 *               the taskwk in ebx, the character's work, CharObj2Base and SonicCharObj2 being one
 *               pointer, in edi). Its arm for action 71, grinding (jump table 0x0071E1A0, entry
 *               0x47 = 0x0071A56A), calls 0x00721C00 at 0x0071A6F4 and, when that returns 0,
 *               tests the balance counter: `cmp word [esi+360h],78h; jle` at 0x0071A704 (esi =
 *               the SonicCharObj2, a signed 16-bit compare). Above 0x78 the character falls off:
 *               action 10 (0x0071A756), animation 0x0F (0x0071A763), and, when the next animation
 *               (word at +0x176) is 0x70 or 0x71, the two off-balance animations, a quarter turn of
 *               yaw (taskwk+0x0C, -0x4000 at 0x0071A71B or +0x4000 at 0x0071A72A).
 *   0x00718359  later in the same frame, in its own action switch (jump table 0x007197B4, entry
 *               0x47 = 0x0071834B), calls 0x00725F30, the grind balance, the function's only caller
 *               in the image: `push edi; push edi; push esi; push ecx`, so on entry the
 *               CharObj2Base is guest [ESP+0x0C] and the SonicCharObj2, the same pointer, [ESP+0x10].
 *
 * 0x00725F30 (3529 bytes, unnamed) moves the character along the rail and keeps the balance. It
 * reads the current animation at 0x00726A7D from the word at +0x178. In the grind poses 0x6A, 0x6B,
 * 0x6E and 0x6F it goes to 0x00726AAE, and once the pose has played to the end its animation table
 * gives (0x00726AAE-0x00726AEC), it sets the next animation to 0x70 or 0x71 with no speed test
 * (0x00726AF0-0x00726B0E: 0x70 after 0x6A and 0x6E, 0x71 after 0x6B and 0x6F). In the poses 0x68,
 * 0x69, 0x6C and 0x6D it does the same only when |CharObj2Base+0x64| is also under a limit the body
 * computes (0x00726B2B-0x00726B3D) and the pose has played to its end (0x00726B43-0x00726B81,
 * against a different fraction of the length; 0x70 after 0x68 and 0x6C, 0x71 after 0x69 and 0x6D).
 * Either way it writes 0x70 or 0x71 to +0x176 (0x00726B97, 0x00726BBA) and then adds si to the
 * counter (`add [edx+360h],si` at 0x00726B9E and 0x00726BC1, edx = [ESP+0x10] of the entry, loaded
 * at 0x00726A51; si = 1, set at 0x007266CD); no path reaches either add without first writing the
 * next animation. While the current animation is 0x70 or 0x71 it adds at 0x00726C89 too, without
 * writing the next one. Otherwise it zeroes the counter (0x00726BD7, 0x00726C0C). [unverified:
 * where the limit comes from; the speed reading of +0x64 is the Mod Loader's naming.] Seen in
 * play [private: a test-spawn stage run of Sky Rail as Shadow]: standing still at the first rail's
 * start with the stick held right, Shadow is in 0x71 at once, the counter climbs one a frame, and
 * 121 frames later he falls with the quarter turn; held forward, he rides the rail and never wobbles.
 *
 * The wrap reads the two pointers from the stack before the lifted body runs and, after it, reads
 * the counter and both animation words; when the counter is above 0x78 and either animation is 0x70
 * or 0x71, it writes 0x78. The next frame's test at 0x0071A704 then takes the jle and the character
 * stays on the rail in the off-balance pose for as long as the body keeps it there, and the body
 * zeroes the counter when the character recovers. 0x78 is the most the counter holds in the game on a
 * frame that does not end in a fall, so it is a value the game reaches itself; nothing in
 * 0x00725F30 tests the counter, and in action 71 the fall test is its only reader. Sonic_Main also
 * adds one to it before its switch, in any action, while taskwk status bit 0x100 is set
 * (0x00717C6E); that add comes after the fall test in the frame, and the body's adds and zeroes
 * follow it, so the held value is what the next test reads. The counter's other readers belong to
 * other actions (0x00721970; 0x00725080, reached from the action checks' arm for action 3); its
 * other writers (0x0072254A, 0x00722720, 0x007234DA, 0x007240F4) were not read further [unverified
 * that each is an action's start].
 *
 * Off is identical: the wrap tests its bit first and otherwise only calls the lifted body. On, the
 * wrap writes only the counter, only after a frame on which the body raised it past 0x78 in an
 * off-balance animation; a grind that never loses balance reads three words a frame and writes
 * nothing.
 *
 * Left alone, said once in its own line: a frame whose stack or character work does not read, or
 * whose counter does not write (the fall happens as usual). A counter above 0x78 with neither
 * animation 0x70 or 0x71 is left too: the body did not raise it in a wobble, and the fall test still
 * throws the character off from it (without the turn), as in the game.
 *
 * The first 16 times a run holds a character on a rail after a frame it did not hold that
 * character (each character's work by its address), it says so:
 * "sa2 tweaks: rails forgiving: held on the rail (balance 0x7D -> 0x78, animation 0x70, work
 * 0x...)"; after the 16th, it says once that it goes on without a line each.
 *
 * Risks. A gameplay change: losing balance on a rail no longer ends the grind, so a slow grind
 * can no longer be failed. A route that needs the fall to drop to a lower path now needs a
 * jump off the rail [unverified whether any route needs the fall]. The jump, the trick-off and the
 * other ways off a rail are the game's. The counter is the SonicCharObj2's, so Shadow, Amy and
 * Metal Sonic, in 2P battles too, are held as Sonic is. Nothing extra is saved; the times and
 * ranks a run earns with it are saved as usual.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define RAIL_COUNTER   0x360u   /* s16 at SonicCharObj2+0x360: the grind balance's counter */
#define RAIL_ANIM_NEXT 0x176u   /* u16 at CharObj2Base+0x176: the animation 0x00725F30 sets */
#define RAIL_ANIM_CUR  0x178u   /* u16 at CharObj2Base+0x178: the animation it reads */
#define RAIL_LIMIT     0x78     /* the fall test at 0x0071A704: above this, the character falls */
#define RAIL_HOLDS_SAID 16u

static int g_rail_read_said, g_rail_write_said, g_rail_more_said;
static unsigned g_rail_holds;
/* Whether the last frame held each character (a 2P battle has two): its work, and the flag. */
static struct { uint32_t work; int held; } g_rail_last[4];

static int off_balance(uint16_t anim) { return anim == 0x70 || anim == 0x71; }

/* The flag of `work`'s last frame, set to `held`; a work not seen yet takes a free row. */
static int rail_was_held(uint32_t work, int held) {
    unsigned i = 0;
    while (i < 4 && g_rail_last[i].work != work && g_rail_last[i].work != 0) i++;
    if (i == 4) i = 0;
    const int was = g_rail_last[i].work == work && g_rail_last[i].held;
    g_rail_last[i].work = work;
    g_rail_last[i].held = held;
    return was;
}

void *__real_sub_725f30(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_725f30(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_RAIL_FORGIVING)) return __real_sub_725f30(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t co2 = 0, s2 = 0;
    if (sa2_peek(esp + 0x0Cu, 4, &co2) != SA2_OK || sa2_peek(esp + 0x10u, 4, &s2) != SA2_OK) {
        if (!g_rail_read_said++)
            fprintf(stderr, "sa2 tweaks: rails forgiving: the guest stack at 0x%08X does not read, "
                    "so the rail's balance check is left alone\n", esp);
        return __real_sub_725f30(state, pc, memory);
    }
    void *ret = __real_sub_725f30(state, pc, memory);
    int16_t counter = 0;
    uint16_t next = 0, cur = 0;
    if (sa2_peek(s2 + RAIL_COUNTER, 2, &counter) != SA2_OK ||
        sa2_peek(co2 + RAIL_ANIM_NEXT, 2, &next) != SA2_OK ||
        sa2_peek(co2 + RAIL_ANIM_CUR, 2, &cur) != SA2_OK) {
        if (!g_rail_read_said++)
            fprintf(stderr, "sa2 tweaks: rails forgiving: the character's work at 0x%08X does not "
                    "read, so the rail's balance check is left alone\n", s2);
        return ret;
    }
    const int hold = counter > RAIL_LIMIT && (off_balance(next) || off_balance(cur));
    if (!hold) {
        rail_was_held(s2, 0);
        return ret;
    }
    const int16_t kept = RAIL_LIMIT;
    if (sa2_poke(s2 + RAIL_COUNTER, 2, &kept) != SA2_OK) {
        if (!g_rail_write_said++)
            fprintf(stderr, "sa2 tweaks: rails forgiving: the balance counter at 0x%08X does not "
                    "write, so the character falls off as usual\n", s2 + RAIL_COUNTER);
        return ret;
    }
    if (!rail_was_held(s2, 1)) {
        if (g_rail_holds < RAIL_HOLDS_SAID)
            fprintf(stderr, "sa2 tweaks: rails forgiving: held on the rail (balance 0x%02X -> 0x78, "
                    "animation 0x%02X, work 0x%08X)\n", (unsigned)(uint16_t)counter,
                    off_balance(next) ? next : cur, s2);
        else if (!g_rail_more_said++)
            fprintf(stderr, "sa2 tweaks: rails forgiving: %u holds named; later ones are not\n",
                    RAIL_HOLDS_SAID);
        g_rail_holds++;
    }
    return ret;
}
