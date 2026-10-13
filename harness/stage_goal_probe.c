/* M6's "replay complete", lifted side: the goal latch, one row per GameLoop call.
 *
 * Documentation/decisions/2026-09-23-S4-stage-route.md: a stage replay reaches
 * its goal when the goal latch, the byte at 0x0174B002, rises. AwardWin
 * (0x0043E6D0) sets it to 1 at 0x0043E781; 0x00439C80 (at 0x0043A130) and
 * MissionStartVariableSetup (at 0x0043C8C0) clear it. No other instruction in
 * the map's functions stores to it through an absolute address; a store
 * through a register is not excluded. Natively, harness/replay/native_input_probe.c
 * reads it in the Mod Loader's OnFrame, which runs at GameLoop's exit, and
 * writes the first OnFrame count at which it is nonzero to sa2-input-end.csv as
 * goal_tick. This file is the lifted reading that tools/s4/stage_boot.py holds
 * against that tick.
 *
 * Where it reads. GameLoop's exit is harness/replay/wrap_tick_probe.c's wrap,
 * and a symbol can be wrapped only once. So this file reads at GameLoop's next
 * entry instead. GameLoop's first call, at 0x00433EE9 once it has saved its
 * registers, is to the CRT's clock() at 0x007A8948. No other lifted object
 * calls clock() directly; only GameLoop's object and the function table name
 * it. Linked with `-wrap sub_7a8948`, every call to clock() lands here. A call
 * whose return address is not 0x00433EEE did not come from GameLoop, and it
 * passes through with no row.
 *
 * A row is keyed by GameLoop's returns so far, the count wrap_tick_probe.c
 * keeps, so row t is the latch as GameLoop call t+1 found it: the state tick t
 * left, plus whatever ran between the two calls. On the poll that reaches
 * GameLoop that is WinMain's loop (0x00401DA4..0x00401DFE: PeekMessageW,
 * 0x00425DA0, 0x00402100, 0x0086B320) and the worker threads the pump yields
 * to. None of the latch's three writers is in the direct-call closure of those
 * three functions (the map's direct calls, closed transitively). That argument
 * does not cover their indirect calls, a store through a pointer or a block
 * fill that reaches the byte, a worker thread's code, or a guest window
 * procedure run for a dispatched message. If something between the calls ever
 * did move the latch, the lifted rise would land a tick away from the native
 * one, and stage_boot.py's check would fail rather than pass. Row 0 is the
 * latch before the first tick. The run's last tick has no row, because no
 * GameLoop call follows it.
 *
 * It only reads guest memory (sa2_peek) and the return address; it writes
 * nothing the guest can see. SA2_GOAL_LATCH=<path> turns it on. When the
 * variable is unset, the wrap calls clock() and does nothing else. It needs
 * wrap_tick_probe.c for the tick count, and the link fails without it.
 *
 * It is lifted-only and compares nothing, so it lives outside harness/replay,
 * which holds the replay's native and lifted halves.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"

#define GOAL_LATCH_VA 0x0174B002u           /* the goal latch, one byte */
#define GAMELOOP_CLOCK_RETURN 0x00433EEEu   /* after `call 007A8948h` at 0x00433EE9 */

void *__real_sub_7a8948(void *state, uint32_t pc, void *memory);
unsigned long sa2_replay_gameloop_returns(void);   /* harness/replay/wrap_tick_probe.c */

static FILE *goal_out;
static int goal_off;

static void goal_row(void *state) {
    if (!goal_out && !goal_off) {
        const char *path = getenv("SA2_GOAL_LATCH");
        if (!path || !*path || !(goal_out = fopen(path, "w"))) {
            goal_off = 1;
            return;
        }
        fputs("tick,goal_latch\n", goal_out);
    }
    if (!goal_out) return;
    uint32_t ret = 0;
    if (sa2_peek(sa2_rt_get_gpr(state, SA2_ESP), 4, &ret) != SA2_OK || ret != GAMELOOP_CLOCK_RETURN)
        return;                     /* not GameLoop's call: no row */
    uint8_t latch = 0;
    if (sa2_peek(GOAL_LATCH_VA, 1, &latch) != SA2_OK)
        fprintf(goal_out, "%lu,0xffffffff\n", sa2_replay_gameloop_returns());   /* never a byte */
    else
        fprintf(goal_out, "%lu,0x%02x\n", sa2_replay_gameloop_returns(), (unsigned)latch);
    fflush(goal_out);           /* the run ends in a trap; every row must be on disk */
}

void *__wrap_sub_7a8948(void *state, uint32_t pc, void *memory) {
    goal_row(state);
    return __real_sub_7a8948(state, pc, memory);
}
