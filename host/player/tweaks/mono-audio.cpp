// mono-audio.cpp: the mono-audio host tweak (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST; home: the
// player). Both speakers play the same sound, the left and right channels mixed, for a player with
// one earbud or hearing in one ear.
//
// Where it acts: player.cpp's audio_block, on the block of sound the mixer rendered up to a Present
// (host/player/mixer.h: 48 kHz, interleaved L R float32), before anything reads it. So the fold is
// after the mixer's clamp and, in live play, its limiter, and before both of the block's readers:
// --audio-wav and SDL's playback stream. Every block of the session goes through audio_block, the
// one before the first Present (the WAV's alone) and the last, after it, included. The Android
// session runs the same player main, so this one change is the sound on every platform.
//
// What it does: each pair becomes m = 0.5 L + 0.5 R in both channels. Each half is exact (a float
// times a power of two), and |m| <= max(|L|, |R|) <= 1, so the fold cannot clip a mix the limiter or
// the clamp has already held in [-1, 1]. A sound the game panned to one side plays at half its level
// in each speaker; one in the middle (L = R) is unchanged.
//
// Swapping the channels is a tweak of its own, swap-audio (swap-audio.cpp), called right after this
// one; with both on, the swap has nothing to change and says so.
//
// Its line: at the mixer's first block with samples in it, once a session, "sa2 host tweaks: mono
// audio: ...". What it leaves alone, said there: the report's "audio" levels
// (ds, xa and mix peaks and RMS), which the mixer measures before the fold. A play session with no
// sound at all (the launcher's or the Android session's with sound off: no --audio or --audio-wav)
// has no mixer and no block; the reader's line still names the tweak on, and this file says
// nothing, since there is no sound to fold.
//
// Off (sa2tw::on false: SA2_TWEAKS does not name it, out of play, in a replay), it returns before
// reading anything, and the block is the mixer's to the byte.
#include "../tweaks.h"
#include "mono-audio.h"

#include "../mixer.h"

#include <cstdio>
#include <vector>

namespace sa2tw {

void mono_audio(Sa2Mixer& mixer) {
    if (!on(SA2_TWEAK_MONO_AUDIO)) return;
    std::vector<float>& b = mixer.block_to_change();
    if (b.empty()) return;
    // The mixer's first block with samples in it: every sample it has rendered is in this block.
    if (mixer.samples() == b.size() / Sa2Mixer::kChannels)
        std::fprintf(stderr, "sa2 host tweaks: mono audio: both channels play (L + R) / 2, after the limiter, in "
                             "the speakers and --audio-wav; the report's audio levels are the mix before it\n");
    for (size_t i = 0; i + 1 < b.size(); i += 2) {
        const float m = 0.5f * b[i] + 0.5f * b[i + 1];
        b[i] = m;
        b[i + 1] = m;
    }
}

}  // namespace sa2tw
