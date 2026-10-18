// hold-toggle.h: the host tweak "hold-toggle" (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST), B as a
// toggle. hold-toggle.cpp has what it does and why.
#pragma once

#include "../input.h"

namespace sa2tw::hold_toggle {

// Player::send_pads, with the four slots it is about to send to the game (each pad's and the touch
// controls' together) and whether the window has the input focus: B in each slot made a latch.
// Off, it does nothing.
void apply(sa2_input::PadSet* set, bool focused);

// Player::service_pads, after each pump: true when a send is owed because a latch is held while
// the window has lost the input focus (apply then lets it go). Off, false.
bool owed(bool focused);

// The state apply keeps, and the step it takes, for the tweak's own test
// (Documentation/scripts/tests/test_tweak_hold_toggle.py), which calls it with no reader.
struct Latches {
    uint16_t raw[sa2_input::kPadSlots] = {};      // the buttons each slot held at the last step
    uint16_t latched[sa2_input::kPadSlots] = {};  // the toggled outputs each slot holds
};
// One step: `set` as the player made it, changed into what the game is sent. Each change of a latch
// is said through `say` (nullptr: not said), "slot <k> B held" or "slot <k> B let go (<why>)".
void step(Latches* l, sa2_input::PadSet* set, bool focused, void (*say)(const char* line));

}  // namespace sa2tw::hold_toggle
