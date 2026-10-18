// camera-invert.cpp: the host tweak camera-invert (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST, home
// the player). The camera turns the other way: every pad record the player sends
// (player.cpp send_pads) has each slot's right stick X negated and its LT and RT swapped.
//
// The reading it rests on. The in-level camera's turn is digital: the camera function at
// 0x004F47F0 (Input Controls' CameraKnuckles) tests the player's Controllers entry
// (0x01DEFC08 + 0x34 * [0x01DD92A0]) for 0x20000 at 0x004F4D4B, adding 0x222 to the angle, and for
// 0x10000 at 0x004F4D86, subtracting it. The controller update 0x0077E780 makes them of actions
// 0x40 and 0x20 (the table at 0x009A2088, read by 0x0077E910), which the controller fill
// 0x00425700 gives a pad: action 0x40 for the pad's control 0xB (LT) and 0x20 for 0xC (RT) (the
// table at 0x008AE160), and the same two for the right stick's X (control 0xF) below and above its
// dead zone (0x004257AA, 0x004257C5). Its analog l and r (0x0042590B, 0x0042592F) are each
// trigger or twice the stick's X toward that side, whichever is further. So negating X and
// swapping the two triggers swaps L and R for every reader of them, the digital and the analog
// alike, and nothing else changes: the camera turns the other way in levels, and anywhere else a
// camera turns on L and R or on the analog l and r.
//
// Left alone, said once by name with the first set of slots it is given: the right stick's Y;
// and the scope, since the player does not know the game's mode, so every screen that reads the
// right stick's X or the triggers (menus, kart racing among them) reads them swapped too. The
// touch overlay's LT and RT and a physical button that SA2_PAD_MAP sends as LT or RT are triggers
// in the slot by then, and swap with the rest.
#include "../tweaks.h"
#include "camera-invert.h"

#include <cstdint>
#include <cstdio>
#include <utility>

namespace sa2tw {

void camera_invert(sa2_input::PadSet* set) {
    if (!on(SA2_TWEAK_CAMERA_INVERT)) return;
    static bool said = false;
    if (!said) {
        said = true;
        std::fprintf(stderr,
                     "sa2 host tweaks: camera turn inverted: every pad slot's right stick X negated and LT and RT "
                     "swapped, the touch overlay's and SA2_PAD_MAP's included; the right stick's Y is left alone; "
                     "not limited to levels (the player does not know the game's mode), so menus and every other "
                     "screen that reads the right stick's X or the triggers read them swapped too\n");
    }
    for (sa2_input::PadState& s : set->slot) {
        s.rx = s.rx == INT16_MIN ? INT16_MAX : int16_t(-s.rx);
        std::swap(s.lt, s.rt);
    }
}

}  // namespace sa2tw
