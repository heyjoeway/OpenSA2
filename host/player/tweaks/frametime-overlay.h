// frametime-overlay.h: the host tweak "frametime-overlay" (runtime/sa2_tweaks.h SA2_TWEAK_HOST_LIST),
// a small box in the window's top left corner with the frame rate, the frame time and the frames
// presented late. frametime-overlay.cpp has what it shows and where its numbers come from.
//
// player.cpp calls setup() once, when its pads are set up in play, draw() at each Present, in
// the window's present overlay (Player::present_overlay, the sdl3-gpu host's hook) and in the
// --overlay-out picture that rebuilds what the swapchain was given (Player::overlay_shot), and
// release() at teardown, before the device goes. Each does nothing unless the tweak is on.
#pragma once

#include <SDL3/SDL_gpu.h>
#include <SDL3/SDL_video.h>

#include <array>
#include <cstdint>
#include <deque>
#include <string>
#include <vector>

class Sa2Pace;
namespace sa2_touch {
struct Overlay;
}

namespace sa2_frametime {

// Once, in play: `gpu` true when this player presents through the sdl3-gpu host (sa2_player_gpu,
// whose Present calls the window's overlay); `paced` true when the session paces its frames
// (--pace), which LATE counts against. Says on stderr what the box will show, and what it cannot.
void setup(bool gpu, bool paced);

// At Present `frame`: draws the box onto `target` (w x h pixels) in `cb`. `pace` is the player's
// schedule (null without --pace). `touch` is the player's touch controls (null: none) and `window`
// the window the target is presented in (null: a test's texture, all of it safe); the box goes
// where place() puts it, inside the window's safe area and clear of the touch controls while they
// are drawn. Called twice for one Present (the swapchain, then --overlay-out's copy of it), it
// draws the same box twice.
void draw(SDL_GPUDevice* dev, SDL_GPUCommandBuffer* cb, SDL_GPUTexture* target, uint32_t w, uint32_t h,
          uint32_t frame, const Sa2Pace* pace, const sa2_touch::Overlay* touch, SDL_Window* window);

// Once nothing will draw the box again and before its device goes (Player::teardown, after
// close_pads takes the present overlay away): the box's texture and upload buffer released on the
// device that made them. A later draw() starts afresh.
void release();

// The parts the tests read.
constexpr int kColumns = 18, kLines = 3;
constexpr int kGlyphW = 5, kGlyphH = 7, kCellW = 6, kCellH = 9, kPad = 2;
constexpr uint32_t kBoxW = 2 * kPad + kColumns * kCellW - 1;   // the box in its own pixels
constexpr uint32_t kBoxH = 2 * kPad + kLines * kCellH - 2;
constexpr double kWindowS = 1.0;    // FPS, MS and MAX are over the presents of about the last second
constexpr double kRefreshS = 0.25;  // and the box's numbers change at most this often

// The box's numbers, from the presents as they come: one sample per Present, `count` the presents
// so far and `at` the last one's time (seconds), `late` the frames presented late so far.
class Meter {
public:
    // A new sample; false when it is no new Present (its count is not past the last one's) or the
    // box's numbers did not change.
    bool add(uint64_t count, double at, uint64_t late, bool paced);
    const std::array<std::string, kLines>& lines() const { return lines_; }

private:
    struct Sample {
        uint64_t count;
        double at;
    };
    std::deque<Sample> window_;
    bool refreshed_ = false;
    double refreshed_at_ = 0;
    std::array<std::string, kLines> lines_ = {"FPS    --", "MS    -- MAX    --", "LATE --"};
};

// The box's pixels, RGBA, kBoxW x kBoxH: light text on a dark box.
std::vector<uint8_t> render(const std::array<std::string, kLines>& lines);

// The largest scale the box is drawn at in a w x h area (whole pixels, nearest; about one per 540
// pixels of the short side, so a portrait screen draws it no larger than a landscape one), its
// margin 4 box pixels on each side; 0 when it does not fit even at 1.
uint32_t scale_for(uint32_t w, uint32_t h);

// A rectangle in the target's pixels.
struct Rect {
    float x, y, w, h;
};
// Where the box goes: its top left corner and its scale.
struct Spot {
    uint32_t x = 0, y = 0, scale = 0;
    bool operator==(const Spot& o) const { return x == o.x && y == o.y && scale == o.scale; }
};
// The box's place inside `safe` (the window's safe area, clear of a display cutout), at least its
// margin (4 box pixels) from the safe area's edges and from every rectangle in `avoid`: its top
// left corner, moved down past each rectangle it meets, else its top right corner the same way,
// at scale_for of the safe area and then each smaller scale. False when it fits nowhere.
bool place(const Rect& safe, const std::vector<Rect>& avoid, Spot* out);
// The touch controls' boxes on a w x h target while they are drawn (sa2_touch::active): the stick's
// base at rest and each button. None while the controls are not drawn.
std::vector<Rect> touch_controls(const sa2_touch::Overlay* touch, uint32_t w, uint32_t h);

}  // namespace sa2_frametime
