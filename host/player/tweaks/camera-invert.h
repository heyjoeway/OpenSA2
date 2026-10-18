// camera-invert.h: the host tweak camera-invert (camera-invert.cpp says what it does), called by
// player.cpp's send_pads on every set of pad slots it sends.
#pragma once

#include "../input.h"

namespace sa2tw {

// With camera-invert on, each slot's right stick X negated and its triggers swapped, in place;
// otherwise `set` is left as it is.
void camera_invert(sa2_input::PadSet* set);

}  // namespace sa2tw
