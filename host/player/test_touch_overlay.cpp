// test_touch_overlay.cpp: the touch overlay (touch_overlay.h) on its own: the pad it maps
// from finger events, and what it draws on a real SDL_gpu device (Vulkan, no window): an
// offscreen target cleared to black, the overlay drawn over it, read back and its pixels
// checked where each control is and where none is; nothing drawn while inactive; and its
// cost per frame, CPU encoding and GPU time, over many frames at 1920x1080.
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
    sa2_touch::control_centre(W, H, c, x, y);
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
    const auto colour = [&](Control c) { sa2_touch::control_centre(W, H, c, &x, &y); return pixel(px, W, x, y); };
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
