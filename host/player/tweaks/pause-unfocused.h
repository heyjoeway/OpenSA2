// pause-unfocused.h: the player's host tweak "pause-unfocused" (pause-unfocused.cpp has what it does
// and why it is exact; runtime/sa2_tweaks.h lists it).
#pragma once

#include <SDL3/SDL.h>

#include <functional>

namespace sa2tw {

// Called at the end of every event pump (player.cpp, Player::pump_events). With the tweak on, in a
// `paced` session (the player's --pace: its relay holds the game to it), and once `win` has had
// input focus: while the window is out of focus, minimized, hidden or occluded, and `ending()` says
// nothing (it gives why the session is ending, or nullptr while it plays), it holds here, with
// `audio`'s device paused (when it was playing), calling `pump()` (the player's event pump) about
// every 100 ms, and returns when the window is back or `ending()` gives a reason, which its line
// names. Otherwise it returns at once; an unpaced session is never held (said once, by name).
void hold_while_unfocused(SDL_Window* win, SDL_AudioStream* audio, bool paced,
                          const std::function<const char*()>& ending, const std::function<void()>& pump);

}  // namespace sa2tw
