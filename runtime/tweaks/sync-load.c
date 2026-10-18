/* sync-load: each of the game's asynchronous loads runs its work in place, on the main thread, in
 * the frame that asked for it, as the Mod Loader's SyncLoad does (SA2ModLoader patches.cpp: a jump
 * at 0x00428470 and 0x00428740 to `SyncLoad(void (*f)(void *), void *a) { f(a); }`). The game no
 * longer draws one loading-screen frame for each file it loads, so a load ends that many frames
 * sooner (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_428470, sub_428740
 *
 * Where, read from the unpacked image.
 *
 *   0x00428470 (SOC_AsynDoWithNowLoading, its log string 0x008B74CC) and 0x00428740
 *   (SOC_AsynDoWithCurrentFrame, 0x008B74E8) are cdecl, `push ebp; mov ebp,esp` with one plain
 *   `ret` (0x00428731, 0x00428A24): [ESP+4] on entry is the work function and [ESP+8] its
 *   argument ([ebp+8], [ebp+0Ch] at 0x004284C4 and 0x00428794). Each stores [0x01A557E4] = 1 and
 *   [0x01A557F0] = 0 first (0x004284A0, 0x00428770), so GameLoop's loading draw (0x0041B2D0,
 *   which counts 0x01A557F0 up to 5 and then clears 0x01A557E4) runs for five frames after the
 *   last load. It then makes a 12-byte AsynDo object (operator new 0x007A5DA1, built by
 *   0x00427DD0): a task (vtable 0x008B751C; +4 the work, +8 the argument, +0xC the done byte), and
 *   a worker thread (0x00865870, CreateThread created suspended, start 0x008653D0) whose task
 *   method 0x00427DB0 is `push [task+8]; call [task+4]; add esp,4; mov byte [task+0Ch],1`. The
 *   work is a cdecl call with one argument. It resumes the worker and polls the done byte
 *   (0x00427EE0), drawing one loading frame per pass until it is set: 0x00428470 the Now Loading
 *   screen (0x00428010) while the byte 0x0093BC5B is set, 0x00428740 the frame captured by the
 *   device wrapper's +0x16C (when 0x0093BEB2 is set; it then clears it) under the same draw. Each
 *   pass calls Sleep(20) when [0x01933EAC] is set (0 in every log). Then the log call 0x00426740
 *   (a no-op past its stack cookie) and the object's destructor.
 *
 *   The callers, 16 sites at 8 places, each choosing by [0x0174B48C] (0x00428470 when it is set):
 *   0x0040C920/0x0040C931, 0x0041D1F3/0x0041D204, 0x0041D45A/0x0041D474, 0x0041D89A/0x0041D8AE,
 *   0x00428C70/0x00428C81, 0x004300B9/0x004300CD, 0x00430D7F/0x00430D93, 0x0077DD34/0x0077DD4D.
 *   Each pushes the argument and the work, one of 0x0041D2A0, 0x00428B60, 0x00428A60 and
 *   0x0077DD60, calls, and pops 8; none reads EAX, ECX or EDX after the call (the next
 *   instructions load them or leave the function). The work's result goes to the argument block.
 *
 *   In the lifted runtime the worker runs only when the main thread hands off, which it does in
 *   the frame's wait, and its work costs no virtual time, so every load costs exactly one paced
 *   loading frame (agents-journals/s4/2026-09-26-loading-frames.md [private]: the boot's tick 369
 *   has 14 loads and draws 15 frames; Metal Harbor's stage load at tick 117, 29 and 30).
 *
 *   Measured on the Linux release in paced sessions, the same input with the tweak off and on
 *   (2026-10-18 [private]): every tick's GameMode and CurrentMenu equal; the boot to the title's
 *   intro 36 frames fewer, 0.62 s less virtual time and 0.58 s less wall time; a story continue into
 *   Pumpkin Hill 0.67 s sooner; practice-stage's Metal Harbor 1.07 s sooner by its first playable
 *   tick. The wall time saved is the frames' 1/60 s each less the work's own host time, which a
 *   slower device spends whichever way the load runs.
 *
 * The wraps. On, each reads the return address, the work and the argument from the guest stack;
 * makes the two stores the body makes first; and calls the work's lifted function (the function
 * table's, sa2_ft_lookup) on the live state as the task method would: a sentinel return address
 * 0x40 bytes below ESP and the argument above it, the x87 control word a new thread starts with
 * (0x027F, runtime/sa2_threads.c call_on_own_stack, where the main thread holds Direct3D's 0x007F),
 * and after it every register and the control word put back. A return anywhere but the sentinel,
 * or with ESP not 4 above it, stops the run by name (SA2_TRAP_CONTROL_LEFT). Then the wrap returns
 * as the body's `ret` does: EIP = the return address, ESP + 4. No AsynDo object or thread is
 * made, no frame is drawn and 0x00427EE0 is never polled. The bytes 0x0093BC5B and 0x0093BEB2
 * stay as they were (the Mod Loader leaves them too): only these two functions read either, the
 * first gates only their own loading draw, and the second asks the next 0x00428740 for a frame
 * capture that only that draw shows. 0x01933EFC to 0x01933F14, which the draw zeroes each pass,
 * are left too.
 *
 * Off is identical: each wrap tests the bit first and otherwise only calls `__real_` and returns.
 * Nothing is written to the save: the two stores are frame state the body writes too.
 *
 * Risks. The work runs on the main thread, with its stack, TEB and C runtime thread data: file
 * reads (ReadFile, _read), parsing and, in two of the boot's loads,
 * D3DXCreateTextureFromFileInMemoryEx; the Mod Loader has run them this way for years. A wait
 * inside the work still hands off to the game's other threads, as any main-thread file read does.
 * The Now Loading animation does not move during these loads (it moved one step a load), and a
 * load over the current frame shows the last frame presented, not its capture. The heap and the
 * thread table no longer see the AsynDo objects and threads, so later allocations can land
 * elsewhere (play only). A work function missing from the function table, or a stack that does
 * not read, leaves that load to the body, and the run says so once. A future work function that
 * needs a thread of its own (thread-local or fiber-local state) would break; the four the image
 * has do not.
 *
 * The lines the run prints, each prefixed "sa2 tweaks: loads in place: ": the count at 1, 10,
 * 100, 1000 and so on, "100 loads run in place (no AsynDo thread, no loading frame)"; and once
 * per helper and work function (eight pairs at most), "work 0x0041D2A0 runs in place from
 * 0x0040C936 (SOC_AsynDoWithCurrentFrame)".
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_call.h"
#include "sa2_trap.h"
#include "sa2_tweaks.h"

#define SYNC_LOAD_NOW_LOADING   0x00428470u   /* SOC_AsynDoWithNowLoading */
#define SYNC_LOAD_CURRENT_FRAME 0x00428740u   /* SOC_AsynDoWithCurrentFrame */
#define SYNC_LOAD_SHOWN         0x01A557E4u   /* int32: GameLoop draws the loading screen while set */
#define SYNC_LOAD_SHOWN_FRAMES  0x01A557F0u   /* int32: GameLoop's count of those frames, to 5 */
#define SYNC_LOAD_SLACK         0x40u         /* sa2_guest_call.h's SA2_GUEST_CALL_SLACK */
#define SYNC_LOAD_THREAD_CW     0x027Fu       /* a new thread's x87 control word */

static unsigned long g_sync_loads, g_sync_next_said = 1;
static struct { uint32_t helper, work; } g_sync_named[8];
static unsigned g_sync_nnamed;
static int g_sync_stack_said, g_sync_miss_said;

void *__real_sub_428470(void *state, uint32_t pc, void *memory);
void *__real_sub_428740(void *state, uint32_t pc, void *memory);

static const char *helper_name(uint32_t helper) {
    return helper == SYNC_LOAD_NOW_LOADING ? "SOC_AsynDoWithNowLoading" : "SOC_AsynDoWithCurrentFrame";
}

/* The work's cdecl call, fn(arg), on the live state: the sentinel 0x40 bytes below ESP, the argument
 * above it, the new thread's control word, and every register and the control word put back after.
 * A return anywhere but the sentinel with ESP 4 above it stops the run. */
static void *sync_call(void *s, void *m, sa2_lifted_fn fn, uint32_t va, uint32_t arg) {
    uint32_t saved[SA2_GPR_COUNT];
    for (int i = 0; i < SA2_GPR_COUNT; ++i) saved[i] = sa2_rt_get_gpr(s, i);
    const uint16_t cw = sa2_rt_get_cw(s);
    const int round = sa2_rt_get_host_round();
    const uint32_t base = saved[SA2_ESP] - SYNC_LOAD_SLACK, frame[2] = {SA2_CALL_SENTINEL, arg};
    if (sa2_poke(base, 8, frame) != SA2_OK) {
        sa2_trap_note("sa2 tweaks: loads in place: the guest stack at %#010x does not take the "
                      "return address for %#010x", base, va);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, va);
    }
    sa2_rt_set_cw(s, SYNC_LOAD_THREAD_CW);
    sa2_rt_set_host_round(sa2_rt_round_from_cw(SYNC_LOAD_THREAD_CW));
    sa2_rt_set_gpr(s, SA2_ESP, base);
    sa2_rt_set_gpr(s, SA2_EIP, va);
    m = fn(s, va, m);
    const uint32_t eip = sa2_rt_get_gpr(s, SA2_EIP), esp = sa2_rt_get_gpr(s, SA2_ESP);
    if (eip != SA2_CALL_SENTINEL || esp != base + 4u) {
        sa2_trap_note("sa2 tweaks: loads in place: work %#010x returned to %#010x with esp %#010x, "
                      "not to the sentinel with esp %#010x", va, eip, esp, base + 4u);
        sa2_trap_raise(SA2_TRAP_CONTROL_LEFT, eip);
    }
    for (int i = 0; i < SA2_GPR_COUNT; ++i) sa2_rt_set_gpr(s, i, saved[i]);
    sa2_rt_set_cw(s, cw);
    sa2_rt_set_host_round(round);
    return m;
}

/* One load in place, or the body's when the stack or the work cannot be had. */
static void *sync_load(void *s, uint32_t pc, void *m, uint32_t helper,
                       void *(*real)(void *, uint32_t, void *)) {
    const uint32_t esp = sa2_rt_get_gpr(s, SA2_ESP);
    uint32_t in[3];                                   /* return address, work, argument */
    if (sa2_peek(esp, sizeof in, in) != SA2_OK) {
        if (!g_sync_stack_said++)
            fprintf(stderr, "sa2 tweaks: loads in place: the guest stack at 0x%08X does not read, so "
                    "%s's load runs as usual\n", esp, helper_name(helper));
        return real(s, pc, m);
    }
    const sa2_lifted_fn fn = sa2_ft_lookup(in[1]);
    if (!fn) {
        if (!g_sync_miss_said++)
            fprintf(stderr, "sa2 tweaks: loads in place: work 0x%08X (from 0x%08X) is not in the "
                    "function table, so its load runs as usual\n", in[1], in[0]);
        return real(s, pc, m);
    }
    const uint32_t one = 1, zero = 0;
    sa2_poke(SYNC_LOAD_SHOWN, 4, &one);
    sa2_poke(SYNC_LOAD_SHOWN_FRAMES, 4, &zero);
    m = sync_call(s, m, fn, in[1], in[2]);
    sa2_rt_set_gpr(s, SA2_EIP, in[0]);
    sa2_rt_set_gpr(s, SA2_ESP, esp + 4u);
    unsigned i = 0;
    while (i < g_sync_nnamed && (g_sync_named[i].helper != helper || g_sync_named[i].work != in[1])) i++;
    if (i == g_sync_nnamed && i < sizeof g_sync_named / sizeof g_sync_named[0]) {
        g_sync_named[g_sync_nnamed].helper = helper;
        g_sync_named[g_sync_nnamed++].work = in[1];
        fprintf(stderr, "sa2 tweaks: loads in place: work 0x%08X runs in place from 0x%08X (%s)\n",
                in[1], in[0], helper_name(helper));
    }
    if (++g_sync_loads == g_sync_next_said) {
        g_sync_next_said *= 10;
        fprintf(stderr, "sa2 tweaks: loads in place: %lu load%s run in place (no AsynDo thread, no "
                "loading frame)\n", g_sync_loads, g_sync_loads == 1 ? "" : "s");
    }
    return m;
}

__attribute__((used, retain))
void *__wrap_sub_428470(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_SYNC_LOAD)) return __real_sub_428470(state, pc, memory);
    return sync_load(state, pc, memory, SYNC_LOAD_NOW_LOADING, __real_sub_428470);
}

__attribute__((used, retain))
void *__wrap_sub_428740(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_SYNC_LOAD)) return __real_sub_428740(state, pc, memory);
    return sync_load(state, pc, memory, SYNC_LOAD_CURRENT_FRAME, __real_sub_428740);
}
