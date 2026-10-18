// frametime-overlay.cpp: the host tweak "frametime-overlay" (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST,
// home "player"). With it on, the sdl3-gpu player draws a small box over the window's top left
// corner at each Present, three lines of 5x7 text:
//
//   FPS  59.9            the presents per second over the last second (from the newest present at
//                        least kWindowS old, so a stall longer than that still has a rate)
//   MS  16.7 MAX  33.4   the mean time between two presents over that span, and the longest
//                        between two presents in a row (ms; no decimal from 1000)
//   LATE 3               the frames presented more than one 60 Hz frame (16.7 ms) after their time
//                        so far: pace.h's "over_frame", which the report's "pace" holds
//
// Where the numbers come from. With --pace (the launcher's and the Android session's play), from
// the player's schedule (pace.h): its count of paced presents, the time of the last one and its
// count of late frames, the numbers of the report's "pace" and of --pace-log's rows. Drawn during
// Present N, the box shows the presents up to N-1, the last the schedule has counted. Without
// --pace there is no schedule: the presents are timed at the box's own drawing (SDL_GetTicksNS)
// and LATE reads "--", said once. The numbers change at most every kRefreshS, so they can be read.
//
// How it is drawn: the text is rendered on the CPU into a small RGBA texture (kBoxW x kBoxH),
// uploaded when the numbers change, and blitted (SDL_BlitGPUTexture, nearest) at a whole-pixel
// scale onto the swapchain texture, in the window's present overlay, after the frame's blit and
// the touch controls. A blit takes no shader, so every SDL_gpu driver draws it. The blit is
// opaque, so the box never goes over a touch control: place() puts it in the top left corner of
// the window's safe area (SDL_GetWindowSafeArea, clear of a display cutout: Android lays the
// window out into the short edges' cutouts), and while the touch controls are drawn it moves down
// past those it meets (LT, at the top left in the default layout) or, failing that, goes top
// right. Its scale comes from the short side, so an upright phone draws it no larger than a
// landscape one. A new place is said once on stderr. It is drawn over
// the swapchain only: the frames file (--frames-out) and --snap read the frame back before
// Present, so they never hold the box, and neither does anything made from them (receipts).
// --overlay-out's frame-N-swapchain.png holds it, as it holds the touch controls, and
// frame-N-game.png does not.
//
// Cases it leaves alone, each said once by name: the dxvk-native host (sa2_player), whose Present
// is DXVK's and has no overlay hook, draws no box; a window whose safe area has no room for the box
// at its smallest clear of the touch controls gets none; a safe area SDL cannot give takes the whole
// window; a texture SDL cannot make stops the box with SDL's reason.
//
// What it makes on the device (the texture and its upload buffer) release() releases at teardown,
// while the device is still there; nothing of it outlives the device.
#include "frametime-overlay.h"

#include "../pace.h"
#include "../touch_overlay.h"
#include "../tweaks.h"

#include <SDL3/SDL_error.h>
#include <SDL3/SDL_timer.h>

#include <algorithm>
#include <cinttypes>
#include <cmath>
#include <cstdio>
#include <cstring>

namespace sa2_frametime {

namespace {

constexpr const char* kSaid = "sa2 host tweaks: frame-time overlay on: ";

// The glyphs, rows top to bottom, bit 4 the left column. Every character the lines use.
struct Glyph {
    char c;
    uint8_t rows[kGlyphH];
};
constexpr Glyph kFont[] = {
    {' ', {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00}},
    {'-', {0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00}},
    {'.', {0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C}},
    {'0', {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E}},
    {'1', {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E}},
    {'2', {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F}},
    {'3', {0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E}},
    {'4', {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02}},
    {'5', {0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E}},
    {'6', {0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E}},
    {'7', {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08}},
    {'8', {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E}},
    {'9', {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C}},
    {'A', {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11}},
    {'E', {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F}},
    {'F', {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10}},
    {'L', {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F}},
    {'M', {0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11}},
    {'P', {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10}},
    {'S', {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E}},
    {'T', {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04}},
    {'X', {0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11}},
};
constexpr uint8_t kInk[4] = {255, 255, 255, 255}, kBox[4] = {24, 24, 24, 255};

const Glyph* glyph(char c) {
    for (const Glyph& g : kFont)
        if (g.c == c) return &g;
    return &kFont[0];
}

// A value in five columns: one decimal below 1000, none from there (to 99999), "--" when it is not
// known.
std::string five(bool known, double v) {
    char b[32];
    if (!known) return "   --";
    v = std::min(std::max(v, 0.0), 99999.0);
    std::snprintf(b, sizeof b, v < 999.95 ? "%5.1f" : "%5.0f", v);
    return b;
}

// The process's box: one window, one device at a time.
struct State {
    bool said_setup = false, paced = false, failed = false, said_small = false, said_safe = false, dirty = true;
    uint32_t frame = 0;
    Spot spot;                           // the last place said
    Meter meter;
    SDL_GPUDevice* dev = nullptr;
    SDL_GPUTexture* tex = nullptr;
    SDL_GPUTransferBuffer* up = nullptr;
};
State& state() {
    static State s;
    return s;
}

void fail(State& s, const char* what) {
    s.failed = true;
    std::fprintf(stderr, "%snot drawn from here on: %s: %s\n", kSaid, what, SDL_GetError());
}

}  // namespace

bool Meter::add(uint64_t count, double at, uint64_t late, bool paced) {
    if (!window_.empty() && count <= window_.back().count) return false;
    window_.push_back({count, at});
    while (window_.size() > 1 && at - window_[1].at >= kWindowS) window_.pop_front();
    if (refreshed_ && at - refreshed_at_ < kRefreshS) return false;
    refreshed_ = true;
    refreshed_at_ = at;
    const Sample &a = window_.front(), &b = window_.back();
    const double span = b.at - a.at;
    const uint64_t n = b.count - a.count;
    const bool rate = n > 0 && span > 0;
    double longest = -1;   // the longest gap between two presents in a row (a missed sample is none)
    for (size_t i = 1; i < window_.size(); i++)
        if (window_[i].count == window_[i - 1].count + 1) longest = std::max(longest, window_[i].at - window_[i - 1].at);
    char late_s[32] = "--";
    if (paced) std::snprintf(late_s, sizeof late_s, "%" PRIu64, late);
    std::array<std::string, kLines> next = {
        "FPS " + five(rate, rate ? double(n) / span : 0),
        "MS " + five(rate, rate ? span * 1e3 / double(n) : 0) + " MAX " + five(longest >= 0, longest * 1e3),
        std::string("LATE ") + late_s,
    };
    if (next == lines_) return false;
    lines_ = next;
    return true;
}

std::vector<uint8_t> render(const std::array<std::string, kLines>& lines) {
    std::vector<uint8_t> px(size_t(kBoxW) * kBoxH * 4);
    for (size_t i = 0; i < px.size(); i += 4) std::memcpy(&px[i], kBox, 4);
    for (int l = 0; l < kLines; l++)
        for (int c = 0; c < kColumns && c < int(lines[l].size()); c++) {
            const Glyph* g = glyph(lines[l][c]);
            for (int y = 0; y < kGlyphH; y++)
                for (int x = 0; x < kGlyphW; x++)
                    if (g->rows[y] & (0x10 >> x)) {
                        const size_t at = (size_t(kPad + l * kCellH + y) * kBoxW + kPad + c * kCellW + x) * 4;
                        std::memcpy(&px[at], kInk, 4);
                    }
        }
    return px;
}

uint32_t scale_for(uint32_t w, uint32_t h) {
    for (uint32_t s = std::max<uint32_t>(1, (std::min(w, h) + 300) / 540); s > 0; s--)
        if (kBoxW * s + 8 * s <= w && kBoxH * s + 8 * s <= h) return s;
    return 0;
}

bool place(const Rect& safe, const std::vector<Rect>& avoid, Spot* out) {
    if (!(safe.w > 0 && safe.h > 0)) return false;
    for (uint32_t s = scale_for(uint32_t(safe.w), uint32_t(safe.h)); s > 0; s--) {
        const float m = 4.0f * float(s), bw = float(kBoxW * s), bh = float(kBoxH * s);
        for (const float x : {std::ceil(safe.x + m), std::floor(safe.x + safe.w - m - bw)}) {
            if (x - m < safe.x || x + bw + m > safe.x + safe.w) continue;
            // Down from the top, past the bottom of whatever the box (with its margin) meets: each
            // step ends below a rectangle it met, so it ends.
            for (float y = std::ceil(safe.y + m); y + bh + m <= safe.y + safe.h;) {
                float below = -1;
                for (const Rect& r : avoid)
                    if (x - m < r.x + r.w && r.x < x + bw + m && y - m < r.y + r.h && r.y < y + bh + m)
                        below = std::max(below, r.y + r.h);
                if (below < 0) {
                    *out = Spot{uint32_t(x), uint32_t(y), s};
                    return true;
                }
                y = std::ceil(below + m);
            }
        }
    }
    return false;
}

std::vector<Rect> touch_controls(const sa2_touch::Overlay* touch, uint32_t w, uint32_t h) {
    std::vector<Rect> out;
    if (!touch || !sa2_touch::active(touch)) return out;
    using C = sa2_touch::Control;
    for (const C c : {C::Stick, C::A, C::B, C::X, C::Y, C::Start, C::LT, C::RT}) {
        float x, y, hw, hh;
        sa2_touch::control_box(touch, w, h, c, &x, &y, &hw, &hh);
        out.push_back(Rect{x - hw, y - hh, 2 * hw, 2 * hh});
    }
    return out;
}

namespace {

// The window's safe area in the target's pixels (SDL gives it in the window's coordinates); the
// whole target for a test's texture (no window), or, said once, when SDL cannot give it.
Rect safe_area(State& s, SDL_Window* window, uint32_t w, uint32_t h) {
    const Rect all{0, 0, float(w), float(h)};
    if (!window) return all;
    SDL_Rect r{};
    int ww = 0, wh = 0;
    if (!SDL_GetWindowSafeArea(window, &r) || !SDL_GetWindowSize(window, &ww, &wh) || ww <= 0 || wh <= 0) {
        if (!s.said_safe)
            std::fprintf(stderr, "%sthe window's safe area is not known (SDL_GetWindowSafeArea: %s): the box takes "
                                 "the whole window as safe\n", kSaid, SDL_GetError());
        s.said_safe = true;
        return all;
    }
    const float kx = float(w) / float(ww), ky = float(h) / float(wh);
    return Rect{float(r.x) * kx, float(r.y) * ky, float(r.w) * kx, float(r.h) * ky};
}

}  // namespace

void release() {
    State& s = state();
    if (s.dev) {
        if (s.tex) SDL_ReleaseGPUTexture(s.dev, s.tex);
        if (s.up) SDL_ReleaseGPUTransferBuffer(s.dev, s.up);
    }
    s.tex = nullptr;
    s.up = nullptr;
    s.dev = nullptr;
    s.dirty = true;
}

void setup(bool gpu, bool paced) {
    if (!sa2tw::on(sa2tw::SA2_TWEAK_FRAMETIME_OVERLAY)) return;
    State& s = state();
    if (s.said_setup) return;
    s.said_setup = true;
    s.paced = paced;
    if (!gpu) {
        s.failed = true;
        std::fprintf(stderr, "%snot drawn: the dxvk-native host (sa2_player) presents through DXVK, whose Present has "
                             "no overlay; the sdl3-gpu host (sa2_player_gpu) draws it\n", kSaid);
        return;
    }
    std::fprintf(stderr, "%sa box at the window's top left: FPS and MS (mean and longest) over the last second, %s; "
                         "on the swapchain only, never in the frames file or the snaps\n", kSaid,
                 paced ? "LATE the frames presented more than 16.7 ms after their time (the report's pace over_frame)"
                       : "LATE not counted, since the session does not pace (no --pace)");
}

void draw(SDL_GPUDevice* dev, SDL_GPUCommandBuffer* cb, SDL_GPUTexture* target, uint32_t w, uint32_t h,
          uint32_t frame, const Sa2Pace* pace, const sa2_touch::Overlay* touch, SDL_Window* window) {
    if (!sa2tw::on(sa2tw::SA2_TWEAK_FRAMETIME_OVERLAY)) return;
    State& s = state();
    if (s.failed || !dev || !cb || !target) return;
    if (frame != s.frame) {
        s.frame = frame;
        bool changed = false;
        if (pace && s.paced) {
            if (pace->frames()) changed = s.meter.add(pace->frames(), pace->last_at_s(), pace->over_frame(), true);
        } else {
            changed = s.meter.add(frame, double(SDL_GetTicksNS()) / 1e9, 0, false);
        }
        s.dirty = s.dirty || changed;
    }
    const Rect safe = safe_area(s, window, w, h);
    const std::vector<Rect> controls = touch_controls(touch, w, h);
    Spot at;
    if (!place(safe, controls, &at)) {
        if (!s.said_small)
            std::fprintf(stderr, "%snot drawn while the window is %ux%u: the box needs %ux%u inside the safe area "
                                 "(%.0f,%.0f %.0fx%.0f)%s\n", kSaid, w, h, kBoxW + 8, kBoxH + 8, safe.x, safe.y,
                         safe.w, safe.h, controls.empty() ? "" : " clear of the touch controls");
        s.said_small = true;
        return;
    }
    if (!(at == s.spot)) {
        s.spot = at;
        std::fprintf(stderr, "%sthe box at %u,%u, scale %u, in the %ux%u window (safe area %.0f,%.0f %.0fx%.0f%s)\n",
                     kSaid, at.x, at.y, at.scale, w, h, safe.x, safe.y, safe.w, safe.h,
                     controls.empty() ? "" : "; clear of the touch controls");
    }
    // Another device than the one it made its texture with: what that one made is forgotten, not
    // released, since it may be gone. The player has one device until teardown, which calls
    // release() before it goes, so this is a test's second device after release() or none.
    if (s.dev != dev) {
        s.dev = dev;
        s.tex = nullptr;
        s.up = nullptr;
        s.dirty = true;
        SDL_GPUTextureCreateInfo ti{};
        ti.type = SDL_GPU_TEXTURETYPE_2D;
        ti.format = SDL_GPU_TEXTUREFORMAT_R8G8B8A8_UNORM;
        ti.usage = SDL_GPU_TEXTUREUSAGE_SAMPLER;
        ti.width = kBoxW;
        ti.height = kBoxH;
        ti.layer_count_or_depth = 1;
        ti.num_levels = 1;
        ti.sample_count = SDL_GPU_SAMPLECOUNT_1;
        if (!(s.tex = SDL_CreateGPUTexture(dev, &ti))) return fail(s, "SDL_CreateGPUTexture");
        SDL_GPUTransferBufferCreateInfo bi{};
        bi.usage = SDL_GPU_TRANSFERBUFFERUSAGE_UPLOAD;
        bi.size = kBoxW * kBoxH * 4;
        if (!(s.up = SDL_CreateGPUTransferBuffer(dev, &bi))) return fail(s, "SDL_CreateGPUTransferBuffer");
    }
    if (s.dirty) {
        void* p = SDL_MapGPUTransferBuffer(dev, s.up, true);
        if (!p) return fail(s, "SDL_MapGPUTransferBuffer");
        const std::vector<uint8_t> px = render(s.meter.lines());
        std::memcpy(p, px.data(), px.size());
        SDL_UnmapGPUTransferBuffer(dev, s.up);
        SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
        if (!cp) return fail(s, "SDL_BeginGPUCopyPass");
        SDL_GPUTextureTransferInfo from{};
        from.transfer_buffer = s.up;
        SDL_GPUTextureRegion to{};
        to.texture = s.tex;
        to.w = kBoxW;
        to.h = kBoxH;
        to.d = 1;
        SDL_UploadToGPUTexture(cp, &from, &to, true);
        SDL_EndGPUCopyPass(cp);
        s.dirty = false;
    }
    SDL_GPUBlitInfo bi{};
    bi.source = SDL_GPUBlitRegion{s.tex, 0, 0, 0, 0, kBoxW, kBoxH};
    bi.destination = SDL_GPUBlitRegion{target, 0, 0, at.x, at.y, kBoxW * at.scale, kBoxH * at.scale};
    bi.load_op = SDL_GPU_LOADOP_LOAD;
    bi.flip_mode = SDL_FLIP_NONE;
    bi.filter = SDL_GPU_FILTER_NEAREST;
    SDL_BlitGPUTexture(cb, &bi);
}

}  // namespace sa2_frametime
