/* all-events-skippable: Start skips the four in-engine cutscenes (events) the game will not let a
 * player skip, the Hero story's ending (event 28) and the Last story's finale (events 208, 210
 * and 211), as it skips every other event (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_5fb270
 *
 * Where the lock is, read from the unpacked image. 0x005FB270 (1761 bytes, unnamed) is the event
 * mode's executor. Nothing calls it directly; the game runs it through a pointer, and the lifted
 * program reaches it through the function table. Its state is the dword at 0x01A283FC, and a jump
 * table at 0x005FB954 picks the arm; state 3, the arm at 0x005FB421, plays the event, with the event
 * ID in [0x01DB0FCC]. That arm loads ECX = [0x01DB0FCC] and EBP = 4 (0x005FB4D9, 0x005FB4E1), draws
 * the side bars or not (runtime/tweaks/no-event-bars.c), and then decides whether Start skips:
 *
 *   0x005FB5E8  [0x01DB0F80] == 0, else no skip
 *   0x005FB5F0  the event's view count, byte [ECX + 0x01DEB080], != 0, else no skip; the count is
 *               raised when an event loads (0x005FF983), so the event playing has passed it
 *   0x005FB5F8  byte [0x0174AFC2] == 0: then ECX must not be 0x1C, 0xD0, 0xD2 or 0xD3
 *               (0x005FB600-0x005FB61B), else no skip; when the byte is not 0 these four compares
 *               are not made
 *   0x005FB61D  Start newly pressed, bit 8 of [[0x01DEFB60] + 0x10] (ControllerPointers[0]->press),
 *               and [0x01DB0BE8] == 0, else no skip
 *   0x005FB631  the skip: state = EBP = 4, the arm that ends an event (0x005FB659), the state an
 *               event's own end writes too (0x005FB3C8, 0x005FB644)
 *
 * The four IDs are the locked events. Event 28 is entry 42 of the Hero story's list
 * (HeroStoryEntries, 0x0173A148) and 208, 210 and 211 are entry 6 of the Last story's
 * (LastEpisodeStoryEntries, 0x0173A580). The byte at 0x0174AFC2 (.dataa, 0 in the image) is the
 * only thing that lets them through: no instruction in .text names it as a destination, and four
 * name it as a source, 0x005FB5F8 here and 0x0045206B, 0x00452083 and 0x0045209B in
 * setMissionTimeLimits (0x00451F00). The Mod Loader's "All Events Skippable" code (Codes.lst)
 * NOPs the four branches at 0x005FB603, 0x005FB60B, 0x005FB613 and 0x005FB61B; OpenSA2 never runs
 * guest code bytes, so the tweak opens the same gate with the byte instead.
 *
 * The wrap: when the tweak is on, the executor is in state 3 and [0x01DB0FCC] is one of the four
 * IDs, it writes 1 to the byte at 0x0174AFC2 (saving the byte first; when the byte is not 0 the gate
 * is open already and the wrap does nothing), calls the lifted executor, and writes the saved
 * byte back if the byte still reads 1. The skip then takes the path every other event's skip
 * takes, state 4, so the event ends, and the story goes on and is saved as after any skipped event.
 * The tweak writes no save data of its own. Every other event, every other state, and every call
 * with the tweak off run the lifted executor alone.
 *
 * Why off is identical: the wrap's first statement tests the tweak's bit and otherwise only calls
 * `__real_sub_5fb270`, so nothing is read or written.
 *
 * Risks. The byte reads 1 for the length of one executor call while a locked event plays; if a
 * callee of 0x005FB270 reached setMissionTimeLimits it would see it. setMissionTimeLimits has one
 * direct caller, 0x0043CB50, and the direct-call closure of 0x005FB270 (1161 functions in the
 * analyzer's map) does not hold 0x00451F00; that closure has indirect calls, which the map cannot
 * follow. A skipped event is shorter than a watched one, so frame counts after it differ; replays
 * keep the tweak off. A skip also cuts what the rest of the event would have done, as any skip
 * does: the game marks a song heard for the sound test when it plays (0x00443480 by file name, into
 * the flags at 0x01DEB328, saved at +0x325F), so event 28 skipped before its song starts leaves
 * E028_SNG.ADX (flag 92) unheard, and the save's play time is shorter. A guest read or write that
 * fails is said once, and the executor then runs as with the tweak off.
 *
 * The first time a locked event is skipped, the run says so, once per event: "sa2 tweaks: every
 * event skippable: event 28 skipped with Start". The line is a report: the wrap counts a skip when
 * Start was newly pressed as the call began and the call left the executor in state 4 with
 * [0x01DB0EB0] = 0 (the event's own end, 0x005FB64A, writes 1 there).
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define SKIP_STATE         0x01A283FCu   /* the executor's state; 3 plays an event, 4 ends it */
#define SKIP_EVENT         0x01DB0FCCu   /* the event playing */
#define SKIP_GATE          0x0174AFC2u   /* byte; 0 locks the four events below */
#define SKIP_CONTROLLER    0x01DEFB60u   /* ControllerPointers[0] */
#define SKIP_ENDED         0x01DB0EB0u   /* 1 when the event's own end ended it */
#define SKIP_PLAYING       3u
#define SKIP_ENDING        4u
#define SKIP_START_PRESSED 0x8u          /* PDS_PERIPHERAL.press (+0x10), Start */

/* The events Start cannot skip without the tweak (0x005FB600-0x005FB61B). */
static const uint32_t LOCKED[] = {0x1C, 0xD0, 0xD2, 0xD3};

static int g_skip_said[4];
static int g_skip_fail_said;
static int g_skip_restore_said;

static int locked(uint32_t event) {
    for (unsigned i = 0; i < sizeof LOCKED / sizeof LOCKED[0]; i++)
        if (LOCKED[i] == event) return (int)i;
    return -1;
}

/* Reads n bytes at a; on failure says once which address did not read and returns 0. */
static int peek(uint32_t a, uint32_t n, void *out) {
    if (sa2_peek(a, n, out) == SA2_OK) return 1;
    if (!g_skip_fail_said++)
        fprintf(stderr, "sa2 tweaks: every event skippable: guest 0x%08X does not read, so the "
                "executor runs as with the tweak off\n", a);
    return 0;
}

void *__real_sub_5fb270(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_5fb270(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_ALL_EVENTS_SKIPPABLE)) return __real_sub_5fb270(state, pc, memory);
    uint32_t mode = 0, event = 0;
    uint8_t saved = 0;
    if (!peek(SKIP_STATE, 4, &mode) || mode != SKIP_PLAYING) return __real_sub_5fb270(state, pc, memory);
    if (!peek(SKIP_EVENT, 4, &event)) return __real_sub_5fb270(state, pc, memory);
    const int which = locked(event);
    if (which < 0 || !peek(SKIP_GATE, 1, &saved) || saved != 0)
        return __real_sub_5fb270(state, pc, memory);
    const uint8_t open = 1;
    if (sa2_poke(SKIP_GATE, 1, &open) != SA2_OK) {
        if (!g_skip_fail_said++)
            fprintf(stderr, "sa2 tweaks: every event skippable: guest 0x%08X does not take a write, so "
                    "event %u stays unskippable\n", SKIP_GATE, event);
        return __real_sub_5fb270(state, pc, memory);
    }
    uint32_t pad = 0, press = 0;
    const int pressed = sa2_peek(SKIP_CONTROLLER, 4, &pad) == SA2_OK && pad != 0
        && sa2_peek(pad + 0x10u, 4, &press) == SA2_OK && (press & SKIP_START_PRESSED);

    void *ret = __real_sub_5fb270(state, pc, memory);

    uint8_t now = 0;
    if (sa2_peek(SKIP_GATE, 1, &now) != SA2_OK || (now == 1 && sa2_poke(SKIP_GATE, 1, &saved) != SA2_OK)) {
        if (!g_skip_restore_said++)
            fprintf(stderr, "sa2 tweaks: every event skippable: guest 0x%08X could not be put back to "
                    "%u after event %u\n", SKIP_GATE, saved, event);
    }
    uint32_t after = 0, ended = 1;
    if (pressed && !g_skip_said[which] && sa2_peek(SKIP_STATE, 4, &after) == SA2_OK
        && after == SKIP_ENDING && sa2_peek(SKIP_ENDED, 4, &ended) == SA2_OK && ended == 0) {
        g_skip_said[which] = 1;
        fprintf(stderr, "sa2 tweaks: every event skippable: event %u skipped with Start\n", event);
    }
    return ret;
}
