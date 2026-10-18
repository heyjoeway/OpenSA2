/* fov: the gameplay camera shows a wider (or narrower) field of view, 1.25 times the game's own
 * angle by default, or SA2_FOV_SCALE times it (0.8 to 1.4); the HUD, events, menus and every other
 * view keep the game's angle (runtime/sa2_tweaks.h: the tweaks and their rules).
 *
 * TWEAK_WRAPS: sub_42b300
 *
 * Where the angle goes, read from the unpacked image. SetViewStuff (0x0042B300, 242 bytes) takes
 * the vertical view angle in ESI (Ninja units, 0x10000 a full turn) and three floats on the stack;
 * it calls ___njSetPerspective (0x0042C390) with EAX = ESI, which stores the angle at 0x025EFEE8
 * and a derived value at 0x025EFEE4, calls the projection 0x0042B230 (the widescreen guard's), and
 * from ESI/2 again computes the screen's half-extents at 0x02670300-0x0267031C. It never writes
 * ESI. Its three callers, each linked against it directly (no address of it is taken):
 *
 *   0x0077E1A0  CameraApplyView (0x0077E0C0), ESI = [EBX+0x1C], return address 0x0077E1A5. It has
 *               13 callers: the gameplay camera's CameraApplyScreenView (0x004EB550, its call at
 *               0x004EB630, return address 0x004EB635) and ResetViewStuff, the event executors
 *               (0x005FA160, 0x00601010, 0x00601690) and menu and title views.
 *               CameraApplyScreenView sets EBX to camera i's view (0x01DD00AC + i x 0x24D8) and
 *               [EBX+0x1C] to CameraData+0x0 (0x01DCFF40 + i x 0x24D8), the angle the camera's own
 *               easing (CameraUpdateFOV 0x004EC920) leaves.
 *   0x00458FF5  0x00458EE0, the HUD's "restore the view" (23 HUD display callers), ESI = the angle
 *               0x00458EA0 saved from 0x025EFEE8 (at 0x0194571C).
 *   0x0044BB90  a third (FUN_0044BB90), which sets its own view.
 *
 * The wrap acts only when the call came from CameraApplyView ([ESP] = 0x0077E1A5) and that call
 * from CameraApplyScreenView ([ESP+0x64] = 0x004EB635: CameraApplyView's frame is 0x4C of locals,
 * ESI and EDI pushed, and 12 bytes of arguments), and GameMode (0x0174B040) is 5, a level. Then it
 * sets ESI to the scaled angle, calls the lifted body, and puts the game's ESI back (the caller's
 * register, which SetViewStuff leaves alone). Every other view is the game's: events and menus go
 * through CameraApplyView from other callers. The HUD's save and restore pass back the angle they
 * saved, the scaled one, unscaled, so nothing compounds; wrapping ___njSetPerspective itself would
 * scale each restore again.
 *
 * The scaled angle: (angle x k_milli + 500) / 1000 in integers (no floating point, so every host
 * agrees), held within 0x1000-0x7000 (22.5 to 157.5 degrees) unless the game's own angle is already
 * beyond that bound, when the game's is kept. k_milli is SA2_FOV_SCALE read once, as a decimal with
 * at most three places, 0.8 to 1.4; unset or empty, 1.25. A value that does not read is named and
 * the default used; one outside the range is named and held to it. An angle of 0 or below is left.
 *
 * Off: the wrap's first test is sa2_tweak_on, and then it only calls __real_sub_42b300; nothing is
 * read, SA2_FOV_SCALE included.
 *
 * Risks. A wider frustum can bring objects into view that the game's own "on screen" tests would
 * not, and those can change what runs; it is gameplay until a tick comparison says otherwise. The
 * two return addresses are the image's; a build whose code moved would make the wrap leave every
 * view alone, never scale a wrong one. The 2P split-screen cameras take the same path and are
 * scaled alike.
 *
 * The run says what it does: once, the first scaled angle ("sa2 tweaks: fov <k>: the gameplay
 * camera's angle 0x2E38 -> 0x39C6"), once the first time a GameMode other than 5 reaches the
 * gameplay camera's call, once the first clamp, and once an unreadable guest stack or GameMode.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#include "cpu_state.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_tweaks.h"

#define FOV_APPLY_VIEW_RET   0x0077E1A5u   /* after CameraApplyView's call of SetViewStuff */
#define FOV_SCREEN_VIEW_RET  0x004EB635u   /* after CameraApplyScreenView's call of CameraApplyView */
#define FOV_SCREEN_VIEW_SLOT 0x64u         /* where that return address is from SetViewStuff's ESP */
#define FOV_GAME_MODE        0x0174B040u   /* GameMode, int */
#define FOV_GAME_MODE_LEVEL  5u
#define FOV_MIN              0x1000u
#define FOV_MAX              0x7000u
#define FOV_DEFAULT_MILLI    1250u
#define FOV_LOW_MILLI        800u
#define FOV_HIGH_MILLI       1400u

static unsigned g_fov_milli;       /* 0 until read */
static int g_fov_said, g_fov_mode_said, g_fov_clamp_said, g_fov_stack_said, g_fov_angle_said;

/* SA2_FOV_SCALE as thousandths: digits, an optional point and at most three more digits; -1 when
 * it is not that. */
static long fov_parse_milli(const char *s) {
    long whole = 0, frac = 0;
    int nwhole = 0, nfrac = 0;
    while (*s >= '0' && *s <= '9' && nwhole < 4) whole = whole * 10 + (*s++ - '0'), nwhole++;
    if (*s == '.') {
        s++;
        while (*s >= '0' && *s <= '9' && nfrac < 3) frac = frac * 10 + (*s++ - '0'), nfrac++;
    }
    if (*s || (nwhole == 0 && nfrac == 0)) return -1;
    while (nfrac < 3) frac *= 10, nfrac++;
    return whole * 1000 + frac;
}

static unsigned fov_milli(void) {
    if (g_fov_milli) return g_fov_milli;
    const char *v = getenv("SA2_FOV_SCALE");
    g_fov_milli = FOV_DEFAULT_MILLI;
    if (v && *v) {
        const long m = fov_parse_milli(v);
        if (m < 0)
            fprintf(stderr, "sa2 tweaks: fov: SA2_FOV_SCALE=%s is not a number like 1.25, so the "
                    "scale is 1.25\n", v);
        else if (m < FOV_LOW_MILLI || m > FOV_HIGH_MILLI) {
            g_fov_milli = m < FOV_LOW_MILLI ? FOV_LOW_MILLI : FOV_HIGH_MILLI;
            fprintf(stderr, "sa2 tweaks: fov: SA2_FOV_SCALE=%s is outside 0.8 to 1.4, so the scale "
                    "is %u.%03u\n", v, g_fov_milli / 1000, g_fov_milli % 1000);
        } else g_fov_milli = (unsigned)m;
    }
    return g_fov_milli;
}

/* The angle the gameplay camera gets for the game's `angle` at scale k_milli. Exposed for the unit
 * test (Documentation/scripts/tests/test_tweak_fov.py). */
uint32_t sa2_fov_scaled(uint32_t angle, unsigned k_milli) {
    if ((int32_t)angle <= 0) return angle;
    uint64_t s = ((uint64_t)angle * k_milli + 500u) / 1000u;
    if (s > FOV_MAX) s = angle > FOV_MAX ? angle : FOV_MAX;
    if (s < FOV_MIN) s = angle < FOV_MIN ? angle : FOV_MIN;
    return (uint32_t)s;
}

void *__real_sub_42b300(void *state, uint32_t pc, void *memory);

__attribute__((used, retain))
void *__wrap_sub_42b300(void *state, uint32_t pc, void *memory) {
    if (!sa2_tweak_on(SA2_TWEAK_FOV)) return __real_sub_42b300(state, pc, memory);
    const uint32_t esp = sa2_rt_get_gpr(state, SA2_ESP);
    uint32_t site = 0, outer = 0, mode = 0;
    if (sa2_peek(esp, 4, &site) != SA2_OK) {
        if (!g_fov_stack_said++)
            fprintf(stderr, "sa2 tweaks: fov: the guest stack at 0x%08X does not read, so the caller "
                    "is unknown and the view keeps the game's angle\n", esp);
        return __real_sub_42b300(state, pc, memory);
    }
    if (site != FOV_APPLY_VIEW_RET) return __real_sub_42b300(state, pc, memory);
    if (sa2_peek(esp + FOV_SCREEN_VIEW_SLOT, 4, &outer) != SA2_OK) {
        if (!g_fov_stack_said++)
            fprintf(stderr, "sa2 tweaks: fov: the guest stack at 0x%08X does not read, so the caller "
                    "is unknown and the view keeps the game's angle\n", esp + FOV_SCREEN_VIEW_SLOT);
        return __real_sub_42b300(state, pc, memory);
    }
    if (outer != FOV_SCREEN_VIEW_RET) return __real_sub_42b300(state, pc, memory);
    if (sa2_peek(FOV_GAME_MODE, 4, &mode) != SA2_OK) {
        if (!g_fov_mode_said++)
            fprintf(stderr, "sa2 tweaks: fov: GameMode at 0x%08X does not read, so the gameplay "
                    "camera's view keeps the game's angle\n", FOV_GAME_MODE);
        return __real_sub_42b300(state, pc, memory);
    }
    if (mode != FOV_GAME_MODE_LEVEL) {
        if (!g_fov_mode_said++)
            fprintf(stderr, "sa2 tweaks: fov: the gameplay camera's view outside a level (GameMode "
                    "%u) keeps the game's angle\n", mode);
        return __real_sub_42b300(state, pc, memory);
    }
    const unsigned k = fov_milli();
    const uint32_t angle = sa2_rt_get_gpr(state, SA2_ESI);
    const uint32_t scaled = sa2_fov_scaled(angle, k);
    if ((int32_t)angle <= 0 && !g_fov_angle_said++)
        fprintf(stderr, "sa2 tweaks: fov: the gameplay camera's angle 0x%X is not above 0, so it is "
                "left\n", angle);
    if (!g_fov_said++)
        fprintf(stderr, "sa2 tweaks: fov %u.%03u: the gameplay camera's angle 0x%X -> 0x%X\n",
                k / 1000, k % 1000, angle, scaled);
    if ((int32_t)angle > 0 && scaled != ((uint64_t)angle * k + 500u) / 1000u && !g_fov_clamp_said++)
        fprintf(stderr, "sa2 tweaks: fov: the angle 0x%X x %u.%03u is held at 0x%X (the bounds are "
                "0x%X to 0x%X)\n", angle, k / 1000, k % 1000, scaled, FOV_MIN, FOV_MAX);
    sa2_rt_set_gpr(state, SA2_ESI, scaled);
    void *ret = __real_sub_42b300(state, pc, memory);
    sa2_rt_set_gpr(state, SA2_ESI, angle);
    return ret;
}
