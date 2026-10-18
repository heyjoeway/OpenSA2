// deadzone.h: the host tweak "deadzone" (deadzone.cpp has what it does), as player.cpp calls it.
#pragma once

#include "../input.h"

namespace sa2tw {

// With the tweak on, each connected pad's left stick in `set` through a round deadzone and outer
// edge (deadzone.cpp); off, `set` as it is. player.cpp's send_pads calls it on the physical pads'
// slots, before slot 0 is shared with the touch overlay.
void deadzone(sa2_input::PadSet* set);

// One left stick, (x, y) as XInput has them (-32768..32767, up positive), through the deadzone:
// what deadzone() does to each pad, whether or not the tweak is on (its unit test calls it).
void deadzone_stick(int16_t* x, int16_t* y);

}  // namespace sa2tw
