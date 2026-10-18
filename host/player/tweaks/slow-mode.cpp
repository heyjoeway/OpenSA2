// slow-mode.cpp: the host tweak slow-mode, the game at three quarters of real time.
//
// The game never sees it. The guest reads only its virtual clock, a tick of 16,666 us per frame
// whatever the wall clock does, and in play it is held to real time by the stream alone: the
// player's --pace presents each frame at t0 + (v - v0) / speed (pace.h), reads no further while it
// waits, and the relay (host/launch/sa2_relay.h; the Android session's) holds the game in its
// next Present's write once its lead of frames is queued. So a schedule at speed 0.75 holds the
// game to 0.75 virtual seconds a wall second, with the relay's lead unchanged, and every frame,
// tick and input it computes is the one it computes at full speed. The stage timer, which counts
// ticks, then advances 0.75 s a wall second.
//
// The sound is rendered per FRAME's virtual time (mixer.h): each block player.cpp queues holds the
// sound of its frame's virtual span, which at 0.75 is shown over 1/0.75 of that in wall time. The
// device stream is given a frequency ratio of 0.75 (SDL_SetAudioStreamFrequencyRatio): SDL
// resamples it, consuming the queue at 0.75 of its rate, so the sound keeps to the picture and the
// queue neither drains nor grows. The pitch drops with the rate (a ratio of 0.75 is about five
// semitones). The blocks themselves, and so --audio-wav, stay at the game's virtual rate.
//
// The queue's lead: player.cpp queues kAudioLeadMs of silence at the first Present and holds its
// queue (SDL_GetAudioStreamQueued, bytes of the stream's input) at about that, which the device
// now plays in kAudioLeadMs / 0.75 of wall time. The live schedule's slack is the wall time a
// stall can last before the queue runs dry (pace.h), so it is scaled with it: kAudioLeadMs / 0.75.
// The queue's bound (kAudioQueueBound) and the report's queue depths stay in the input's
// milliseconds, the game's.
//
// The end: at a ratio other than 1, SDL's resampler holds the last few frames queued (seven, at
// 0.75) for the samples after them, and player.cpp's end waits for its queue to empty (at most its
// length and a second). finish() flushes the stream there, which plays them out with silence after.
//
// Off, and in every case it leaves alone, the schedule and the stream are the player's as they
// were. The cases it leaves alone, each said once:
//   - no schedule (play without --pace, the launcher's --no-pace): nothing holds the game to a
//     rate, so there is no rate to lower; neither the frames nor the sound are touched.
//   - no device stream (--no-sound, or a device that failed, which the report's "audio" names):
//     the frames are slowed and there is no sound to slow.
//   - the stream refuses the ratio: nothing is slowed, since frames at 0.75 beside sound at full
//     rate would starve the device.
#include "slow-mode.h"

#include <SDL3/SDL.h>

#include <cstdio>

#include "../tweaks.h"

namespace sa2tw {
namespace slow_mode {

namespace {
SDL_AudioStream* slowed_ = nullptr;   // the device stream start() slowed, which finish() flushes
}  // namespace

void start(std::unique_ptr<Sa2Pace>& pace, SDL_AudioStream* audio, double speed, double slack_s) {
    if (!on(SA2_TWEAK_SLOW_MODE)) return;
    if (!pace) {
        std::fprintf(stderr, "sa2 host tweaks: game at 0.75 speed: left alone: play without --pace, which nothing "
                             "holds to a rate; the frames and the sound are not slowed\n");
        return;
    }
    if (audio && !SDL_SetAudioStreamFrequencyRatio(audio, float(kSpeed))) {
        std::fprintf(stderr, "sa2 host tweaks: game at 0.75 speed: not slowed: SDL_SetAudioStreamFrequencyRatio(%g): "
                             "%s\n", kSpeed, SDL_GetError());
        return;
    }
    const double slack = slack_s >= 0.0 ? slack_s / kSpeed : slack_s;
    slowed_ = audio;
    pace = std::make_unique<Sa2Pace>(speed * kSpeed, slack);
    std::fprintf(stderr, "sa2 host tweaks: game at 0.75 speed: frames paced at speed %g (%g of the player's %g)",
                 speed * kSpeed, kSpeed, speed);
    if (slack >= 0.0) std::fprintf(stderr, ", the live schedule's slack %.0f ms", slack * 1e3);
    if (audio)
        std::fprintf(stderr, "; the sound played at %g of its rate (SDL frequency ratio %g; pitch lower), its "
                             "queue's lead %g ms of the game's sound\n",
                     kSpeed, double(SDL_GetAudioStreamFrequencyRatio(audio)), slack_s * 1e3);
    else
        std::fprintf(stderr, "; left alone: the sound, since there is no device stream (--no-sound, or the device "
                             "failed: the report's audio)\n");
}

void finish(SDL_AudioStream* audio) {
    if (!on(SA2_TWEAK_SLOW_MODE) || !audio || audio != slowed_) return;
    if (!SDL_FlushAudioStream(audio))
        std::fprintf(stderr, "sa2 host tweaks: game at 0.75 speed: SDL_FlushAudioStream at the end: %s; the end "
                             "waits for the queue's last frames until its limit\n", SDL_GetError());
}

}  // namespace slow_mode
}  // namespace sa2tw
