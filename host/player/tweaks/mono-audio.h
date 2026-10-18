// mono-audio.h: the mono-audio host tweak's one call, from player.cpp's audio_block
// (mono-audio.cpp has what it does).
#pragma once

class Sa2Mixer;

namespace sa2tw {

// With mono-audio on, each L R pair of the mixer's block becomes (L + R) / 2 in both channels, before
// the block is written to --audio-wav and queued to the device; off, nothing.
void mono_audio(Sa2Mixer& mixer);

}  // namespace sa2tw
