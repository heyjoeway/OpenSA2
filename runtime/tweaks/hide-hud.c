/* hide-hud: the gameplay HUD's score window (score, time, rings and lives) and the kart races' HUD are
 * not drawn, for a clean picture to watch or capture. Button prompts, the radar, title cards, the
 * trick score, the countdowns and every gameplay rule are left as they are, and drawn where they
 * are drawn with the HUD on (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_61bae0, sub_61b1d0
 *
 * What it rests on, read from the unpacked image.
 *
 * The score window. 0x0044E9C0 draws it, called directly from the three game loops (0x0043D26F,
 * 0x0043D4D5, 0x0043D134). It takes no stack arguments and ends in a plain `ret`, and begins
 * `cmp byte [0x0174AFCC], 0; ... je 0x0044EAF6`, the pops and `ret` at its end: with that byte 0
 * (the game's own "no score window": events, the Chao World, the kart races) it draws nothing and
 * changes no register it does not restore. The runtime already wraps 0x0044E9C0 (SA2_PLAY_HUD43,
 * runtime/sa2_widescreen.c), and a function is wrapped once, so that wrap asks this file first:
 * sa2_tweak_hide_hud_score_window below. With the tweak on and the byte not 0 it takes the call
 * as the byte-0 path would, the RET alone (EIP = [ESP], ESP + 4, every other register as the
 * caller left it), and writes the HUD's x origin first. With the byte 0 it leaves the call to the
 * lifted body, which draws nothing. The byte itself is never written: the game's own saves and
 * restores of it (ChaoMain 0x0052ACFB/0x0052AFD6, the event executor 0x005FBCC1/0x005FBDE9, the
 * kart's Cart_Init 0x0061A8B8 and Cart_UnknownA 0x0061A9EC) see what they would see with the tweak
 * off. The skipped body's two calls of 0x0042A870 around its drawing (the fog switched off for the
 * HUD at 0x025EFFCC = 0 and back on at 1) are skipped as on the byte-0 path.
 *
 * The x origin O, the guest float at 0x01A559E4 (.bss, 0.0 at the start). Only two instructions
 * set it from the screen: 0x0044E1A0 in the score window's layout 0x0044E080 and 0x0044E3E1 in
 * the kart time window 0x0044E350, both O = 40 - 276(k - 1) with k = (H/640)/(V/480), H and V the
 * floats at 0x0174BA64 and 0x0174BA68 (40 at 4:3). Every other store saves, writes and restores
 * it (0x0044FF46/66/87, 0x0046BBA4/C3/C04, ...), so if neither ran, O would stay 0.0 for the
 * whole process. Elements this tweak keeps add O to their x: the trick score DispTechniqueScore
 * (0x0044D415, 0x0044D63E, 0x0044D6C7), the digits 0x0044EB00 (the boss rush results 0x004526D0,
 * the 15-second countdown 0x006F41B0), the countdown beside BreathCounterP (0x0046BBA4) and more;
 * with O at 0.0 each sat 40 canvas pixels left of its place at 4:3. So the tweak writes O where
 * the game would have: in the score window's call, when its layout would have run (0x0044E080 is
 * called only when the byte at 0x0174AFCE is 1, at 0x0044EA7E and 0x0044EAB8; its first call
 * always stores O, the two-player one by the same arithmetic), and once when the kart layout is
 * not loaded (below), whose 0x0044E350 would have written the same value each frame. The value is
 * the guest's arithmetic at PC24, each step rounded to float (the game runs with x87 control word
 * 0x007F, runtime/sa2_d3d9.c), from the H that the layout would read: under SA2_PLAY_HUD43 the 4:3
 * H that runtime/sa2_widescreen.c gives its layout sites (sa2_hud43_hud_h), so that O holds one
 * value for every reader, as that file's rule asks.
 *
 * Checked: a test-only build that calls AddTechniqueScore with a 2000 and a 1000 bonus in City
 * Escape (hud43-fold's hook) prints O at each call: with the tweak on it is the off run's bits at
 * 1440x1080 (40), 1920x1080 (-52.000008, where rounding only at the end would give the next float
 * down), 2560x1080 (-174.66667) and 2560x1080 under SA2_PLAY_HUD43 (40), and in every dumped frame
 * of both phases the on run's draws are the off run's with the score window's 23 taken out, extents
 * equal. The tweak before this, whose O stayed 0.0, drew the trick name and digits 40 canvas pixels
 * left at 1440x1080.
 *
 * The kart HUD (Codes.lst's "Disable Kart Racing HUD" NOPs three 5-byte calls in .text, which
 * OpenSA2 never runs as code; the wraps do what the NOPs do instead). Both callees are cdecl and end
 * in a plain `ret`; the callers pop their own arguments after the call. A skipped call is the RET
 * alone, as after a NOP.
 *
 *   0x0061BAE0  loads the kart's "layoutExec" task (display 0x0061BBA0, the kart layout), zeroes
 *               0x01D96F50 and sets the three colour bytes at 0x01D94423; its one caller, the kart
 *               setup 0x0061B060, calls it at 0x0061B120 (return 0x0061B125). Skipped there, the
 *               task is never made and nothing draws the layout; O is written as its time window
 *               would write it. The bytes it would write are read only by that task's display
 *               (0x0061BBA9, 0x0061BF00); the kart race's own code writes 0x01D96F50 before it
 *               reads it (0x00622832, 0x00622873).
 *   0x0061B1D0  one HUD element's draw. Of its callers, two are skipped: 0x0061B7D3 and 0x0061B87E in
 *               the kart's timeExt display (returns 0x0061B7D8 and 0x0061B883). Every other call
 *               reaches the lifted body.
 *
 * The hidden pause, in the game already and not changed here: PauseMenu_Main (0x0043D310) sets
 * IsNotPauseHide (0x01934B06) to 0xFF while X and Y (0x600) are both held on the first pad and then
 * skips drawing the menu (0x0043D49B), so holding X and Y in the pause menu should show the paused
 * scene alone. Not yet seen in a run: a City Escape pause holding U and K (a guess at this
 * installation's X and Y keys) kept the menu up, so the binding, or this reading, is unconfirmed.
 *
 * Left out of this tweak on purpose (the spec's photo freeze): "Disable HUD: Prompts/Radar"
 * (0x0174AFCB) also stops the Eggman Quarters guards (0x0069117C, 0x00697469) and the title cards
 * (0x0043CD9B, 0x0043D17C), so it is gameplay, not HUD; and freezing TimeStopped (0x0174AFF7) stops
 * the stage timer, whose times are kept in the save, and wants a key a phone does not have.
 *
 * Off is the lifted code alone: each wrap, and sa2_tweak_hide_hud_score_window, tests the tweak's
 * bit first and otherwise only calls its `__real_` (or answers 0, so the score window's wrap goes
 * on as without this file); no guest byte is read or written.
 *
 * Risks. A score window drawn by a mod or a later game build from another function would not be
 * covered. If a build moved the kart call sites, the return-address test would no longer match:
 * the run says the first time each site is skipped, and says by name when 0x0061BAE0 is called from
 * anywhere but its known site (it is then left alone), so a missing line shows a moved site. When
 * guest memory the tweak needs does not read or write (the stack, the switches, H and V, O), the
 * call goes through to the lifted body, drawn, and the run says which once.
 *
 * Its lines, each said once: "sa2 tweaks: hud hidden: score window not drawn (x origin 40 written
 * as 0x0044E080 would)" the first time it takes the score window's call, "sa2 tweaks: hud hidden:
 * kart layout not loaded (call at 0x0061B120 skipped; x origin 40 written as 0x0044E350 would)" and
 * "sa2 tweaks: hud hidden: kart timeExt element not drawn (call at 0x0061B7D3 skipped)" (and
 * 0x0061B87E) the first time each site is skipped.
 */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define HUD_SCORE_WINDOW_ON   0x0174AFCCu   /* the score window's switch; 0 draws nothing */
#define HUD_SCORE_LAYOUT_ON   0x0174AFCEu   /* 1: the score window calls its layout 0x0044E080 */
#define HUD_ORIGIN_X          0x01A559E4u   /* O, the HUD's x origin, float */
#define HUD_H                 0x0174BA64u   /* HorizontalResolution, float */
#define HUD_V                 0x0174BA68u   /* VerticalResolution, float */
#define HUD_KART_LAYOUT_CALL  0x0061B120u   /* 0x0061B060's call of 0x0061BAE0 */
#define HUD_KART_LAYOUT_RET   0x0061B125u
#define HUD_KART_TIMEEXT_A    0x0061B7D8u   /* after the timeExt display's calls of 0x0061B1D0 */
#define HUD_KART_TIMEEXT_B    0x0061B883u

/* runtime/sa2_widescreen.c: the H bits the HUD's layout sites read (the 4:3 value under
 * SA2_PLAY_HUD43 on a wider screen, else H). */
uint32_t sa2_hud43_hud_h(uint32_t h_bits, uint32_t v_bits);

static int g_hud_score_said, g_hud_layout_said, g_hud_layout_other_said;
static int g_hud_timeext_said[2], g_hud_stack_said, g_hud_switch_said, g_hud_origin_said;

/* The return address the lifted CALL pushed, at guest [ESP]; 0 (and said once) when it does not
 * read, so the caller is unknown and the call goes through. */
static uint32_t hud_site(void *state, const char *callee) {
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0;
    if (sa2_peek(esp, 4, &site) == SA2_OK) return site;
    if (!g_hud_stack_said++)
        fprintf(stderr, "sa2 tweaks: hud hidden: the guest stack at 0x%08X does not read, so the "
                "caller of %s is unknown and the call goes through\n", esp, callee);
    return 0;
}

/* The RET alone: what the caller sees after its CALL became a NOP. */
static void hud_skip(void *state, uint32_t site) {
    sa2_rt_set_gpr(state, SA2_ESP, sa2_rt_get_gpr(state, SA2_ESP) + 4u);
    sa2_rt_set_gpr(state, SA2_EIP, site);
}

/* O = 40 - 276(k - 1), k = (H/640)/(V/480), as 0x0044E146-0x0044E1A0 and 0x0044E3B5-0x0044E3E1
 * compute it at PC24: every step rounded to float. Exposed for the unit test. */
float sa2_hide_hud_origin(float h, float v) {
    volatile float a = h / 640.0f, b = v / 480.0f;
    volatile float k = a / b;
    volatile float d = k - 1.0f;
    volatile float m = d * 276.0f;
    return 40.0f - m;
}

/* Writes O as the layout `who` would at the current H and V; 0 and said once when it cannot. */
static int hud_write_origin(const char *who, float *o) {
    uint32_t h = 0, v = 0;
    if (sa2_peek(HUD_H, 4, &h) != SA2_OK || sa2_peek(HUD_V, 4, &v) != SA2_OK) {
        if (!g_hud_origin_said++)
            fprintf(stderr, "sa2 tweaks: hud hidden: the resolution at 0x%08X does not read, so the "
                    "HUD's x origin cannot be written as %s would and %s is drawn\n", HUD_H, who,
                    who);
        return 0;
    }
    const uint32_t hb = sa2_hud43_hud_h(h, v);
    float fh, fv;
    memcpy(&fh, &hb, 4);
    memcpy(&fv, &v, 4);
    *o = sa2_hide_hud_origin(fh, fv);
    if (sa2_poke(HUD_ORIGIN_X, 4, o) != SA2_OK) {
        if (!g_hud_origin_said++)
            fprintf(stderr, "sa2 tweaks: hud hidden: the HUD's x origin at 0x%08X does not write, "
                    "so %s is drawn\n", HUD_ORIGIN_X, who);
        return 0;
    }
    return 1;
}

/* Called first by runtime/sa2_widescreen.c's wrap of 0x0044E9C0, the score window: 1 when the tweak
 * has taken the call itself (O written if the window would have laid itself out, then the RET),
 * 0 when the lifted body is to run. */
int sa2_tweak_hide_hud_score_window(void *state) {
    if (!sa2_tweak_on(SA2_TWEAK_HIDE_HUD)) return 0;
    uint8_t on = 0, layout = 0;
    if (sa2_peek(HUD_SCORE_WINDOW_ON, 1, &on) != SA2_OK ||
        (on && sa2_peek(HUD_SCORE_LAYOUT_ON, 1, &layout) != SA2_OK)) {
        if (!g_hud_switch_said++)
            fprintf(stderr, "sa2 tweaks: hud hidden: the score window's switches at 0x%08X do not "
                    "read, so the score window is drawn\n", HUD_SCORE_WINDOW_ON);
        return 0;
    }
    if (!on) return 0;                      /* the game's own 0: the body draws nothing */
    const uint32_t site = hud_site(state, "0x0044E9C0");
    if (!site) return 0;
    float o = 0.0f;
    if (layout == 1 && !hud_write_origin("0x0044E080", &o)) return 0;
    if (!g_hud_score_said++) {
        if (layout == 1)
            fprintf(stderr, "sa2 tweaks: hud hidden: score window not drawn (x origin %g written as "
                    "0x0044E080 would)\n", (double)o);
        else
            fprintf(stderr, "sa2 tweaks: hud hidden: score window not drawn (its layout off, "
                    "0x%08X = %u, so the x origin is left as it is)\n", HUD_SCORE_LAYOUT_ON, layout);
    }
    hud_skip(state, site);
    return 1;
}

void *__real_sub_61bae0(void *state, uint32_t pc, void *memory);
void *__real_sub_61b1d0(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_61bae0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_HIDE_HUD)) return __real_sub_61bae0(state, pc, memory);
    const uint32_t site = hud_site(state, "0x0061BAE0");
    if (site == HUD_KART_LAYOUT_RET) {
        float o = 0.0f;
        if (!hud_write_origin("the kart layout", &o)) return __real_sub_61bae0(state, pc, memory);
        if (!g_hud_layout_said++)
            fprintf(stderr, "sa2 tweaks: hud hidden: kart layout not loaded (call at 0x%08X skipped; "
                    "x origin %g written as 0x0044E350 would)\n", HUD_KART_LAYOUT_CALL, (double)o);
        hud_skip(state, site);
        return memory;
    }
    if (site && !g_hud_layout_other_said++)
        fprintf(stderr, "sa2 tweaks: hud hidden: 0x0061BAE0 called from 0x%08X, not 0x%08X, so the "
                "kart layout is left alone\n", site, HUD_KART_LAYOUT_RET);
    return __real_sub_61bae0(state, pc, memory);
}

__attribute__((used, retain))
void *__wrap_sub_61b1d0(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_HIDE_HUD)) return __real_sub_61b1d0(state, pc, memory);
    const uint32_t site = hud_site(state, "0x0061B1D0");
    const int which = site == HUD_KART_TIMEEXT_A ? 0 : site == HUD_KART_TIMEEXT_B ? 1 : -1;
    if (which < 0) return __real_sub_61b1d0(state, pc, memory);
    if (!g_hud_timeext_said[which]++)
        fprintf(stderr, "sa2 tweaks: hud hidden: kart timeExt element not drawn (call at 0x%08X "
                "skipped)\n", site - 5u);
    hud_skip(state, site);
    return memory;
}
