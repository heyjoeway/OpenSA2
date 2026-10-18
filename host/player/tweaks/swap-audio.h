// swap-audio.h: the swap-audio host tweak's one call, from player.cpp's audio_block, after
// mono-audio's (swap-audio.cpp has what it does).
#pragma once

class Sa2Mixer;

namespace sa2tw {

// With swap-audio on, the two samples of each L R pair of the mixer's block trade places, before the
// block is written to --audio-wav and queued to the device; off, nothing.
void swap_audio(Sa2Mixer& mixer);

}  // namespace sa2tw
