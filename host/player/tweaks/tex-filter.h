// tex-filter.h: the host tweak tex-filter (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST), its values and
// its report.
//
// Subtitle: with SA2_TWEAKS=tex-filter in play, the sdl3-gpu player (sa2_player_gpu, every
// platform's) makes the game's samplers as DXVK does with two of its own options set,
// d3d9.samplerAnisotropy = kAnisotropy and d3d9.samplerLodBias = kLodBias (d3d9_options.cpp:52,72;
// host/d3d9gpu/d9g_sampler.cpp map_sampler mirrors the rule, d3d9_device.cpp:7242-7264): a stage
// whose texture has more than one mip level and a mip filter has kLodBias added to the game's own
// bias (its mip level chosen a quarter sharper), and, when its min filter is above POINT, is
// filtered anisotropically, kAnisotropy times. The game is not touched: its D3DSAMP_* values are the
// same, and the change is in the description the device makes of them
// (host/d3d9gpu/d9g_device_draw.cpp sampler_options, which tests the bit).
//
// What it leaves alone, by DXVK's rule: a texture with one level, which has no mip chain to bias
// (the render targets the game samples among them), a stage with no mip filter, and the dxvk-native
// player (sa2_player), whose DXVK configuration is a pinned file it verifies (player.cpp refuses
// DXVK_CONFIG). A 2D draw of a mip-mapped texture at its own size or larger is drawn as it was: its
// footprint is square, so anisotropy adds nothing, and its level of detail is at or below 0, where
// the bias still picks level 0. So the tweak does not tell 2D draws apart. In the recorded Chao
// Garden stream [private: the s5 E garden run] every textured stage is LINEAR in all three filters,
// and 30 of its 35 distinct samplers have a texture with mip levels; the 4 single-level ones carry
// about 0.96 million of its draws.
//
// kAnisotropy: 16, DXVK's own cap (setAniso, dxvk_sampler.h:71-73) and Vulkan's least
// maxSamplerAnisotropy on a device with samplerAnisotropy, so no device that has the feature limits
// it lower. A device without the feature (some phones' GPUs) gets its samplers without anisotropy
// (host/d3d9gpu/d9g_gpu_sdl.cpp, Report::anisotropy_dropped_samplers), which the tweak says once by
// name; the bias stays. kLodBias: -0.25, a quarter of a level: anisotropy keeps the textures along a
// floor sharp, and the bias keeps the ones that face the camera a little sharper too, short of the
// shimmer a whole level would bring.
#pragma once

#include "../tweaks.h"

namespace sa2tw::tex_filter {

inline constexpr int kAnisotropy = 16;
inline constexpr float kLodBias = -0.25f;

// The tweak's line, "sa2 host tweaks: texture filter 16x: ...", when it is on (otherwise nothing):
// what it does, and what it leaves alone by name. player.cpp's main calls it once, after reading
// SA2_TWEAKS.
void start();

}  // namespace sa2tw::tex_filter
