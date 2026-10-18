// vanilla-profile.h: the launcher's vanilla profile (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST,
// vanilla-profile.cpp has what it does).
#pragma once

#include <string>

#include "tweaks.h"

namespace sa2tw {

// The launcher's reading of `value` (game.env's SA2_TWEAKS, which the game and the player are
// given), set as g_on; `why_off` (sa2tw::off's answer for the game's environment, nullptr in play)
// turns every tweak off, as read() takes it. With vanilla-profile on, `value` becomes "vanilla-profile" alone, `said` the
// tweak's own line, which names every name it dropped, and the reading is of the new value: the
// launcher's own tweaks are off as the game's and the player's are. Off, `value` stays as it was
// and `said` is "".
Reading launcher_reading(std::string& value, std::string& said, const char* why_off);

}  // namespace sa2tw
