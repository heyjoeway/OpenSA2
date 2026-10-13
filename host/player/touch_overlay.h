// touch_overlay.h: the player's on-screen gamepad for touch screens (Android first).
//
// The overlay is inactive and draws nothing until the first SDL finger event; from then on
// it is a virtual XInput pad, which the player sends in slot 0 of its gamepad records
// (input.h send_pads; player.cpp "Gamepads" says how it shares the slot): a stick on the left
// half of the screen whose origin is wherever the finger lands and stays there until it lifts
// (radius 12% of the screen's short side; a finger pulled past the radius is a full push in its
// direction from the origin, which does not move), the A/B/X/Y diamond bottom right, START top
// centre, LT and RT top left and right.
// Every finger is tracked by its id, so the stick and any buttons are held together; a finger
// that lands on a button can slide onto another. Fingers of SDL's mouse-touch device are not
// touches and are ignored.
//
// draw() renders onto a target texture after the game's frame (the swapchain texture the
// device presents), translucent, in one render pass of one draw from one uniform block (no
// vertex buffer, no texture): touch_overlay_shaders.py's SPIR-V, so a device that takes no
// SPIR-V gets no drawing (the pad still works). Before the overlay is active, draw() returns
// at once and encodes nothing, so a run without touch input presents exactly what it did.
// A null device makes an overlay that only maps touches (no drawing at all).
#pragma once

#include <SDL3/SDL_events.h>
#include <SDL3/SDL_gpu.h>
#include <SDL3/SDL_video.h>

#include <cstdint>
#include <string>

#include "input.h"

namespace sa2_touch {

struct Overlay;

// Borrowed device and window, both outliving the overlay. The window sizes the touch layout
// (its size in pixels) and, once a device has claimed it, gives the target format.
Overlay* create(SDL_GPUDevice* device, SDL_Window* window);
// True when the event was a touch event the overlay took (finger down, motion, up, cancel).
bool handle_event(Overlay* o, const SDL_Event& e);
// Whether the overlay is in use: from the first finger down until deactivate().
bool active(const Overlay* o);
// The virtual pad: connected only while active.
void pad(const Overlay* o, sa2_input::PadState* out);
// Encodes the overlay onto `target` (w x h pixels) in `cb`; nothing while inactive.
void draw(Overlay* o, SDL_GPUCommandBuffer* cb, SDL_GPUTexture* target, uint32_t w, uint32_t h);
void destroy(Overlay* o);

// Beyond contract C. Inactive, hidden and every finger let go (the player calls it when a
// physical pad's button is pressed); the next finger down reactivates it.
void deactivate(Overlay* o);
// The format draw() targets when the window has no claimed swapchain (a test's texture).
void set_target_format(Overlay* o, SDL_GPUTextureFormat format);
// The device to draw with, when create() had none (the player learns it at the first Present).
// Another device than the one it drew with makes its shaders and pipeline afresh; what it made
// with the old one is forgotten, not released, since that device may be gone already.
void attach_device(Overlay* o, SDL_GPUDevice* device);

struct Stats {
    uint64_t finger_events = 0, ignored_fingers = 0, activations = 0, draws = 0, draw_failures = 0;
    bool gpu = false;            // a device that takes SPIR-V, and a pipeline made when one was needed
    std::string error;           // the first thing that went wrong while drawing
};
Stats stats(const Overlay* o);

// The layout's shapes in pixels, for tests: the centre of each control on a w x h target.
enum class Control { Stick, A, B, X, Y, Start, LT, RT };
void control_centre(uint32_t w, uint32_t h, Control c, float* x, float* y);
// The stick's radius in pixels on a w x h target: a full push is this far from its origin.
float stick_radius(uint32_t w, uint32_t h);

}  // namespace sa2_touch
