// tex-filter.cpp: the host tweak tex-filter's report (tex-filter.h has what it does). Its change is
// made in host/d3d9gpu (d9g_device_draw.cpp Device::mapped_sampler, d9g_gpu_sdl.cpp for a device
// without samplerAnisotropy), which tests sa2tw::on(sa2tw::SA2_TWEAK_TEX_FILTER) there.
#include "tex-filter.h"

#include <cstdio>

namespace sa2tw::tex_filter {

void start() {
    if (!on(SA2_TWEAK_TEX_FILTER)) return;
#ifdef SA2_PLAYER_GPU
    std::fprintf(stderr,
                 "sa2 host tweaks: texture filter 16x: mip bias %+.2f, and anisotropy %dx where the min filter is "
                 "above point, on each texture with mip levels sampled with a mip filter (DXVK's "
                 "d3d9.samplerLodBias and d3d9.samplerAnisotropy rule); textures with one level, and stages with "
                 "no mip filter, are left alone\n",
                 double(kLodBias), kAnisotropy);
#else
    std::fprintf(stderr,
                 "sa2 host tweaks: texture filter 16x: the dxvk-native player (sa2_player) is left alone: its DXVK "
                 "configuration is a pinned file it verifies; the tweak acts in the sdl3-gpu player (sa2_player_gpu)\n");
#endif
}

}  // namespace sa2tw::tex_filter
