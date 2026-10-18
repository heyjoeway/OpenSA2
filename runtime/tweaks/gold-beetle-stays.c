/* gold-beetle-stays: a Gold Beetle (E_GOLD) that has appeared no longer flies away when its time
 * is up; it keeps circling where it is until the player destroys it (runtime/sa2_tweaks.h: the
 * tweaks and their rules). It is the Mod Loader code "Gold Beetle stays" (Codes.lst: write8
 * 0x00506059 = 0xEB, the despawn test's JLE made a JMP), done as a wrap, since OpenSA2 never runs
 * the game's code bytes.
 *
 * TWEAK_WRAPS: sub_505fa0
 *
 * Where the beetle leaves, read from the unpacked image. E_GOLD (0x00506510) dispatches on the
 * state byte of its taskwk (the task's +0x34) through a table at 0x005066D0: 0 the set-up
 * (0x00505D40), which stores the lifetime in frames, s16 ewp+0x48 = (rot.x & 0xFF) * 10 (ewp, the
 * enemy work, is the task's +0x38), and goes to state 2; 2 waits for the player to come near
 * (0x00506080); 3 grows into view (0x00500FA0); 1 flies (0x00505FA0); 4 shrinks away and ends in
 * state 5 (0x00506140); 5 and 6 are gone. 0x00505FA0 (210 bytes, unnamed) is the flying state,
 * called from one place, E_GOLD's arm at 0x00506572: `push ebx; push esi; mov eax,ebp; call
 * 0x00505FA0`, so on entry EAX = the taskwk, [ESP+4] = the task and [ESP+8] = the ewp. It moves
 * the beetle along its circle, asks 0x00505EE0 whether the player has hit it (then state 5, the
 * timer 0 and the points), and then, unless the state is 5:
 *
 *   0x00506042  unless TimeStopped (byte 0x0174AFF7) is set, `inc word [esi+6]`: the u16 timer
 *               at taskwk+6 counts the frames flown;
 *   0x0050604F  `movsx edx, word [ebp+48h]; movzx eax, word [esi+6]; cmp eax,edx; jle` to the
 *               return; past the lifetime, 0x0050605B-0x00506069 clears flag 0x1000 at
 *               taskwk+4, sets state 4 and the timer to 0, and the beetle leaves.
 *
 * Nothing else reads the timer in this state: not E_GOLD, its display (0x00506250), nor the
 * helpers 0x00505FA0 calls (0x00479F10, 0x0046DDC0, 0x00505EE0, which only zeroes it), so no
 * animation runs on it. The wrap reads the timer and the lifetime before the lifted body runs, and
 * when the timer has reached the lifetime (t >= lifetime), sets it to lifetime - 1: the body's
 * increment then brings it to the lifetime, which passes the JLE as on the last frame a beetle
 * flies in the vanilla game, and the next frame does the same. The beetle behaves as in the game
 * up to the frame it would leave, and from then on stays in state 1. The timer is held at the
 * edge, never zeroed, so it reads as a flown-out beetle's would.
 *
 * A lifetime of 0 (rot.x's low byte 0; the lifetime is 0..2550, never negative) is kept too: the
 * increment is 16-bit (0x0050604B `66 FF 46 06`, `inc word [esi+6]`), so lifetime - 1 = 0xFFFF
 * wraps to 0 and the JLE passes (0 <= 0), as the Codes.lst JMP keeps it. TimeStopped skips the
 * increment, so the wrap reads that byte too and, when it is set, writes the lifetime itself
 * (0xFFFF unincremented would compare 65535 > 0 and leave). Only lifetime 0 needs that, since
 * lifetime - 1 also passes while time is stopped, but one rule covers both: stopped, the timer is
 * set to the lifetime.
 *
 * Off is identical: the wrap tests its bit first and otherwise only calls the lifted body. On,
 * before the lifetime it reads three values and writes nothing, so a beetle's first lifetime of
 * frames is the game's either way; Metal Harbor's one beetle (set0010_s.bin) has a lifetime of
 * 20 frames.
 *
 * Left alone, said once in its own line: a beetle whose taskwk, ewp or the TimeStopped byte does
 * not read, or whose timer does not write (it leaves as usual).
 *
 * The first time the tweak holds a beetle, the run says so, once per beetle (the first 16 taskwk
 * addresses): "sa2 tweaks: gold beetle stays: gold beetle held at t=20 (lifetime 20 frames,
 * taskwk 0x...)".
 *
 * Risks. A beetle the player keeps is a beetle the player can still destroy, for its 1000 or 2000
 * points: the tweak raises scores, so ranks and the scores the game saves at a stage's end, and a
 * run timed or scored against the vanilla game is not comparable. The beetle's circle continues
 * from the same counters (ewp+0x1C4, ewp+0x1CC) as in the game, which only grow.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define GOLD_TIMER    6u      /* u16 at taskwk+6: frames flown in state 1 */
#define GOLD_LIFETIME 0x48u   /* s16 at ewp+0x48: frames a beetle flies, (rot.x & 0xFF) * 10 */
#define TIME_STOPPED  0x0174AFF7u  /* byte: set, the body skips the timer's increment */

static uint32_t g_gold_held[16];
static unsigned g_gold_nheld;
static int g_gold_read_said, g_gold_stopped_said, g_gold_write_said;

void *__real_sub_505fa0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_505fa0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_GOLD_BEETLE_STAYS)) return __real_sub_505fa0(state, pc, memory);
    const uint32_t taskwk = sa2_rt_get_gpr(state, SA2_EAX);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t ewp = 0;
    uint16_t timer = 0;
    int16_t lifetime = 0;
    if (sa2_peek(esp + 8u, 4, &ewp) != SA2_OK || sa2_peek(taskwk + GOLD_TIMER, 2, &timer) != SA2_OK ||
        sa2_peek(ewp + GOLD_LIFETIME, 2, &lifetime) != SA2_OK) {
        if (!g_gold_read_said++)
            fprintf(stderr, "sa2 tweaks: gold beetle stays: a gold beetle's work does not read "
                    "(taskwk 0x%08X, ewp 0x%08X), so it leaves as usual\n", taskwk, ewp);
        return __real_sub_505fa0(state, pc, memory);
    }
    if ((int32_t)timer < (int32_t)lifetime) return __real_sub_505fa0(state, pc, memory);
    uint8_t stopped = 0;
    if (sa2_peek(TIME_STOPPED, 1, &stopped) != SA2_OK) {
        if (!g_gold_stopped_said++)
            fprintf(stderr, "sa2 tweaks: gold beetle stays: TimeStopped at 0x%08X does not read, "
                    "so a gold beetle leaves as usual\n", TIME_STOPPED);
        return __real_sub_505fa0(state, pc, memory);
    }
    /* Not stopped, the body's 16-bit increment brings lifetime - 1 to the lifetime (0xFFFF to 0
     * for lifetime 0); stopped, there is no increment, so the timer is the lifetime itself. */
    const uint16_t kept = stopped ? (uint16_t)lifetime : (uint16_t)(lifetime - 1);
    if (sa2_poke(taskwk + GOLD_TIMER, 2, &kept) != SA2_OK) {
        if (!g_gold_write_said++)
            fprintf(stderr, "sa2 tweaks: gold beetle stays: a gold beetle's timer at 0x%08X does "
                    "not write, so it leaves as usual\n", taskwk + GOLD_TIMER);
        return __real_sub_505fa0(state, pc, memory);
    }
    unsigned i = 0;
    while (i < g_gold_nheld && g_gold_held[i] != taskwk) i++;
    if (i == g_gold_nheld && i < sizeof g_gold_held / sizeof g_gold_held[0]) {
        g_gold_held[g_gold_nheld++] = taskwk;
        fprintf(stderr, "sa2 tweaks: gold beetle stays: gold beetle held at t=%u (lifetime %d "
                "frames, taskwk 0x%08X)\n", timer, lifetime, taskwk);
    }
    return __real_sub_505fa0(state, pc, memory);
}
