// touch_overlay.cpp: the on-screen gamepad (touch_overlay.h).
#include "touch_overlay.h"

#include <SDL3/SDL_error.h>
#include <SDL3/SDL_touch.h>

#include <algorithm>
#include <cmath>
#include <cstring>

#include "touch_overlay_spirv.h"

namespace sa2_touch {

namespace {

constexpr int kMaxFingers = 10;
constexpr int kMaxShapes = sa2_touch_spirv::kMaxShapes;
// XInput's XINPUT_GAMEPAD_* bits.
constexpr uint16_t kA = 0x1000, kB = 0x2000, kX = 0x4000, kY = 0x8000, kStart = 0x0010;

// The controls, in pixels of a w x h surface, all sized by its short side s.
struct Button {
    float x, y, hx, hy, r;       // centre, half size, corner radius
    uint16_t bit;                // an XInput button, or 0
    int trigger;                 // 1 LT, 2 RT, 0 none
    float rgb[3];
    bool round;                  // hit by distance from the centre, else by the box
};
enum { B_A, B_B, B_X, B_Y, B_START, B_LT, B_RT, B_COUNT };
struct Layout {
    float s;
    float stick_x, stick_y, stick_r;   // the stick's resting place and radius
    Button b[B_COUNT];
};

Layout layout(float w, float h) {
    Layout l{};
    const float s = std::min(w, h);
    const float m = 0.04f * s;
    l.s = s;
    l.stick_r = 0.12f * s;
    l.stick_x = m + 1.6f * l.stick_r;
    l.stick_y = h - m - 1.6f * l.stick_r;
    const float r = 0.075f * s, d = 0.135f * s;
    const float cx = w - m - d - r, cy = h - m - d - r;
    l.b[B_A] = {cx, cy + d, r, r, r, kA, 0, {0.30f, 0.80f, 0.35f}, true};
    l.b[B_B] = {cx + d, cy, r, r, r, kB, 0, {0.92f, 0.30f, 0.30f}, true};
    l.b[B_X] = {cx - d, cy, r, r, r, kX, 0, {0.30f, 0.55f, 0.95f}, true};
    l.b[B_Y] = {cx, cy - d, r, r, r, kY, 0, {0.95f, 0.80f, 0.25f}, true};
    const float ph = 0.035f * s;
    l.b[B_START] = {w * 0.5f, m + ph, 0.075f * s, ph, ph, kStart, 0, {0.85f, 0.85f, 0.85f}, false};
    const float th = 0.04f * s, tw = 0.09f * s;
    l.b[B_LT] = {m + tw, m + th, tw, th, th, 0, 1, {0.85f, 0.85f, 0.85f}, false};
    l.b[B_RT] = {w - m - tw, m + th, tw, th, th, 0, 2, {0.85f, 0.85f, 0.85f}, false};
    return l;
}

// The button under (x, y), or -1: the nearest round one within 1.3 radii, else a box with a
// margin of 2% of s.
int hit(const Layout& l, float x, float y) {
    int best = -1;
    float best_d = 0;
    for (int i = 0; i < B_COUNT; ++i) {
        const Button& b = l.b[i];
        if (b.round) {
            const float d = std::hypot(x - b.x, y - b.y);
            if (d < 1.3f * b.r && (best < 0 || d < best_d)) { best = i; best_d = d; }
        }
    }
    if (best >= 0) return best;
    const float pad = 0.02f * l.s;
    for (int i = 0; i < B_COUNT; ++i) {
        const Button& b = l.b[i];
        if (!b.round && std::fabs(x - b.x) <= b.hx + pad && std::fabs(y - b.y) <= b.hy + pad) return i;
    }
    return -1;
}

enum Role : uint8_t { R_FREE, R_NONE, R_STICK, R_BUTTON };
struct Finger {
    Role role = R_FREE;
    SDL_TouchID touch = 0;
    SDL_FingerID id = 0;
    int button = -1;
    float x = 0, y = 0;          // where it is, normalised to the window
    float ox = 0, oy = 0;        // R_STICK: the stick's origin, where it landed, normalised
};

struct Uniforms {                // touch_overlay_shaders.py's block, std140: vec4s throughout
    float target[4];
    float geom[kMaxShapes][4];
    float style[kMaxShapes][4];
    float color[kMaxShapes][4];
};

}  // namespace

struct Overlay {
    SDL_GPUDevice* dev = nullptr;
    SDL_Window* win = nullptr;
    bool active = false;
    Finger fingers[kMaxFingers];
    sa2_input::PadState state;   // recomputed at every event the overlay takes
    SDL_GPUShader* vs = nullptr;
    SDL_GPUShader* fs = nullptr;
    SDL_GPUGraphicsPipeline* pipe = nullptr;
    SDL_GPUTextureFormat pipe_format = SDL_GPU_TEXTUREFORMAT_INVALID;
    SDL_GPUTextureFormat override_format = SDL_GPU_TEXTUREFORMAT_INVALID;
    bool gpu_failed = false;
    Stats st;
    Uniforms u{};
};

namespace {

void window_size(const Overlay* o, float* w, float* h) {
    int iw = 0, ih = 0;
    if (!o->win || !SDL_GetWindowSizeInPixels(o->win, &iw, &ih) || iw <= 0 || ih <= 0) { iw = 1280; ih = 720; }
    *w = float(iw);
    *h = float(ih);
}

// The stick's displacement in pixels of a w x h surface: from its origin, which stays where the
// finger landed, to the finger, clamped to its radius, so a finger at or past the edge is a full
// push in its direction.
void stick_offset(const Finger& f, const Layout& l, float w, float h, float* dx, float* dy) {
    float x = (f.x - f.ox) * w, y = (f.y - f.oy) * h;
    const float d = std::hypot(x, y);
    if (d > l.stick_r && d > 0) { x *= l.stick_r / d; y *= l.stick_r / d; }
    *dx = x;
    *dy = y;
}

int16_t axis(float v) {
    const float c = std::max(-1.0f, std::min(1.0f, v));
    return int16_t(std::lround(c * 32767.0f));
}

void recompute(Overlay* o) {
    sa2_input::PadState s;
    if (o->active) {
        s.connected = true;
        float w, h;
        window_size(o, &w, &h);
        const Layout l = layout(w, h);
        for (const Finger& f : o->fingers) {
            if (f.role == R_BUTTON && f.button >= 0) {
                const Button& b = l.b[f.button];
                s.buttons |= b.bit;
                if (b.trigger == 1) s.lt = 255;
                if (b.trigger == 2) s.rt = 255;
            } else if (f.role == R_STICK) {
                float dx, dy;
                stick_offset(f, l, w, h, &dx, &dy);
                float nx = dx / l.stick_r, ny = -dy / l.stick_r;   // XInput: up is positive
                if (std::hypot(nx, ny) < 0.06f) nx = ny = 0;       // a resting thumb's jitter
                s.lx = axis(nx);
                s.ly = axis(ny);
            }
        }
    }
    o->state = s;
}

Finger* find(Overlay* o, SDL_TouchID touch, SDL_FingerID id) {
    for (Finger& f : o->fingers)
        if (f.role != R_FREE && f.touch == touch && f.id == id) return &f;
    return nullptr;
}

bool stick_held(const Overlay* o) {
    for (const Finger& f : o->fingers) if (f.role == R_STICK) return true;
    return false;
}

void note_error(Overlay* o, const std::string& why) {
    if (o->st.error.empty()) o->st.error = why;
}

bool ensure_pipeline(Overlay* o) {
    if (o->gpu_failed) return false;
    SDL_GPUTextureFormat fmt = o->override_format;
    if (fmt == SDL_GPU_TEXTUREFORMAT_INVALID && o->win) fmt = SDL_GetGPUSwapchainTextureFormat(o->dev, o->win);
    if (fmt == SDL_GPU_TEXTUREFORMAT_INVALID) {
        note_error(o, "no target format (the window is not claimed and none was set)");
        o->gpu_failed = true;
        return false;
    }
    if (o->pipe && o->pipe_format == fmt) return true;
    if (!(SDL_GetGPUShaderFormats(o->dev) & SDL_GPU_SHADERFORMAT_SPIRV)) {
        note_error(o, "the device takes no SPIR-V");
        o->gpu_failed = true;
        return false;
    }
    if (!o->vs) {
        SDL_GPUShaderCreateInfo ci{};
        ci.code = reinterpret_cast<const Uint8*>(sa2_touch_spirv::kVertex);
        ci.code_size = sizeof sa2_touch_spirv::kVertex;
        ci.entrypoint = "main";
        ci.format = SDL_GPU_SHADERFORMAT_SPIRV;
        ci.stage = SDL_GPU_SHADERSTAGE_VERTEX;
        ci.num_uniform_buffers = 1;
        o->vs = SDL_CreateGPUShader(o->dev, &ci);
        ci.code = reinterpret_cast<const Uint8*>(sa2_touch_spirv::kFragment);
        ci.code_size = sizeof sa2_touch_spirv::kFragment;
        ci.stage = SDL_GPU_SHADERSTAGE_FRAGMENT;
        ci.num_uniform_buffers = 0;
        o->fs = o->vs ? SDL_CreateGPUShader(o->dev, &ci) : nullptr;
        if (!o->vs || !o->fs) {
            note_error(o, std::string("SDL_CreateGPUShader: ") + SDL_GetError());
            o->gpu_failed = true;
            return false;
        }
    }
    if (o->pipe) { SDL_ReleaseGPUGraphicsPipeline(o->dev, o->pipe); o->pipe = nullptr; }
    SDL_GPUColorTargetDescription ct{};
    ct.format = fmt;
    ct.blend_state.enable_blend = true;
    ct.blend_state.src_color_blendfactor = SDL_GPU_BLENDFACTOR_SRC_ALPHA;
    ct.blend_state.dst_color_blendfactor = SDL_GPU_BLENDFACTOR_ONE_MINUS_SRC_ALPHA;
    ct.blend_state.color_blend_op = SDL_GPU_BLENDOP_ADD;
    ct.blend_state.src_alpha_blendfactor = SDL_GPU_BLENDFACTOR_ZERO;
    ct.blend_state.dst_alpha_blendfactor = SDL_GPU_BLENDFACTOR_ONE;
    ct.blend_state.alpha_blend_op = SDL_GPU_BLENDOP_ADD;
    // The target's alpha is left as the frame wrote it (a compositor may read it).
    ct.blend_state.color_write_mask = SDL_GPU_COLORCOMPONENT_R | SDL_GPU_COLORCOMPONENT_G | SDL_GPU_COLORCOMPONENT_B;
    ct.blend_state.enable_color_write_mask = true;
    SDL_GPUGraphicsPipelineCreateInfo pi{};
    pi.vertex_shader = o->vs;
    pi.fragment_shader = o->fs;
    pi.primitive_type = SDL_GPU_PRIMITIVETYPE_TRIANGLELIST;
    pi.rasterizer_state.fill_mode = SDL_GPU_FILLMODE_FILL;
    pi.rasterizer_state.cull_mode = SDL_GPU_CULLMODE_NONE;
    pi.multisample_state.sample_count = SDL_GPU_SAMPLECOUNT_1;
    pi.target_info.color_target_descriptions = &ct;
    pi.target_info.num_color_targets = 1;
    o->pipe = SDL_CreateGPUGraphicsPipeline(o->dev, &pi);
    if (!o->pipe) {
        note_error(o, std::string("SDL_CreateGPUGraphicsPipeline: ") + SDL_GetError());
        o->gpu_failed = true;
        return false;
    }
    o->pipe_format = fmt;
    return true;
}

struct ShapeList {
    Uniforms* u;
    int n = 0;
    void add(float x, float y, float hx, float hy, float r, float ring, const float rgb[3], float a) {
        if (n >= kMaxShapes) return;
        float* g = u->geom[n];
        g[0] = x; g[1] = y; g[2] = hx; g[3] = hy;
        float* s = u->style[n];
        s[0] = r; s[1] = ring; s[2] = 0; s[3] = 0;
        float* c = u->color[n];
        c[0] = rgb[0]; c[1] = rgb[1]; c[2] = rgb[2]; c[3] = a;
        ++n;
    }
};

}  // namespace

Overlay* create(SDL_GPUDevice* device, SDL_Window* window) {
    Overlay* o = new Overlay;
    o->dev = device;
    o->win = window;
    return o;
}

bool handle_event(Overlay* o, const SDL_Event& e) {
    if (!o) return false;
    if (e.type != SDL_EVENT_FINGER_DOWN && e.type != SDL_EVENT_FINGER_UP && e.type != SDL_EVENT_FINGER_MOTION &&
        e.type != SDL_EVENT_FINGER_CANCELED)
        return false;
    const SDL_TouchFingerEvent& t = e.tfinger;
    if (t.touchID == SDL_MOUSE_TOUCHID ||
        (o->win && t.windowID != 0 && t.windowID != SDL_GetWindowID(o->win))) {
        ++o->st.ignored_fingers;
        return false;
    }
    ++o->st.finger_events;
    float w, h;
    window_size(o, &w, &h);
    const Layout l = layout(w, h);
    Finger* f = find(o, t.touchID, t.fingerID);
    if (e.type == SDL_EVENT_FINGER_DOWN) {
        if (!o->active) { o->active = true; ++o->st.activations; }
        if (!f)
            for (Finger& free : o->fingers) if (free.role == R_FREE) { f = &free; break; }
        if (!f) return true;          // more fingers than tracked: this one does nothing
        *f = Finger{};
        f->touch = t.touchID;
        f->id = t.fingerID;
        f->x = f->ox = t.x;
        f->y = f->oy = t.y;
        f->button = hit(l, t.x * w, t.y * h);
        if (f->button >= 0) f->role = R_BUTTON;
        else if (t.x < 0.5f && !stick_held(o)) f->role = R_STICK;
        else f->role = R_NONE;
    } else if (e.type == SDL_EVENT_FINGER_MOTION) {
        if (!f) return true;
        f->x = t.x;
        f->y = t.y;
        // A stick finger's origin stays where it landed, however far the finger goes.
        if (f->role == R_BUTTON) f->button = hit(l, t.x * w, t.y * h);
    } else {                          // up or cancelled
        if (f) *f = Finger{};
    }
    recompute(o);
    return true;
}

bool active(const Overlay* o) { return o && o->active; }

void pad(const Overlay* o, sa2_input::PadState* out) {
    if (!out) return;
    *out = o ? o->state : sa2_input::PadState{};
}

void deactivate(Overlay* o) {
    if (!o) return;
    o->active = false;
    for (Finger& f : o->fingers) f = Finger{};
    recompute(o);
}

void attach_device(Overlay* o, SDL_GPUDevice* device) {
    if (!o || o->dev == device) return;
    o->pipe = nullptr;
    o->vs = o->fs = nullptr;
    o->pipe_format = SDL_GPU_TEXTUREFORMAT_INVALID;
    o->gpu_failed = false;
    o->dev = device;
}

void set_target_format(Overlay* o, SDL_GPUTextureFormat format) {
    if (o) { o->override_format = format; o->gpu_failed = false; }
}

void draw(Overlay* o, SDL_GPUCommandBuffer* cb, SDL_GPUTexture* target, uint32_t w, uint32_t h) {
    if (!o || !o->active) return;
    if (!o->dev || !cb || !target || !w || !h) return;
    if (!ensure_pipeline(o)) { ++o->st.draw_failures; return; }
    const float fw = float(w), fh = float(h);
    const Layout l = layout(fw, fh);
    Uniforms& u = o->u;
    u.target[0] = fw; u.target[1] = fh; u.target[2] = 0; u.target[3] = 0;
    ShapeList sh{&u};
    static const float white[3] = {1, 1, 1}, dark[3] = {0.05f, 0.05f, 0.08f};
    // The stick: its base where the finger landed, or at its resting place, and its knob at the
    // push, clamped to the base's rim.
    const Finger* stick = nullptr;
    for (const Finger& f : o->fingers) if (f.role == R_STICK) stick = &f;
    float sx = l.stick_x, sy = l.stick_y, kx = 0, ky = 0;
    if (stick) {
        sx = stick->ox * fw;
        sy = stick->oy * fh;
        stick_offset(*stick, l, fw, fh, &kx, &ky);
    }
    const float R = l.stick_r, line = std::max(2.0f, 0.008f * l.s);
    sh.add(sx, sy, R, R, R, 0, dark, stick ? 0.30f : 0.18f);
    sh.add(sx, sy, R, R, R, line, white, stick ? 0.55f : 0.35f);
    sh.add(sx + kx, sy + ky, 0.42f * R, 0.42f * R, 0.42f * R, 0, white, stick ? 0.70f : 0.40f);
    uint32_t held = 0;
    for (const Finger& f : o->fingers) if (f.role == R_BUTTON && f.button >= 0) held |= 1u << f.button;
    for (int i = 0; i < B_COUNT; ++i) {
        const Button& b = l.b[i];
        const bool down = held & (1u << i);
        sh.add(b.x, b.y, b.hx, b.hy, b.r, 0, b.rgb, down ? 0.80f : 0.38f);
        sh.add(b.x, b.y, b.hx, b.hy, b.r, std::max(1.5f, 0.005f * l.s), white, down ? 0.85f : 0.45f);
    }
    // START's mark: two bars; LT and RT: one.
    const Button& st = l.b[B_START];
    sh.add(st.x, st.y - 0.3f * st.hy, 0.45f * st.hx, 0.1f * st.hy, 0.1f * st.hy, 0, white, 0.8f);
    sh.add(st.x, st.y + 0.3f * st.hy, 0.45f * st.hx, 0.1f * st.hy, 0.1f * st.hy, 0, white, 0.8f);
    for (int i : {B_LT, B_RT}) {
        const Button& b = l.b[i];
        sh.add(b.x, b.y, 0.5f * b.hx, 0.12f * b.hy, 0.12f * b.hy, 0, white, 0.8f);
    }
    SDL_GPUColorTargetInfo ct{};
    ct.texture = target;
    ct.load_op = SDL_GPU_LOADOP_LOAD;
    ct.store_op = SDL_GPU_STOREOP_STORE;
    SDL_GPURenderPass* rp = SDL_BeginGPURenderPass(cb, &ct, 1, nullptr);
    if (!rp) { note_error(o, std::string("SDL_BeginGPURenderPass: ") + SDL_GetError()); ++o->st.draw_failures; return; }
    SDL_BindGPUGraphicsPipeline(rp, o->pipe);
    SDL_PushGPUVertexUniformData(cb, 0, &u, sizeof u);
    SDL_DrawGPUPrimitives(rp, Uint32(6 * sh.n), 1, 0, 0);
    SDL_EndGPURenderPass(rp);
    ++o->st.draws;
}

void destroy(Overlay* o) {
    if (!o) return;
    if (o->dev) {
        if (o->pipe) SDL_ReleaseGPUGraphicsPipeline(o->dev, o->pipe);
        if (o->vs) SDL_ReleaseGPUShader(o->dev, o->vs);
        if (o->fs) SDL_ReleaseGPUShader(o->dev, o->fs);
    }
    delete o;
}

Stats stats(const Overlay* o) {
    if (!o) return {};
    Stats s = o->st;
    s.gpu = o->dev && !o->gpu_failed && (SDL_GetGPUShaderFormats(o->dev) & SDL_GPU_SHADERFORMAT_SPIRV);
    return s;
}

void control_centre(uint32_t w, uint32_t h, Control c, float* x, float* y) {
    const Layout l = layout(float(w), float(h));
    int i = -1;
    switch (c) {
    case Control::Stick: *x = l.stick_x; *y = l.stick_y; return;
    case Control::A: i = B_A; break;
    case Control::B: i = B_B; break;
    case Control::X: i = B_X; break;
    case Control::Y: i = B_Y; break;
    case Control::Start: i = B_START; break;
    case Control::LT: i = B_LT; break;
    case Control::RT: i = B_RT; break;
    }
    *x = l.b[i].x;
    *y = l.b[i].y;
}

float stick_radius(uint32_t w, uint32_t h) { return layout(float(w), float(h)).stick_r; }

}  // namespace sa2_touch
