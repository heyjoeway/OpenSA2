// swap-audio.cpp: the swap-audio host tweak (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST; home: the
// player). The left channel plays from the right speaker and the right from the left, for
// headphones or speakers wired or worn the other way round.
//
// Where it acts: player.cpp's audio_block, on the block of sound the mixer rendered up to a Present
// (host/player/mixer.h: 48 kHz, interleaved L R float32), right after mono-audio's call and before
// anything reads the block. So the swap is after the mixer's clamp and, in live play, its limiter,
// and before both of the block's readers: --audio-wav and SDL's playback stream. Every block of the
// session goes through audio_block (mono-audio.cpp says which), and the Android session runs the same
// player main, so this one change is the sound on every platform.
//
// What it does: each pair (L, R) becomes (R, L). Nothing is computed, so every sample is the mixer's
// to the bit, only in the other channel.
//
// Its line: at the mixer's first block with samples in it, once a session, "sa2 host tweaks: swap
// audio: ...". What it leaves alone, said there: the report's "audio" levels, which the mixer
// measures before the swap; and, with mono-audio on as well, the sound itself, since mono-audio has
// made both channels the same and swapping them changes nothing. A play session with no sound at all
// has no mixer and no block; the reader's line still names the tweak on, and this file says nothing.
//
// Off (sa2tw::on false: SA2_TWEAKS does not name it, out of play, in a replay), it returns before
// reading anything, and the block is the mixer's (or mono-audio's) to the byte.
#include "../tweaks.h"
#include "swap-audio.h"

#include "../mixer.h"

#include <cstdio>
#include <utility>
#include <vector>

namespace sa2tw {

void swap_audio(Sa2Mixer& mixer) {
    if (!on(SA2_TWEAK_SWAP_AUDIO)) return;
    std::vector<float>& b = mixer.block_to_change();
    if (b.empty()) return;
    // The mixer's first block with samples in it: every sample it has rendered is in this block.
    if (mixer.samples() == b.size() / Sa2Mixer::kChannels) {
        if (on(SA2_TWEAK_MONO_AUDIO))
            std::fprintf(stderr, "sa2 host tweaks: swap audio: changes nothing with mono audio on, which plays the "
                                 "same sound in both channels\n");
        else
            std::fprintf(stderr, "sa2 host tweaks: swap audio: the left channel plays right and the right left, "
                                 "after the limiter, in the speakers and --audio-wav; the report's audio levels "
                                 "are the mix before it\n");
    }
    for (size_t i = 0; i + 1 < b.size(); i += 2) std::swap(b[i], b[i + 1]);
}

}  // namespace sa2tw
