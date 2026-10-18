/* event-bars-all: every in-engine cutscene (event) draws the black bars at the sides of a screen
 * wider than 4:3, as the game draws them for the 14 events it lists, so each scene shows in the
 * 4:3 column alone, as on a 4:3 screen; movies are left as they are (runtime/sa2_tweaks.h: the
 * tweaks and their rules). It is the third way of the same answer: the game's own list (no tweak),
 * no bars on any in-engine event (no-event-bars), and bars on every event (this tweak). The two
 * tweaks cannot both be on (SA2_TWEAK_EXCLUSIVE in runtime/sa2_tweaks.h): named together, neither
 * is on, and the report line says so.
 *
 * TWEAK_WRAPS: none (it acts in no-event-bars.c's wrap of sub_5f8ec0)
 *
 * The bars come from 0x005F8EC0, "is this event shown at 4:3?" (runtime/tweaks/no-event-bars.c has
 * the full reading): EAX = 1 when ECX, an event ID, is in the table of 14 IDs at 0x008A0ED8 (.rdata:
 * 21, 24, 25, 100, 101, 126, 128, 129, 203, 205, 206, 360, 361, 409), else 0. The event mode's
 * executor (the arm 0x005FB421 of 0x005FB270) computes k = (H/640)/(V/480) and asks it only when
 * k > 1 (the call at 0x005FB503, return address 0x005FB508, then `test eax,eax; je 0x005FB5B9`). On
 * 1, the block 0x005FB510-0x005FB5B7 sets render states, draws the two black quads on the 640x480
 * canvas (x from -320(k-1) to 0 and from 641 to 641 + 320(k-1), 480 high), restores the states,
 * reloads ECX from the event ID ([0x01DB0FCC]) and rejoins at 0x005FB5BF; on 0, 0x005FB5B9 pops the
 * three x87 values the bar block would have used and falls into 0x005FB5BF. Nothing in the block
 * reads the event ID. After 0x005FB5BF the code compares ECX, the event ID on both paths, and pops
 * ESI and EDI, which the block changes, at each of its returns (0x005FB5E0, 0x005FB407, 0x005FB419,
 * 0x005FB949); whatever else the block leaves, it leaves for every event in the table in the game as
 * it is. So the block draws the same bars for any event, and an event outside the table takes the
 * path every event in it takes.
 *
 * The function sa2_event_bars_all, which no-event-bars.c's wrap calls first when this tweak is on
 * (0x005F8EC0 has one wrap; the tools refuse a second), reads the return address at guest [ESP]
 * (the lifted CALL has pushed it), calls the lifted body, and when the call came from the executor
 * (0x005FB508) and the answer is 0, sets EAX to 1. The movie player's call (return address
 * 0x00601948, in 0x00601690) is left alone: its bars are drawn beside 4:3 footage, and how a movie
 * fills a wide screen is a question of its own. On a screen no wider than 4:3 the predicate is never
 * asked, and the tweak has nothing to do.
 *
 * Off is the lifted code alone: unless this tweak is on, the wrap does not call this file, and the
 * function's own first test, sa2_tweak_on, calls the lifted body and returns.
 *
 * Risks: visual only. What a Hor+ scene shows beyond the 4:3 column is hidden behind the bars, as
 * on a 4:3 screen; nothing is written to the guest but EAX at that one return.
 *
 * The first time the tweak meets an event at the executor's call, the run says what it did, once
 * per event (the first 16 events, at most): "sa2 tweaks: event-bars always: event 50's side bars
 * drawn (the game draws none for it)", or "sa2 tweaks: event-bars always: event 361 has its side
 * bars already (it is in the game's list)". A guest stack that does not read is said once, and the
 * event is left to the game.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define EVENT_BARS_ALL_EXECUTOR_RET 0x005FB508u   /* after the executor's call at 0x005FB503 */

static uint32_t g_bars_all_named[16];
static unsigned g_bars_all_nnamed;
static int g_bars_all_stack_said;

/* Called by no-event-bars.c's wrap of sub_5f8ec0, with the lifted body as `real`. */
void *sa2_event_bars_all(void *state, uint32_t pc, void *memory, void *(*real)(void *, uint32_t, void *));

void *sa2_event_bars_all(void *state, uint32_t pc, void *memory, void *(*real)(void *, uint32_t, void *)) {
    if (!sa2_tweak_on(SA2_TWEAK_EVENT_BARS_ALL)) return real(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0;
    if (sa2_peek(esp, 4, &site) != SA2_OK) {
        if (!g_bars_all_stack_said++)
            fprintf(stderr, "sa2 tweaks: event-bars always: the guest stack at 0x%08X does not read, so "
                    "the caller is unknown and the event is left to the game\n", esp);
        return real(state, pc, memory);
    }
    const uint32_t event = sa2_rt_get_gpr(state, SA2_ECX);
    void *ret = real(state, pc, memory);
    if (site != EVENT_BARS_ALL_EXECUTOR_RET) return ret;
    const int listed = sa2_rt_get_gpr(state, SA2_EAX) != 0;
    if (!listed) sa2_rt_set_gpr(state, SA2_EAX, 1);
    unsigned i = 0;
    while (i < g_bars_all_nnamed && g_bars_all_named[i] != event) i++;
    if (i == g_bars_all_nnamed && i < sizeof g_bars_all_named / sizeof g_bars_all_named[0]) {
        g_bars_all_named[g_bars_all_nnamed++] = event;
        if (listed)
            fprintf(stderr, "sa2 tweaks: event-bars always: event %u has its side bars already (it is in "
                    "the game's list)\n", event);
        else
            fprintf(stderr, "sa2 tweaks: event-bars always: event %u's side bars drawn (the game draws "
                    "none for it)\n", event);
    }
    return ret;
}
