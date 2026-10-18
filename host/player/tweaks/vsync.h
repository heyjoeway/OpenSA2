// vsync.h: the vsync tweak's one call, from the player's main (tweaks/vsync.cpp has what it does).
#pragma once

namespace sa2tw {

// The run's vsync option: true when the vsync tweak is on, else `asked`, the --vsync the player was
// given. Says what it did, once, on stderr.
bool vsync(bool asked);

}  // namespace sa2tw
