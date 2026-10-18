/* no-loading-tips: the loading screen's picture (Omochao's tip card, its text and the animated
 * "Now Loading") is not drawn, as SA2's Codes.lst "Disable Loading Tips" makes it
 * (runtime/sa2_tweaks.h: the tweaks and their rules). Most loads then show a black screen; a load
 * over the current frame keeps the picture its caller drew first (a stage's title card) and loses
 * only the "Now Loading" overlay.
 *
 * TWEAK_WRAPS: sub_428010
 *
 * Where the picture comes from, read from the unpacked image. 0x00428010 (1117 bytes, unnamed;
 * `sub esp,34h` at the entry and one plain `ret` at 0x0042846C, so no stack arguments) draws one
 * frame of the loading screen: it sets a render state through the device wrapper at
 * [0x01A557C0], steps its own animation counter (0x0174B494), and then, when the "show tips"
 * flag dword [0x01A557E8] is set, Omochao's card and a tip (the tip index at 0x01A557EC), or
 * else only the "Now Loading" text (gated by byte 0x0093BC5A). It has three callers, each linked
 * against it directly and each inside a frame's scene, before its end of scene (the Clear before
 * it, device wrapper vtable +0x3C, clears to black; below, which callers make it):
 *
 *   0x004285D7  0x00428470, the asynchronous load with the loading screen (a stage's load);
 *               return address 0x004285DC
 *   0x0042892E  0x00428740, the asynchronous load over the current frame; return 0x00428933
 *   0x0041B333  0x0041B2D0, reached from the game loop and the movie player; return 0x0041B338
 *
 * None of them reads EAX, ECX or EDX after the call (each reloads ECX from [0x01A557C0] or
 * compares a global), its x87 stack is balanced, and it keeps ESI and EDI. The globals it writes
 * are the loading screen's own (its counter, tip index and card state), so no save data and no
 * random number is touched: its two calls besides the drawing are 0x0041DC80 (a sprite draw) and
 * 0x007A7E65 (the C library's invalid-parameter report, on a bad table index). Codes.lst writes
 * `C3` (ret) over its first byte; OpenSA2 never runs guest code, so the wrap does what that byte
 * does: EIP = the return address at guest [ESP], ESP + 4, and nothing else. The caller then
 * ends its scene on what it drew before the call. 0x00428470 makes the black Clear just before
 * its call (0x004285D5), so its frame is black. 0x0041B2D0 makes it just before its call too
 * (0x0041B331), but only when dword [0x0174B48C] is set; otherwise its frame keeps what it holds.
 * 0x00428740 draws the current picture (its sprite draw, call 0x0041DC80 at 0x00428926) just
 * before its call at 0x0042892E, so that picture stays on screen without the "Now Loading" ring
 * over it, for example the stage's title card before Pumpkin Hill.
 *
 * Off is the lifted function alone: the wrap's first statement calls `__real_sub_428010` and
 * returns when the tweak is off.
 *
 * The first time the tweak skips a frame for each caller, the run says so once:
 * "sa2 tweaks: loading-tips off: loading screen not drawn (the stage load, 0x00428470)". When the
 * guest stack does not read, the frame is drawn as usual and the run says why, once.
 *
 * Risks: the loading screen is black instead of animated, which is the Codes.lst behaviour; a
 * load that takes long gives no sign of progress. A load over the current frame (0x00428740, a
 * stage's title card before the stage) is not black: the card stays on screen with no ring, so
 * the player's wording (SA2_TWEAK_LIST's summary, GameSettings.TWEAKS) says "black, or the
 * picture already on screen", never only black. Codes.lst's variant that keeps the "Now Loading"
 * text (dword [0x01A557E8] = 0 around the call) is not offered.
 */
#include <stdint.h>
#include <stdio.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

/* The callers' return addresses (the header), and what the run's line calls each. */
static const struct {
    uint32_t ret;
    const char *what;
} LOADING_CALLERS[] = {
    {0x004285DCu, "the stage load, 0x00428470"},
    {0x00428933u, "the load over the current frame, 0x00428740"},
    {0x0041B338u, "the game loop's loading frame, 0x0041B2D0"},
};

static uint32_t g_loading_tips_said;   /* a bit per LOADING_CALLERS entry; bit 31 another caller */
static int g_loading_tips_stack_said;

void *__real_sub_428010(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_428010(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_NO_LOADING_TIPS)) return __real_sub_428010(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t ret = 0;
    if (sa2_peek(esp, 4, &ret) != SA2_OK) {
        if (!g_loading_tips_stack_said++)
            fprintf(stderr, "sa2 tweaks: loading-tips off: the guest stack at 0x%08X does not read, "
                    "so the loading screen is drawn\n", esp);
        return __real_sub_428010(state, pc, memory);
    }
    /* The `ret` Codes.lst puts at 0x00428010. */
    sa2_rt_set_gpr(state, SA2_EIP, ret);
    sa2_rt_set_gpr(state, SA2_ESP, esp + 4u);
    unsigned i = 0;
    const unsigned n = sizeof LOADING_CALLERS / sizeof LOADING_CALLERS[0];
    while (i < n && LOADING_CALLERS[i].ret != ret) i++;
    const uint32_t bit = i < n ? 1u << i : 1u << 31;
    if (!(g_loading_tips_said & bit)) {
        g_loading_tips_said |= bit;
        if (i < n)
            fprintf(stderr, "sa2 tweaks: loading-tips off: loading screen not drawn (%s)\n",
                    LOADING_CALLERS[i].what);
        else
            fprintf(stderr, "sa2 tweaks: loading-tips off: loading screen not drawn (a call "
                    "returning to 0x%08X)\n", ret);
    }
    return memory;
}
