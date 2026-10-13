/* The guest's busy-wait polls, wrapped: where the scheduler's guest-poll rule
 * (runtime/sa2_threads.h, sa2_sched_poll) is reached from.
 *
 * One guest function, linked in every build with `-Wl,-wrap=sub_427ee0`
 * (tools/s4/link_s4.py PRODUCTION_WRAPS), so both of its call sites land here:
 * the direct call at 0x004284FD and the dispatch-stub call at 0x004286FB.
 *
 * 0x00427EE0 is
 *
 *     mov eax,[eax+4] ; test eax,eax ; je +5 ; mov al,[eax+0Ch] ; ret ; mov al,1 ; ret
 *
 * the done byte of the operation that the container in EAX points to. The
 * "AsynDo" thread (start 0x008653D0) sets it through the operation's vtable
 * slot 1 (0x00427DB0) after running the work function, and nothing else
 * does. Its only callers are the two AsynDo wait loops,
 * SOC_AsynDoWithNowLoading (0x00428470) and SOC_AsynDoWithCurrentFrame
 * (0x00428740), read with tools/s4/xrefs.py and tools/s4/disas.py
 * (agents-journals/s5/2026-10-02-newfile-hang.md).
 *
 * The wrapper reads the byte the same way, without running the guest code, to
 * tell the scheduler whether the poll is about to find the operation not done;
 * then it runs the lifted function, whose answer is the one the guest gets. A
 * read that the reimplementation got wrong could only move where a hand-off
 * happens, never what the guest is told.
 *
 * The reference to `__real_sub_427ee0` is strong on purpose: a link that
 * compiles this file and omits the flag fails ("undefined symbol"), so a link
 * path cannot drop the rule silently. The run record shows the rest
 * (harness/crt_probe.c `threads.poll_handoff`): a build whose every call of the
 * poll bypassed the wrap records no polls, and Documentation/scripts/
 * check_poll_handoff.py fails a registered run that does; a bypass of the
 * wait loop's call alone brings the hang back, which the NEW FILE run that
 * gate holds to the tree catches (Documentation/receipts/s5-newfile-wait-v1.json). */
#include <stdint.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_threads.h"

void *__real_sub_427ee0(void *state, uint32_t pc, void *memory);

/* What 0x00427EE0 is about to return: 1 done (or no operation, which it also
 * answers 1), 0 not done, -1 when the container or operation cannot be read
 * (the lifted function then faults on the same read, and sa2_sched_poll does
 * not hand off before it). */
static int asyndo_done(uint32_t container) {
    uint32_t op = 0;
    if (sa2_peek(container + 4u, 4, &op) != SA2_OK) return -1;
    if (!op) return 1;
    uint8_t done = 0;
    if (sa2_peek(op + 0x0Cu, 1, &done) != SA2_OK) return -1;
    return done != 0;
}

/* `retain` keeps the definition through the linker's section garbage collection
 * even when nothing references it, which is exactly the build that omitted
 * -wrap: its strong `__real_sub_427ee0` reference then fails that link. */
__attribute__((used, retain))
void *__wrap_sub_427ee0(void *state, uint32_t pc, void *memory) {
    sa2_sched_poll(asyndo_done(sa2_rt_get_gpr(state, SA2_EAX)), "AsynDo poll (0x00427EE0)");
    return __real_sub_427ee0(state, pc, memory);
}
