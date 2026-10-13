// test_d3d9gpu.cpp: the device's unit test, on the CPU model (d9g_gpu_cpu.cpp): no
// GPU, no window, no SDL library. Documentation/scripts/tests/test_s5_d3d9gpu.py builds
// and runs it.
//
//   test_d3d9gpu                  every unit check; TEST_D3D9GPU_OK and exit 0 when all pass
//   test_d3d9gpu --clear-cases    one clear case per stdin line, one JSON result per line:
//       vp_x vp_y vp_w vp_h sc_l sc_t sc_r sc_b scissor count has_rects n x1 y1 x2 y2 ... img_w img_h
//   test_d3d9gpu --clear-matrix   the clear matrix (clear_matrix.h) on the CPU model
//   test_d3d9gpu --report PATH    a scripted device session's report, for its shape
//   test_d3d9gpu --pipeline-session PATH [FAULTS] [--caps=NAMES]   R2b's session (pipeline_session):
//                                 its report and the keys file beside it; NAMES the SDL patch's
//                                 capabilities the CPU model reports ("border", "depth-clamp",
//                                 comma-separated; "none" or no --caps: neither, a stock SDL3's)
#include "d9g.h"
#include "sha256.h"
#include "texcodec.h"

#include "clear_matrix.h"

#include <SDL3/SDL_gpu.h>

#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

namespace {

int g_checks = 0, g_fails = 0;
#define CHECK(c) do { g_checks++; if (!(c)) { g_fails++; std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #c); } } while (0)

constexpr uint32_t kDXT1 = 0x31545844u, kDXT3 = 0x33545844u, kDXT5 = 0x35545844u;
constexpr DWORD kBehavior = D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE;

D3DPRESENT_PARAMETERS present_params() {
    D3DPRESENT_PARAMETERS pp{};
    pp.BackBufferWidth = 1280;
    pp.BackBufferHeight = 720;
    pp.BackBufferFormat = D3DFMT_A8R8G8B8;
    pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.Windowed = TRUE;
    pp.EnableAutoDepthStencil = TRUE;
    pp.AutoDepthStencilFormat = D3DFMT_D24S8;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    return pp;
}

d9g::Device* make_device(uint32_t faults = 0, DWORD behavior = kBehavior, bool border = false, bool clamp = false) {
    d9g::report() = d9g::Report{};
    d9g::report().fault.bits = faults;
    d9g::DeviceSetup s;
    s.pp = present_params();
    s.behavior = behavior;
    auto* d = new d9g::Device(d9g::make_cpu_gpu(border, clamp), s);
    std::string why;
    const bool ok = d->init(&why);
    CHECK(ok);
    if (!ok) std::printf("  init: %s\n", why.c_str());
    return d;
}

// The back buffer's pixel (x, y) as BGRA, read back as the player reads it.
uint32_t bb_pixel(d9g::Device* d, uint32_t x, uint32_t y) {
    IDirect3DSurface9 *bb = nullptr, *sys = nullptr;
    d->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb);
    d->CreateOffscreenPlainSurface(1280, 720, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &sys, nullptr);
    uint32_t v = 0xDEADBEEF;
    if (SUCCEEDED(d->GetRenderTargetData(bb, sys))) {
        D3DLOCKED_RECT lr{};
        if (SUCCEEDED(sys->LockRect(&lr, nullptr, D3DLOCK_READONLY))) {
            std::memcpy(&v, static_cast<uint8_t*>(lr.pBits) + size_t(y) * lr.Pitch + 4 * x, 4);
            sys->UnlockRect();
        }
    }
    sys->Release();
    bb->Release();
    return v;   // little-endian: B | G << 8 | R << 16 | A << 24
}

uint32_t cpu_word(d9g::Device* d, IDirect3DSurface9* s, uint32_t x, uint32_t y) {
    auto* surf = static_cast<d9g::Surface*>(s);
    std::string bytes;
    if (!d9g::cpu_gpu_bytes(d->gpu(), surf->image()->gpu, surf->level(), &bytes)) return 0xDEADBEEF;
    uint32_t v;
    std::memcpy(&v, bytes.data() + (size_t(y) * surf->width() + x) * 4, 4);
    return v;
}

void test_formats() {
    using namespace d9g;
    const FormatInfo* a = format_info(21);
    CHECK(a && a->gpu == GFmt::bgra8 && a->pixel_bytes == 4 && !a->x8);
    CHECK(format_info(22) && format_info(22)->x8);
    CHECK(format_info(kDXT1)->gpu == GFmt::bc1 && format_info(kDXT1)->block_bytes == 8);
    CHECK(format_info(kDXT3)->gpu == GFmt::bc2 && format_info(kDXT3)->block_bytes == 16);
    CHECK(format_info(kDXT5)->gpu == GFmt::bc3 && format_info(kDXT5)->block_bytes == 16);
    CHECK(format_info(75)->depth && format_info(75)->stencil);
    CHECK(!format_info(23) && !format_info(0) && !format_info(77));
    // The packed pitch and rows are texcodec's (the recording device's) for every size.
    for (uint32_t f : {21u, 22u, kDXT1, kDXT3, kDXT5})
        for (uint32_t w = 1; w <= 300; w++) {
            CHECK(level_pitch(w, *format_info(f)) == tex::level_pitch(w, f));
            CHECK(level_rows(w, *format_info(f)) == tex::level_rows(w, f));
        }
    CHECK(level_pitch(64, *format_info(21)) == 256 && level_pitch(64, *format_info(kDXT1)) == 128);
    CHECK(level_rows(64, *format_info(kDXT5)) == 16 && level_pitch(2, *format_info(kDXT1)) == 8);
    CHECK(full_chain(256, 256) == 9 && full_chain(1280, 720) == 11 && full_chain(1, 1) == 1 && full_chain(512, 1) == 10);
    uint8_t px[8] = {1, 2, 3, 0, 5, 6, 7, 9};
    x8_alpha_fill(px, sizeof px);
    CHECK(px[3] == 0xFF && px[7] == 0xFF && px[0] == 1 && px[6] == 7);
    float c[4];
    decode_d3dcolor(0xFF336699u, c);
    CHECK(c[0] == float(0x33) / 255.0f && c[1] == float(0x66) / 255.0f && c[2] == float(0x99) / 255.0f && c[3] == 1.0f);
    decode_d3dcolor(0x80000000u, c);
    CHECK(c[3] == 128.0f / 255.0f && c[0] == 0.0f);
}

void test_declarations_and_fans() {
    using namespace d9g;
    const D3DVERTEXELEMENT9 e[] = {{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0},
                                   {0, 12, D3DDECLTYPE_D3DCOLOR, 0, D3DDECLUSAGE_COLOR, 0},
                                   {0, 16, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0},
                                   {0, 24, D3DDECLTYPE_UBYTE4, 0, D3DDECLUSAGE_BLENDINDICES, 0}};
    const DeclInfo d = decl_info(e, 4);
    CHECK(d.elements.size() == 4);
    CHECK(d.elements[0].sdl_format == SDL_GPU_VERTEXELEMENTFORMAT_FLOAT3);
    CHECK(d.elements[1].sdl_format == SDL_GPU_VERTEXELEMENTFORMAT_UBYTE4_NORM);
    CHECK(d.elements[2].sdl_format == SDL_GPU_VERTEXELEMENTFORMAT_FLOAT2);
    CHECK(d.elements[3].sdl_format == SDL_GPU_VERTEXELEMENTFORMAT_INVALID);   // USCALED: no SDL_gpu format
    CHECK(d.bgra_mask == 0x2u && d.without_sdl_format == 1);
    CHECK(d.elements[1].offset == 12 && d.elements[2].usage == D3DDECLUSAGE_TEXCOORD);
    CHECK(decltype_sdl_format(D3DDECLTYPE_SHORT2N) == SDL_GPU_VERTEXELEMENTFORMAT_SHORT2_NORM);
    CHECK(decltype_sdl_format(D3DDECLTYPE_FLOAT16_4) == SDL_GPU_VERTEXELEMENTFORMAT_HALF4);
    CHECK(decltype_sdl_format(D3DDECLTYPE_DEC3N) == SDL_GPU_VERTEXELEMENTFORMAT_INVALID);
    // Vulkan's fan order: (i + 1, i + 2, 0), each a rotation of D3D9's (0, i + 1, i + 2).
    const std::vector<uint32_t> f = fan_indices(5);
    CHECK((f == std::vector<uint32_t>{1, 2, 0, 2, 3, 0, 3, 4, 0}));
    CHECK(fan_indices(2).empty() && fan_indices(3) == (std::vector<uint32_t>{1, 2, 0}));
    for (size_t t = 0; t < f.size() / 3; t++) {
        const uint32_t d3d[3] = {0, uint32_t(t + 1), uint32_t(t + 2)};
        CHECK(f[3 * t] == d3d[1] && f[3 * t + 1] == d3d[2] && f[3 * t + 2] == d3d[0]);
    }
    CHECK(vertex_count(D3DPT_POINTLIST, 7) == 7 && vertex_count(D3DPT_LINELIST, 7) == 14);
    CHECK(vertex_count(D3DPT_LINESTRIP, 7) == 8 && vertex_count(D3DPT_TRIANGLELIST, 7) == 21);
    CHECK(vertex_count(D3DPT_TRIANGLESTRIP, 7) == 9 && vertex_count(D3DPT_TRIANGLEFAN, 7) == 9);
    CHECK(vertex_count(D3DPRIMITIVETYPE(9), 7) == 21);   // DXVK's default is the list's
    CHECK(primitive_name(5) == "TRIANGLESTRIP" && primitive_name(9) == "TYPE_9");
}

void test_clear_rule() {
    using namespace d9g;
    ClearInput in;
    in.vp_w = 1280; in.vp_h = 720; in.sc_right = 1280; in.sc_bottom = 720;
    auto regs = clear_regions(in);
    CHECK(regs.size() == 1 && regs[0].w == 1280 && clear_image(regs[0], 1280, 720).kind == ClearKind::full);
    in.vp_w = 2560; in.vp_h = 1080;                         // the 2560x1080 viewport is a full clear
    regs = clear_regions(in);
    CHECK(regs.size() == 1 && clear_image(regs[0], 1280, 720).kind == ClearKind::full);
    in.vp_w = 512; in.vp_h = 512;                           // Metal Harbor's shadow passes
    in.scissor = true; in.sc_right = 256; in.sc_bottom = 192;
    regs = clear_regions(in);
    const ImageClear ic = clear_image(regs[0], 512, 512);
    CHECK(ic.kind == ClearKind::partial && ic.box.w == 256 && ic.box.h == 192 && !ic.past_edge);
    in.sc_left = 600;                                       // sc.right - offset.x wraps: the viewport's width stays
    regs = clear_regions(in);
    CHECK(regs.size() == 1 && regs[0].x == 600 && regs[0].w == 512);
    CHECK(clear_image(regs[0], 512, 512).kind == ClearKind::skip);
    ClearBox past{1000, 0, 400, 100};
    CHECK(clear_image(past, 1280, 720).past_edge);
    in = ClearInput{};
    in.vp_w = 100; in.vp_h = 100;
    const int32_t rects[8] = {10, 10, 20, 20, 200, 200, 300, 300};
    in.count = 2; in.rects = rects;
    regs = clear_regions(in);
    CHECK(regs.size() == 1 && regs[0].x == 10 && regs[0].w == 10);
    in.rects = nullptr;                                     // Count with NULL rectangles: the whole region
    regs = clear_regions(in);
    CHECK(regs.size() == 1 && regs[0].w == 100);
    in.vp_w = 0;
    CHECK(clear_regions(in).empty());
}

void test_shaders() {
    using namespace d9g;
    bool ni = false;
    std::string why;
    // vs_3_0: a comment of 2 tokens, dcl (2 operands), mov (2 operands), END.
    const DWORD vs[] = {0xFFFE0300u, 0x0002FFFEu, 0x11111111u, 0x22222222u, 0x0200001Fu, 0x80000000u, 0x900F0000u,
                        0x02000001u, 0xC00F0000u, 0x90E40000u, 0x0000FFFFu, 0xAAAAAAAAu};
    CHECK(shader_tokens(vs, true, &ni, &why) == 11 && !ni);
    CHECK(shader_tokens(vs, false, &ni, &why) == 0 && !ni);  // a VS for the pixel stage: refused
    const DWORD ps2[] = {0xFFFF0200u, 0x0000FFFFu};
    CHECK(shader_tokens(ps2, false, &ni, &why) == 2);
    const DWORD ps31[] = {0xFFFF0301u, 0x0000FFFFu};
    CHECK(shader_tokens(ps31, false, &ni, &why) == 0 && !ni);
    const DWORD vs11[] = {0xFFFE0101u, 0x0000FFFFu};
    CHECK(shader_tokens(vs11, true, &ni, &why) == 0 && ni);
    const DWORD vs12[] = {0xFFFE0102u, 0x0000FFFFu};
    CHECK(shader_tokens(vs12, true, &ni, &why) == 0 && !ni);
    CHECK(shader_tokens(nullptr, true, &ni, &why) == 0);
}

void test_faults_paths_and_parameters() {
    using namespace d9g;
    FaultSet f = parse_faults("clear-colour,present-scribble");
    CHECK(f.bits == (F_CLEAR_COLOUR | F_PRESENT_SCRIBBLE) && f.error.empty());
    f = parse_faults("clear-noop,bogus");
    CHECK(f.bits == F_CLEAR_NOOP && f.error == "unknown fault bogus");
    CHECK(parse_faults(nullptr).bits == 0 && parse_faults("").names.empty());
    // the phase-B mapping faults are named, each its own bit
    CHECK(parse_faults("map-border-colour,map-depth-clamp-off,map-depth-bias-push").map_bits ==
          (MF_BORDER_COLOUR | MF_DEPTH_CLAMP_OFF | MF_DEPTH_BIAS_PUSH));
    CHECK(parse_faults("map-depth-clamp-extra,map-border-swizzled-view,map-border-fallback").map_bits ==
          (MF_DEPTH_CLAMP_EXTRA | MF_BORDER_SWIZZLED_VIEW | MF_BORDER_FALLBACK));
    // the capability override: a test run's only (SA2_D3D9GPU_TEST_RUN=1), its names known
    CapsOff c = parse_caps_off("depth-clamp,border", "1");
    CHECK(c.border && c.depth_clamp && c.names == "border,depth-clamp" && c.error.empty());
    c = parse_caps_off("border", nullptr);
    CHECK(c.border && !c.error.empty() && c.error.find("refused outside one") != std::string::npos);
    c = parse_caps_off("border", "0");
    CHECK(!c.error.empty());
    c = parse_caps_off("border,stencil", "1");
    CHECK(c.error.find("stencil") != std::string::npos);
    c = parse_caps_off(nullptr, nullptr);
    CHECK(c.names.empty() && c.error.empty() && !c.border && !c.depth_clamp && !c.sub_dword);
    // Present's fit of the back buffer: a 1280x720 back buffer on a 19.5:9 phone (2400x1080) is
    // pillarboxed to 1920x1080 at x 240; on a 4:3 window (1024x768) letterboxed to 1024x576 at y 96;
    // on its own aspect (1920x1080, 1280x720) it covers the swapchain whole
    auto fit = [](uint32_t bw, uint32_t bh, uint32_t w, uint32_t h) {
        const FitRect r = fit_rect(bw, bh, w, h);
        return std::array<uint32_t, 4>{r.x, r.y, r.w, r.h};
    };
    CHECK((fit(1280, 720, 2400, 1080) == std::array<uint32_t, 4>{240, 0, 1920, 1080}));
    CHECK((fit(1280, 720, 1024, 768) == std::array<uint32_t, 4>{0, 96, 1024, 576}));
    CHECK((fit(1280, 720, 1920, 1080) == std::array<uint32_t, 4>{0, 0, 1920, 1080}));
    CHECK((fit(1280, 720, 1280, 720) == std::array<uint32_t, 4>{0, 0, 1280, 720}));
    CHECK((fit(640, 480, 2340, 1080) == std::array<uint32_t, 4>{450, 0, 1440, 1080}));
    // play's back buffer at the phone's aspect (runtime/sa2_widescreen.c): exact (1560x720 on
    // 2340x1080) or within kFitWholeSlack px of it on both axes (1561x720 fits 2340x1079, 1559x720
    // 2339x1080, 2338x1080 2338x1080) covers the swapchain whole; 3 px off keeps its bars, and so
    // does the installer's 1280x720 on that phone, pillarboxed as before
    CHECK((fit(1560, 720, 2340, 1080) == std::array<uint32_t, 4>{0, 0, 2340, 1080}));
    CHECK((fit(1561, 720, 2340, 1080) == std::array<uint32_t, 4>{0, 0, 2340, 1080}));
    CHECK((fit(1559, 720, 2340, 1080) == std::array<uint32_t, 4>{0, 0, 2340, 1080}));
    CHECK((fit(2340, 1081, 2340, 1080) == std::array<uint32_t, 4>{0, 0, 2340, 1080}));
    CHECK((fit(2338, 1080, 2340, 1080) == std::array<uint32_t, 4>{0, 0, 2340, 1080}));
    CHECK((fit(2337, 1080, 2340, 1080) == std::array<uint32_t, 4>{1, 0, 2337, 1080}));
    CHECK((fit(1280, 720, 2340, 1080) == std::array<uint32_t, 4>{210, 0, 1920, 1080}));
    CHECK((fit(1707, 720, 2560, 1080) == std::array<uint32_t, 4>{0, 0, 2560, 1080}));
    CHECK((fit(1280, 720, 0, 0) == std::array<uint32_t, 4>{0, 0, 0, 0}));
    // sub-dword: the device made without the sub-dword features (the state words' leg), named last
    c = parse_caps_off("sub-dword,border", "1");
    CHECK(c.border && !c.depth_clamp && c.sub_dword && c.names == "border,sub-dword" && c.error.empty());
    c = parse_caps_off("sub-dword", nullptr);
    CHECK(c.sub_dword && c.error.find("refused outside one") != std::string::npos);
    GpuInfo g;
    g.sampler_clamp_to_border_detected = g.depth_clip_and_clamp_detected = true;
    apply_caps_off(g, parse_caps_off("depth-clamp", "1"));
    CHECK(g.sampler_clamp_to_border && !g.depth_clip_and_clamp && g.depth_clip_and_clamp_detected &&
          g.capability_override == "depth-clamp");
    apply_caps_off(g, CapsOff{});
    CHECK(g.sampler_clamp_to_border && g.depth_clip_and_clamp && g.capability_override.empty());
    // AGENTS.md rule 9: inside a working tree only private/ (this test runs from anywhere,
    // so it makes its own tree under the build directory).
    const std::string root = std::string(SA2_TEST_TMP) + "/refuse-tree";
    std::error_code ec;
    std::filesystem::create_directories(root + "/.git", ec);
    std::filesystem::create_directories(root + "/private/x", ec);
    CHECK(!refuse_output(root + "/report.json").empty());
    CHECK(refuse_output(root + "/private/x/report.json").empty());
    CHECK(!refuse_output(root + "/private-not/report.json").empty());
    bool ni = false;
    std::string why;
    D3DPRESENT_PARAMETERS pp = present_params();
    CHECK(check_present_parameters(pp, &ni, &why) == D3D_OK);
    pp.BackBufferCount = 2;
    CHECK(check_present_parameters(pp, &ni, &why) == D3DERR_INVALIDCALL && ni);   // DXVK would rotate
    pp = present_params();
    pp.SwapEffect = D3DSWAPEFFECT_COPY;
    pp.BackBufferCount = 2;
    CHECK(check_present_parameters(pp, &ni, &why) == D3DERR_INVALIDCALL && !ni);  // DXVK refuses it
    pp = present_params();
    pp.SwapEffect = D3DSWAPEFFECT_FLIP;
    CHECK(check_present_parameters(pp, &ni, &why) == D3DERR_INVALIDCALL && ni);
    pp = present_params();
    pp.Windowed = FALSE;
    CHECK(check_present_parameters(pp, &ni, &why) == D3DERR_INVALIDCALL && ni);
    pp = present_params();
    pp.BackBufferFormat = D3DFMT_R5G6B5;
    CHECK(check_present_parameters(pp, &ni, &why) == D3DERR_INVALIDCALL && ni);
    pp = present_params();
    pp.SwapEffect = D3DSWAPEFFECT(0);
    CHECK(check_present_parameters(pp, &ni, &why) == D3DERR_INVALIDCALL && !ni);
}

void test_lifetimes() {
    d9g::Device* d = make_device();
    IDirect3DSurface9* bb = nullptr;
    CHECK(d->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb) == D3D_OK && bb);
    CHECK(d->GetBackBuffer(0, 1, D3DBACKBUFFER_TYPE_MONO, &bb) == D3DERR_INVALIDCALL && !bb);
    CHECK(d->GetBackBuffer(1, 0, D3DBACKBUFFER_TYPE_MONO, &bb) == D3DERR_INVALIDCALL);
    d->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb);
    CHECK(bb->Release() == 1);              // two answers, one released
    CHECK(bb->Release() == 0);              // the device keeps its own back buffer
    CHECK(d->live_count() == 0);
    IDirect3DTexture9* t = nullptr;
    CHECK(d->CreateTexture(64, 64, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr) == D3D_OK);
    CHECK(t->GetLevelCount() == 7 && d->live_count() == 1);
    IDirect3DSurface9 *lv = nullptr, *bad = nullptr;
    CHECK(t->GetSurfaceLevel(7, &bad) == D3DERR_INVALIDCALL && !bad);
    CHECK(t->GetSurfaceLevel(2, &lv) == D3D_OK && lv);
    CHECK(t->Release() == 1);               // the level counts on its texture (D3D9's container rule)
    CHECK(d->live_count() == 1);
    CHECK(lv->AddRef() == 2 && lv->Release() == 1);
    CHECK(lv->Release() == 0 && d->live_count() == 0);
    // A binding keeps an object the caller released, until it is unbound.
    CHECK(d->CreateTexture(16, 16, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr) == D3D_OK);
    CHECK(d->SetTexture(0, t) == D3D_OK);
    CHECK(t->Release() == 0 && d->live_count() == 1);
    CHECK(d->SetTexture(0, nullptr) == D3D_OK && d->live_count() == 0);
    CHECK(d->SetTexture(17, nullptr) == D3D_OK);   // DXVK's InvalidSampler: answered D3D_OK
    // An object the caller never releases is counted at the device's end and cut loose.
    IDirect3DVertexBuffer9* vb = nullptr;
    CHECK(d->CreateVertexBuffer(96, D3DUSAGE_WRITEONLY, 0, D3DPOOL_MANAGED, &vb, nullptr) == D3D_OK);
    CHECK(d->Release() == 0);
    CHECK(d9g::report().complete && d9g::report().live_at_destroy["vertex_buffer"] == 1);
    CHECK(vb->Release() == 0);              // after the device: no crash, nothing to free on a GPU
}

void test_answers() {
    d9g::Device* d = make_device();
    CHECK(d->BeginScene() == D3D_OK && d->BeginScene() == D3DERR_INVALIDCALL);
    CHECK(d->EndScene() == D3D_OK && d->EndScene() == D3DERR_INVALIDCALL);
    IDirect3DSurface9 *bb = nullptr, *sys = nullptr, *sysx = nullptr, *rt = nullptr;
    d->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb);
    CHECK(d->SetRenderTarget(0, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->SetRenderTarget(4, bb) == D3DERR_INVALIDCALL);
    CHECK(d->SetRenderTarget(1, nullptr) == D3D_OK);
    CHECK(d->CreateOffscreenPlainSurface(1280, 720, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &sys, nullptr) == D3D_OK);
    CHECK(d->CreateOffscreenPlainSurface(1280, 720, D3DFMT_X8R8G8B8, D3DPOOL_SYSTEMMEM, &sysx, nullptr) == D3D_OK);
    CHECK(d->SetRenderTarget(0, sys) == D3DERR_INVALIDCALL);       // no RENDERTARGET usage
    CHECK(d->CreateOffscreenPlainSurface(16, 16, D3DFMT_D24S8, D3DPOOL_SYSTEMMEM, &rt, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateRenderTarget(256, 128, D3DFMT_A8R8G8B8, D3DMULTISAMPLE_NONE, 0, FALSE, &rt, nullptr) == D3D_OK);
    CHECK(d->SetRenderTarget(0, rt) == D3D_OK);
    D3DVIEWPORT9 vp{};
    RECT sc{};
    d->GetViewport(&vp);
    d->GetScissorRect(&sc);
    CHECK(vp.X == 0 && vp.Width == 256 && vp.Height == 128 && vp.MaxZ == 1.0f && sc.right == 256 && sc.bottom == 128);
    CHECK(d->SetRenderTarget(0, bb) == D3D_OK);
    IDirect3DSurface9* got = nullptr;
    CHECK(d->GetRenderTarget(1, &got) == D3DERR_NOTFOUND && !got);
    CHECK(d->GetRenderTarget(5, &got) == D3DERR_INVALIDCALL);
    CHECK(d->GetRenderTarget(0, &got) == D3D_OK && got == bb);
    got->Release();
    // GetRenderTargetData (DXVK :1134-1165).
    CHECK(d->GetRenderTargetData(bb, bb) == D3D_OK);
    CHECK(d->GetRenderTargetData(nullptr, sys) == D3DERR_INVALIDCALL);
    CHECK(d->GetRenderTargetData(bb, sysx) == D3DERR_INVALIDCALL);   // another format
    CHECK(d->GetRenderTargetData(rt, sys) == D3DERR_INVALIDCALL);    // another size
    CHECK(d->GetRenderTargetData(bb, sys) == D3D_OK && d9g::report().readbacks == 1);
    // Constants (DXVK :8049-8100).
    const float f4[8] = {1, 2, 3, 4, 5, 6, 7, 8};
    CHECK(d->SetVertexShaderConstantF(0, f4, 2) == D3D_OK);
    CHECK(d->SetVertexShaderConstantF(256, f4, 1) == D3D_OK);        // past the hardware count: stored nowhere
    CHECK(d->SetVertexShaderConstantF(8192, f4, 1) == D3DERR_INVALIDCALL);
    CHECK(d->SetVertexShaderConstantF(0, nullptr, 1) == D3DERR_INVALIDCALL);
    CHECK(d->SetVertexShaderConstantF(0, nullptr, 0) == D3D_OK);
    CHECK(d->SetPixelShaderConstantF(223, f4, 1) == D3D_OK && d->SetPixelShaderConstantF(224, f4, 1) == D3DERR_INVALIDCALL);
    const int i4[4] = {1, 2, 3, 4};
    CHECK(d->SetPixelShaderConstantI(15, i4, 1) == D3D_OK && d->SetPixelShaderConstantI(16, i4, 1) == D3DERR_INVALIDCALL);
    CHECK(d->SetStreamSource(16, nullptr, 0, 0) == D3DERR_INVALIDCALL);
    CHECK(d->SetStreamSourceFreq(0, D3DSTREAMSOURCE_INSTANCEDATA | 1) == D3DERR_INVALIDCALL);
    CHECK(d->SetStreamSourceFreq(1, 0) == D3DERR_INVALIDCALL && d->SetStreamSourceFreq(1, 1) == D3D_OK);
    CHECK(d->SetSoftwareVertexProcessing(TRUE) == D3DERR_INVALIDCALL && d->SetSoftwareVertexProcessing(FALSE) == D3D_OK);
    CHECK(d->SetRenderState(D3DRENDERSTATETYPE(3), 1) == D3D_OK && d->SetRenderState(D3DRENDERSTATETYPE(300), 1) == D3D_OK);
    DWORD v = 0;
    CHECK(d->GetRenderState(D3DRENDERSTATETYPE(3), &v) == D3DERR_INVALIDCALL);
    CHECK(d->SetSamplerState(0, D3DSAMPLERSTATETYPE(14), 1) == D3DERR_INVALIDCALL && d9g::report().ni_value_count == 1);
    // Buffers and textures (ValidateBufferProperties, NormalizeTextureProperties).
    IDirect3DVertexBuffer9* vb = nullptr;
    CHECK(d->CreateVertexBuffer(0, 0, 0, D3DPOOL_MANAGED, &vb, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateVertexBuffer(64, D3DUSAGE_DYNAMIC, 0, D3DPOOL_MANAGED, &vb, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateVertexBuffer(64, 0, 0, D3DPOOL_SCRATCH, &vb, nullptr) == D3DERR_INVALIDCALL);
    IDirect3DTexture9* t = nullptr;
    CHECK(d->CreateTexture(0, 4, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateTexture(6, 8, 1, 0, D3DFORMAT(kDXT1), D3DPOOL_MANAGED, &t, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateTexture(8, 8, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateTexture(8, 8, 1, D3DUSAGE_WRITEONLY, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateTexture(8, 8, 1, D3DUSAGE_DEPTHSTENCIL, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &t, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateTexture(8, 8, 1, 0, D3DFMT_R5G6B5, D3DPOOL_MANAGED, &t, nullptr) == D3DERR_INVALIDCALL);   // not implemented
    CHECK(d9g::report().ni_value_count == 2);
    sys->Release(); sysx->Release(); rt->Release(); bb->Release();
    CHECK(d->Release() == 0 && d9g::report().live_at_destroy.empty());
    // Every answer above was counted, by method.
    CHECK(d9g::report().ans[d9g::I_DEVICE][41].calls == 2 && d9g::report().ans[d9g::I_DEVICE][41].failed == 1);
}

void test_scene_fault() {
    d9g::Device* d = make_device(d9g::F_SCENE_ANSWER);
    CHECK(d->BeginScene() == D3D_OK && d->BeginScene() == D3D_OK);
    d->Release();
}

void test_clears() {
    d9g::Device* d = make_device();
    CHECK(d->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF336699u, 1.0f, 0) == D3D_OK);
    CHECK(bb_pixel(d, 0, 0) == 0xFF336699u && bb_pixel(d, 1279, 719) == 0xFF336699u);
    const D3DRECT r{0, 0, 10, 10};
    CHECK(d->Clear(0, &r, D3DCLEAR_TARGET, 0xFF000000u, 1.0f, 0) == D3D_OK);   // Count 0 with rectangles: nothing
    CHECK(bb_pixel(d, 5, 5) == 0xFF336699u && d9g::report().clear_zero_count_rects == 1);
    const D3DVIEWPORT9 vp{100, 50, 300, 200, 0.0f, 1.0f};
    d->SetViewport(&vp);
    CHECK(d->Clear(0, nullptr, D3DCLEAR_TARGET, 0x8000FF00u, 1.0f, 0) == D3D_OK);
    CHECK(bb_pixel(d, 100, 50) == 0x8000FF00u && bb_pixel(d, 399, 249) == 0x8000FF00u);
    CHECK(bb_pixel(d, 99, 50) == 0xFF336699u && bb_pixel(d, 400, 249) == 0xFF336699u && bb_pixel(d, 100, 250) == 0xFF336699u);
    CHECK(d9g::report().clear_partial_colour == 1 && d9g::report().clear_full_colour == 1);
    // The depth-stencil: a partial depth clear keeps the stencil, and the depth outside.
    IDirect3DSurface9* ds = nullptr;
    d->GetDepthStencilSurface(&ds);
    const D3DVIEWPORT9 full{0, 0, 1280, 720, 0.0f, 1.0f};
    d->SetViewport(&full);
    CHECK(d->Clear(0, nullptr, D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL, 0, 0.5f, 7) == D3D_OK);
    const uint32_t half = uint32_t(std::lrint(0.5 * 0xFFFFFF));
    CHECK(cpu_word(d, ds, 0, 0) == (half | 7u << 24));
    const D3DVIEWPORT9 corner{0, 0, 64, 48, 0.0f, 1.0f};
    d->SetViewport(&corner);
    CHECK(d->Clear(0, nullptr, D3DCLEAR_ZBUFFER, 0xFFFFFFFFu, 1.0f, 0) == D3D_OK);
    CHECK(cpu_word(d, ds, 63, 47) == (0xFFFFFFu | 7u << 24) && cpu_word(d, ds, 64, 47) == (half | 7u << 24));
    CHECK(d->Clear(0, nullptr, D3DCLEAR_STENCIL, 0, 0.0f, 9) == D3D_OK);
    CHECK(cpu_word(d, ds, 0, 0) == (0xFFFFFFu | 9u << 24) && cpu_word(d, ds, 64, 0) == (half | 7u << 24));
    CHECK(bb_pixel(d, 10, 10) == 0xFF336699u);              // no colour for a depth-only clear
    CHECK(d9g::report().clear_partial_depth == 2);
    // No depth-stencil bound: ZBUFFER refused before anything is cleared (DXVK :1909).
    d->SetViewport(&full);
    d->SetDepthStencilSurface(nullptr);
    CHECK(d->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, 0xFF000000u, 1.0f, 0) == D3DERR_INVALIDCALL);
    CHECK(bb_pixel(d, 0, 0) == 0xFF336699u && d9g::report().clear_refused == 1);
    d->SetDepthStencilSurface(ds);
    // A 512x512 render-target level, partial by the scissor; the back buffer untouched.
    IDirect3DTexture9* t = nullptr;
    IDirect3DSurface9* lv = nullptr;
    CHECK(d->CreateTexture(512, 512, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &t, nullptr) == D3D_OK);
    t->GetSurfaceLevel(0, &lv);
    CHECK(d->SetRenderTarget(0, lv) == D3D_OK);
    d->SetRenderState(D3DRS_SCISSORTESTENABLE, TRUE);
    const RECT s{0, 0, 256, 192};
    d->SetScissorRect(&s);
    CHECK(d->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF102030u, 1.0f, 0) == D3D_OK);
    CHECK(cpu_word(d, lv, 255, 191) == 0xFF102030u && cpu_word(d, lv, 256, 191) == 0 && cpu_word(d, lv, 0, 192) == 0);
    // SRGBWRITEENABLE at a colour Clear: not implemented (DXVK clears through the sRGB view).
    d->SetRenderState(D3DRS_SRGBWRITEENABLE, TRUE);
    CHECK(d->Clear(0, nullptr, D3DCLEAR_TARGET, 0, 1.0f, 0) == D3DERR_INVALIDCALL && d9g::report().ni_value_count == 1);
    d->SetRenderState(D3DRS_SRGBWRITEENABLE, FALSE);
    d->SetRenderTarget(0, d->back_buffer());
    CHECK(bb_pixel(d, 0, 0) == 0xFF336699u);
    lv->Release(); t->Release(); ds->Release();
    d->Release();
    // The faults: the colour XOR 0x00010101; a Clear that does nothing; the scribble.
    d = make_device(d9g::F_CLEAR_COLOUR);
    d->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF000000u, 1.0f, 0);
    CHECK(bb_pixel(d, 0, 0) == 0xFF010101u);
    d->Release();
    d = make_device(d9g::F_CLEAR_NOOP | d9g::F_PRESENT_SCRIBBLE);
    CHECK(d->Present(nullptr, nullptr, nullptr, nullptr) == D3D_OK);
    CHECK(bb_pixel(d, 0, 0) == 0xFF4080BFu);                // (0.25, 0.5, 0.75, 1): B 0xBF, G 0x80, R 0x40
    d->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF000000u, 1.0f, 0);
    CHECK(bb_pixel(d, 0, 0) == 0xFF4080BFu && d9g::report().clear_fault_noop == 1);
    d->Release();
    d = make_device(d9g::F_PRESENT_SCRIBBLE);
    d->Present(nullptr, nullptr, nullptr, nullptr);
    d->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF000000u, 1.0f, 0);
    CHECK(bb_pixel(d, 0, 0) == 0xFF000000u && d9g::report().scribbles == 1);
    d->Release();
}

void test_locks() {
    d9g::Device* d = make_device();
    IDirect3DTexture9* t = nullptr;
    CHECK(d->CreateTexture(64, 64, 2, 0, D3DFMT_X8R8G8B8, D3DPOOL_MANAGED, &t, nullptr) == D3D_OK);
    D3DLOCKED_RECT lr{};
    CHECK(t->LockRect(0, &lr, nullptr, 0) == D3D_OK && lr.Pitch == 256);
    CHECK(t->LockRect(0, &lr, nullptr, 0) == D3DERR_INVALIDCALL);   // already locked (DXVK :4962)
    std::memset(lr.pBits, 0x11, 256 * 64);                          // alpha 0x11
    CHECK(t->UnlockRect(0) == D3D_OK && t->UnlockRect(0) == D3D_OK); // a texture level answers D3D_OK twice
    std::string bytes;
    auto* tex = static_cast<d9g::Texture*>(t);
    CHECK(d9g::cpu_gpu_bytes(d->gpu(), tex->image()->gpu, 0, &bytes));
    CHECK(bytes.size() == 64 * 64 * 4 && uint8_t(bytes[3]) == 0xFF && uint8_t(bytes[0]) == 0x11);   // the X8 fill on upload
    CHECK(tex->image()->store[0][3] == 0x11);                       // the CPU store keeps what was written
    const RECT rc{8, 4, 16, 8};
    CHECK(t->LockRect(1, &lr, &rc, 0) == D3D_OK);
    CHECK(static_cast<uint8_t*>(lr.pBits) == tex->image()->store[1].data() + 4 * 128 + 8 * 4);
    t->UnlockRect(1);
    CHECK(t->LockRect(2, &lr, nullptr, 0) == D3DERR_INVALIDCALL);
    IDirect3DSurface9* sys = nullptr;
    d->CreateOffscreenPlainSurface(8, 8, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &sys, nullptr);
    CHECK(sys->UnlockRect() == D3DERR_INVALIDCALL);                // a surface's second unlock is refused
    const RECT bad{4, 4, 2, 6};
    CHECK(sys->LockRect(&lr, &bad, 0) == D3DERR_INVALIDCALL);      // a SYSTEMMEM surface's rectangle is checked
    IDirect3DTexture9* rtt = nullptr;
    d->CreateTexture(64, 64, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &rtt, nullptr);
    CHECK(rtt->LockRect(0, &lr, nullptr, 0) == D3DERR_INVALIDCALL); // DEFAULT, not DYNAMIC: not lockable
    IDirect3DTexture9* dxt = nullptr;
    CHECK(d->CreateTexture(64, 64, 0, 0, D3DFORMAT(kDXT5), D3DPOOL_MANAGED, &dxt, nullptr) == D3D_OK);
    CHECK(dxt->LockRect(0, &lr, nullptr, 0) == D3D_OK && lr.Pitch == 256 && dxt->UnlockRect(0) == D3D_OK);
    CHECK(dxt->GetLevelCount() == 7);
    CHECK(dxt->SetLOD(10) == 0 && dxt->GetLOD() == 6 && dxt->SetLOD(1) == 6);   // min(LOD, levels - 1)
    IDirect3DVertexBuffer9* vb = nullptr;
    CHECK(d->CreateVertexBuffer(96, D3DUSAGE_WRITEONLY, 0, D3DPOOL_MANAGED, &vb, nullptr) == D3D_OK);
    void* p = nullptr;
    CHECK(vb->Lock(24, 48, &p, 0) == D3D_OK && p);
    CHECK(vb->Lock(0, 0, nullptr, 0) == D3DERR_INVALIDCALL);
    std::memset(p, 0x5A, 48);
    CHECK(vb->Unlock() == D3D_OK && vb->Unlock() == D3D_OK);
    CHECK(d9g::report().buffer_upload_bytes == 48);
    vb->Release(); dxt->Release(); rtt->Release(); sys->Release(); t->Release();
    d->Release();
}

// A DXT format the GPU cannot sample (a Mali GPU's: make_cpu_gpu's bc false): the texture is made
// BGRA8 and each upload is the store's blocks decoded (tex::decode_level), a partial lock's level
// decoded whole and every level kept; a DXT1 level never written is a zero block decoded (opaque
// black). With BC the texture keeps its blocks, as before.
void test_decoded_dxt() {
    for (bool bc : {true, false}) {
        d9g::report() = d9g::Report{};
        d9g::DeviceSetup s;
        s.pp = present_params();
        s.behavior = kBehavior;
        auto* d = new d9g::Device(d9g::make_cpu_gpu(false, false, true, bc), s);
        std::string why;
        CHECK(d->init(&why));
        CHECK(d->gpu()->info().bc_unsupported.empty() == bc);
        const uint64_t held = d9g::report().gpu_texture_bytes;
        IDirect3DTexture9 *t1 = nullptr, *t5 = nullptr;
        CHECK(d->CreateTexture(8, 8, 0, 0, D3DFORMAT(kDXT1), D3DPOOL_MANAGED, &t1, nullptr) == D3D_OK);
        CHECK(d->CreateTexture(8, 8, 2, 0, D3DFORMAT(kDXT5), D3DPOOL_MANAGED, &t5, nullptr) == D3D_OK);
        if (!t1 || !t5) {
            if (t1) t1->Release();
            if (t5) t5->Release();
            d->Release();
            continue;
        }
        d9g::Image* i1 = static_cast<d9g::Texture*>(t1)->image();
        d9g::Image* i5 = static_cast<d9g::Texture*>(t5)->image();
        CHECK(i1->decoded == !bc && i5->decoded == !bc);
        d9g::TexDesc desc;
        CHECK(d9g::cpu_gpu_desc(d->gpu(), i1->gpu, &desc) && desc.levels == 4 && desc.fmt == (bc ? d9g::GFmt::bc1 : d9g::GFmt::bgra8));
        CHECK(d9g::cpu_gpu_desc(d->gpu(), i5->gpu, &desc) && desc.levels == 2 && desc.fmt == (bc ? d9g::GFmt::bc3 : d9g::GFmt::bgra8));
        // 8x8 DXT1, 4 levels: 32 + 8 + 8 + 8 block bytes, or 4 * (64 + 16 + 4 + 1) texel bytes; 8x8 DXT5,
        // 2 levels: 64 + 16, or 4 * (64 + 16)
        CHECK(d9g::report().gpu_texture_bytes - held == (bc ? 56u + 80u : 340u + 320u));
        // Level 0 of the DXT1: block (0, 0) red over blue and block (1, 1) green over black in four colours
        // (c0 > c1), blocks (1, 0) and (0, 1) blue over red in three and transparent black (c0 <= c1); each
        // row of a block holds indices 0, 1, 2, 3. Green's thirds are the GPU's 175 and 80, not D3DX's
        // 170 and 85 (tex::decode_level_sampled).
        D3DLOCKED_RECT lr{};
        CHECK(t1->LockRect(0, &lr, nullptr, 0) == D3D_OK && lr.Pitch == 16);
        const uint8_t four[8] = {0x00, 0xF8, 0x1F, 0x00, 0xE4, 0xE4, 0xE4, 0xE4};
        const uint8_t three[8] = {0x1F, 0x00, 0x00, 0xF8, 0xE4, 0xE4, 0xE4, 0xE4};
        const uint8_t green[8] = {0xE0, 0x07, 0x00, 0x00, 0xE4, 0xE4, 0xE4, 0xE4};
        auto* p = static_cast<uint8_t*>(lr.pBits);
        std::memcpy(p, four, 8);
        std::memcpy(p + 8, three, 8);
        std::memcpy(p + 16, three, 8);
        std::memcpy(p + 24, green, 8);
        CHECK(t1->UnlockRect(0) == D3D_OK);
        // The DXT5's level 0 locked at block (1, 1) alone: alpha 255 (a0, index 0), green (c0, index 0).
        const RECT rc{4, 4, 8, 8};
        CHECK(t5->LockRect(0, &lr, &rc, 0) == D3D_OK);
        const uint8_t opaque_green[16] = {0xFF, 0x00, 0, 0, 0, 0, 0, 0, 0xE0, 0x07, 0x00, 0x00, 0, 0, 0, 0};
        std::memcpy(lr.pBits, opaque_green, 16);
        CHECK(t5->UnlockRect(0) == D3D_OK);
        std::string b1, b5, b1l1, b5l1;
        CHECK(d9g::cpu_gpu_bytes(d->gpu(), i1->gpu, 0, &b1) && d9g::cpu_gpu_bytes(d->gpu(), i5->gpu, 0, &b5));
        CHECK(d9g::cpu_gpu_bytes(d->gpu(), i1->gpu, 1, &b1l1) && d9g::cpu_gpu_bytes(d->gpu(), i5->gpu, 1, &b5l1));
        auto word = [](const std::string& b, uint32_t w, uint32_t x, uint32_t y) {
            uint32_t v;
            std::memcpy(&v, b.data() + (size_t(y) * w + x) * 4, 4);
            return v;   // B | G << 8 | R << 16 | A << 24
        };
        if (bc) {
            CHECK(b1 == std::string(reinterpret_cast<const char*>(i1->store[0].data()), 32));
            CHECK(b5 == std::string(reinterpret_cast<const char*>(i5->store[0].data()), 64));
            CHECK(b1l1 == std::string(8, '\0') && b5l1 == std::string(16, '\0'));
            CHECK(d9g::report().decoded_textures.empty() && d9g::report().decoded_levels == 0);
        } else {
            std::vector<uint8_t> want;
            CHECK(tex::decode_level_sampled(i1->store[0].data(), 8, 8, kDXT1, 0, &want));
            CHECK(b1 == std::string(want.begin(), want.end()) && b1.size() == 8 * 8 * 4);
            CHECK(word(b1, 8, 0, 0) == 0xFFFF0000u && word(b1, 8, 1, 3) == 0xFF0000FFu);   // c0 red, c1 blue
            CHECK(word(b1, 8, 4, 0) == 0xFF0000FFu && word(b1, 8, 5, 0) == 0xFFFF0000u);   // c0 blue, c1 red
            CHECK(word(b1, 8, 7, 0) == 0u && word(b1, 8, 3, 7) == 0u);                     // transparent black
            CHECK(word(b1, 8, 4, 4) == 0xFF00FF00u && word(b1, 8, 6, 5) == 0xFF00AF00u && word(b1, 8, 7, 7) == 0xFF005000u);
            CHECK(b1l1.size() == 4 * 4 * 4);
            for (uint32_t i = 0; i < 16; i++) CHECK(word(b1l1, 4, i % 4, i / 4) == 0xFF000000u);   // zero block: opaque black
            CHECK(b5.size() == 8 * 8 * 4 && b5l1 == std::string(4 * 4 * 4, '\0'));               // DXT5's zero block: zero
            for (uint32_t y = 0; y < 8; y++)
                for (uint32_t x = 0; x < 8; x++)
                    CHECK(word(b5, 8, x, y) == (x >= 4 && y >= 4 ? 0xFF00FF00u : 0u));
            CHECK(d9g::report().decoded_textures == (std::map<std::string, uint64_t>{{"DXT1", 1}, {"DXT5", 1}}));
            CHECK(d9g::report().decoded_levels == 2 && d9g::report().decoded_block_bytes == 32 + 64);
        }
        t1->Release();
        t5->Release();
        CHECK(d9g::report().gpu_texture_bytes == held);
        CHECK(d9g::report().gpu_texture_bytes_peak == held + (bc ? 136u : 660u));
        d->Release();
    }
}

void test_draws_and_frames() {
    d9g::Device* d = make_device();
    float v[64] = {};
    CHECK(d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 16) == D3DERR_INVALIDCALL);   // no declaration
    const D3DVERTEXELEMENT9 e[] = {{0, 0, D3DDECLTYPE_FLOAT4, 0, D3DDECLUSAGE_POSITION, 0}, D3DDECL_END()};
    IDirect3DVertexDeclaration9* decl = nullptr;
    CHECK(d->CreateVertexDeclaration(e, &decl) == D3D_OK);
    CHECK(d->SetVertexDeclaration(decl) == D3D_OK);
    CHECK(d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 0) == D3DERR_INVALIDCALL);    // stride 0
    CHECK(d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 0, v, 16) == D3D_OK);               // empty
    CHECK(d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 16) == D3D_OK);               // skipped
    CHECK(d->DrawPrimitiveUP(D3DPT_TRIANGLEFAN, 2, v, 16) == D3D_OK);
    const uint16_t idx[6] = {0, 1, 2, 2, 1, 3};
    CHECK(d->DrawIndexedPrimitiveUP(D3DPT_TRIANGLELIST, 0, 4, 2, idx, D3DFMT_INDEX16, v, 16) == D3D_OK);
    CHECK(d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1) == D3D_OK);
    CHECK(d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 0, 0, 1) == D3D_OK);      // no vertices: empty
    d9g::Report& r = d9g::report();
    CHECK(r.draws.refused["DrawPrimitiveUP"]["TRIANGLESTRIP"] == 2);
    CHECK(r.draws.empty["DrawPrimitiveUP"]["TRIANGLESTRIP"] == 1);
    CHECK(r.draws.unmapped["DrawPrimitiveUP"]["TRIANGLESTRIP"] == 1 && r.draws.unmapped["DrawPrimitiveUP"]["TRIANGLEFAN"] == 1);
    CHECK(r.draws.unmapped["DrawIndexedPrimitiveUP"]["TRIANGLELIST"] == 1 && r.draws.unmapped["DrawPrimitive"]["TRIANGLELIST"] == 1);
    CHECK(r.draws.empty["DrawIndexedPrimitive"]["TRIANGLELIST"] == 1);
    CHECK(r.draws_total == 8 && r.drawn == 0);
    CHECK(r.up_vertex_bytes == 4 * 16 + 4 * 16 + 4 * 16 && r.up_index_bytes == 12);
    CHECK(r.fan_cache_vertices == 64 && r.fan_cache_uploads == 1);
    CHECK(d->Present(nullptr, nullptr, nullptr, nullptr) == D3D_OK);
    CHECK(d->Present(nullptr, nullptr, nullptr, nullptr) == D3D_OK);
    d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1);
    CHECK(d->Present(nullptr, nullptr, nullptr, nullptr) == D3D_OK);
    d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1);
    CHECK((r.per_frame == std::vector<std::array<uint64_t, 3>>{{1, 1, 8}, {2, 2, 0}, {3, 3, 1}}) && r.frame_draws == 1);
    CHECK(r.up_arena_uploads == 0 && r.up_uploaded_bytes == 0 && r.up_frame_peak == 3 * 64 + 12);
    const RECT dummy{0, 0, 1, 1};
    CHECK(d->Present(&dummy, nullptr, nullptr, nullptr) == D3DERR_INVALIDCALL);
    decl->Release();
    d->Release();
    // The count-draw fault: the first real draw goes uncounted.
    d = make_device(d9g::F_COUNT_DRAW);
    d->CreateVertexDeclaration(e, &decl);
    d->SetVertexDeclaration(decl);
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 16);
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 16);
    CHECK(d9g::report().draws_total == 1 && d9g::report().fault_uncounted == 1);
    decl->Release();
    d->Release();
}

// R2b: the pipeline and sampler descriptions the device computes at each skipped draw, on
// the CPU model (d9g_device.cpp "pipeline state"): the defaults are DXVK's ResetState, the
// dirty bits recompute only on a change, a BORDER stage is the patch's CLAMP_TO_BORDER or the
// shader route's fallback by the device's capabilities and never not_implemented, a mapping fault
// reaches the report, and the UP counters keep R2a's meaning.
const D3DVERTEXELEMENT9 kCensusDecl[] = {{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0},
                                         {0, 12, D3DDECLTYPE_D3DCOLOR, 0, D3DDECLUSAGE_COLOR, 0},
                                         {0, 16, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0}, D3DDECL_END()};
// vs_3_0 dcl_position v0, dcl_position o0, mov o0, v0; ps_3_0 def c0 (1, 0, 0, 1), mov oC0, c0
// (host/d3d9gpu/test/sm3_asm.py): the smallest shaders the device's translator takes. DXVK's compiler,
// which the device runs at creation (R1), stops at an assertion on a vertex shader without a position
// output (dxbc-spirv sm3_io_map.cpp getPositionValue), as DXVK itself would.
const DWORD kVs[] = {0xFFFE0300u, 0x0200001Fu, 0x80000000u, 0x900F0000u, 0x0200001Fu, 0x80000000u,
                     0xE00F0000u, 0x02000001u, 0xE00F0000u, 0x90E40000u, 0x0000FFFFu};
const DWORD kPs[] = {0xFFFF0300u, 0x05000051u, 0xA00F0000u, 0x3F800000u, 0x00000000u, 0x00000000u,
                     0x3F800000u, 0x02000001u, 0x800F0800u, 0xA0E40000u, 0x0000FFFFu};
// vs_3_0 dcl_color0 v0, dcl_position0 v1, dcl_normal0 v2, writing position, colour and a texture
// coordinate from them (sm3_asm.py): against kCensusDecl its input signature reorders the colour to
// location 0 (BGRA by location, not the declaration's index 1), reads a normal the declaration lacks
// (DXVK's null stream) and leaves the declaration's texture coordinate unread (U9's vertex input)
const DWORD kVsReorder[] = {0xFFFE0300u, 0x0200001Fu, 0x8000000Au, 0x900F0000u, 0x0200001Fu, 0x80000000u, 0x900F0001u,
                            0x0200001Fu, 0x80000003u, 0x900F0002u, 0x0200001Fu, 0x80000000u, 0xE00F0000u, 0x0200001Fu,
                            0x8000000Au, 0xE00F0001u, 0x0200001Fu, 0x80000005u, 0xE00F0002u, 0x02000001u, 0xE00F0000u,
                            0x90E40001u, 0x02000001u, 0xE00F0001u, 0x90E40000u, 0x02000001u, 0xE00F0002u, 0x90E40002u,
                            0x0000FFFFu};
// ps_3_0 dcl_texcoord0 v0, dcl_2d s5, texld r0, v0, s5, mov oC0, r0: a pixel shader sampling stage 5
// (its reflection's sampler mask 0x20)
const DWORD kPsSample5[] = {0xFFFF0300u, 0x0200001Fu, 0x80000005u, 0x900F0000u, 0x0200001Fu, 0x90000000u, 0xA00F0805u,
                            0x03000042u, 0x800F0000u, 0x90E40000u, 0xA0E40805u, 0x02000001u, 0x800F0800u, 0x80E40000u,
                            0x0000FFFFu};

// A session the pytest reads back (--pipeline-session): two vertex shaders and a pixel shader, a
// census-like declaration, blending on and off, a BORDER sampler on a mip-mapped texture, a fan, and
// a vertex shader whose signature is not the declaration (kVsReorder: the colour reordered, a
// texture coordinate unread, an input the declaration lacks).
void pipeline_session(d9g::Device* d) {
    IDirect3DVertexDeclaration9* decl = nullptr;
    IDirect3DVertexShader9 *vs = nullptr, *vs2 = nullptr;
    IDirect3DPixelShader9* ps = nullptr;
    IDirect3DTexture9* tex = nullptr;
    d->CreateVertexDeclaration(kCensusDecl, &decl);
    d->CreateVertexShader(kVs, &vs);
    d->CreateVertexShader(kVsReorder, &vs2);
    d->CreatePixelShader(kPs, &ps);
    d->CreateTexture(64, 64, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &tex, nullptr);
    d->SetVertexDeclaration(decl);
    d->SetVertexShader(vs);
    d->SetPixelShader(ps);
    d->SetRenderState(D3DRS_MULTISAMPLEANTIALIAS, FALSE);
    float v[6 * 24] = {};
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);                    // input 1
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);                    // the same: counted, not recomputed
    d->SetRenderState(D3DRS_ALPHABLENDENABLE, TRUE);
    d->SetRenderState(D3DRS_SRCBLEND, D3DBLEND_SRCALPHA);
    d->SetRenderState(D3DRS_DESTBLEND, D3DBLEND_INVSRCALPHA);
    d->DrawPrimitiveUP(D3DPT_TRIANGLEFAN, 2, v, 24);                      // input 2: blended, a fan
    d->SetTexture(0, tex);
    d->SetSamplerState(0, D3DSAMP_ADDRESSU, D3DTADDRESS_BORDER);
    d->SetSamplerState(0, D3DSAMP_MIPFILTER, D3DTEXF_LINEAR);
    d->DrawPrimitiveUP(D3DPT_TRIANGLEFAN, 2, v, 24);                      // the same input, a BORDER stage
    const uint16_t idx[6] = {0, 1, 2, 2, 1, 3};
    d->DrawIndexedPrimitiveUP(D3DPT_TRIANGLELIST, 0, 4, 2, idx, D3DFMT_INDEX16, v, 24);   // input 3
    d->SetVertexShader(vs2);
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);                    // input 4: the signature's vertex input
    d->Present(nullptr, nullptr, nullptr, nullptr);
    d->SetTexture(0, nullptr);
    tex->Release();
    ps->Release();
    vs2->Release();
    vs->Release();
    decl->Release();
}

void test_pipeline_tracking() {
    d9g::Device* d = make_device();
    // ResetState's defaults (d9g_state.h state_defaults; the state check holds them to DXVK's)
    DWORD v = 0;
    CHECK(d->GetRenderState(D3DRS_BLENDOPALPHA, &v) == D3D_OK && v == D3DBLENDOP_ADD);
    CHECK(d->GetRenderState(D3DRS_MULTISAMPLEANTIALIAS, &v) == D3D_OK && v == TRUE);
    CHECK(d->GetRenderState(D3DRS_STENCILMASK, &v) == D3D_OK && v == 0xFFFFFFFFu);
    float pmax = 0;
    CHECK(d->GetRenderState(D3DRS_POINTSIZE_MAX, &v) == D3D_OK && (std::memcpy(&pmax, &v, 4), pmax == 64.0f));
    pipeline_session(d);
    const d9g::Report::Pipeline& p = d9g::report().pipeline;
    CHECK(p.draws_mapped == 6);
    CHECK(p.recomputes == 4);                          // the repeated strip and the BORDER draw reuse their inputs
    CHECK(p.distinct_inputs == 4 && p.distinct_keys == 4 && p.distinct_state_keys == 4);
    CHECK(p.unmapped_inputs == 0 && p.unmapped_samplers == 0);
    // a device without the SDL patch's capabilities (the CPU model's default, a stock SDL3's):
    // BORDER and the depth clamp are the shader route's fallback obligations
    CHECK(p.fallback_border_samplers == 1 && p.fallback_border_stage_draws == 3);   // the BORDER fan, the indexed draw, input 4
    // kVs reads the position alone (no D3DCOLOR attribute); kVsReorder reads the colour (BGRA)
    CHECK(p.fallback_depth_clamp_keys == 4 && p.bgra_keys == 1 && p.fan_as_list_draws == 2);
    CHECK(p.border_mapped_samplers == 0 && p.depth_clamp_property_keys == 0);
    CHECK(d9g::report().ni_value_count == 0);
    // R2a's meaning: the data bytes (six draws of four 24-byte vertices) and the index bytes
    CHECK(d9g::report().up_vertex_bytes == 6 * 4 * 24 && d9g::report().up_index_bytes == 12);
    d->Release();
    // with both capabilities (the patched SDL3 here): the same session maps BORDER to the patch's
    // CLAMP_TO_BORDER and sets the depth-clamp property on every key; nothing is owed
    d = make_device(0, kBehavior, true, true);
    pipeline_session(d);
    CHECK(p.border_mapped_samplers == 1 && p.depth_clamp_property_keys == 4);
    CHECK(p.fallback_border_samplers == 0 && p.fallback_border_stage_draws == 0 && p.fallback_depth_clamp_keys == 0);
    d->Release();
    // the mapping itself: each capability decides its own feature
    {
        using namespace d9g;
        SamplerInput in;
        uint32_t rs[256] = {};
        const DeviceParams none{true, 64.0f, false, false}, both{true, 64.0f, true, true};
        state_defaults(rs, in.ss.data(), both, true);
        in.ss[D3DSAMP_ADDRESSU] = D3DTADDRESS_BORDER;
        in.tex = true; in.levels = 1; in.tex_fmt = D3DFMT_A8R8G8B8;
        SamplerDesc sd = map_sampler(in, SamplerOptions{}, none, 0);
        CHECK(sd.fallback() && !sd.info && sd.border_colour == kBorderNone && !sd.fallback_why.empty());
        sd = map_sampler(in, SamplerOptions{}, both, 0);
        CHECK(!sd.fallback() && sd.info && sd.info->address_mode_u == kAddressClampToBorder &&
              sd.border_colour == kBorderTransparentBlack && sd.info->props == 0);
        // SDL takes create_info(props), the info with the props d9g_props.h makes
        CHECK(sd.create_info(SDL_PropertiesID(7)).props == 7 && sd.create_info(0).address_mode_u == kAddressClampToBorder);
        CHECK(map_sampler(in, SamplerOptions{}, both, MF_BORDER_COLOUR).border_colour == kBorderOpaqueBlack);
        CHECK(map_sampler(in, SamplerOptions{}, both, MF_BORDER_CLAMP).info->address_mode_u == SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_EDGE);
        in.ss[D3DSAMP_BORDERCOLOR] = 0xFF000000u;
        CHECK(map_sampler(in, SamplerOptions{}, both, 0).border_colour == kBorderOpaqueBlack);
        in.ss[D3DSAMP_BORDERCOLOR] = 0xFFFFFFFFu;
        CHECK(map_sampler(in, SamplerOptions{}, both, 0).border_colour == kBorderOpaqueWhite);
        in.ss[D3DSAMP_BORDERCOLOR] = 0x80402010u;   // a custom colour: the shader route's
        CHECK(map_sampler(in, SamplerOptions{}, both, 0).fallback());
        in.ss[D3DSAMP_BORDERCOLOR] = 0;
        in.tex_fmt = D3DFMT_X8R8G8B8;                // DXVK's view of it swizzles A to 1: the shader route's
        CHECK(map_sampler(in, SamplerOptions{}, both, 0).fallback());
        CHECK(!map_sampler(in, SamplerOptions{}, both, MF_BORDER_SWIZZLED_VIEW).fallback());   // its control maps it
        in.tex_fmt = D3DFMT_A8R8G8B8;
        CHECK(map_sampler(in, SamplerOptions{}, both, MF_BORDER_FALLBACK).fallback());         // and this one refuses it
        in.tex_fmt = D3DFMT_X8R8G8B8;
        // X8R8G8B8 is the one format d9g makes whose DXVK view swizzles (d3d9_format.cpp)
        size_t nf = 0;
        const FormatInfo* fs = all_formats(&nf);
        std::string swizzled;
        for (size_t i = 0; i < nf; i++)
            if (!fs[i].dxvk_view_identity) swizzled += fs[i].name;
        CHECK(nf == 6 && swizzled == "X8R8G8B8");
        // with depth compare DXVK matches the red component alone (determineBorderColorType)
        CHECK(dxvk_standard_border_colour(0xFF000000u, true) == kBorderTransparentBlack);
        CHECK(dxvk_standard_border_colour(0x00FF0000u, true) == kBorderOpaqueWhite);
        CHECK(dxvk_standard_border_colour(0xFF000000u, false) == kBorderOpaqueBlack);
        CHECK(dxvk_standard_border_colour(0x00FF0000u, false) == kBorderNone);
        PipelineInput pi;
        for (size_t i = 0; i < kNumPipelineRS; i++) pi.rs[i] = rs[kPipelineRenderStates[i]];
        pi.prim = D3DPT_TRIANGLELIST;
        pi.n_elems = 1;
        pi.elems[0] = VertexElem{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0};
        pi.stride0 = 12;
        pi.vs_id = pi.ps_id = pi.decl_id = 1;
        pi.rt[0] = TargetDesc{D3DFMT_A8R8G8B8, 64, 64, 0, 0, 0, true, false};
        pi.ds = TargetDesc{D3DFMT_D24S8, 64, 64, 0, 0, 0, true, false};
        PipelineDesc pd = map_pipeline(pi, none, 0);
        CHECK(!pd.depth_clamp && (pd.residues & R_DEPTH_CLAMP) && pd.rs.enable_depth_clip);
        pd = map_pipeline(pi, both, 0);
        CHECK(pd.depth_clamp && !(pd.residues & R_DEPTH_CLAMP) && pd.rs.enable_depth_clip);
        pd = map_pipeline(pi, both, MF_DEPTH_CLAMP_OFF);
        CHECK(!pd.depth_clamp && !(pd.residues & R_DEPTH_CLAMP));
        pd = map_pipeline(pi, both, MF_DEPTH_CLAMP_EXTRA);
        CHECK(pd.depth_clamp && (pd.residues & R_DEPTH_CLAMP));
        CHECK(!map_pipeline(pi, both, 0).rs.enable_depth_bias);
        pd = map_pipeline(pi, both, MF_DEPTH_BIAS_PUSH);
        CHECK(pd.rs.enable_depth_bias && pd.rs.depth_bias_constant_factor == 16777216.0f);
        // the property is in the key: the two descriptions key differently
        CHECK(key_of(map_pipeline(pi, both, 0), pi, true).bytes != key_of(map_pipeline(pi, both, MF_DEPTH_CLAMP_OFF), pi, true).bytes);
        // U9: the vertex input follows the vertex shader's input signature (BindInputLayout). The
        // census declaration (position @0 FLOAT3, colour @12 D3DCOLOR, texcoord @16 FLOAT2) under a
        // shader reading colour, position and a normal the declaration lacks: the colour at location
        // 0 with its BGRA bit there, the position at 1, the normal on the null stream's slot at 2 per
        // instance with pitch 0, and the texture coordinate unread
        pi.n_elems = 3;
        for (int i = 0; i < 3; i++)
            pi.elems[i] = VertexElem{0, uint16_t(kCensusDecl[i].Offset), kCensusDecl[i].Type, 0, kCensusDecl[i].Usage, 0};
        pi.stride0 = 24;
        pi.vs_n_inputs = 3;
        pi.vs_inputs[0] = VsInput{D3DDECLUSAGE_COLOR, 0};
        pi.vs_inputs[1] = VsInput{D3DDECLUSAGE_POSITION, 0};
        pi.vs_inputs[2] = VsInput{D3DDECLUSAGE_NORMAL, 0};
        pd = map_pipeline(pi, both, 0);
        auto attr = [](const PipelineDesc& d, uint32_t i) {
            const SDL_GPUVertexAttribute& a = d.attrs[i];
            return std::array<uint32_t, 4>{a.location, a.buffer_slot, uint32_t(a.format), a.offset};
        };
        CHECK(pd.unmapped.empty() && pd.n_attrs == 3 && pd.bgra_mask == 1u && pd.null_stream);
        CHECK((attr(pd, 0) == std::array<uint32_t, 4>{0, 0, uint32_t(SDL_GPU_VERTEXELEMENTFORMAT_UBYTE4_NORM), 12}));
        CHECK((attr(pd, 1) == std::array<uint32_t, 4>{1, 0, uint32_t(SDL_GPU_VERTEXELEMENTFORMAT_FLOAT3), 0}));
        CHECK((attr(pd, 2) == std::array<uint32_t, 4>{2, kNullStreamSlot, uint32_t(SDL_GPU_VERTEXELEMENTFORMAT_FLOAT4), 0}));
        CHECK(pd.n_vbs == 2 && pd.vbs[0].slot == 0 && pd.vbs[0].pitch == 24 && pd.vbs[0].input_rate == SDL_GPU_VERTEXINPUTRATE_VERTEX);
        CHECK(pd.vbs[1].slot == kNullStreamSlot && pd.vbs[1].pitch == 0 && pd.vbs[1].input_rate == SDL_GPU_VERTEXINPUTRATE_INSTANCE &&
              pd.vbs[1].instance_step_rate == 0);
        CHECK(pd.attr_semantic[0] == (D3DDECLUSAGE_COLOR << 8) && pd.attr_semantic[2] == (D3DDECLUSAGE_NORMAL << 8));
        // BindInputLayout normalizes declaration POSITIONT only. A shader POSITIONT input
        // cannot match this declaration's ordinary POSITION and must use the null stream.
        PipelineInput positiont = pi;
        positiont.vs_n_inputs = 1;
        positiont.vs_inputs[0] = VsInput{D3DDECLUSAGE_POSITIONT, 0};
        pd = map_pipeline(positiont, both, 0);
        CHECK(pd.unmapped.empty() && pd.null_stream && pd.n_attrs == 1 && pd.n_vbs == 1);
        CHECK((attr(pd, 0) == std::array<uint32_t, 4>{0, kNullStreamSlot, uint32_t(SDL_GPU_VERTEXELEMENTFORMAT_FLOAT4), 0}));
        // POSITIONT is read as POSITION, a usage index packs into four bits, and the last element of a
        // semantic wins (D3D9InputSignature::find on the packed semantic)
        PipelineInput q = pi;
        q.elems[2] = VertexElem{0, 16, D3DDECLTYPE_FLOAT4, 0, D3DDECLUSAGE_COLOR, 0};   // the colour twice: the FLOAT4 wins
        pd = map_pipeline(q, both, 0);
        CHECK(pd.bgra_mask == 0 && attr(pd, 0)[2] == uint32_t(SDL_GPU_VERTEXELEMENTFORMAT_FLOAT4) && attr(pd, 0)[3] == 16);
        q = pi;
        q.elems[2].usage = D3DDECLUSAGE_NORMAL;
        q.elems[2].usage_index = 16;   // NORMAL16 packs as NORMAL0
        pd = map_pipeline(q, both, 0);
        CHECK(!pd.null_stream && pd.n_vbs == 1 && attr(pd, 2) == (std::array<uint32_t, 4>{2, 0, uint32_t(SDL_GPU_VERTEXELEMENTFORMAT_FLOAT2), 16}));
        // an unread element on another stream or of type UNUSED is no attribute and no refusal; read, it is unmapped
        q = pi;
        q.elems[2].stream = 1;
        CHECK(map_pipeline(q, both, 0).unmapped.empty());
        q.vs_inputs[2] = VsInput{D3DDECLUSAGE_TEXCOORD, 0};
        q.stream_stride[1] = 8;
        pd = map_pipeline(q, both, 0);   // read, it is stream 1's binding at its stride (SetStreamSourceFreq's streams)
        CHECK(pd.unmapped.empty() && attr(pd, 2)[1] == 1 && pd.n_vbs >= 2 && pd.vbs[1].slot == 1 && pd.vbs[1].pitch == 8 &&
              pd.vbs[1].input_rate == SDL_GPU_VERTEXINPUTRATE_VERTEX);
        q.elems[2].stream = 15;          // SDL's slot for the null stream: unmapped
        CHECK(!map_pipeline(q, both, 0).unmapped.empty());
        // each control is one wrong rule
        pd = map_pipeline(pi, both, MF_VI_DECLORDER);
        CHECK(pd.n_attrs == 3 && !pd.null_stream && pd.bgra_mask == 2u && attr(pd, 0)[3] == 0 && attr(pd, 2)[3] == 16);
        pd = map_pipeline(pi, both, MF_VI_NO_NULL);
        CHECK(pd.n_attrs == 2 && !pd.null_stream && pd.n_vbs == 1);
        pd = map_pipeline(pi, both, MF_NULL_VERTEX_RATE);
        CHECK(pd.n_vbs == 2 && pd.vbs[1].input_rate == SDL_GPU_VERTEXINPUTRATE_VERTEX);
        pd = map_pipeline(pi, both, MF_VI_UNREAD_ATTR);
        CHECK(pd.n_attrs == 4 && attr(pd, 3) == (std::array<uint32_t, 4>{3, 0, uint32_t(SDL_GPU_VERTEXELEMENTFORMAT_FLOAT2), 16}));
        CHECK(map_pipeline(pi, both, MF_BGRA_DECL_INDEX).bgra_mask == 2u);
        // the pixel shader's sampler mask decides the hazard: RT0 is level 0 of a texture at stage 5
        pi.rt[0].texture = true;
        pi.rt_tex_sampled = 1;
        pi.rt_tex_stages = 1u << 5;
        pi.ps_rt_mask = 1;
        pi.ps_sampler_mask = 0;
        CHECK(!map_pipeline(pi, both, 0).rt_feedback && map_pipeline(pi, both, MF_PS_SAMPLER_SUPERSET).rt_feedback);
        pi.ps_sampler_mask = 1u << 5;
        CHECK(map_pipeline(pi, both, 0).rt_feedback);
    }
    // a mapping fault reaches the report's fault field and the pipeline block
    d9g::report() = d9g::Report{};
    d9g::report().fault = d9g::parse_faults("map-blend-swap");
    CHECK(d9g::report().fault.map_bits == d9g::MF_BLEND_SWAP && d9g::report().fault.error.empty());
    d9g::DeviceSetup s;
    s.pp = present_params();
    s.behavior = kBehavior;
    d = new d9g::Device(d9g::make_cpu_gpu(), s);
    std::string why;
    CHECK(d->init(&why));
    const std::string j = d9g::report().json();
    CHECK(j.find("\"fault\": \"map-blend-swap\"") != std::string::npos);
    CHECK(j.find("\"map_faults\": \"map-blend-swap\"") != std::string::npos);
    d->Release();
    CHECK(d9g::parse_faults("map-no-such").error == "unknown fault map-no-such");
}

// The hazard bits a draw's pipeline input carries (rt_tex_sampled, rt_tex_stages,
// ds_tex_sampled) and the dirty tracking behind them (the R2b review's N6): binding a
// render-target texture, or the depth-stencil surface's own texture, recomputes the input; the
// keys file records the bits and each shader's bytecode sha256 (the state check's link to the
// recorded reflection).
std::string read_text(const std::string& path) {
    std::ifstream f(path, std::ios::binary);
    std::stringstream s;
    s << f.rdbuf();
    return s.str();
}

void test_hazard_tracking() {
    d9g::Device* d = make_device();
    const std::string dir = std::string(SA2_TEST_TMP) + "/hazard-test";
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    d9g::report().path = dir + "/d3d9gpu.json";
    IDirect3DVertexDeclaration9* decl = nullptr;
    IDirect3DVertexShader9* vs = nullptr;
    IDirect3DPixelShader9* ps = nullptr;
    IDirect3DTexture9 *rt_tex = nullptr, *ds_tex = nullptr, *rt_mip = nullptr;
    IDirect3DSurface9 *rt_surf = nullptr, *ds_surf = nullptr, *mip1 = nullptr;
    CHECK(d->CreateVertexDeclaration(kCensusDecl, &decl) == D3D_OK);
    IDirect3DPixelShader9* ps5 = nullptr;
    CHECK(d->CreateVertexShader(kVs, &vs) == D3D_OK);
    CHECK(d->CreatePixelShader(kPs, &ps) == D3D_OK);
    CHECK(d->CreatePixelShader(kPsSample5, &ps5) == D3D_OK);
    CHECK(d->CreateTexture(64, 64, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &rt_tex, nullptr) == D3D_OK);
    CHECK(d->CreateTexture(64, 64, 1, D3DUSAGE_DEPTHSTENCIL, D3DFMT_D24S8, D3DPOOL_DEFAULT, &ds_tex, nullptr) == D3D_OK);
    CHECK(d->CreateTexture(128, 128, 2, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &rt_mip, nullptr) == D3D_OK);
    if (!rt_tex || !ds_tex || !rt_mip) return;
    rt_tex->GetSurfaceLevel(0, &rt_surf);
    ds_tex->GetSurfaceLevel(0, &ds_surf);
    rt_mip->GetSurfaceLevel(1, &mip1);
    d->SetVertexDeclaration(decl);
    d->SetVertexShader(vs);
    d->SetPixelShader(ps);
    d->SetRenderState(D3DRS_MULTISAMPLEANTIALIAS, FALSE);
    CHECK(d->SetRenderTarget(0, rt_surf) == D3D_OK);
    CHECK(d->SetDepthStencilSurface(ds_surf) == D3D_OK);
    const d9g::Report::Pipeline& p = d9g::report().pipeline;
    float v[6 * 24] = {};
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);   // input 1: no hazard
    const uint64_t rc = p.recomputes;
    CHECK(p.rt_feedback_keys == 0 && p.unmapped_inputs == 0);
    // the target's own texture at stage 5, written, under a pixel shader that samples nothing (its
    // reflection's sampler mask 0): no hazard (UpdateActiveHazardsRT, :6329-6332)
    d->SetTexture(5, rt_tex);
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);   // input 2
    CHECK(p.recomputes == rc + 1 && p.rt_feedback_keys == 0 && p.distinct_inputs == 2);
    // ... and under one that samples stage 5 (mask 0x20): the unresolvable hazard
    d->SetPixelShader(ps5);
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);   // input 3
    CHECK(p.recomputes == rc + 2 && p.rt_feedback_keys == 1 && p.distinct_inputs == 3);
    d->SetPixelShader(ps);
    d->SetTexture(5, nullptr);
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);   // input 1 again
    CHECK(p.recomputes == rc + 3 && p.distinct_inputs == 3);
    // the depth-stencil surface's own texture at stage 3: recomputed (N6), and not mapped
    d->SetTexture(3, ds_tex);
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);   // input 4
    CHECK(p.recomputes == rc + 4 && p.distinct_inputs == 4 && p.unmapped_inputs == 1);
    d->SetTexture(3, nullptr);
    // a texture whose level 1 is the target: no hazard (DXVK's level-0 rule, :6320-6325)
    CHECK(d->SetRenderTarget(0, mip1) == D3D_OK);
    d->SetTexture(6, rt_mip);
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);   // input 5
    CHECK(p.recomputes == rc + 5 && p.distinct_inputs == 5 && p.rt_feedback_keys == 1);
    d->SetTexture(6, nullptr);
    CHECK(d->SetRenderTarget(0, rt_surf) == D3D_OK);
    mip1->Release();
    rt_surf->Release();
    ds_surf->Release();
    rt_mip->Release();
    ds_tex->Release();
    rt_tex->Release();
    ps5->Release();
    ps->Release();
    vs->Release();
    decl->Release();
    d->Release();
    const std::string keys = read_text(dir + "/d3d9gpu.keys.json");
    CHECK(keys.find("\"rt_tex_sampled\": 1, \"rt_tex_stages\": 32, \"ds_tex_sampled\": 0") != std::string::npos);
    CHECK(keys.find("\"rt_tex_sampled\": 0, \"rt_tex_stages\": 0, \"ds_tex_sampled\": 1") != std::string::npos);
    // each input's reflection, the shaders' own (U9): kPs writes oC0 and samples nothing, kPsSample5
    // samples stage 5; kVs reads the position alone
    CHECK(keys.find("\"ps_rt_mask\": 1, \"ps_sampler_mask\": 0, \"vs_inputs\": [[0, 0]], \"vs_inputs_overflow\": false") != std::string::npos);
    CHECK(keys.find("\"ps_rt_mask\": 1, \"ps_sampler_mask\": 32, \"vs_inputs\": [[0, 0]]") != std::string::npos);
    Sha256 hv, hp, hp5;
    hv.update(kVs, sizeof kVs);
    hp.update(kPs, sizeof kPs);
    hp5.update(kPsSample5, sizeof kPsSample5);
    CHECK(keys.find("\"shaders\": {\"vs\": [\"" + hv.hex() + "\"], \"ps\": [\"" + hp.hex() + "\", \"" + hp5.hex() +
                    "\"], \"vs_input_signature\": [[[0, 0]]], \"ps_masks\": [[0, 1], [32, 1]]}") != std::string::npos);
    CHECK(keys.find("\"schema\": \"sa2-d3d9gpu-keys/v3\"") != std::string::npos);
}

void test_report_shape() {
    d9g::Device* d = make_device();
    d->Clear(0, nullptr, D3DCLEAR_TARGET, 0, 1.0f, 0);
    d->Present(nullptr, nullptr, nullptr, nullptr);
    d->Release();
    const std::string j = d9g::report().json();
    for (const char* key : {"\"schema\": \"sa2-d3d9gpu-report/v1\"", "\"complete\": true", "\"fault\": null",
                            "\"encoded\"", "\"unmapped\"", "\"backend_failed\"", "\"fault_suppressed\"", "\"per_frame\": [[1, 1, 0]]", "\"not_implemented\"",
                            "\"translated\": 0", "\"live_at_destroy\": {}"})
        CHECK(j.find(key) != std::string::npos);
}

int run_clear_cases() {
    std::string line;
    while (std::getline(std::cin, line)) {
        std::istringstream in(line);
        d9g::ClearInput c;
        int32_t scissor = 0, has = 0, n = 0;
        in >> c.vp_x >> c.vp_y >> c.vp_w >> c.vp_h >> c.sc_left >> c.sc_top >> c.sc_right >> c.sc_bottom >> scissor >>
            c.count >> has >> n;
        std::vector<int32_t> rects(size_t(n) * 4);
        for (auto& v : rects) in >> v;
        uint32_t iw = 0, ih = 0;
        in >> iw >> ih;
        if (!in) { std::printf("{\"error\": \"bad line\"}\n"); continue; }
        c.scissor = scissor != 0;
        c.rects = has ? rects.data() : nullptr;
        std::string out = "{\"regions\": [";
        std::string images = "[";
        bool first = true;
        for (const d9g::ClearBox& b : d9g::clear_regions(c)) {
            const d9g::ImageClear ic = d9g::clear_image(b, iw, ih);
            char buf[256];
            std::snprintf(buf, sizeof buf, "%s[%d, %d, %u, %u]", first ? "" : ", ", b.x, b.y, b.w, b.h);
            out += buf;
            std::snprintf(buf, sizeof buf, "%s{\"kind\": \"%s\", \"box\": [%d, %d, %u, %u], \"past_edge\": %s}",
                          first ? "" : ", ",
                          ic.kind == d9g::ClearKind::full ? "full" : ic.kind == d9g::ClearKind::partial ? "partial" : "skip",
                          ic.box.x, ic.box.y, ic.box.w, ic.box.h, ic.past_edge ? "true" : "false");
            images += buf;
            first = false;
        }
        std::printf("%s], \"images\": %s]}\n", out.c_str(), images.c_str());
    }
    return 0;
}

}  // namespace

// R1: every shader is translated at its creation by DXVK's compiler (d9g_device.cpp
// translate_shader) with the profile the GPU's facts give, its reflection is DXVK's analysis's,
// its bytecode is dumped by sha256 where SA2_D3D9GPU_SHADER_DUMP asks, and a shader the translator
// refuses is the call's failure and the device's not-implemented case, never a silent draw.
void test_shader_translation() {
    d9g::Device* d = make_device();
    d9g::Report& r = d9g::report();
    const std::string dir = std::string(SA2_TEST_TMP) + "/shader-dump-test";
    std::error_code ec;
    std::filesystem::remove_all(dir, ec);
    std::filesystem::create_directories(dir, ec);
    r.shader_tx.dump_dir = dir;
    CHECK(!r.shader_tx.profile_sha256.empty());
    IDirect3DVertexShader9* vs = nullptr;
    IDirect3DPixelShader9* ps = nullptr;
    CHECK(d->CreateVertexShader(kVs, &vs) == D3D_OK && vs);
    CHECK(d->CreatePixelShader(kPs, &ps) == D3D_OK && ps);
    CHECK(r.shader_tx.translated == 2 && r.shader_tx.failures == 0 && r.shader_tx.dumped == 2);
    const d9g::shader::Translated& tv = static_cast<d9g::VertexShader*>(vs)->translated();
    const d9g::shader::Translated& tp = static_cast<d9g::PixelShader*>(ps)->translated();
    CHECK(tv.ok && tv.stage == d9g::shader::Stage::Vertex && tv.reflection.major == 3 && tv.reflection.minor == 0);
    CHECK(tv.reflection.input_signature.size() == 1 && tv.reflection.input_signature[0] == std::make_pair(0u, 0u));
    CHECK(tp.ok && tp.stage == d9g::shader::Stage::Pixel && tp.reflection.rt_mask == 1 && tp.reflection.sampler_mask == 0);
    // the dump: the bytecode itself, by its sha256
    Sha256 h;
    h.update(kVs, sizeof kVs);
    CHECK(read_text(dir + "/" + h.hex() + ".bin") == std::string(reinterpret_cast<const char*>(kVs), sizeof kVs));
    // a second creation of the same bytecode is translated again and not dumped again
    IDirect3DVertexShader9* vs2 = nullptr;
    CHECK(d->CreateVertexShader(kVs, &vs2) == D3D_OK && r.shader_tx.translated == 3 && r.shader_tx.dumped == 2);
    // a translator fault outside a test run: the translation is refused, the call fails, the case is named
    setenv("SA2_D3D9GPU_SHADER_FAULT", "fp16", 1);
    unsetenv("SA2_D3D9GPU_TEST_RUN");
    IDirect3DPixelShader9* bad = nullptr;
    const uint64_t ni = r.ni_value_count;
    CHECK(d->CreatePixelShader(kPs, &bad) == D3DERR_INVALIDCALL && !bad);
    CHECK(r.shader_tx.failures == 1 && r.ni_value_count == ni + 1 && r.shader_tx.failure_reasons.size() == 1);
    CHECK(r.shader_tx.failure_reasons[0].find("only in a test run") != std::string::npos);
    unsetenv("SA2_D3D9GPU_SHADER_FAULT");
    vs->Release();
    vs2->Release();
    ps->Release();
    d->Release();
    // a device that can do software vertex processing: DXVK compiles every shader of it with isSWVP
    // (d3d9_device.cpp:7886, :7904), which the translator does not mirror, so each is refused by name
    d9g::Device* sw = make_device(0, D3DCREATE_MIXED_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE);
    d9g::Report& rs = d9g::report();
    IDirect3DVertexShader9* swvs = nullptr;
    IDirect3DPixelShader9* swps = nullptr;
    CHECK(sw->CreateVertexShader(kVs, &swvs) == D3DERR_INVALIDCALL && !swvs);
    CHECK(sw->CreatePixelShader(kPs, &swps) == D3DERR_INVALIDCALL && !swps);
    CHECK(rs.shader_tx.translated == 0 && rs.shader_tx.failures == 2 && rs.shader_tx.failure_reasons.size() == 2);
    CHECK(rs.shader_tx.failure_reasons[0].find("software vertex processing") != std::string::npos);
    sw->Release();
}

void test_draw_requests() {
    auto* d = make_device(0, kBehavior, true, true);
    const DWORD constant_ps[] = {0xFFFF0300u, 0x02000001u, 0x800F0800u, 0xA0E40005u, 0x0000FFFFu};
    IDirect3DVertexShader9* vs = nullptr;
    IDirect3DPixelShader9* ps = nullptr;
    IDirect3DVertexDeclaration9* decl = nullptr;
    IDirect3DVertexBuffer9* vb = nullptr;
    IDirect3DIndexBuffer9* ib = nullptr;
    const D3DVERTEXELEMENT9 e[] = {{0, 0, D3DDECLTYPE_FLOAT4, 0, D3DDECLUSAGE_POSITION, 0}, D3DDECL_END()};
    CHECK(d->CreateVertexShader(kVs, &vs) == D3D_OK);
    CHECK(d->CreatePixelShader(constant_ps, &ps) == D3D_OK);
    CHECK(d->CreateVertexDeclaration(e, &decl) == D3D_OK);
    CHECK(d->CreateVertexBuffer(128, 0, 0, D3DPOOL_DEFAULT, &vb, nullptr) == D3D_OK);
    CHECK(d->CreateIndexBuffer(16, 0, D3DFMT_INDEX16, D3DPOOL_DEFAULT, &ib, nullptr) == D3D_OK);
    d->SetVertexShader(vs); d->SetPixelShader(ps); d->SetVertexDeclaration(decl);
    d->SetStreamSource(0, vb, 16, 16); d->SetIndices(ib);
    const float red[] = {1, 0, 0, 1}, blue[] = {0, 0, 1, 1};
    CHECK(d->SetPixelShaderConstantF(5, red, 1) == D3D_OK);
    CHECK(d->DrawPrimitive(D3DPT_TRIANGLELIST, 1, 1) == D3D_OK);
    const auto* trace = d9g::cpu_gpu_draws(d->gpu());
    CHECK(trace && trace->size() == 1);
    if (!trace || trace->empty()) { d->Release(); return; }
    const auto& first = std::get<d9g::draw::NonIndexed>(trace->at(0).geometry);
    CHECK(first.count == 3 && first.first_vertex == 1);
    const auto& bound = std::get<d9g::draw::BoundVertices>(first.vertices).stream_zero;
    CHECK(bound.offset == 16 && bound.available_bytes == 112);
    CHECK(trace->at(0).pixel.uniforms.slots[0].size() == 16);
    CHECK(std::memcmp(trace->at(0).pixel.uniforms.slots[0].data(), red, 16) == 0);
    CHECK(d->SetPixelShaderConstantF(5, blue, 1) == D3D_OK);
    CHECK(d->DrawPrimitive(D3DPT_TRIANGLELIST, 1, 1) == D3D_OK);
    CHECK(trace->size() == 2);
    CHECK(std::memcmp(trace->at(1).pixel.uniforms.slots[0].data(), blue, 16) == 0);
    CHECK(std::memcmp(trace->at(0).pixel.uniforms.slots[0].data(), red, 16) == 0);
    CHECK(d9g::report().presents == 0 && d9g::report().drawn == 2);
    void* index_data = nullptr;
    const uint16_t offset_indices[] = {0, 0, 2, 3, 4};
    CHECK(ib->Lock(0, sizeof offset_indices, &index_data, 0) == D3D_OK);
    std::memcpy(index_data, offset_indices, sizeof offset_indices); CHECK(ib->Unlock() == D3D_OK);
    CHECK(d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, -1, 2, 3, 2, 1) == D3D_OK);
    CHECK(trace->size() == 3);
    const auto& indexed = std::get<d9g::draw::BufferIndexed>(trace->at(2).geometry);
    CHECK(indexed.count == 3 && indexed.first_index == 2 && indexed.base_vertex == -1 && indexed.instances == 1);
    float up_vertices[20] = {};
    const uint16_t up_indices[] = {2, 3, 4};
    CHECK(d->DrawIndexedPrimitiveUP(D3DPT_TRIANGLELIST, 2, 3, 1, up_indices, D3DFMT_INDEX16, up_vertices, 16) == D3D_OK);
    CHECK(trace->size() == 4);
    const auto& up = std::get<d9g::draw::OwnedIndexedUp>(trace->at(3).geometry);
    CHECK(up.vertex_bytes == 80 && up.index_offset == 80 && up.index_bytes == 6 && up.bytes.size() == 86);
    CHECK(std::memcmp(up.bytes.data() + 80, up_indices, 6) == 0);
    up_vertices[0] = 99;
    const float zero = 0; CHECK(std::memcmp(up.bytes.data(), &zero, 4) == 0);
    CHECK(d9g::report().up_uploaded_bytes == 86 && d9g::report().up_arena_uploads == 1);
    CHECK(d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1) == D3D_OK);
    CHECK(trace->size() == 4 && d9g::report().unmapped_draws == 1 && d9g::report().accepted_draws == 5);
    d->SetStreamSource(0, vb, 16, 16);
    CHECK(d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 3, 0, 1) == D3D_OK);
    CHECK(trace->size() == 4 && d9g::report().unmapped_draws == 2);
    CHECK(std::find(d9g::report().draw_failures.begin(), d9g::report().draw_failures.end(), "UNBOUND_INDEX_BUFFER") != d9g::report().draw_failures.end());
    d->SetStreamSource(0, vb, 112, 16);
    CHECK(d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1) == D3D_OK);
    CHECK(trace->size() == 4 && d9g::report().unmapped_draws == 3);
    CHECK(d9g::report().failure.empty() && d9g::report().draws_total == 7);
    vs->Release(); ps->Release(); decl->Release(); vb->Release(); ib->Release(); d->Release();
}

// A device without shaderClipDistance (a Mali GPU: the CPU model's clip_distance false, so
// ShaderProfile::no_clip_distance): its vertex shaders are translated with no ClipDistance output,
// a draw with no user clip plane enabled is drawn, and a draw with one enabled is refused by name
// (USER_CLIP_PLANES), since the module cannot clip; a plane set but not enabled, or enabled but all
// zero (DXVK's rule: no plane), is drawn. The control, a device with the feature, keeps the output
// and draws with the plane enabled.
void test_no_clip_distance() {
    auto has_clip_output = [](const d9g::shader::Translated& t) {
        return std::any_of(t.reflection.outputs.begin(), t.reflection.outputs.end(),
                           [](const d9g::shader::IoVar& v) { return v.builtin && v.builtin_name == "ClipDistance"; });
    };
    const DWORD constant_ps[] = {0xFFFF0300u, 0x02000001u, 0x800F0800u, 0xA0E40005u, 0x0000FFFFu};
    const D3DVERTEXELEMENT9 e[] = {{0, 0, D3DDECLTYPE_FLOAT4, 0, D3DDECLUSAGE_POSITION, 0}, D3DDECL_END()};
    const float plane[4] = {0, 1, 0, 0}, zero[4] = {0, 0, 0, 0};
    for (const bool clip_distance : {false, true}) {
        d9g::report() = d9g::Report{};
        d9g::DeviceSetup setup;
        setup.pp = present_params();
        setup.behavior = kBehavior;
        auto* d = new d9g::Device(d9g::make_cpu_gpu(true, true, clip_distance), setup);   // the SDL patch's, as test_draw_requests
        std::string why;
        CHECK(d->init(&why));
        IDirect3DVertexShader9* vs = nullptr;
        IDirect3DPixelShader9* ps = nullptr;
        IDirect3DVertexDeclaration9* decl = nullptr;
        IDirect3DVertexBuffer9* vb = nullptr;
        CHECK(d->CreateVertexShader(kVs, &vs) == D3D_OK && vs);
        CHECK(d->CreatePixelShader(constant_ps, &ps) == D3D_OK && ps);
        CHECK(d->CreateVertexDeclaration(e, &decl) == D3D_OK);
        CHECK(d->CreateVertexBuffer(128, 0, 0, D3DPOOL_DEFAULT, &vb, nullptr) == D3D_OK);
        if (!vs || !ps || !decl || !vb) { d->Release(); return; }
        CHECK(has_clip_output(static_cast<d9g::VertexShader*>(vs)->translated()) == clip_distance);
        CHECK(d->gpu()->info().clip_distance == clip_distance);
        d->SetVertexShader(vs); d->SetPixelShader(ps); d->SetVertexDeclaration(decl);
        d->SetStreamSource(0, vb, 0, 16);
        CHECK(d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1) == D3D_OK);                   // no plane
        CHECK(d->SetClipPlane(0, plane) == D3D_OK);
        CHECK(d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1) == D3D_OK);                   // set, not enabled
        CHECK(d->SetRenderState(D3DRS_CLIPPLANEENABLE, 2) == D3D_OK);
        CHECK(d->SetClipPlane(1, zero) == D3D_OK);
        CHECK(d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1) == D3D_OK);                   // enabled, all zero
        CHECK(d9g::report().drawn == 3 && d9g::report().unmapped_draws == 0);
        CHECK(d->SetRenderState(D3DRS_CLIPPLANEENABLE, 1) == D3D_OK);
        CHECK(d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1) == D3D_OK);                   // enabled
        const auto& f = d9g::report().draw_failures;
        const bool refused = std::find(f.begin(), f.end(), "USER_CLIP_PLANES without shaderClipDistance") != f.end();
        CHECK(refused == !clip_distance);
        CHECK(d9g::report().drawn == (clip_distance ? 4u : 3u) && d9g::report().unmapped_draws == (clip_distance ? 0u : 1u));
        vs->Release(); ps->Release(); decl->Release(); vb->Release(); d->Release();
    }
}

// IDirect3DCubeTexture9 and IDirect3DVolumeTexture9 (d9g_resources.cpp CubeTexture,
// VolumeTexture) on the CPU model: DXVK's creation rules (NormalizeTextureProperties' cube and
// volume clauses), the level counts and descriptions, a face's surface whose LockRect and UnlockRect
// upload that face's level and nothing else, a volume's boxes and slices, the container rule for a
// face, DXVK's second-unlock answer (D3D_OK for a 2D texture alone), the GPU image's type, and a draw
// sampling a bound cube or volume refused by name.
// A render-target cube bound while its face is the target recomputes the pipeline input and
// records the hazard, as the 2D texture whose level 0 is the target does in test_hazard_tracking
// (SetTexture marks the pipeline dirty for a render-target cube too; review of the cube work).
void test_cube_hazard_census() {
    d9g::Device* d = make_device();
    const std::string dir = std::string(SA2_TEST_TMP) + "/cube-hazard-test";
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    d9g::report().path = dir + "/d3d9gpu.json";
    IDirect3DVertexDeclaration9* decl = nullptr;
    IDirect3DVertexShader9* vs = nullptr;
    IDirect3DPixelShader9* ps = nullptr;
    CHECK(d->CreateVertexDeclaration(kCensusDecl, &decl) == D3D_OK);
    CHECK(d->CreateVertexShader(kVs, &vs) == D3D_OK && d->CreatePixelShader(kPs, &ps) == D3D_OK);
    IDirect3DCubeTexture9* cube = nullptr;
    CHECK(d->CreateCubeTexture(64, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &cube, nullptr) == D3D_OK);
    if (!cube || !decl || !vs || !ps) return;
    IDirect3DSurface9 *face = nullptr, *bb = nullptr;
    CHECK(cube->GetCubeMapSurface(D3DCUBEMAP_FACE_POSITIVE_X, 0, &face) == D3D_OK && face);
    CHECK(d->GetRenderTarget(0, &bb) == D3D_OK && bb);
    if (!face || !bb) return;
    d->SetVertexDeclaration(decl); d->SetVertexShader(vs); d->SetPixelShader(ps);
    d->SetRenderState(D3DRS_MULTISAMPLEANTIALIAS, FALSE);
    d->SetRenderState(D3DRS_ZENABLE, D3DZB_FALSE);
    const d9g::Report::Pipeline& p = d9g::report().pipeline;
    float v[6 * 24] = {};
    CHECK(d->SetRenderTarget(0, face) == D3D_OK);
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);
    const uint64_t rc = p.recomputes, di = p.distinct_inputs;
    d->SetTexture(5, cube);
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);
    CHECK(p.recomputes == rc + 1 && p.distinct_inputs == di + 1);
    d->SetTexture(5, nullptr);
    d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 24);
    CHECK(p.recomputes == rc + 2 && p.distinct_inputs == di + 1);
    d->SetRenderTarget(0, bb);
    bb->Release(); face->Release(); cube->Release();
    d->SetVertexDeclaration(nullptr); d->SetVertexShader(nullptr); d->SetPixelShader(nullptr);
    decl->Release(); vs->Release(); ps->Release();
    d->Release();
    const std::string keys = read_text(dir + "/d3d9gpu.keys.json");
    CHECK(keys.find("\"rt_tex_sampled\": 1, \"rt_tex_stages\": 32") != std::string::npos);
}

void test_cube_and_volume_textures() {
    d9g::Device* d = make_device(0, kBehavior, true, true);
    d9g::Report& r = d9g::report();
    const std::string dir = std::string(SA2_TEST_TMP) + "/cube-test";
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    r.path = dir + "/d3d9gpu.json";
    IDirect3DCubeTexture9* cube = nullptr;
    CHECK(d->CreateCubeTexture(64, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, nullptr, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateCubeTexture(64, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &cube, nullptr) == D3D_OK && cube);
    CHECK(cube->GetLevelCount() == 7 && cube->GetType() == D3DRTYPE_CUBETEXTURE && d->live_count() == 1);
    CHECK(r.created["cube_texture"] == 1);
    D3DSURFACE_DESC sd{};
    CHECK(cube->GetLevelDesc(1, &sd) == D3D_OK && sd.Width == 32 && sd.Height == 32 && sd.Format == D3DFMT_A8R8G8B8 &&
          sd.Type == D3DRTYPE_SURFACE && sd.Pool == D3DPOOL_MANAGED);
    CHECK(cube->GetLevelDesc(7, &sd) == D3DERR_INVALIDCALL && cube->GetLevelDesc(0, nullptr) == D3DERR_INVALIDCALL);
    auto* cimg = static_cast<d9g::CubeTexture*>(cube)->image();
    d9g::TexDesc td{};
    CHECK(d9g::cpu_gpu_desc(d->gpu(), cimg->gpu, &td) && td.type == d9g::TexType::cube && td.w == 64 && td.levels == 7);
    CHECK(cimg->store.size() == 42 && cimg->store[3 * 7 + 1].size() == 32 * 32 * 4);
    // GetCubeMapSurface: face 3's level 1, its lock the face's own store, its unlock an upload of it alone
    IDirect3DSurface9 *face = nullptr, *bad = nullptr;
    CHECK(cube->GetCubeMapSurface(D3DCUBEMAP_FACES(6), 0, &bad) == D3DERR_INVALIDCALL && !bad);
    CHECK(cube->GetCubeMapSurface(D3DCUBEMAP_FACE_POSITIVE_X, 7, &bad) == D3DERR_INVALIDCALL && !bad);
    CHECK(cube->GetCubeMapSurface(D3DCUBEMAP_FACE_POSITIVE_X, 0, nullptr) == D3DERR_INVALIDCALL);
    CHECK(cube->GetCubeMapSurface(D3DCUBEMAP_FACE_NEGATIVE_Y, 1, &face) == D3D_OK && face);
    CHECK(face->GetDesc(&sd) == D3D_OK && sd.Width == 32 && sd.Height == 32);
    D3DLOCKED_RECT lr{};
    CHECK(face->LockRect(&lr, nullptr, 0) == D3D_OK && lr.Pitch == 128);
    CHECK(static_cast<uint8_t*>(lr.pBits) == cimg->store[3 * 7 + 1].data());
    CHECK(cube->LockRect(D3DCUBEMAP_FACE_NEGATIVE_Y, 1, &lr, nullptr, 0) == D3DERR_INVALIDCALL);   // the same subresource
    for (uint32_t i = 0; i < 32 * 32; i++) std::memcpy(static_cast<uint8_t*>(lr.pBits) + 4 * i, "\x10\x20\x30\x40", 4);
    const uint64_t uploads = r.texture_level_uploads;
    CHECK(face->UnlockRect() == D3D_OK && r.texture_level_uploads == uploads + 1);
    CHECK(face->UnlockRect() == D3DERR_INVALIDCALL);                 // a cube's second unlock (DXVK :5219-5224)
    std::string bytes;
    CHECK(d9g::cpu_gpu_bytes(d->gpu(), cimg->gpu, 1, &bytes, 3) && bytes.size() == 32 * 32 * 4 &&
          bytes.substr(0, 4) == "\x10\x20\x30\x40" && bytes.substr(4 * 1023, 4) == "\x10\x20\x30\x40");
    CHECK(d9g::cpu_gpu_bytes(d->gpu(), cimg->gpu, 1, &bytes, 2) && bytes == std::string(32 * 32 * 4, '\0'));
    CHECK(d9g::cpu_gpu_bytes(d->gpu(), cimg->gpu, 0, &bytes, 3) && bytes == std::string(64 * 64 * 4, '\0'));
    // the cube's own LockRect: face 0 level 2 with a rectangle, the face's packed pitch
    const RECT rc{4, 2, 8, 4};
    CHECK(cube->LockRect(D3DCUBEMAP_FACE_POSITIVE_X, 2, &lr, &rc, 0) == D3D_OK && lr.Pitch == 64);
    CHECK(static_cast<uint8_t*>(lr.pBits) == cimg->store[2].data() + 2 * 64 + 4 * 4);
    CHECK(cube->UnlockRect(D3DCUBEMAP_FACE_POSITIVE_X, 2) == D3D_OK);
    CHECK(cube->UnlockRect(D3DCUBEMAP_FACES(6), 0) == D3DERR_INVALIDCALL && cube->UnlockRect(D3DCUBEMAP_FACE_POSITIVE_X, 7) == D3DERR_INVALIDCALL);
    CHECK(cube->SetLOD(9) == 0 && cube->GetLOD() == 6);
    // the container rule: the face's counts are the cube's
    CHECK(cube->Release() == 1 && d->live_count() == 1);
    CHECK(face->AddRef() == 2 && face->Release() == 1);
    CHECK(face->Release() == 0 && d->live_count() == 0);
    // NormalizeTextureProperties: a depth cube, a DXT edge off its block, MANAGED and DYNAMIC, a
    // render target outside DEFAULT
    IDirect3DCubeTexture9* c2 = nullptr;
    CHECK(d->CreateCubeTexture(64, 1, D3DUSAGE_DEPTHSTENCIL, D3DFMT_D24S8, D3DPOOL_DEFAULT, &c2, nullptr) == D3DERR_INVALIDCALL && !c2);
    CHECK(d->CreateCubeTexture(6, 1, 0, D3DFORMAT(kDXT1), D3DPOOL_MANAGED, &c2, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateCubeTexture(64, 1, D3DUSAGE_DYNAMIC, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &c2, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateCubeTexture(64, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &c2, nullptr) == D3DERR_INVALIDCALL);
    // a render-target cube, as the game's render-target creator makes one: a face is a render target
    // (Clear and a draw write that face's layer and no other), not lockable, its readback named
    IDirect3DCubeTexture9* rtc = nullptr;
    CHECK(d->CreateCubeTexture(32, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &rtc, nullptr) == D3D_OK && rtc);
    auto* rimg = static_cast<d9g::CubeTexture*>(rtc)->image();
    CHECK(d9g::cpu_gpu_desc(d->gpu(), rimg->gpu, &td) && td.type == d9g::TexType::cube && td.target && rimg->store.empty());
    IDirect3DSurface9 *rface = nullptr, *bb = nullptr;
    CHECK(rtc->GetCubeMapSurface(D3DCUBEMAP_FACE_POSITIVE_Z, 0, &rface) == D3D_OK && rface);
    CHECK(rface->LockRect(&lr, nullptr, 0) == D3DERR_INVALIDCALL);   // DEFAULT, not DYNAMIC
    CHECK(d->GetRenderTarget(0, &bb) == D3D_OK);
    CHECK(d->SetRenderTarget(0, rface) == D3D_OK);
    D3DVIEWPORT9 vp{};
    CHECK(d->GetViewport(&vp) == D3D_OK && vp.Width == 32 && vp.Height == 32);
    CHECK(d->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF336699u, 1.0f, 0) == D3D_OK);
    CHECK(d9g::cpu_gpu_bytes(d->gpu(), rimg->gpu, 0, &bytes, 4) && bytes.substr(0, 4) == "\x99\x66\x33\xFF" &&
          bytes.substr(4 * 1023, 4) == "\x99\x66\x33\xFF");
    CHECK(d9g::cpu_gpu_bytes(d->gpu(), rimg->gpu, 0, &bytes, 3) && bytes == std::string(32 * 32 * 4, '\0'));
    const D3DRECT part{8, 8, 16, 12};
    CHECK(d->Clear(1, &part, D3DCLEAR_TARGET, 0xFF00FF00u, 1.0f, 0) == D3D_OK);   // a partial clear: the face's own rectangle
    CHECK(d9g::cpu_gpu_bytes(d->gpu(), rimg->gpu, 0, &bytes, 4) && bytes.substr((8 * 32 + 8) * 4, 4) == std::string("\x00\xFF\x00\xFF", 4) &&
          bytes.substr((7 * 32 + 8) * 4, 4) == "\x99\x66\x33\xFF");
    CHECK(d9g::cpu_gpu_bytes(d->gpu(), rimg->gpu, 0, &bytes, 3) && bytes == std::string(32 * 32 * 4, '\0'));
    IDirect3DSurface9* sys = nullptr;
    CHECK(d->CreateOffscreenPlainSurface(32, 32, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &sys, nullptr) == D3D_OK);
    const uint64_t ni = r.ni_value_count;
    CHECK(d->GetRenderTargetData(rface, sys) == D3DERR_INVALIDCALL && r.ni_value_count == ni + 1);
    sys->Release();
    // a draw into the face: its colour attachment is the cube's layer 4
    {
        IDirect3DVertexShader9* fvs = nullptr;
        IDirect3DPixelShader9* fps = nullptr;
        IDirect3DVertexDeclaration9* fdecl = nullptr;
        const D3DVERTEXELEMENT9 fe[] = {{0, 0, D3DDECLTYPE_FLOAT4, 0, D3DDECLUSAGE_POSITION, 0}, D3DDECL_END()};
        CHECK(d->CreateVertexShader(kVs, &fvs) == D3D_OK && d->CreatePixelShader(kPs, &fps) == D3D_OK &&
              d->CreateVertexDeclaration(fe, &fdecl) == D3D_OK);
        d->SetVertexShader(fvs); d->SetPixelShader(fps); d->SetVertexDeclaration(fdecl);
        d->SetRenderState(D3DRS_ZENABLE, D3DZB_FALSE);
        float fv[12] = {};
        const auto* ftrace = d9g::cpu_gpu_draws(d->gpu());
        const size_t before = ftrace ? ftrace->size() : 0;
        CHECK(d->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, fv, 16) == D3D_OK);
        CHECK(ftrace && ftrace->size() == before + 1);
        if (ftrace && ftrace->size() == before + 1) {
            const auto& att = ftrace->back().attachments;
            CHECK(att.colours.size() == 1 && att.colours[0].texture.value == rimg->gpu && att.colours[0].layer == 4 &&
                  att.colours[0].level == 0);
        }
        // the cube bound where the pixel shader samples while its face is the target: DXVK's
        // feedback-loop hazard (UpdateActiveHazardsRT on the common texture), the user's item
        IDirect3DPixelShader9* sps = nullptr;
        CHECK(d->CreatePixelShader(kPsSample5, &sps) == D3D_OK);
        d->SetPixelShader(sps);
        CHECK(d->SetTexture(5, rtc) == D3D_OK);
        CHECK(d->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, fv, 16) == D3D_OK && ftrace->size() == before + 1);
        CHECK(std::find(r.draw_failures.begin(), r.draw_failures.end(), "RT_FEEDBACK") != r.draw_failures.end());
        CHECK(d->SetTexture(5, nullptr) == D3D_OK);
        d->SetRenderState(D3DRS_ZENABLE, D3DZB_TRUE);
        d->SetVertexShader(nullptr); d->SetPixelShader(nullptr); d->SetVertexDeclaration(nullptr);
        fvs->Release(); fps->Release(); fdecl->Release(); sps->Release();
    }
    CHECK(d->SetRenderTarget(0, bb) == D3D_OK);
    bb->Release();
    CHECK(rtc->Release() == 1 && rface->Release() == 0);              // the face held its cube
    CHECK(d->CreateCubeTexture(16, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &c2, nullptr) == D3D_OK);
    CHECK(c2->LockRect(D3DCUBEMAP_FACE_POSITIVE_X, 0, &lr, nullptr, 0) == D3DERR_INVALIDCALL);   // DEFAULT, not DYNAMIC
    // the volume: 16 x 8 x 4, five levels (computeMipLevelCount of all three)
    IDirect3DVolumeTexture9* vol = nullptr;
    CHECK(d->CreateVolumeTexture(16, 8, 4, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &vol, nullptr) == D3D_OK && vol);
    CHECK(vol->GetLevelCount() == 5 && vol->GetType() == D3DRTYPE_VOLUMETEXTURE && r.created["volume_texture"] == 1);
    D3DVOLUME_DESC vd{};
    CHECK(vol->GetLevelDesc(2, &vd) == D3D_OK && vd.Width == 4 && vd.Height == 2 && vd.Depth == 1 &&
          vd.Type == D3DRTYPE_VOLUME && vd.Format == D3DFMT_A8R8G8B8 && vd.Pool == D3DPOOL_MANAGED);
    CHECK(vol->GetLevelDesc(0, &vd) == D3D_OK && vd.Depth == 4 && vol->GetLevelDesc(5, &vd) == D3DERR_INVALIDCALL);
    auto* vimg = static_cast<d9g::VolumeTexture*>(vol)->image();
    CHECK(d9g::cpu_gpu_desc(d->gpu(), vimg->gpu, &td) && td.type == d9g::TexType::volume && td.depth == 4 && td.levels == 5);
    D3DLOCKED_BOX lb{};
    CHECK(vol->LockBox(0, &lb, nullptr, 0) == D3D_OK && lb.RowPitch == 64 && lb.SlicePitch == 512);
    CHECK(vol->LockBox(0, &lb, nullptr, 0) == D3DERR_INVALIDCALL && lb.pBits == nullptr && lb.RowPitch == 0);
    CHECK(vol->LockBox(0, &lb, nullptr, 0) == D3DERR_INVALIDCALL);
    uint8_t* bits = vimg->store[0].data();
    for (uint32_t z = 0; z < 4; z++) std::memset(bits + 512 * z, int(0x30 + z), 512);
    CHECK(vol->UnlockBox(0) == D3D_OK && vol->UnlockBox(0) == D3DERR_INVALIDCALL);
    CHECK(d9g::cpu_gpu_bytes(d->gpu(), vimg->gpu, 0, &bytes) && bytes.size() == 2048 && uint8_t(bytes[0]) == 0x30 &&
          uint8_t(bytes[3 * 512 + 7]) == 0x33);
    const D3DBOX box{1, 1, 3, 2, 1, 2};
    CHECK(vol->LockBox(0, &lb, &box, 0) == D3D_OK && static_cast<uint8_t*>(lb.pBits) == bits + 512 + 64 + 4);
    CHECK(vol->UnlockBox(0) == D3D_OK);
    const D3DBOX deep{0, 0, 4, 4, 0, 3};
    CHECK(vol->LockBox(1, &lb, &deep, 0) == D3DERR_INVALIDCALL);       // level 1 is 8 x 4 x 2
    CHECK(vol->LockBox(5, &lb, nullptr, 0) == D3DERR_INVALIDCALL && vol->UnlockBox(5) == D3DERR_INVALIDCALL);
    IDirect3DVolume9* level = nullptr;
    CHECK(vol->GetVolumeLevel(0, &level) == D3DERR_INVALIDCALL);      // IDirect3DVolume9: not implemented, by name
    CHECK(r.ni_methods[d9g::I_VOLUME_TEX][18] == 1);
    IDirect3DVolumeTexture9* v2 = nullptr;
    CHECK(d->CreateVolumeTexture(16, 16, 0, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &v2, nullptr) == D3DERR_INVALIDCALL);
    CHECK(d->CreateVolumeTexture(16, 16, 4, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8, D3DPOOL_DEFAULT, &v2, nullptr) ==
          D3DERR_INVALIDCALL);
    CHECK(d->CreateVolumeTexture(16, 16, 4, 1, D3DUSAGE_DYNAMIC, D3DFMT_A8R8G8B8, D3DPOOL_SCRATCH, &v2, nullptr) ==
          D3DERR_INVALIDCALL);
    CHECK(d->CreateVolumeTexture(8, 8, 2, 0, 0, D3DFORMAT(kDXT5), D3DPOOL_MANAGED, &v2, nullptr) == D3D_OK);
    CHECK(v2->LockBox(0, &lb, nullptr, 0) == D3D_OK && lb.RowPitch == 32 && lb.SlicePitch == 64 && v2->UnlockBox(0) == D3D_OK);
    const D3DBOX unaligned{1, 0, 4, 4, 0, 1};
    CHECK(v2->LockBox(0, &lb, &unaligned, 0) == D3DERR_INVALIDCALL);   // a volume's level 0 is block-aligned in any pool
    CHECK(v2->Release() == 0);
    CHECK(d->CreateVolumeTexture(256, 1, 4, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &v2, nullptr) == D3D_OK);
    CHECK(v2->GetLevelCount() == 9);
    v2->Release();
    // deeper than wide or high: the mip count is taken over the depth too (computeMipLevelCount)
    CHECK(d->CreateVolumeTexture(4, 4, 16, 0, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &v2, nullptr) == D3D_OK);
    CHECK(v2->GetLevelCount() == 5);
    v2->Release();
    // a bound cube or volume: kept by its binding, described to the sampler census, and a draw whose
    // pixel shader samples its stage refused by name
    IDirect3DVertexShader9* vs = nullptr;
    IDirect3DPixelShader9* ps = nullptr;
    IDirect3DVertexDeclaration9* decl = nullptr;
    const D3DVERTEXELEMENT9 e[] = {{0, 0, D3DDECLTYPE_FLOAT4, 0, D3DDECLUSAGE_POSITION, 0}, D3DDECL_END()};
    CHECK(d->CreateVertexShader(kVs, &vs) == D3D_OK && d->CreatePixelShader(kPsSample5, &ps) == D3D_OK);
    CHECK(d->CreateVertexDeclaration(e, &decl) == D3D_OK);
    d->SetVertexShader(vs); d->SetPixelShader(ps); d->SetVertexDeclaration(decl);
    CHECK(d->SetTexture(5, c2) == D3D_OK && c2->Release() == 0);       // the binding keeps it
    float verts[16] = {};
    CHECK(d->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, verts, 16) == D3D_OK);
    auto has = [&](const char* reason) {
        return std::find(r.draw_failures.begin(), r.draw_failures.end(), reason) != r.draw_failures.end();
    };
    CHECK(r.unmapped_draws == 2 && has("CUBE_TEXTURE_SAMPLE stage 5"));
    CHECK(d->SetTexture(5, vol) == D3D_OK);                            // the cube goes with its binding
    CHECK(d->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, verts, 16) == D3D_OK);
    CHECK(r.unmapped_draws == 3 && has("VOLUME_TEXTURE_SAMPLE stage 5"));
    const auto* trace = d9g::cpu_gpu_draws(d->gpu());
    CHECK(trace && trace->size() == 1);                                // the draw into the cube face alone
    CHECK(d->SetTexture(4, vol) == D3D_OK && d->SetTexture(5, nullptr) == D3D_OK);   // a stage the shader does not sample
    CHECK(d->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, verts, 16) == D3D_OK && trace->size() == 2 && r.unmapped_draws == 3);
    // a volume bound over a 2D texture at one stage takes the 2D texture's binding away
    IDirect3DTexture9* t2 = nullptr;
    CHECK(d->CreateTexture(8, 8, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, &t2, nullptr) == D3D_OK && t2);
    const auto live = d->live_count();
    CHECK(d->SetTexture(6, t2) == D3D_OK && t2->Release() == 0 && d->live_count() == live);   // the binding keeps it
    CHECK(d->SetTexture(6, vol) == D3D_OK && d->live_count() == live - 1);
    CHECK(d->SetTexture(6, nullptr) == D3D_OK);

    d->SetTexture(4, nullptr);
    CHECK(vol->Release() == 0);
    d->SetVertexShader(nullptr); d->SetPixelShader(nullptr); d->SetVertexDeclaration(nullptr);
    vs->Release(); ps->Release(); decl->Release();
    CHECK(d->live_count() == 0);
    // the per-method table: the cube's and the volume's methods by name
    const std::string j = r.json();
    for (const char* m : {"\"type\": \"CubeTexture\", \"method\": \"GetCubeMapSurface\", \"slot\": 18",
                          "\"type\": \"VolumeTexture\", \"method\": \"GetLevelDesc\", \"slot\": 17",
                          "\"type\": \"Device\", \"method\": \"CreateCubeTexture\", \"slot\": 25",
                          "\"type\": \"Device\", \"method\": \"CreateVolumeTexture\", \"slot\": 24",
                          "\"VolumeTexture::GetVolumeLevel\": 1"})
        CHECK(j.find(m) != std::string::npos);
    d->Release();
    // the sampler census's records of the bound cube and volume (a cube's DXVK cube rule, d9g_sampler.cpp)
    const std::string keys = read_text(dir + "/d3d9gpu.keys.json");
    CHECK(keys.find("\"tex\": true, \"levels\": 1, \"cube\": true, \"depth\": false, \"fmt\": 21") != std::string::npos);
    CHECK(keys.find("\"tex\": true, \"levels\": 5, \"cube\": false, \"depth\": false, \"fmt\": 21") != std::string::npos);
}

// SetStreamSourceFreq's instancing (d9g_pipeline.cpp map_pipeline's streams 1-14, prepare_draw's
// instance count, d9g_gpu_sdl.cpp's binding of each): the wave-1 synthetic case's shape (stream 0
// INDEXEDDATA | 2 with one indexed quad, stream 1 INSTANCEDATA | 1 with a per-instance TEXCOORD0, one
// DrawIndexedPrimitive, both reset to 1), the same streams per vertex after the reset, DXVK's rule
// that an instanced stream the pipeline does not read draws one instance, a divisor SDL cannot
// step by, an unbound or short stream, a UP draw reading another stream, and DXVK's argument checks.
// M7_VS_INSTANCE and M7_PS_COLOR of tools/s5/m7_synthetic.py (sm3_asm.py): position + texcoord0.xy,
// colour passed through.
const DWORD kVsInstance[] = {0xFFFE0300u, 0x0200001Fu, 0x80000000u, 0x900F0000u, 0x0200001Fu, 0x8000000Au, 0x900F0001u,
                             0x0200001Fu, 0x80000005u, 0x900F0002u, 0x0200001Fu, 0x80000000u, 0xE00F0000u, 0x0200001Fu,
                             0x8000000Au, 0xE00F0001u, 0x02000001u, 0x800F0000u, 0x90E40000u, 0x03000002u, 0x80030000u,
                             0x90540000u, 0x90540002u, 0x02000001u, 0xE00F0000u, 0x80E40000u, 0x02000001u, 0xE00F0001u,
                             0x90E40001u, 0x0000FFFFu};
const DWORD kPsColor[] = {0xFFFF0300u, 0x0200001Fu, 0x8000000Au, 0x900F0000u, 0x02000001u, 0x800F0800u, 0x90E40000u, 0x0000FFFFu};

void test_instanced_streams() {
    d9g::Device* d = make_device(0, kBehavior, true, true);
    d9g::Report& r = d9g::report();
    const std::string dir = std::string(SA2_TEST_TMP) + "/instancing-test";
    std::error_code ec;
    std::filesystem::create_directories(dir, ec);
    d9g::report().path = dir + "/d3d9gpu.json";
    IDirect3DVertexShader9* vs = nullptr;
    IDirect3DPixelShader9* ps = nullptr;
    IDirect3DVertexDeclaration9 *decl = nullptr, *decl0 = nullptr;
    IDirect3DVertexBuffer9 *vb = nullptr, *ivb = nullptr, *small = nullptr;
    IDirect3DIndexBuffer9* ib = nullptr;
    const D3DVERTEXELEMENT9 e[] = {{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0},
                                   {0, 12, D3DDECLTYPE_D3DCOLOR, 0, D3DDECLUSAGE_COLOR, 0},
                                   {1, 0, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0}, D3DDECL_END()};
    CHECK(d->CreateVertexShader(kVsInstance, &vs) == D3D_OK && d->CreatePixelShader(kPsColor, &ps) == D3D_OK);
    CHECK(d->CreateVertexDeclaration(e, &decl) == D3D_OK && d->CreateVertexDeclaration(e + 1, &decl0) == D3D_OK);
    CHECK(d->CreateVertexBuffer(64, D3DUSAGE_WRITEONLY, 0, D3DPOOL_MANAGED, &vb, nullptr) == D3D_OK);
    CHECK(d->CreateVertexBuffer(32, D3DUSAGE_WRITEONLY, 0, D3DPOOL_MANAGED, &ivb, nullptr) == D3D_OK);
    CHECK(d->CreateVertexBuffer(8, D3DUSAGE_WRITEONLY, 0, D3DPOOL_MANAGED, &small, nullptr) == D3D_OK);
    CHECK(d->CreateIndexBuffer(12, D3DUSAGE_WRITEONLY, D3DFMT_INDEX16, D3DPOOL_MANAGED, &ib, nullptr) == D3D_OK);
    void* p = nullptr;
    const uint16_t idx[6] = {0, 1, 2, 2, 1, 3};
    CHECK(ib->Lock(0, 0, &p, 0) == D3D_OK); std::memcpy(p, idx, sizeof idx); ib->Unlock();
    d->SetVertexShader(vs); d->SetPixelShader(ps); d->SetVertexDeclaration(decl);
    // DXVK's argument checks (:3789-3808)
    CHECK(d->SetStreamSourceFreq(0, D3DSTREAMSOURCE_INSTANCEDATA | 1u) == D3DERR_INVALIDCALL);
    CHECK(d->SetStreamSourceFreq(1, D3DSTREAMSOURCE_INSTANCEDATA | D3DSTREAMSOURCE_INDEXEDDATA | 1u) == D3DERR_INVALIDCALL);
    CHECK(d->SetStreamSourceFreq(1, 0) == D3DERR_INVALIDCALL && d->SetStreamSourceFreq(16, 1) == D3DERR_INVALIDCALL);
    // the synthetic case: two instances, stream 1 per instance at its stride
    CHECK(d->SetStreamSource(0, vb, 0, 16) == D3D_OK && d->SetStreamSourceFreq(0, D3DSTREAMSOURCE_INDEXEDDATA | 2u) == D3D_OK);
    CHECK(d->SetStreamSource(1, ivb, 0, 8) == D3D_OK && d->SetStreamSourceFreq(1, D3DSTREAMSOURCE_INSTANCEDATA | 1u) == D3D_OK);
    CHECK(d->SetIndices(ib) == D3D_OK);
    CHECK(d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 4, 0, 2) == D3D_OK);
    const auto* trace = d9g::cpu_gpu_draws(d->gpu());
    CHECK(trace && trace->size() == 1 && r.drawn == 1);
    if (!trace || trace->size() != 1) { d->Release(); return; }
    {
        const d9g::draw::Values& v = trace->at(0);
        const auto& g = std::get<d9g::draw::BufferIndexed>(v.geometry);
        CHECK(g.instances == 2 && g.count == 6);
        CHECK(v.pipeline.n_vbs == 2 && v.pipeline.vbs[0].slot == 0 && v.pipeline.vbs[0].pitch == 16 &&
              v.pipeline.vbs[0].input_rate == SDL_GPU_VERTEXINPUTRATE_VERTEX);
        CHECK(v.pipeline.vbs[1].slot == 1 && v.pipeline.vbs[1].pitch == 8 &&
              v.pipeline.vbs[1].input_rate == SDL_GPU_VERTEXINPUTRATE_INSTANCE && v.pipeline.vbs[1].instance_step_rate == 0);
        CHECK(v.pipeline.n_attrs == 3 && v.pipeline.attrs[2].buffer_slot == 1 && v.pipeline.attrs[2].location == 2 &&
              v.pipeline.attrs[2].format == SDL_GPU_VERTEXELEMENTFORMAT_FLOAT2 && !v.pipeline.null_stream);
        CHECK(v.streams.size() == 1 && v.streams[0].stream == 1 && v.streams[0].slice.offset == 0 &&
              v.streams[0].slice.available_bytes == 32 && !v.semantic_null);
        CHECK(v.pipeline.unmapped.empty());
    }
    // both reset to 1: stream 1 per vertex, one instance (the omit stream's reading)
    CHECK(d->SetStreamSourceFreq(0, 1) == D3D_OK && d->SetStreamSourceFreq(1, 1) == D3D_OK);
    CHECK(d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 4, 0, 2) == D3D_OK && trace->size() == 2);
    {
        const d9g::draw::Values& v = trace->at(1);
        CHECK(std::get<d9g::draw::BufferIndexed>(v.geometry).instances == 1);
        CHECK(v.pipeline.vbs[1].input_rate == SDL_GPU_VERTEXINPUTRATE_VERTEX && v.pipeline.vbs[1].pitch == 8);
        CHECK(v.streams.size() == 1 && v.streams[0].stream == 1);
    }
    // an instanced stream the pipeline does not read: one instance (DXVK's CS check :3114-3126)
    CHECK(d->SetStreamSourceFreq(0, D3DSTREAMSOURCE_INDEXEDDATA | 3u) == D3D_OK);
    CHECK(d->SetStreamSourceFreq(1, D3DSTREAMSOURCE_INSTANCEDATA | 1u) == D3D_OK);
    d->SetVertexDeclaration(decl0);                                 // COLOR0 and TEXCOORD0 on stream 1: no stream 0
    d->SetVertexDeclaration(decl);
    CHECK(d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 4, 0, 2) == D3D_OK && trace->size() == 3);
    CHECK(std::get<d9g::draw::BufferIndexed>(trace->at(2).geometry).instances == 3);
    CHECK(d->SetStreamSourceFreq(2, D3DSTREAMSOURCE_INSTANCEDATA | 1u) == D3D_OK && d->SetStreamSourceFreq(1, 1) == D3D_OK);
    CHECK(d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 4, 0, 2) == D3D_OK && trace->size() == 4);
    CHECK(std::get<d9g::draw::BufferIndexed>(trace->at(3).geometry).instances == 1);   // stream 2 instanced, unread
    // DrawPrimitive: one instance, stream 1 still per instance (every vertex reads its first element)
    CHECK(d->SetStreamSourceFreq(2, 1) == D3D_OK && d->SetStreamSourceFreq(1, D3DSTREAMSOURCE_INSTANCEDATA | 1u) == D3D_OK);
    CHECK(d->DrawPrimitive(D3DPT_TRIANGLELIST, 0, 1) == D3D_OK && trace->size() == 5);
    CHECK(std::holds_alternative<d9g::draw::NonIndexed>(trace->at(4).geometry) &&
          trace->at(4).pipeline.vbs[1].input_rate == SDL_GPU_VERTEXINPUTRATE_INSTANCE);
    auto has = [&](const std::string& reason) {
        return std::find(r.draw_failures.begin(), r.draw_failures.end(), reason) != r.draw_failures.end();
    };
    // a divisor SDL cannot step by: not mapped
    CHECK(d->SetStreamSourceFreq(1, D3DSTREAMSOURCE_INSTANCEDATA | 2u) == D3D_OK);
    CHECK(d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 4, 0, 2) == D3D_OK && trace->size() == 5);
    CHECK(has("an instanced stream with divisor 2 (SDL_gpu steps per instance by one)"));
    // a short stream: three instances of 8 bytes need 24 (ROBUST_VERTEX_BUFFER_OOB on stream 1)
    CHECK(d->SetStreamSourceFreq(1, D3DSTREAMSOURCE_INSTANCEDATA | 1u) == D3D_OK);
    CHECK(d->SetStreamSource(1, small, 0, 8) == D3D_OK);
    CHECK(d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 4, 0, 2) == D3D_OK && trace->size() == 5);
    CHECK(has("ROBUST_VERTEX_BUFFER_OOB stream 1"));
    // per vertex, the short stream holds one vertex of the four the indices fetch
    CHECK(d->SetStreamSourceFreq(1, 1) == D3D_OK && d->SetStreamSourceFreq(0, 1) == D3D_OK);
    CHECK(d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 4, 0, 2) == D3D_OK && trace->size() == 5);
    // an unbound stream 1, and a UP draw that would read it
    CHECK(d->SetStreamSource(1, nullptr, 0, 0) == D3D_OK);
    CHECK(d->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, 0, 0, 4, 0, 2) == D3D_OK && trace->size() == 5);
    CHECK(has("DECLARED_UNBOUND_STREAM 1"));
    float up[16] = {};
    CHECK(d->DrawPrimitiveUP(D3DPT_TRIANGLELIST, 1, up, 16) == D3D_OK && trace->size() == 5);
    CHECK(has("UP_DRAW_READS_STREAM 1"));
    CHECK(r.unmapped_draws == 5 && r.failure.empty());
    // the keys file names stream 1's stride and frequency on the inputs that read it
    d->Release();
    const std::string keys = read_text(dir + "/d3d9gpu.keys.json");
    CHECK(keys.find("\"freq0\": 1073741826, \"streams\": [[1, 8, 2147483649]]") != std::string::npos);
    CHECK(keys.find("\"freq0\": 1, \"streams\": [[1, 8, 1]]") != std::string::npos);
    CHECK(keys.find("\"streams\": [[1, 0, 1]]") != std::string::npos);   // unbound: stride 0
    vs->Release(); ps->Release(); decl->Release(); decl0->Release();
    vb->Release(); ivb->Release(); small->Release(); ib->Release();
}

// map_pipeline's streams (d9g_pipeline.cpp): every stream an attribute reads one binding, ascending,
// the null stream last; stream 15 (SDL's null-stream slot) and a fifth binding not mapped; and the
// key of a stream-0 description unchanged by any other stream's state.
void test_pipeline_streams() {
    d9g::PipelineInput in;
    d9g::DeviceParams dp{true, 64.0f, true, true};
    uint32_t rs[256] = {}, ss[14] = {};
    d9g::state_defaults(rs, ss, dp, true);
    for (size_t i = 0; i < d9g::kNumPipelineRS; i++) in.rs[i] = rs[d9g::kPipelineRenderStates[i]];
    in.prim = D3DPT_TRIANGLELIST; in.method = d9g::M_DIP; in.stride0 = 16; in.vs_id = in.ps_id = in.decl_id = 1;
    in.ps_rt_mask = 1;
    in.rt[0] = d9g::TargetDesc{D3DFMT_A8R8G8B8, 64, 64, 0, 0, 0, true, false};
    in.vs_n_inputs = 3;
    in.vs_inputs[0] = {D3DDECLUSAGE_POSITION, 0}; in.vs_inputs[1] = {D3DDECLUSAGE_TEXCOORD, 0}; in.vs_inputs[2] = {D3DDECLUSAGE_NORMAL, 0};
    in.n_elems = 2;
    in.elems[0] = d9g::VertexElem{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0};
    in.elems[1] = d9g::VertexElem{2, 4, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0};
    in.stream_stride[2] = 12; in.stream_freq[2] = 1;
    d9g::PipelineDesc dsc = d9g::map_pipeline(in, dp, 0);
    CHECK(dsc.unmapped.empty() && dsc.n_vbs == 3 && dsc.null_stream);
    CHECK(dsc.vbs[0].slot == 0 && dsc.vbs[1].slot == 2 && dsc.vbs[1].pitch == 12 &&
          dsc.vbs[1].input_rate == SDL_GPU_VERTEXINPUTRATE_VERTEX && dsc.vbs[2].slot == d9g::kNullStreamSlot);
    CHECK(dsc.attrs[1].buffer_slot == 2 && dsc.attrs[1].offset == 4 && dsc.attrs[2].buffer_slot == d9g::kNullStreamSlot);
    in.elems[1].stream = 15; in.stream_stride[15] = 12; in.stream_freq[15] = 1;
    dsc = d9g::map_pipeline(in, dp, 0);
    CHECK(dsc.unmapped.size() == 1 && dsc.unmapped[0] == "a vertex element on stream 15 (SDL's slot for the null stream)");
    // four streams and the null stream: a fifth binding
    in.vs_n_inputs = 5;
    in.vs_inputs[3] = {D3DDECLUSAGE_TEXCOORD, 1}; in.vs_inputs[4] = {D3DDECLUSAGE_TEXCOORD, 2};
    in.n_elems = 4;
    in.elems[1] = d9g::VertexElem{1, 0, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0};
    in.elems[2] = d9g::VertexElem{2, 0, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 1};
    in.elems[3] = d9g::VertexElem{3, 0, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 2};
    for (uint32_t s = 1; s <= 3; s++) { in.stream_stride[s] = 8; in.stream_freq[s] = 1; }
    dsc = d9g::map_pipeline(in, dp, 0);
    CHECK(dsc.n_vbs == 4 && dsc.unmapped.size() == 1 && dsc.unmapped[0] == "more than four vertex bindings");
    // a description of stream 0 alone keys as before, whatever the other streams hold
    d9g::PipelineInput a = in, b = in;
    a.vs_n_inputs = b.vs_n_inputs = 1; a.n_elems = b.n_elems = 1;
    b.stream_stride.fill(0); b.stream_freq.fill(0);
    const d9g::PipelineDesc da = d9g::map_pipeline(a, dp, 0), db = d9g::map_pipeline(b, dp, 0);
    CHECK(da.n_vbs == 1 && d9g::key_of(da, a, true).bytes == d9g::key_of(db, b, true).bytes);
}

int main(int argc, char** argv) {
    if (argc > 1 && std::strcmp(argv[1], "--clear-cases") == 0) return run_clear_cases();
    if (argc > 1 && std::strcmp(argv[1], "--clear-matrix") == 0) {
        d9g::Device* d = make_device();
        clear_matrix::print(clear_matrix::run(d), stdout);
        const ULONG left = d->Release();
        return left || g_fails ? 1 : 0;
    }
    if (argc > 2 && std::strcmp(argv[1], "--pipeline-session") == 0) {
        std::string faults;
        d9g::CapsOff caps;   // the capabilities named, parsed by the override's names
        for (int i = 3; i < argc; i++) {
            const std::string a = argv[i];
            if (a.rfind("--caps=", 0) == 0) {
                const std::string n = a.substr(7);
                caps = d9g::parse_caps_off(n == "none" ? "" : n.c_str(), "1");
                if (!caps.error.empty()) { std::fprintf(stderr, "%s\n", caps.error.c_str()); return 2; }
            } else {
                faults = a;
            }
        }
        d9g::Device* d = make_device(0, kBehavior, caps.border, caps.depth_clamp);
        d9g::report().path = argv[2];
        if (!faults.empty()) d9g::report().fault = d9g::parse_faults(faults.c_str());
        pipeline_session(d);
        d->Release();
        return g_fails ? 1 : 0;
    }
    if (argc > 2 && std::strcmp(argv[1], "--report") == 0) {
        d9g::Device* d = make_device();
        d9g::report().path = argv[2];
        const D3DVERTEXELEMENT9 e[] = {{0, 0, D3DDECLTYPE_FLOAT3, 0, D3DDECLUSAGE_POSITION, 0},
                                       {0, 12, D3DDECLTYPE_D3DCOLOR, 0, D3DDECLUSAGE_COLOR, 0}, D3DDECL_END()};
        IDirect3DVertexDeclaration9* decl = nullptr;
        d->CreateVertexDeclaration(e, &decl);
        d->SetVertexDeclaration(decl);
        float v[32] = {};
        d->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF000000u, 1.0f, 0);
        d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v, 16);
        d->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 0, v, 16);
        d->Present(nullptr, nullptr, nullptr, nullptr);
        d->DrawPrimitiveUP(D3DPT_TRIANGLEFAN, 1, v, 0);
        d->Present(nullptr, nullptr, nullptr, nullptr);
        d->GetRenderState(D3DRENDERSTATETYPE(3), nullptr);
        d->TestCooperativeLevel();                              // a generated stub: not implemented
        decl->Release();
        d->Release();
        return 0;
    }
    test_formats();
    test_declarations_and_fans();
    test_clear_rule();
    test_shaders();
    test_faults_paths_and_parameters();
    test_lifetimes();
    test_answers();
    test_scene_fault();
    test_clears();
    test_locks();
    test_decoded_dxt();
    test_draws_and_frames();
    test_report_shape();
    test_pipeline_tracking();
    test_hazard_tracking();
    test_cube_hazard_census();
    test_shader_translation();
    test_draw_requests();
    test_no_clip_distance();
    test_cube_and_volume_textures();
    test_instanced_streams();
    test_pipeline_streams();
    if (g_fails) {
        std::printf("TEST_D3D9GPU_FAIL %d of %d checks\n", g_fails, g_checks);
        return 1;
    }
    std::printf("TEST_D3D9GPU_OK %d checks\n", g_checks);
    return 0;
}
