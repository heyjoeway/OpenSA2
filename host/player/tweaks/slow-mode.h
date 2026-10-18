// slow-mode.h: the host tweak slow-mode (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST), which plays the
// game at three quarters of real time. slow-mode.cpp has what it does and why.
#pragma once

#include <memory>

#include "../pace.h"

struct SDL_AudioStream;

namespace sa2tw {
namespace slow_mode {

// The pace slow-mode plays at: of the speed the player was given (--speed, 1 in play).
inline constexpr double kSpeed = 0.75;

// player.cpp's first call, once the live stream is open, its schedule made (`pace`, null without
// --pace) and the sound's device stream open (`audio`, null without --audio or when the device
// failed), before the first record is read. `speed` is the player's --speed and `slack_s` the
// schedule's slack (the sound's lead, kAudioLeadMs, in seconds; < 0 for a file's replay, which
// play never is). With the tweak off it does nothing.
void start(std::unique_ptr<Sa2Pace>& pace, SDL_AudioStream* audio, double speed, double slack_s);

// player.cpp's call at the sound's end (finish_audio), before it waits for the device stream's
// queue to empty: with the tweak on and the stream slowed, the stream is flushed, since SDL's
// resampler otherwise holds its last few frames for samples that never come, and the queue never
// empties. With the tweak off, or the stream left alone, it does nothing.
void finish(SDL_AudioStream* audio);

}  // namespace slow_mode
}  // namespace sa2tw
