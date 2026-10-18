// deadzone.cpp: the host tweak "deadzone", a round deadzone and a full-reach outer edge for each
// gamepad's left stick (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST; "Add a host tweak").
//
// SDL gives a pad's stick raw: a worn stick at rest reads a little off centre, and many never reach
// the edge of their range on a diagonal. With the tweak on, the player sends the game each
// physical pad's left stick (LX, LY) measured by its distance r from the centre, its direction
// kept:
//
//   r <= kInner           the centre, (0, 0): a resting stick's drift in any direction;
//   kInner < r < kOuter   scaled from 0 at kInner to the full 32767 at kOuter, so the stick's whole
//                         travel is used and there is no step at the deadzone's edge;
//   r >= kOuter           the full 32767: a stick that falls short of its edge, or a square gate's
//                         corner, reaches full tilt, and no direction reaches further than another.
//
// The stick goes to the game in its gamepad record (input.h send_pads). What the game then does
// with it is the game's: the HD port's XInput-to-pad conversion, and WriteAnalogs (0x00441EF0) with
// its square per-axis deadzone of 14, run as before. This tweak makes the stick round before
// them; it changes nothing of the game's, so a replay, which carries the game's own input, is the
// same with it or without it.
//
// Left alone, and said once by name: the right stick (the left stick moves the character, which is
// what a deadzone is for), and the touch overlay's stick, which already has a small round
// deadzone of its own and a round edge (touch_overlay.cpp recompute): player.cpp calls this on the
// physical pads before slot 0 is shared with the overlay.
#include "deadzone.h"

#include <cmath>
#include <cstdio>

#include "../tweaks.h"

namespace sa2tw {

namespace {

constexpr double kFull = 32767.0;          // a stick's full tilt, XInput's
constexpr double kInner = 0.12 * kFull;    // 3932.04: the deadzone's radius
constexpr double kOuter = 0.95 * kFull;    // 31128.65: the radius that is full tilt

bool said = false;

}  // namespace

void deadzone_stick(int16_t* x, int16_t* y) {
    const double fx = *x, fy = *y;
    const double r = std::hypot(fx, fy);
    if (r <= kInner) {
        *x = 0;
        *y = 0;
        return;
    }
    const double reach = r >= kOuter ? kFull : (r - kInner) / (kOuter - kInner) * kFull;
    // |fx| <= r, so neither axis passes 32767 in magnitude.
    *x = int16_t(std::lround(fx / r * reach));
    *y = int16_t(std::lround(fy / r * reach));
}

void deadzone(sa2_input::PadSet* set) {
    if (!on(SA2_TWEAK_DEADZONE)) return;
    if (!said) {
        said = true;
        std::fprintf(stderr,
                     "sa2 host tweaks: round stick deadzone: each gamepad's left stick, centre to %.0f%% of its "
                     "reach (%d) at rest, full tilt from %.0f%% (%d), scaled between; the right stick and the "
                     "touch overlay's stick are left as they are\n",
                     kInner / kFull * 100, int(kInner), kOuter / kFull * 100, int(std::ceil(kOuter)));
    }
    for (sa2_input::PadState& s : set->slot)
        if (s.connected) deadzone_stick(&s.lx, &s.ly);
}

}  // namespace sa2tw
