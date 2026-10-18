// vsync.cpp: the vsync tweak (SA2_TWEAKS=vsync), the player's --vsync as a play setting.
//
// Subtitle: on, the play session presents as --vsync makes it: PresentationInterval ONE in place of
// IMMEDIATE. The player's main asks it once, after the host's reader, and the run is then the
// --vsync run in every respect (the device line's "interval ONE", the report's options.vsync). Off,
// the option is the one the player was given.
//
// What ONE gives, by host: DXVK (host 1) picks FIFO for a non-zero interval (share/dxvk.conf leaves
// d3d9.presentInterval to the application, -1); host 2 (host/d3d9gpu/d9g_entry.cpp,
// attach_window) claims the window with SDL_GPU_PRESENTMODE_VSYNC, which Vulkan always has (FIFO).
// Without it each takes IMMEDIATE, or MAILBOX where the window has no IMMEDIATE. So the mode it
// replaces depends on the window. Where IMMEDIATE is offered (X11, Windows), off can tear and on
// stops it. Android's swapchain and a Wayland compositor offer no IMMEDIATE, so there off is already
// MAILBOX and tear-free, and on only adds up to a refresh of delay. The game is unchanged: the pacer
// (--pace, pace.h) still drives its virtual clock and presentation is decoupled from it, so a frame
// waits at most one refresh more for the display. The community advice to turn driver vsync off and
// pace in the game is the player's default; this switch is for a player who sees tearing and will
// take the frame of latency.
//
// Cases it leaves alone, each said once by name: --vsync given already (the run is as it was), and
// host 2 with SA2_D3D9GPU_PRESENT=none (no window is presented to, so the interval changes nothing; the
// GPU player's build alone, SA2_PLAYER_GPU, since the DXVK player's binary holds no host 2 name).
#include "../tweaks.h"
#include "vsync.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>

namespace sa2tw {

bool vsync(bool asked) {
    if (!on(SA2_TWEAK_VSYNC)) return asked;
    if (asked) {
        std::fprintf(stderr, "sa2 host tweaks: vsync on: --vsync is given already, so the run is as it was\n");
        return true;
    }
#if SA2_PLAYER_GPU   // host 2's variable: the DXVK player (host/player/build.sh) names nothing of host 2's
    const char* present = std::getenv("SA2_D3D9GPU_PRESENT");
    if (present && std::strcmp(present, "none") == 0) {
        std::fprintf(stderr, "sa2 host tweaks: vsync on: PresentationInterval ONE in place of IMMEDIATE, but "
                             "SA2_D3D9GPU_PRESENT=none presents to no window, so it changes nothing\n");
        return true;
    }
#endif
    std::fprintf(stderr, "sa2 host tweaks: vsync on: PresentationInterval ONE in place of IMMEDIATE, as --vsync "
                         "(swapchain FIFO in place of IMMEDIATE, or of MAILBOX where the window has no IMMEDIATE)\n");
    return true;
}

}  // namespace sa2tw
