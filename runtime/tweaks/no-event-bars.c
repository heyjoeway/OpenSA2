/* no-event-bars: the black bars that in-engine cutscenes (events) draw at the sides of a screen
 * wider than 4:3 are not drawn, so the scene shows across the whole width; the five events whose
 * scenes are framed for the 4:3 column keep theirs, and so do movies (runtime/sa2_tweaks.h: the
 * tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_5f8ec0
 *
 * Where the bars come from, read from the unpacked image. 0x005F8EC0 (28 bytes, unnamed) answers
 * "is this event shown at 4:3?": EAX = 1 when ECX, an event ID, is in a fixed table of 14 IDs at
 * 0x008A0ED8 (21, 24, 25, 100, 101, 126, 128, 129, 203, 205, 206, 360, 361, 409), else 0. It has
 * two callers, each linked against it directly, and each asks it only after computing
 * k = (H/640)/(V/480) from HorizontalResolution and VerticalResolution and finding k > 1:
 *
 *   0x005FB503  the event mode's executor (the arm 0x005FB421 of 0x005FB270), ECX = EventID
 *               ([0x01DB0FCC]); return address 0x005FB508, then `test eax,eax; je 0x005FB5B9`.
 *               On 1, the block 0x005FB510-0x005FB5B7 sets render states, draws two black quads
 *               on the 640x480 canvas, x from -320(k-1) to 0 and from 641 to 641 + 320(k-1), each
 *               480 high (the screen's overhang on each side of the 4:3 column), and restores the
 *               states. The bars are pillarbox bars; no event draws bars at the top or bottom.
 *   0x00601943  the event movie player (0x00601690), ECX = its event ID; return address
 *               0x00601948, and the same bars beside the movie. Only 409 in the table is a movie.
 *
 * The wrap reads the return address at guest [ESP] on entry (the lifted CALL has pushed it), calls
 * the lifted body, and when the call came from the executor (0x005FB508), the answer is 1 and the
 * event is not one of the five below, sets EAX to 0. The executor then takes the branch it takes
 * for every event outside the table, and for every event on a 4:3 screen: no quads, no
 * render-state changes, and it rejoins the bar path at 0x005FB5BF with ECX the event ID, as the bar
 * path leaves it. Nothing crops the scene, so nothing needs widening: the event's screen is the
 * full canvas (0x0173A128: 0, 0, 640, 480) and its 3D view is Hor+ across the back buffer, as in
 * gameplay, so the area the bars covered is cleared and drawn already. On a screen no wider than
 * 4:3 the predicate is never asked, and the tweak has nothing to do.
 *
 * The events that keep their bars: 21, 205, 206, 360 and 361 (FRAMED_43). Their scenes are framed
 * by the bars, not by the screen: the boot intro, event 361, draws its title cards, flashes,
 * backdrops and the logo's glow over the 4:3 column alone, so with its bars removed each of them
 * stops at a hard edge where a bar began. SA2 Render Fix's "specific" mode keeps the bars for the
 * same five events for this reason. For them the wrap leaves the answer at 1, and the executor
 * draws the bars as it does with the tweak off. The other eight in-engine events in the table
 * (24, 25, 100, 101, 126, 128, 129 and 203) lose theirs.
 *
 * The movie's bars stay (the movie player's call is left alone): the movie is 4:3 footage, and
 * without its bars the sides would show only what is behind it.
 *
 * The first time the tweak meets an event at the executor's call, the run says what it did, once
 * per event (the first 16 events, at most): "sa2 tweaks: event-bars off: event 24's side bars not
 * drawn", or "sa2 tweaks: event-bars off: event 361 keeps its side bars (its scene is framed for
 * 4:3)".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define EVENT_BARS_EXECUTOR_RET 0x005FB508u   /* after the executor's call at 0x005FB503 */
#define EVENT_BARS_MOVIE_RET    0x00601948u   /* after the movie player's call; left alone */

/* The events whose scenes are framed for the 4:3 column, which keep their bars (the header). */
static const uint32_t FRAMED_43[] = {21, 205, 206, 360, 361};

static uint32_t g_event_bars_named[16];
static unsigned g_event_bars_nnamed;
static int g_event_bars_stack_said;

static int framed_43(uint32_t event) {
    for (unsigned i = 0; i < sizeof FRAMED_43 / sizeof FRAMED_43[0]; i++)
        if (FRAMED_43[i] == event) return 1;
    return 0;
}

void *__real_sub_5f8ec0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_5f8ec0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_NO_EVENT_BARS)) return __real_sub_5f8ec0(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0;
    if (sa2_peek(esp, 4, &site) != SA2_OK) {
        if (!g_event_bars_stack_said++)
            fprintf(stderr, "sa2 tweaks: event-bars off: the guest stack at 0x%08X does not read, so "
                    "the caller is unknown and the event keeps its bars\n", esp);
        return __real_sub_5f8ec0(state, pc, memory);
    }
    const uint32_t event = sa2_rt_get_gpr(state, SA2_ECX);
    void *ret = __real_sub_5f8ec0(state, pc, memory);
    if (site != EVENT_BARS_EXECUTOR_RET || sa2_rt_get_gpr(state, SA2_EAX) == 0) return ret;
    const int keep = framed_43(event);
    if (!keep) sa2_rt_set_gpr(state, SA2_EAX, 0);
    unsigned i = 0;
    while (i < g_event_bars_nnamed && g_event_bars_named[i] != event) i++;
    if (i == g_event_bars_nnamed && i < sizeof g_event_bars_named / sizeof g_event_bars_named[0]) {
        g_event_bars_named[g_event_bars_nnamed++] = event;
        if (keep)
            fprintf(stderr, "sa2 tweaks: event-bars off: event %u keeps its side bars (its scene is "
                    "framed for 4:3)\n", event);
        else
            fprintf(stderr, "sa2 tweaks: event-bars off: event %u's side bars not drawn\n", event);
    }
    return ret;
}
