// test_touch_overlay.cpp: the touch overlay (touch_overlay.h) on its own: the pad it maps
// from finger events, its settings (size, opacity, places, what each button sends) and its
// controls in the band under a portrait window's picture, and what it draws on a real SDL_gpu
// device (Vulkan, no window): an offscreen target cleared to black, the overlay drawn over it,
// read back and its pixels checked where each control is and where none is; nothing drawn while
// inactive or at opacity 0; and its cost per frame, CPU encoding and GPU time, over many frames
// at 1920x1080.
//
//   test_touch_overlay [OUT_DIR]     OUT_DIR gets overlay.png (the drawn frame) when given
//
// Exit 0 and "TOUCH_OVERLAY_OK" when every check passes.
#include "touch_overlay.h"
#include "texcodec.h"

#include <SDL3/SDL.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <string>
#include <vector>

using sa2_touch::Control;

static int fails = 0;
#define CHECK(c, ...)                                                          \
    do {                                                                       \
        if (!(c)) {                                                            \
            std::printf("FAIL %s:%d: %s: ", __FILE__, __LINE__, #c);           \
            std::printf(__VA_ARGS__);                                          \
            std::printf("\n");                                                 \
            ++fails;                                                           \
        }                                                                      \
    } while (0)

constexpr uint32_t W = 1280, H = 720;   // the overlay's layout without a window

static SDL_Event finger(SDL_EventType t, SDL_FingerID id, float x, float y, SDL_TouchID touch = 7) {
    SDL_Event e{};
    e.type = t;
    e.tfinger.touchID = touch;
    e.tfinger.fingerID = id;
    e.tfinger.x = x;
    e.tfinger.y = y;
    return e;
}
static void at(Control c, float* x, float* y) {
    sa2_touch::control_centre(nullptr, W, H, c, x, y);
    *x /= float(W);
    *y /= float(H);
}
static sa2_input::PadState pad_of(const sa2_touch::Overlay* o) {
    sa2_input::PadState p;
    sa2_touch::pad(o, &p);
    return p;
}

struct Gpu {
    SDL_GPUDevice* dev = nullptr;
    SDL_GPUTexture* tex = nullptr;
    SDL_GPUTransferBuffer* down = nullptr;
    uint32_t w = 0, h = 0;
};

static bool make_target(Gpu* g, uint32_t w, uint32_t h) {
    SDL_GPUTextureCreateInfo ti{};
    ti.type = SDL_GPU_TEXTURETYPE_2D;
    ti.format = SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM;
    ti.usage = SDL_GPU_TEXTUREUSAGE_COLOR_TARGET;
    ti.width = w;
    ti.height = h;
    ti.layer_count_or_depth = 1;
    ti.num_levels = 1;
    ti.sample_count = SDL_GPU_SAMPLECOUNT_1;
    g->tex = SDL_CreateGPUTexture(g->dev, &ti);
    SDL_GPUTransferBufferCreateInfo bi{SDL_GPU_TRANSFERBUFFERUSAGE_DOWNLOAD, w * h * 4, 0};
    g->down = SDL_CreateGPUTransferBuffer(g->dev, &bi);
    g->w = w;
    g->h = h;
    return g->tex && g->down;
}

static void release_target(Gpu* g) {
    if (g->tex) SDL_ReleaseGPUTexture(g->dev, g->tex);
    if (g->down) SDL_ReleaseGPUTransferBuffer(g->dev, g->down);
    g->tex = nullptr;
    g->down = nullptr;
}

// The target cleared to black, the overlay over it, read back as BGRA rows.
static bool frame(Gpu* g, sa2_touch::Overlay* o, std::vector<uint8_t>* px) {
    SDL_GPUCommandBuffer* cb = SDL_AcquireGPUCommandBuffer(g->dev);
    if (!cb) return false;
    SDL_GPUColorTargetInfo ct{};
    ct.texture = g->tex;
    ct.load_op = SDL_GPU_LOADOP_CLEAR;
    ct.store_op = SDL_GPU_STOREOP_STORE;
    ct.clear_color = SDL_FColor{0, 0, 0, 1};
    SDL_EndGPURenderPass(SDL_BeginGPURenderPass(cb, &ct, 1, nullptr));
    sa2_touch::draw(o, cb, g->tex, g->w, g->h);
    SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
    SDL_GPUTextureRegion from{};
    from.texture = g->tex;
    from.w = g->w;
    from.h = g->h;
    from.d = 1;
    SDL_GPUTextureTransferInfo to{g->down, 0, 0, 0};
    SDL_DownloadFromGPUTexture(cp, &from, &to);
    SDL_EndGPUCopyPass(cp);
    SDL_GPUFence* f = SDL_SubmitGPUCommandBufferAndAcquireFence(cb);
    if (!f) return false;
    SDL_WaitForGPUFences(g->dev, true, &f, 1);
    SDL_ReleaseGPUFence(g->dev, f);
    const uint8_t* p = static_cast<const uint8_t*>(SDL_MapGPUTransferBuffer(g->dev, g->down, false));
    if (!p) return false;
    px->assign(p, p + size_t(g->w) * g->h * 4);
    SDL_UnmapGPUTransferBuffer(g->dev, g->down);
    return true;
}

struct Bgr { int b, g, r; };
static Bgr pixel(const std::vector<uint8_t>& px, uint32_t w, float x, float y) {
    const size_t i = (size_t(std::lround(y)) * w + size_t(std::lround(x))) * 4;
    return {px[i], px[i + 1], px[i + 2]};
}
static uint64_t lit(const std::vector<uint8_t>& px) {
    uint64_t n = 0;
    for (size_t i = 0; i < px.size(); i += 4) n += (px[i] | px[i + 1] | px[i + 2]) != 0;
    return n;
}

int main(int argc, char** argv) {
    const std::string out = argc > 1 ? argv[1] : "";
    // ---------------------------------------------------------------- the pad
    sa2_touch::Overlay* o = sa2_touch::create(nullptr, nullptr);
    CHECK(!sa2_touch::active(o), "fresh overlay active");
    CHECK(!pad_of(o).connected, "fresh overlay connected");
    float ax, ay, bx, by, ltx, lty, rtx, rty, stx, sty, yx, yy;
    at(Control::A, &ax, &ay);
    at(Control::B, &bx, &by);
    at(Control::Y, &yx, &yy);
    at(Control::LT, &ltx, &lty);
    at(Control::RT, &rtx, &rty);
    at(Control::Start, &stx, &sty);
    // A mouse's synthesized touch is not a touch.
    CHECK(!sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, ax, ay, SDL_MOUSE_TOUCHID)), "mouse touch taken");
    CHECK(!sa2_touch::active(o), "mouse touch activated the overlay");
    SDL_Event key{};
    key.type = SDL_EVENT_KEY_DOWN;
    CHECK(!sa2_touch::handle_event(o, key), "a key event taken");
    CHECK(sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, ax, ay)), "finger down not taken");
    sa2_input::PadState p = pad_of(o);
    CHECK(sa2_touch::active(o) && p.connected, "first finger did not activate");
    CHECK(p.buttons == 0x1000 && p.lx == 0 && p.ly == 0 && p.lt == 0 && p.rt == 0, "A: buttons %04x", p.buttons);
    // The stick: a finger on the left half, away from every button, is its origin.
    const float R = 0.12f * float(H);        // 12% of the short side
    const float sx = 0.25f, sy = 0.55f;
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 2, sx, sy));
    p = pad_of(o);
    CHECK(p.lx == 0 && p.ly == 0 && p.buttons == 0x1000, "stick at rest: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx + R / W, sy));
    p = pad_of(o);
    CHECK(p.lx == 32767 && p.ly == 0, "stick right: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx, sy - 0.5f * R / H));
    p = pad_of(o);
    CHECK(std::abs(p.lx) <= 1 && std::abs(p.ly - 16384) <= 2, "stick half up (y up positive): %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx - 0.5f * R / W, sy + 0.5f * R / H));
    p = pad_of(o);
    CHECK(std::abs(p.lx + 16384) <= 2 && std::abs(p.ly + 16384) <= 2, "stick down-left: %d %d", p.lx, p.ly);
    // Pulled past the radius, the stick is a full push in the finger's direction from where it
    // landed, and that origin stays put: back where the finger landed is the centre again.
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx + 3 * R / W, sy));
    p = pad_of(o);
    CHECK(p.lx == 32767 && std::abs(p.ly) <= 1, "stick pulled far right: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx - 1.2f * R / W, sy - 1.6f * R / H));
    p = pad_of(o);   // two radii out, (-0.6, 0.8) of a radius each (y up positive): a full push
    CHECK(std::abs(p.lx + 19660) <= 3 && std::abs(p.ly - 26214) <= 3, "stick pulled far up-left: %d %d", p.lx,
          p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx, sy));
    p = pad_of(o);
    CHECK(p.lx == 0 && p.ly == 0, "the origin moved after the finger: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx + 0.5f * R / W, sy));
    // A second finger on the left half while the stick is held is no second stick.
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 3, 0.1f, 0.5f));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 3, 0.3f, 0.5f));
    p = pad_of(o);
    CHECK(std::abs(p.lx - 16384) <= 2 && p.buttons == 0x1000, "second left finger moved the stick: %d", p.lx);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_UP, 3, 0.3f, 0.5f));
    // The A finger slides onto B; then LT, RT and START join.
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 1, bx, by));
    CHECK(pad_of(o).buttons == 0x2000, "slide to B: %04x", pad_of(o).buttons);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 4, ltx, lty));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 5, rtx, rty));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 6, stx, sty));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 7, yx, yy));
    p = pad_of(o);
    CHECK(p.buttons == (0x2000 | 0x0010 | 0x8000) && p.lt == 255 && p.rt == 255, "B+START+Y, LT, RT: %04x %u %u",
          p.buttons, p.lt, p.rt);
    for (SDL_FingerID id : {4, 5, 6, 7}) sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_UP, id, 0, 0));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_CANCELED, 1, bx, by));
    p = pad_of(o);
    CHECK(p.connected && p.buttons == 0 && p.lt == 0 && p.rt == 0 && std::abs(p.lx - 16384) <= 2,
          "after the ups: %04x %u %u lx %d", p.buttons, p.lt, p.rt, p.lx);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_UP, 2, 0, 0));
    p = pad_of(o);
    CHECK(p.connected && p.lx == 0 && p.ly == 0, "stick let go: %d %d", p.lx, p.ly);
    // A new touch is a new origin: where it lands, not where the last one did.
    const float nx = 0.1f, ny = 0.4f;
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 8, nx, ny));
    p = pad_of(o);
    CHECK(p.lx == 0 && p.ly == 0, "a new stick touch at rest: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 8, nx - 0.5f * R / W, ny));
    p = pad_of(o);
    CHECK(std::abs(p.lx + 16384) <= 2 && std::abs(p.ly) <= 1, "not re-centred at the new touch: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_UP, 8, 0, 0));
    sa2_touch::deactivate(o);
    CHECK(!sa2_touch::active(o) && !pad_of(o).connected, "deactivate");
    const sa2_touch::Stats st = sa2_touch::stats(o);
    CHECK(st.ignored_fingers == 1 && st.activations == 1 && st.draws == 0 && !st.gpu, "stats %llu %llu",
          (unsigned long long)st.ignored_fingers, (unsigned long long)st.activations);
    sa2_touch::destroy(o);
    std::printf("pad mapping: %s\n", fails ? "FAILED" : "ok");

    // ---------------------------------------------------------------- the settings
    {
        sa2_touch::Settings s;
        std::string why;
        const auto parse = [&](const char* name, const char* value) {
            return sa2_touch::parse_setting(name, value, &s, &why);
        };
        CHECK(parse("SA2_TOUCH_SCALE", "1.5") && s.scale == 1.5f, "scale 1.5: %s", why.c_str());
        for (const char* bad : {"", "0.4", "2.5", "-1", "1x", "nan", "1e0"})
            CHECK(!parse("SA2_TOUCH_SCALE", bad) && s.scale == 1.5f, "scale \"%s\" taken", bad);
        CHECK(parse("SA2_TOUCH_OPACITY", "0") && s.opacity == 0, "opacity 0: %s", why.c_str());
        CHECK(!parse("SA2_TOUCH_OPACITY", "2.1") && s.opacity == 0, "opacity 2.1 taken");
        CHECK(parse("SA2_TOUCH_LAYOUT", "STICK=0.8,0.7;FACE=0.2,0.7") && s.over[0].set && s.over[0].x == 0.8f &&
              s.over[0].y == 0.7f && s.over[1].set && s.over[1].x == 0.2f && !s.over[2].set && !s.below[0].set,
              "layout: %s", why.c_str());
        for (const char* bad : {"STICK=0.8", "PAD=0.1,0.1", "STICK=1.2,0.5", "STICK=0.1,0.2;", "STICK=0.1;0.2"})
            CHECK(!parse("SA2_TOUCH_LAYOUT_BELOW", bad) && !s.below[0].set, "layout \"%s\" taken", bad);
        CHECK(parse("SA2_TOUCH_MAP", "A=B,LT=START") && s.mapped[0] && s.sends[0].bit == 0x2000 && s.mapped[5] &&
              s.sends[5].bit == 0x0010 && s.sends[5].trigger == 0 && !s.mapped[1], "map: %s", why.c_str());
        for (const char* bad : {"A=JUMP", "Z=A", "A", "A=B,"})
            CHECK(!parse("SA2_TOUCH_MAP", bad) && !s.mapped[1], "map \"%s\" taken", bad);
        CHECK(!parse("SA2_TOUCH_SIZE", "1"), "an unknown name taken");
    }
    {
        // Bigger, moved and remapped: the stick on the right, the diamond on the left, A sends B
        // and LT sends START.
        sa2_touch::Overlay* q = sa2_touch::create(nullptr, nullptr);
        sa2_touch::Settings s;
        std::string why;
        sa2_touch::parse_setting("SA2_TOUCH_SCALE", "1.5", &s, &why);
        sa2_touch::parse_setting("SA2_TOUCH_LAYOUT", "STICK=0.8,0.7;FACE=0.2,0.7", &s, &why);
        sa2_touch::parse_setting("SA2_TOUCH_MAP", "A=B,LT=START", &s, &why);
        sa2_touch::set_settings(q, s);
        CHECK(std::fabs(sa2_touch::stick_radius(q, W, H) - 1.5f * R) < 0.01f, "scaled stick radius %.2f",
              sa2_touch::stick_radius(q, W, H));
        float qx, qy;
        sa2_touch::control_centre(q, W, H, Control::Stick, &qx, &qy);
        CHECK(std::fabs(qx - 0.8f * W) < 0.01f && std::fabs(qy - 0.7f * H) < 0.01f, "stick placed at %.1f,%.1f", qx, qy);
        sa2_touch::control_centre(q, W, H, Control::A, &qx, &qy);
        CHECK(std::fabs(qx - 0.2f * W) < 0.01f && std::fabs(qy - (0.7f * H + 0.135f * 1.5f * H)) < 0.01f,
              "A placed at %.1f,%.1f", qx, qy);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 1, qx / W, qy / H));
        p = pad_of(q);
        CHECK(p.buttons == 0x2000 && p.lt == 0, "A mapped to B: %04x %u", p.buttons, p.lt);
        sa2_touch::control_centre(q, W, H, Control::LT, &qx, &qy);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 2, qx / W, qy / H));
        p = pad_of(q);
        CHECK(p.buttons == (0x2000 | 0x0010) && p.lt == 0, "LT mapped to START: %04x %u", p.buttons, p.lt);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 1, 0, 0));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 2, 0, 0));
        // The stick takes the right half now, and the left half has none.
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 3, 0.6f, 0.4f));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_MOTION, 3, 0.6f + 1.5f * R / W, 0.4f));
        p = pad_of(q);
        CHECK(p.lx == 32767 && p.buttons == 0, "no stick on the right half: %d %04x", p.lx, p.buttons);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 3, 0, 0));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 4, 0.45f, 0.3f));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_MOTION, 4, 0.45f - 1.5f * R / W, 0.3f));
        p = pad_of(q);
        CHECK(p.lx == 0 && p.ly == 0 && p.buttons == 0, "a stick on the left half: %d %d %04x", p.lx, p.ly, p.buttons);
        sa2_touch::destroy(q);
    }
    {
        // A portrait window and a 4:3 frame: the picture is centred, 720x540 over rows 510 to
        // 1050, and the controls are in the 510 rows below it, at the window's short side's size
        // (the band needs 0.6 x 720 = 432).
        sa2_touch::Overlay* q = sa2_touch::create(nullptr, nullptr);
        sa2_touch::set_size(q, 720, 1560);
        CHECK(!sa2_touch::below_picture(q, 720, 1560), "below the picture with no frame");
        sa2_touch::set_frame(q, 1440, 1080);
        CHECK(sa2_touch::below_picture(q, 720, 1560), "not below a portrait window's picture");
        CHECK(std::fabs(sa2_touch::stick_radius(q, 720, 1560) - 0.12f * 720) < 0.01f, "band stick radius %.2f",
              sa2_touch::stick_radius(q, 720, 1560));
        const float m = 0.04f * 720, d = 0.135f * 720, r = 0.075f * 720;
        float qx, qy;
        sa2_touch::control_centre(q, 720, 1560, Control::A, &qx, &qy);
        CHECK(std::fabs(qx - (720 - m - d - r)) < 0.01f && std::fabs(qy - (1560 - m - r)) < 0.01f, "A at %.1f,%.1f", qx,
              qy);
        sa2_touch::control_centre(q, 720, 1560, Control::LT, &qx, &qy);
        CHECK(std::fabs(qx - (m + 0.09f * 720)) < 0.01f && std::fabs(qy - (1050 + m + 0.04f * 720)) < 0.01f,
              "LT at %.1f,%.1f", qx, qy);
        for (Control c : {Control::Stick, Control::A, Control::B, Control::X, Control::Y, Control::Start, Control::LT,
                          Control::RT}) {
            sa2_touch::control_centre(q, 720, 1560, c, &qx, &qy);
            CHECK(qy > 1050 && qy < 1560 && qx > 0 && qx < 720, "control %d at %.1f,%.1f is not in the band", int(c),
                  qx, qy);
        }
        sa2_touch::control_centre(q, 720, 1560, Control::A, &qx, &qy);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 1, qx / 720, qy / 1560));
        CHECK(pad_of(q).buttons == 0x1000, "A in the band: %04x", pad_of(q).buttons);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 1, 0, 0));
        // A shorter band makes them smaller: a 16:9 portrait window leaves 370 rows (910 to 1280).
        CHECK(sa2_touch::below_picture(q, 720, 1280), "not below a 16:9 portrait window's picture");
        CHECK(std::fabs(sa2_touch::stick_radius(q, 720, 1280) - 0.12f * 370 / 0.6f) < 0.01f,
              "short band stick radius %.2f", sa2_touch::stick_radius(q, 720, 1280));
        // A landscape window with the same frame: over the picture, the places of no frame.
        CHECK(!sa2_touch::below_picture(q, 1560, 720), "below a landscape window's picture");
        float nx2, ny2;
        sa2_touch::control_centre(q, 1560, 720, Control::A, &qx, &qy);
        sa2_touch::control_centre(nullptr, 1560, 720, Control::A, &nx2, &ny2);
        CHECK(qx == nx2 && qy == ny2, "landscape A at %.1f,%.1f, not %.1f,%.1f", qx, qy, nx2, ny2);
        // SA2_TOUCH_LAYOUT_BELOW places in the band, and SA2_TOUCH_LAYOUT does not apply there.
        sa2_touch::Settings s;
        std::string why;
        sa2_touch::parse_setting("SA2_TOUCH_LAYOUT_BELOW", "START=0.5,0.5", &s, &why);
        sa2_touch::parse_setting("SA2_TOUCH_LAYOUT", "START=0.1,0.9", &s, &why);
        sa2_touch::set_settings(q, s);
        sa2_touch::control_centre(q, 720, 1560, Control::Start, &qx, &qy);
        CHECK(std::fabs(qx - 360) < 0.01f && std::fabs(qy - 1305) < 0.01f, "START below at %.1f,%.1f", qx, qy);
        sa2_touch::control_centre(q, 1560, 720, Control::Start, &qx, &qy);
        CHECK(std::fabs(qx - 156) < 0.01f && std::fabs(qy - 648) < 0.01f, "START over at %.1f,%.1f", qx, qy);
        sa2_touch::destroy(q);
    }
    std::printf("settings and the band: %s\n", fails ? "FAILED" : "ok");

    // ---------------------------------------------------------------- drawing
    if (!SDL_Init(SDL_INIT_VIDEO)) { std::printf("FAIL SDL_Init: %s\n", SDL_GetError()); return 1; }
    Gpu g;
    g.dev = SDL_CreateGPUDevice(SDL_GPU_SHADERFORMAT_SPIRV, false, "vulkan");
    if (!g.dev) { std::printf("FAIL SDL_CreateGPUDevice: %s\n", SDL_GetError()); return 1; }
    std::printf("gpu: %s\n", SDL_GetGPUDeviceDriver(g.dev));
    if (!make_target(&g, W, H)) { std::printf("FAIL target: %s\n", SDL_GetError()); return 1; }
    o = sa2_touch::create(g.dev, nullptr);
    sa2_touch::set_target_format(o, SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM);
    std::vector<uint8_t> px;
    CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
    CHECK(lit(px) == 0 && sa2_touch::stats(o).draws == 0, "inactive overlay drew %llu pixels",
          (unsigned long long)lit(px));
    // A held, the stick's finger pulled two radii up and right of where it landed: its base stays
    // there and its knob is at the rim, a radius out.
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, ax, ay));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 2, sx, sy));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx + 1.4f * R / W, sy - 1.4f * R / H));
    CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
    CHECK(sa2_touch::stats(o).draws == 1 && sa2_touch::stats(o).gpu, "draws %llu, error %s",
          (unsigned long long)sa2_touch::stats(o).draws, sa2_touch::stats(o).error.c_str());
    if (!out.empty()) {
        std::string err;
        const std::string path = out + "/overlay.png";
        CHECK(tex::write_png_rgb(path.c_str(), px.data(), W, H, W * 4, &err), "png: %s", err.c_str());
        std::printf("wrote %s\n", path.c_str());
    }
    float x, y;
    const auto colour = [&](Control c) { sa2_touch::control_centre(o, W, H, c, &x, &y); return pixel(px, W, x, y); };
    const Bgr a = colour(Control::A), b = colour(Control::B), xb = colour(Control::X), yb = colour(Control::Y);
    std::printf("A %d,%d,%d  B %d,%d,%d  X %d,%d,%d  Y %d,%d,%d (BGR)\n", a.b, a.g, a.r, b.b, b.g, b.r, xb.b, xb.g,
                xb.r, yb.b, yb.g, yb.r);
    CHECK(a.g > 150 && a.g > a.r + 60 && a.g > a.b + 60, "A (held) is not bright green");
    CHECK(b.r > 60 && b.r > b.g + 30 && b.r > b.b + 30, "B is not red");
    CHECK(xb.b > 60 && xb.b > xb.r + 30, "X is not blue");
    CHECK(yb.r > 60 && yb.g > 50 && yb.b < yb.r - 30, "Y is not yellow");
    CHECK(a.g > b.r + 40, "the held button is not brighter than an idle one");
    // The knob, a radius' 0.7 up and right of the origin; and the pixels no control covers.
    const Bgr knob = pixel(px, W, sx * W + 0.7f * R, sy * H - 0.7f * R);
    CHECK(knob.r > 140 && knob.g > 140 && knob.b > 140, "the stick's knob is not white: %d,%d,%d", knob.b, knob.g,
          knob.r);
    // The base's rim on the side away from the push is where the finger landed, and nothing is
    // under the finger itself, out past the rim.
    const float line = std::max(2.0f, 0.008f * float(H));
    const Bgr rim = pixel(px, W, sx * W - (R - 0.5f * line), sy * H);
    CHECK(rim.r > 100 && rim.g > 100 && rim.b > 100, "the stick's base moved: its rim %d,%d,%d", rim.b, rim.g, rim.r);
    const Bgr under = pixel(px, W, sx * W + 1.4f * R, sy * H - 1.4f * R);
    CHECK(under.r == 0 && under.g == 0 && under.b == 0, "drawn under the finger past the rim: %d,%d,%d", under.b,
          under.g, under.r);
    const Bgr mid = pixel(px, W, W * 0.5f, H * 0.5f), corner = pixel(px, W, W - 3.0f, H * 0.5f);
    CHECK(mid.r == 0 && mid.g == 0 && mid.b == 0 && corner.r == 0 && corner.g == 0 && corner.b == 0,
          "pixels away from the controls were drawn");
    const Bgr start = colour(Control::Start), lt = colour(Control::LT);
    CHECK(start.r > 40 && lt.r > 40, "START or LT not drawn: %d %d", start.r, lt.r);
    const double cover = double(lit(px)) / (double(W) * H);
    std::printf("overlay covers %.1f%% of the frame\n", 100.0 * cover);
    CHECK(cover > 0.03 && cover < 0.30, "coverage %.3f", cover);
    sa2_touch::deactivate(o);
    CHECK(frame(&g, o, &px) && lit(px) == 0, "a deactivated overlay drew");
    // Opacity: twice the default makes an idle button brighter; 0 draws nothing at all.
    {
        sa2_touch::Settings s;
        std::string why;
        sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, 0.5f, 0.6f));
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        const Bgr b1 = colour(Control::B);
        sa2_touch::parse_setting("SA2_TOUCH_OPACITY", "2", &s, &why);
        sa2_touch::set_settings(o, s);
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        const Bgr b2 = colour(Control::B);
        CHECK(b2.r > b1.r + 40, "B at opacity 2 is not brighter: %d, %d at 1", b2.r, b1.r);
        sa2_touch::parse_setting("SA2_TOUCH_OPACITY", "0", &s, &why);
        sa2_touch::set_settings(o, s);
        const uint64_t draws = sa2_touch::stats(o).draws;
        CHECK(frame(&g, o, &px) && lit(px) == 0 && sa2_touch::stats(o).draws == draws, "opacity 0 drew %llu pixels",
              (unsigned long long)lit(px));
        sa2_touch::set_settings(o, sa2_touch::Settings{});
        sa2_touch::deactivate(o);
    }
    // Below a portrait window's 4:3 picture (rows 510 to 1050 of 720x1560): every control in the
    // band, none over the picture.
    {
        release_target(&g);
        if (!make_target(&g, 720, 1560)) { std::printf("FAIL target: %s\n", SDL_GetError()); return 1; }
        sa2_touch::Overlay* q = sa2_touch::create(g.dev, nullptr);
        sa2_touch::set_target_format(q, SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM);
        sa2_touch::set_size(q, 720, 1560);
        sa2_touch::set_frame(q, 1440, 1080);
        float qx, qy;
        sa2_touch::control_centre(q, 720, 1560, Control::A, &qx, &qy);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 1, qx / 720, qy / 1560));
        CHECK(frame(&g, q, &px), "frame: %s", SDL_GetError());
        const Bgr ab = pixel(px, 720, qx, qy);
        CHECK(ab.g > 150 && ab.g > ab.r + 60, "A (held) in the band is not bright green: %d,%d,%d", ab.b, ab.g, ab.r);
        uint64_t over = 0, band = 0;
        for (uint32_t row = 0; row < 1560; ++row)
            for (uint32_t col = 0; col < 720; ++col) {
                const size_t i = (size_t(row) * 720 + col) * 4;
                if (px[i] | px[i + 1] | px[i + 2]) (row < 1050 ? over : band)++;
            }
        CHECK(over == 0 && band > 20000, "portrait: %llu pixels drawn above the band, %llu in it",
              (unsigned long long)over, (unsigned long long)band);
        if (!out.empty()) {
            std::string err;
            const std::string path = out + "/overlay-portrait.png";
            CHECK(tex::write_png_rgb(path.c_str(), px.data(), 720, 1560, 720 * 4, &err), "png: %s", err.c_str());
            std::printf("wrote %s\n", path.c_str());
        }
        sa2_touch::destroy(q);
    }

    // ---------------------------------------------------------------- cost, at 1920x1080
    release_target(&g);
    if (!make_target(&g, 1920, 1080)) { std::printf("FAIL target: %s\n", SDL_GetError()); return 1; }
    // Each frame submitted and waited for alone, with the overlay and (inactive) without: the
    // difference is what the overlay costs a frame; the encoding is timed on the CPU.
    const int N = 300;
    const auto run = [&](bool with, double* encode_s) {
        if (with) {
            sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, ax, ay));
            sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 2, sx, sy));
        } else {
            sa2_touch::deactivate(o);
        }
        *encode_s = 0;
        const auto t0 = std::chrono::steady_clock::now();
        for (int i = 0; i < N; ++i) {
            SDL_GPUCommandBuffer* cb = SDL_AcquireGPUCommandBuffer(g.dev);
            if (with) sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx + 0.001f * (i % 50), sy));
            const auto e0 = std::chrono::steady_clock::now();
            sa2_touch::draw(o, cb, g.tex, g.w, g.h);
            *encode_s += std::chrono::duration<double>(std::chrono::steady_clock::now() - e0).count();
            SDL_GPUFence* f = SDL_SubmitGPUCommandBufferAndAcquireFence(cb);
            SDL_WaitForGPUFences(g.dev, true, &f, 1);
            SDL_ReleaseGPUFence(g.dev, f);
        }
        return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() / N;
    };
    double enc_with = 0, enc_without = 0;
    run(true, &enc_with);                         // warm: the pipeline is made on the first draw
    const double without = run(false, &enc_without);
    const double with = run(true, &enc_with);
    const double cost = with - without;
    std::printf("cost at 1920x1080 over %d frames: encode %.1f us/frame; a frame %.1f us with the overlay, "
                "%.1f us without: %.1f us\n", N, 1e6 * enc_with / N, 1e6 * with, 1e6 * without, 1e6 * cost);
    CHECK(enc_with / N < 0.0001, "encoding costs %.1f us", 1e6 * enc_with / N);
    CHECK(cost < 0.0005, "the overlay costs %.1f us a frame", 1e6 * cost);
    CHECK(sa2_touch::stats(o).draw_failures == 0, "draw failures: %s", sa2_touch::stats(o).error.c_str());
    sa2_touch::destroy(o);
    release_target(&g);
    SDL_DestroyGPUDevice(g.dev);
    SDL_Quit();
    if (fails) { std::printf("%d checks failed\n", fails); return 1; }
    std::printf("TOUCH_OVERLAY_OK\n");
    return 0;
}
