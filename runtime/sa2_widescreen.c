/* Play-only full-width rendering: the game's config answered with the screen's size.
 *
 * Subtitle: one guest function, linked in every build with `-Wl,-wrap=sub_405310`
 * (tools/s4/link_s4.py PRODUCTION_WRAPS): ReadConfig, which reads Config/UserConfig.cfg into the
 * MainUserConfig object. In play, SA2_PLAY_RES or SA2_PLAY_ASPECT rewrites the Width and Height it
 * read, so the game draws its back buffer at the device's aspect and the player presents it with
 * no bars. With neither variable set the wrap only calls the lifted body.
 *
 * Why (Documentation/decisions/2026-10-12-S7-widescreen-pulled-forward.md, the user's request of
 * 2026-10-12: "use the full res available on the devices"): the 2012 port already draws at any
 * aspect. Its 3D view is Hor+, its menus are the 4:3 layout centred, and its back buffer is the
 * config's Width x Height (the widescreen study, private/generated/s8/widescreen-study/). The
 * bars came from our side: nothing asked for the screen's size, so the game drew the installer's
 * 1280x720 and the player pillarboxed it into a 19.5:9 phone.
 *
 * The function, read from the unpacked image:
 *
 *   0x00405310  ReadConfig, `this` in EBX (the MainUserConfig object, [0x01AF19EC]). Each
 *               attribute is read with sscanf("%d") into the object: FullScreen [EBX+4], Width
 *               [EBX+8] (0x00405427), Height [EBX+0Ch] (0x0040544D), RefreshRate [EBX+10h],
 *               Language [EBX+14h], Display [EBX+18h], Res [EBX+1Ch], Analytics [EBX+20h]. It
 *               returns EAX = EBX+4 (0x004054FE, 0x0040550E) when the file was read, and 0
 *               (0x00405579) when it was not, after writing its defaults (853x480 at 60 Hz).
 *   0x00401C0E  its one call, in WinMain: then `fild [eax+4]; fstp [0x0174BA64]` and
 *               `fild [eax+8]; fstp [0x0174BA68]`, HorizontalResolution and VerticalResolution,
 *               which the window, the device and every layout read. Nothing else reads the
 *               object's Width or Height (every other reader of 0x01AF19EC takes FullScreen,
 *               Display or the words past Res), and nothing writes the file back.
 *
 * So the wrap rewrites the two ints at [EAX+4] and [EAX+8] after the lifted body returns, before
 * WinMain reads them, and only when EAX is not 0.
 *
 * The settings (read once, at the one call):
 *   SA2_PLAY_RES=WxH      Width W and Height H, each 320 to 16384.
 *   SA2_PLAY_ASPECT=A:B   Width = round(the config's Height x A / B), Height kept; A and B
 *                         positive numbers ("21:9", "2340:1080", "2.39:1"), the Width 320 to
 *                         16384. SA2_PLAY_RES wins when both are set.
 *   "off" in either       nothing changes.
 *   unset or empty        nothing changes and nothing is printed: the switch is off.
 * A malformed value changes nothing. Either variable is ignored when SA2_REPLAY_TICKS or
 * SA2_REPLAY_WORDS is set (harness/replay/wrap_tick_probe.c): a registered replay keeps the
 * default resolution. tools/s4/device_profile.py OWNED strips both from every replay's
 * environment besides.
 *
 * When either is set the run prints one line to stderr, "widescreen: config 1280x720 ->
 * 2340x1080 (SA2_PLAY_RES=2340x1080)", or why nothing changed. The window and the device take
 * their size from the rewritten values; the virtual screen (SA2_WND_SCREEN) is left at its
 * default, as the study measured a 2340x1080 run with the default and with a matching one
 * byte-identical.
 */
#include <math.h>   /* isfinite, a macro: no libm call */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_threads.h"

void *__real_sub_405310(void *state, uint32_t pc, void *memory);

#define WS_MIN 320u
#define WS_MAX 16384u

/* A whole decimal number in [WS_MIN, WS_MAX] spanning [s, end), or 0. */
static uint32_t ws_dim(const char *s, const char *end) {
    if (s == end || end - s > 5) return 0;
    uint32_t v = 0;
    for (const char *p = s; p < end; p++) {
        if (*p < '0' || *p > '9') return 0;
        v = v * 10u + (uint32_t)(*p - '0');
    }
    return v >= WS_MIN && v <= WS_MAX ? v : 0;
}

/* A positive finite number spanning [s, end) exactly, or 0. */
static double ws_ratio_part(const char *s, const char *end) {
    char buf[32];
    if (s == end || (size_t)(end - s) >= sizeof buf) return 0;
    for (const char *p = s; p < end; p++)
        if (!((*p >= '0' && *p <= '9') || *p == '.')) return 0;
    memcpy(buf, s, (size_t)(end - s));
    buf[end - s] = 0;
    char *stop = NULL;
    const double v = strtod(buf, &stop);
    return stop == buf + (end - s) && isfinite(v) && v > 0 ? v : 0;
}

static int ws_is_off(const char *v) {
    return v && (v[0] == 'o' || v[0] == 'O') && (v[1] == 'f' || v[1] == 'F') &&
           (v[2] == 'f' || v[2] == 'F') && v[3] == 0;
}

/* What the settings ask of a config read as cw x ch: 1 with *w x *h to write (the same size
 * included), 0 for nothing. `line` gets the run's one line, or "" when neither setting is set.
 * `found` is 0 when ReadConfig read no file (EAX 0). Exposed for the unit test
 * (Documentation/scripts/tests/test_widescreen.py). */
int sa2_widescreen_answer(const char *res, const char *aspect, int replay, int found, uint32_t cw,
                          uint32_t ch, uint32_t *w, uint32_t *h, char *line, size_t n) {
    const int res_set = res && *res, asp_set = aspect && *aspect;
    line[0] = 0;
    if (!res_set && !asp_set) return 0;
    const char *name = res_set ? "SA2_PLAY_RES" : "SA2_PLAY_ASPECT";
    const char *value = res_set ? res : aspect;
    if (replay) {
        snprintf(line, n, "widescreen: %s=%s ignored in a replay (SA2_REPLAY_TICKS or "
                 "SA2_REPLAY_WORDS is set); the config is left as read", name, value);
        return 0;
    }
    if (ws_is_off(res) || ws_is_off(aspect)) {
        snprintf(line, n, "widescreen: off (%s=off)", ws_is_off(res) ? "SA2_PLAY_RES" : "SA2_PLAY_ASPECT");
        return 0;
    }
    if (!found) {
        snprintf(line, n, "widescreen: %s=%s not applied: the game read no Config/UserConfig.cfg "
                 "(ReadConfig returned 0)", name, value);
        return 0;
    }
    uint32_t nw = 0, nh = 0;
    if (res_set) {
        const char *x = strpbrk(value, "xX");
        if (x) {
            nw = ws_dim(value, x);
            nh = ws_dim(x + 1, x + 1 + strlen(x + 1));
        }
        if (!nw || !nh) {
            snprintf(line, n, "widescreen: SA2_PLAY_RES=%s is not WxH with each of W and H %u to "
                     "%u; the config's %ux%u is kept", value, WS_MIN, WS_MAX, cw, ch);
            return 0;
        }
    } else {
        const char *c = strchr(value, ':');
        const double a = c ? ws_ratio_part(value, c) : 0;
        const double b = c ? ws_ratio_part(c + 1, c + 1 + strlen(c + 1)) : 0;
        if (!(a > 0 && b > 0)) {
            snprintf(line, n, "widescreen: SA2_PLAY_ASPECT=%s is not W:H with W and H positive "
                     "numbers; the config's %ux%u is kept", value, cw, ch);
            return 0;
        }
        const double fw = (double)ch * a / b + 0.5;          /* rounded half up below */
        if (ch < WS_MIN || ch > WS_MAX || !(fw >= WS_MIN && fw < WS_MAX + 1.0)) {
            snprintf(line, n, "widescreen: SA2_PLAY_ASPECT=%s on the config's %ux%u gives a size "
                     "outside %u to %u; the config is kept", value, cw, ch, WS_MIN, WS_MAX);
            return 0;
        }
        nw = (uint32_t)fw;
        nh = ch;
    }
    snprintf(line, n, "widescreen: config %ux%u -> %ux%u (%s=%s)", cw, ch, nw, nh, name, value);
    *w = nw;
    *h = nh;
    return 1;
}

__attribute__((used, retain))
void *__wrap_sub_405310(void *state, uint32_t pc, void *memory) {
    void *ret = __real_sub_405310(state, pc, memory);
    const char *res = getenv("SA2_PLAY_RES"), *aspect = getenv("SA2_PLAY_ASPECT");
    if (!(res && *res) && !(aspect && *aspect)) return ret;     /* the switch off */
    const int replay = getenv("SA2_REPLAY_TICKS") != NULL || getenv("SA2_REPLAY_WORDS") != NULL;
    const uint32_t cfg = sa2_rt_get_gpr(state, SA2_EAX);
    uint32_t cw = 0, ch = 0, w = 0, h = 0;
    int found = cfg != 0;
    if (found && (sa2_peek(cfg + 4u, 4, &cw) != SA2_OK || sa2_peek(cfg + 8u, 4, &ch) != SA2_OK))
        found = 0;
    char line[256];
    if (sa2_widescreen_answer(res, aspect, replay, found, cw, ch, &w, &h, line, sizeof line) &&
        (w != cw || h != ch) &&
        (sa2_poke(cfg + 4u, 4, &w) != SA2_OK || sa2_poke(cfg + 8u, 4, &h) != SA2_OK))
        snprintf(line, sizeof line, "widescreen: the config at 0x%08X could not be written; "
                 "the game keeps %ux%u", cfg, cw, ch);
    if (line[0]) fprintf(stderr, "%s\n", line);
    return ret;
}

/* ---------------------------------------------------------------- the HUD at its 4:3 positions
 *
 * Subtitle: in play with SA2_PLAY_HUD43=1 on a screen wider than 4:3, the gameplay HUD is laid
 * out as the 4:3 original, centred, while the 3D view and the full-screen backgrounds keep the
 * screen's width. The functions listed in HUD43_SITES are linked with `-Wl,-wrap=` (tools/s4/
 * link_s4.py PRODUCTION_WRAPS); with the switch off, and in every replay, each wrap only calls
 * `__real_`.
 *
 * Why (Documentation/decisions/2026-10-12-S7-widescreen-pulled-forward.md: "keeping the UI
 * anchored to the 4:3 layout the GameCube/Dreamcast shipped with"): the 2012 port draws all 2D on
 * a 640x480 canvas that its 2D vertex shader centres on the screen and scales by the height
 * (g_DeviceInfo.w = 1/k, k = (H/640)/(V/480), set once at device creation), so canvas x 0..640
 * is the 4:3 column whatever the width. Menus, pause and subtitles use it as it is. The gameplay
 * HUD does not: its layout functions read HorizontalResolution H (the guest float at 0x0174BA64)
 * and VerticalResolution V (0x0174BA68) and move each element toward the screen's edge, either by
 * k (the score window's x origin is 40 - 276(k-1), written at 0x0044E146-0x0044E1A0) or by a
 * switch on H/V > 1.6 to fixed wide values (the widescreen study,
 * private/generated/s8/widescreen-study/res_readers.json, 110 readers).
 *
 * How: while a HUD site runs, H reads V x 4/3, so its layout computes k = 1 and takes the 4:3
 * branch of every 1.6 test; the real H is put back when the site returns. The swap is a write to
 * guest memory, so it reaches the lifted code's own loads of 0x0174BA64. The 4:3 value is the
 * largest float not above V x 4/3 for which the guest's k, fl(fl(H/640)/fl(V/480)), is not above
 * 1 (exactly 1 at heights divisible by 3; at 1280 lines k is 1 - 2^-24 and the score window's x
 * origin 40.00002). Nested sites share the one swap.
 *
 * The layout sites:
 *
 *   0x0044E9C0  the score window's display (score, time, rings at top left, lives at bottom
 *               left), called directly from 0x0043D240 (CallControlAndDoOtherThings), 0x0043CE20
 *               and 0x0043D4B0. Its layout 0x0044E080 writes the HUD's x origin O, the guest
 *               float at 0x01A559E4, = 40 - 276(k-1) (the store at 0x0044E1A0).
 *   0x007945C0  the action window's prompt ("Brake" and its button, top right), one prompt
 *   0x00794CB0  and two: placed from the canvas constants 592 or 272, then moved by k in the
 *               branch its H/V > 1.6 tests take (0x00794778, 0x0079499D). Both are called from
 *               0x007951A0, the action window's display, which the function map does not list
 *               (an extra function; its object calls them directly). The button's drawing
 *               0x00794280 reads the x they leave at 0x01AF0F50/0x01AF0F54 and no width.
 *   0x0044D360  the trick score (DispTechniqueScore, the display of the object AddTechniqueScore
 *               0x0044D910 makes): frames 1-60 the trick's name slides in at x = O + 12 +
 *               5k x slide (0x0044D3B9-0x0044D41B); from frame 61 a bonus under 2000 shows its
 *               digits through 0x0044EB00, which adds O, and one of 2000 or more its top-tier
 *               sprite at x = 16 + (k-1)(16-320) - O - 1 when H/V > 1.6, else + O - 1
 *               (0x0044D5BD-0x0044D72B).
 *   0x0061BBA0  the kart race's HUD (layout_Disp): its time window 0x0044E350 writes O from k as
 *               the score window does (0x0044E3E1), 0x0061B9C0 places an element at (x + (k-1)
 *               (x-320) - O) times 21/20 or 19/20 by the 1.6 test, and 0x0061B1D0 and 0x0044E740
 *               add O.
 *   0x0061B6C0  the kart race's time extension (timeExt_Disp): k and the 1.6 test (0x0061B71A,
 *               0x0061B7D8), drawn through 0x0061B1D0, which adds O.
 *   0x004526D0  the boss rush results: the 1.6 test (0x004527FB, 0x00452924), digits through
 *               0x0044DF20 and 0x0044EB00, which adds O.
 *   0x006F41B0  a 15-second countdown (an extra function, reached by the tail jump at 0x006F5758
 *               from the display of the object whose main is 0x006F4260): the 1.6 test
 *               (0x006F421B) moves its x from 300 to 219 (0x00B0421C), and 0x006F4090 draws it
 *               through 0x0044EB00, which adds O.
 *
 * The origin. A swap changes what the site reads, and the site's own writes outlive it: O, written
 * by the score window from the 4:3 k, stays there for every later reader. So the rule the sites
 * keep is that every function that writes O from k (0x0044E080 under the score window,
 * 0x0044E350 under the kart HUD) and every one that reads O and the width in the same
 * layout runs in a swap: with the switch on O holds the 4:3 value (40) everywhere, and nothing
 * mixes it with the real k. The trick score was the case that showed it: unwrapped, its top-tier
 * sprite sat at x = 16 + 0.625 x (16-320) - 40 - 1 = -215 at 2340x1080 for its last second,
 * against 55 in the 4:3 layout and -42.5 at the port's. The other readers of O read it alone
 * and so follow it into the 4:3 column (the game-over countdown 0x00451B70, the animal counter's
 * two-player branches 0x00489470 and 0x0048948A), or set it to a fixed value around their own
 * drawing and put the 4:3 one back (the results' 0x0044FD90, 0x00450B10 and 0x00452CD0, the
 * two-player countdown 0x004516E0, BreathCounterP 0x0046BA80, the animal counter's one-player
 * branch, 0x006D9B50, 0x00724780, 0x00724BD0 and 0x00736A20), so they keep the port's positions
 * (private/generated/s8/hud43-fold/: oevents.py, oroots.py and leaks.py list every access to O,
 * the display roots that reach a relative read of it, and every global a site writes that code
 * outside the swaps reads).
 *
 * Checked (private/generated/s8/widescreen-hud43/, private/generated/s8/hud43-fold/): City
 * Escape at 2340x1080, 2560x1080 and 3120x1440, the central 4:3 column of every gameplay frame
 * from the board's start to past its end equals the 4:3 run's frame (1440x1080, 1920x1440) pixel
 * for pixel; at 2856x1280 every HUD draw's canvas extents equal the 4:3 run's; menus, events,
 * loading and pause frames are byte-identical with the switch on and off. The trick score, in a
 * test-only build that calls AddTechniqueScore with a 2000 and a 1000 bonus (hud43-fold
 * make_test_src.py): at 2560x1080 and 3120x1440 the central 4:3 column equals the 4:3 run's
 * through both phases of both, and at 2856x1280 every draw's canvas extents equal the 1440x1080
 * run's. The kart HUD, the time extension, the boss rush results and the countdown are wrapped
 * by the rule above and were not shown on screen. Left at the port's
 * positions (each reads the width but not O, or sets O itself around its drawing): the boss gauge
 * (0x00761D80), the animal counter (0x004893C0), Extra (0x00487D10), the event message windows
 * (0x005FF640), the stage map (0x00677350), the stage title card's strip (0x004724C0),
 * BreathCounterP 0x0046BA80, 0x006D9B50, 0x00724780, 0x00724BD0 and 0x00736A20.
 *
 * The guards. A function that turns canvas units into pixels or sets the projection has to see
 * the real H even when a HUD site reaches it, through a direct call or one of the HUD trees'
 * 66-143 indirect call sites: the viewport 0x00420210 and scissor 0x00420390 setters (canvas x
 * times H/640), the viewport getter 0x004200F0 (pixels back to canvas units, its pair), Ninja's
 * screen 0x0077E980 (njSetScreen: the clip window's half-width times k), the projection
 * 0x0042B230 (aspect (H/640)/(V/480) times its argument) and the full-screen background
 * 0x0041B0C0 (x from -320(k-1)). Each guard writes the real H for its own call when a swap is
 * active and the swapped value back after, and does nothing otherwise.
 *
 * Threads: guest threads run one at a time and hand off only inside runtime entry points
 * (runtime/sa2_threads.h). A hand-off inside a swap would let another thread read the 4:3 H; a
 * run with the switch on prints one line the first time a swap spans a hand-off
 * (sa2_sched_switches moved), naming the site. No site's direct-call tree reaches the guest's
 * AsynDo poll 0x00427EE0 or SOC_AsynDoWithNowLoading 0x00428470 (the event message windows'
 * 0x005FF640 reaches a loading wait, so it is not a site); the imports they reach are the C
 * library's, the same set as the score window's (hud43-fold trees.py).
 *
 * The setting, read once: SA2_PLAY_HUD43=1 turns it on; unset, empty or 0 leaves it off, and any
 * other value leaves it off with one line. Ignored when SA2_REPLAY_TICKS or SA2_REPLAY_WORDS is
 * set; tools/s4/device_profile.py OWNED strips it from every replay's environment besides. With
 * it on, the run prints one line at the first swap ("widescreen: HUD at 4:3: H 2340 -> 1440 in
 * the HUD's layout (SA2_PLAY_HUD43=1)"), one when the screen is no wider than 4:3 (nothing to
 * do), and one the first time each site acts. */

#define HUD43_H_VA 0x0174BA64u   /* HorizontalResolution, float */
#define HUD43_V_VA 0x0174BA68u   /* VerticalResolution, float */

enum { HUD43_LAYOUT = 0, HUD43_GUARD = 1 };

/* X(va, kind, what): every wrapped site. The va is the symbol's spelling (sub_<va>). */
#define HUD43_SITES(X)                                                                          \
    X(44e9c0, HUD43_LAYOUT, "the score window: score, time, rings, lives (0x0044E9C0)")        \
    X(7945c0, HUD43_LAYOUT, "the action window, one prompt (0x007945C0)")                     \
    X(794cb0, HUD43_LAYOUT, "the action window, two prompts (0x00794CB0)")                    \
    X(44d360, HUD43_LAYOUT, "the trick score (DispTechniqueScore 0x0044D360)")                \
    X(61bba0, HUD43_LAYOUT, "the kart race's HUD (layout_Disp 0x0061BBA0)")                   \
    X(61b6c0, HUD43_LAYOUT, "the kart race's time extension (timeExt_Disp 0x0061B6C0)")       \
    X(4526d0, HUD43_LAYOUT, "the boss rush results (0x004526D0)")                             \
    X(6f41b0, HUD43_LAYOUT, "a countdown placed from the HUD's origin (0x006F41B0)")          \
    X(420210, HUD43_GUARD, "the viewport setter (0x00420210)")                                 \
    X(420390, HUD43_GUARD, "the scissor setter (0x00420390)")                                  \
    X(4200f0, HUD43_GUARD, "the viewport getter (0x004200F0)")                                 \
    X(77e980, HUD43_GUARD, "njSetScreen (0x0077E980)")                                         \
    X(42b230, HUD43_GUARD, "the projection (0x0042B230)")                                      \
    X(41b0c0, HUD43_GUARD, "the full-screen background (0x0041B0C0)")

enum {
#define HUD43_ENUM(va, kind, what) HUD43_SITE_##va,
    HUD43_SITES(HUD43_ENUM)
#undef HUD43_ENUM
    HUD43_NSITES
};

typedef struct {
    int kind;
    const char *what;
    uint64_t acted;        /* calls inside the screen's swap (a layout site's own included) */
} hud43_site;

static hud43_site g_hud43_sites[HUD43_NSITES] = {
#define HUD43_ROW(va, kind, what) {kind, what, 0},
    HUD43_SITES(HUD43_ROW)
#undef HUD43_ROW
};

typedef void *(*hud43_lifted)(void *state, uint32_t pc, void *memory);

static int g_hud43 = -1;            /* -1 not read yet, 0 off, 1 on */
static int g_hud43_swapped;         /* a layout site's swap is active */
static uint32_t g_hud43_real;       /* the real H's bits while swapped */
static uint32_t g_hud43_value;      /* the 4:3 H's bits while swapped */
static uint32_t g_hud43_v;          /* V's bits the 4:3 value was made from */
static int g_hud43_handoff_said, g_hud43_narrow_said;

static float hud43_f(uint32_t b) { float f; memcpy(&f, &b, 4); return f; }
static uint32_t hud43_b(float f) { uint32_t b; memcpy(&b, &f, 4); return b; }

/* The guest's k for H and V, as its x87 code at PC24 computes it: each quotient rounded to
 * float (tools/s8/f80_double.py's double mode and the exact build agree on it). */
static float hud43_k(float h, float v) {
    volatile float a = h / 640.0f, b = v / 480.0f;
    return a / b;
}

/* The 4:3 H for a V: the largest float not above V x 4/3 whose k is not above 1. Exposed for
 * the unit test. */
uint32_t sa2_hud43_value(uint32_t v_bits) {
    const float v = hud43_f(v_bits);
    float h = (float)((double)v * 4.0 / 3.0);
    if ((double)h > (double)v * 4.0 / 3.0) h = hud43_f(hud43_b(h) - 1u);
    for (int i = 0; i < 8 && hud43_k(h, v) > 1.0f; i++) h = hud43_f(hud43_b(h) - 1u);
    return hud43_b(h);
}

/* What SA2_PLAY_HUD43 asks: 1 on, 0 off; `line` gets a line to print, or "". Exposed for the
 * unit test. */
int sa2_hud43_setting(const char *value, int replay, char *line, size_t n) {
    line[0] = 0;
    if (!value || !*value || (value[0] == '0' && !value[1])) return 0;
    if (replay) {
        snprintf(line, n, "widescreen: SA2_PLAY_HUD43=%s ignored in a replay (SA2_REPLAY_TICKS or "
                 "SA2_REPLAY_WORDS is set)", value);
        return 0;
    }
    if (value[0] == '1' && !value[1]) return 1;
    snprintf(line, n, "widescreen: SA2_PLAY_HUD43=%s is not 0 or 1; the HUD keeps the port's "
             "positions", value);
    return 0;
}

static int hud43_on(void) {
    if (g_hud43 < 0) {
        char line[200];
        const int replay = getenv("SA2_REPLAY_TICKS") != NULL || getenv("SA2_REPLAY_WORDS") != NULL;
        g_hud43 = sa2_hud43_setting(getenv("SA2_PLAY_HUD43"), replay, line, sizeof line);
        if (line[0]) fprintf(stderr, "%s\n", line);
    }
    return g_hud43;
}

static void hud43_first(int site, const char *as) {
    if (g_hud43_sites[site].acted++ == 0)
        fprintf(stderr, "widescreen: HUD at 4:3: %s %s\n", g_hud43_sites[site].what, as);
}

static void *hud43_call(hud43_lifted real, int site, void *state, uint32_t pc, void *memory) {
    if (!hud43_on()) return real(state, pc, memory);
    if (g_hud43_sites[site].kind == HUD43_GUARD) {
        if (!g_hud43_swapped) return real(state, pc, memory);
        uint32_t prev = 0;
        if (sa2_peek(HUD43_H_VA, 4, &prev) != SA2_OK) return real(state, pc, memory);
        hud43_first(site, "runs with the real width inside the HUD's layout");
        sa2_poke(HUD43_H_VA, 4, &g_hud43_real);
        void *ret = real(state, pc, memory);
        sa2_poke(HUD43_H_VA, 4, &prev);
        return ret;
    }
    uint32_t prev = 0, v = 0;
    if (sa2_peek(HUD43_H_VA, 4, &prev) != SA2_OK || sa2_peek(HUD43_V_VA, 4, &v) != SA2_OK)
        return real(state, pc, memory);
    const int outer = !g_hud43_swapped;
    uint64_t switches = 0;
    if (outer) {
        const float h = hud43_f(prev), fv = hud43_f(v);
        if (!(fv > 0.0f && (double)h * 3.0 > (double)fv * 4.0)) {     /* no wider than 4:3 */
            if (!g_hud43_narrow_said++)
                fprintf(stderr, "widescreen: HUD at 4:3: the screen %.0fx%.0f is no wider than "
                        "4:3; nothing to move\n", (double)h, (double)fv);
            return real(state, pc, memory);
        }
        if (v != g_hud43_v || !g_hud43_value) {
            g_hud43_value = sa2_hud43_value(v);
            g_hud43_v = v;
            fprintf(stderr, "widescreen: HUD at 4:3: H %.0f -> %.4f in the HUD's layout (k %.8f, "
                    "SA2_PLAY_HUD43=1)\n", (double)h, (double)hud43_f(g_hud43_value),
                    (double)hud43_k(hud43_f(g_hud43_value), fv));
        }
        g_hud43_real = prev;
        g_hud43_swapped = 1;
        switches = sa2_sched_switches();
    }
    hud43_first(site, "laid out at 4:3");
    sa2_poke(HUD43_H_VA, 4, &g_hud43_value);
    void *ret = real(state, pc, memory);
    sa2_poke(HUD43_H_VA, 4, &prev);
    if (outer) {
        g_hud43_swapped = 0;
        if (sa2_sched_switches() != switches && !g_hud43_handoff_said++)
            fprintf(stderr, "widescreen: HUD at 4:3: a guest thread hand-off happened inside %s; "
                    "another thread could have read the 4:3 width\n", g_hud43_sites[site].what);
    }
    return ret;
}

#define HUD43_WRAP(va, kind, what)                                                              \
    void *__real_sub_##va(void *state, uint32_t pc, void *memory);                              \
    __attribute__((used, retain))                                                               \
    void *__wrap_sub_##va(void *state, uint32_t pc, void *memory) {                             \
        return hud43_call(__real_sub_##va, HUD43_SITE_##va, state, pc, memory);                 \
    }
HUD43_SITES(HUD43_WRAP)
#undef HUD43_WRAP
