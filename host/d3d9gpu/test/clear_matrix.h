// clear_matrix.h: the clear matrix, a fixed sequence of Clears, render-target switches
// and readbacks run on any IDirect3DDevice9 made with a 1280x720 A8R8G8B8 back buffer
// and an automatic D24S8: DXVK's (d3d9gpu_clear_matrix with libdxvk_d3d9.so.0), this
// device's on SDL_gpu (sa2_player_gpu --clear-matrix, libsa2_d3d9gpu.a linked in) and this
// device's on the CPU model (test_d3d9gpu --clear-matrix). Each case prints its name, the
// HRESULTs of its calls and an FNV-1a 64 of the target read back (BGRA, row by row): the
// three must agree line for line (host/d3d9gpu/test_d3d9gpu.sh).
//
// It covers what boot cannot (the critic's A6/A7): non-grey colours (channel order),
// viewport sub-rectangles, scissor on, off and intersected, a first rectangle covering
// the region, a rectangle list with one outside, the 2560x1080 viewport, a depth-only
// clear, Count with NULL rectangles and Count 0 with rectangles, Z with no depth-stencil
// bound, a viewport past the image, an RT switch to a 512x512 texture level (viewport
// reset) and Metal Harbor's 64x48 and 256x192 partial clears of it with its own
// depth-stencil, read back with GetRenderTargetData, and the HRESULTs of the scene
// machine, SetRenderTarget and GetRenderTargetData.
#pragma once

#include <windows.h>
#include <d3d9.h>

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace clear_matrix {

struct Line {
    std::string name;
    std::vector<uint32_t> hr;
    uint64_t hash = 0;
};

inline uint64_t hash_surface(IDirect3DSurface9* s, uint32_t w, uint32_t h, uint32_t* hr) {
    D3DLOCKED_RECT lr{};
    *hr = uint32_t(s->LockRect(&lr, nullptr, D3DLOCK_READONLY));
    if (FAILED(HRESULT(*hr))) return 0;
    uint64_t v = 0xcbf29ce484222325ull;
    for (uint32_t y = 0; y < h; y++) {
        const uint8_t* row = static_cast<const uint8_t*>(lr.pBits) + size_t(y) * size_t(lr.Pitch);
        for (uint32_t x = 0; x < 4 * w; x++) { v ^= row[x]; v *= 0x100000001b3ull; }
    }
    s->UnlockRect();
    return v;
}

inline std::vector<Line> run(IDirect3DDevice9* dev) {
    std::vector<Line> out;
    IDirect3DSurface9 *bb = nullptr, *ds = nullptr, *sys = nullptr, *sys512 = nullptr;
    dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb);
    dev->GetDepthStencilSurface(&ds);
    dev->CreateOffscreenPlainSurface(1280, 720, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &sys, nullptr);
    dev->CreateOffscreenPlainSurface(512, 512, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &sys512, nullptr);
    const D3DVIEWPORT9 full{0, 0, 1280, 720, 0.0f, 1.0f};
    auto reset = [&]() {
        dev->SetRenderTarget(0, bb);
        dev->SetDepthStencilSurface(ds);
        dev->SetRenderState(D3DRS_SCISSORTESTENABLE, FALSE);
        dev->SetViewport(&full);
        dev->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL, 0xFF101010u, 1.0f, 0);
    };
    auto read_bb = [&](Line& l) {
        l.hr.push_back(uint32_t(dev->GetRenderTargetData(bb, sys)));
        uint32_t hr = 0;
        l.hash = hash_surface(sys, 1280, 720, &hr);
        l.hr.push_back(hr);
    };
    auto rect = [](LONG x1, LONG y1, LONG x2, LONG y2) { return D3DRECT{x1, y1, x2, y2}; };
    auto vp = [&](DWORD x, DWORD y, DWORD w, DWORD h) {
        const D3DVIEWPORT9 v{x, y, w, h, 0.0f, 1.0f};
        return uint32_t(dev->SetViewport(&v));
    };
    auto sc = [&](LONG l, LONG t, LONG r, LONG b) {
        const RECT s{l, t, r, b};
        return uint32_t(dev->SetScissorRect(&s));
    };
    auto add = [&](const char* name, std::vector<uint32_t> hrs, bool read) {
        Line l;
        l.name = name;
        l.hr = std::move(hrs);
        if (read) read_bb(l);
        out.push_back(l);
    };

    reset();
    add("full", {uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF336699u, 1.0f, 0))}, true);
    reset();
    add("viewport_sub", {vp(100, 50, 300, 200), uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF00FF00u, 1.0f, 0))}, true);
    reset();
    add("scissor_on", {uint32_t(dev->SetRenderState(D3DRS_SCISSORTESTENABLE, TRUE)), sc(200, 100, 700, 400),
                       uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFFFF0000u, 1.0f, 0))}, true);
    reset();
    add("scissor_and_viewport", {vp(100, 50, 600, 400), uint32_t(dev->SetRenderState(D3DRS_SCISSORTESTENABLE, TRUE)),
                                 sc(300, 200, 900, 700),
                                 uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF0000FFu, 1.0f, 0))}, true);
    reset();
    add("scissor_rect_test_off", {sc(10, 10, 20, 20), uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF123456u, 1.0f, 0))},
        true);
    reset();
    {
        const D3DRECT r = rect(-10, -10, 2000, 2000);
        add("first_rect_covers", {uint32_t(dev->Clear(1, &r, D3DCLEAR_TARGET, 0xFF654321u, 1.0f, 0))}, true);
    }
    reset();
    {
        const D3DRECT r[3] = {rect(10, 10, 110, 60), rect(2000, 2000, 2100, 2100), rect(500, 300, 600, 320)};
        add("rect_list", {uint32_t(dev->Clear(3, r, D3DCLEAR_TARGET, 0xFF00C0FFu, 1.0f, 0))}, true);
    }
    reset();
    {
        const D3DRECT r = rect(600, 300, 700, 400);
        add("rect_cut_by_scissor", {uint32_t(dev->SetRenderState(D3DRS_SCISSORTESTENABLE, TRUE)), sc(0, 0, 640, 360),
                                    uint32_t(dev->Clear(1, &r, D3DCLEAR_TARGET, 0xFFFFC000u, 1.0f, 0))}, true);
    }
    reset();
    add("viewport_2560x1080", {vp(0, 0, 2560, 1080), uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF8040C0u, 1.0f, 0))},
        true);
    reset();
    add("depth_only", {uint32_t(dev->Clear(0, nullptr, D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL, 0xFFFFFFFFu, 0.25f, 3))}, true);
    reset();
    add("count_with_null_rects", {uint32_t(dev->Clear(1, nullptr, D3DCLEAR_TARGET, 0xFFAA5500u, 1.0f, 0))}, true);
    reset();
    {
        const D3DRECT r = rect(0, 0, 100, 100);
        add("zero_count_with_rects", {uint32_t(dev->Clear(0, &r, D3DCLEAR_TARGET, 0xFF00AA55u, 1.0f, 0))}, true);
    }
    reset();
    add("z_without_depth_stencil", {uint32_t(dev->SetDepthStencilSurface(nullptr)),
                                    uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, 0xFF5500AAu, 1.0f, 0))},
        true);
    reset();
    add("viewport_past_image", {vp(1280, 0, 100, 100), uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF0F0F0Fu, 1.0f, 0))},
        true);
    reset();
    add("viewport_empty", {vp(10, 10, 0, 100), uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF0F0F0Fu, 1.0f, 0))}, true);

    // A 512x512 render-target texture and its own depth-stencil, as Metal Harbor's
    // shadow passes use them.
    IDirect3DTexture9* rtt = nullptr;
    IDirect3DSurface9 *lvl = nullptr, *rtds = nullptr;
    const uint32_t hr_tex = uint32_t(dev->CreateTexture(512, 512, 1, D3DUSAGE_RENDERTARGET, D3DFMT_A8R8G8B8,
                                                        D3DPOOL_DEFAULT, &rtt, nullptr));
    const uint32_t hr_lvl = rtt ? uint32_t(rtt->GetSurfaceLevel(0, &lvl)) : 0xFFFFFFFFu;
    const uint32_t hr_ds = uint32_t(dev->CreateDepthStencilSurface(512, 512, D3DFMT_D24S8, D3DMULTISAMPLE_NONE, 0,
                                                                   FALSE, &rtds, nullptr));
    auto read_lvl = [&](Line& l) {
        l.hr.push_back(uint32_t(dev->GetRenderTargetData(lvl, sys512)));
        uint32_t hr = 0;
        l.hash = hash_surface(sys512, 512, 512, &hr);
        l.hr.push_back(hr);
    };
    if (lvl && rtds) {
        reset();
        Line l;
        l.name = "rt_texture_full";
        l.hr = {hr_tex, hr_lvl, hr_ds, uint32_t(dev->SetRenderTarget(0, lvl)), uint32_t(dev->SetDepthStencilSurface(rtds))};
        D3DVIEWPORT9 got{};
        dev->GetViewport(&got);
        l.hr.push_back(got.Width == 512 && got.Height == 512 ? 0u : 1u);   // the viewport reset to the target
        l.hr.push_back(uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, 0xFF804020u, 1.0f, 0)));
        read_lvl(l);
        out.push_back(l);
        l = Line{};
        l.name = "rt_texture_partial_64x48";
        l.hr = {vp(0, 0, 64, 48),
                uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER | D3DCLEAR_STENCIL, 0xFF00FFFFu, 1.0f, 0))};
        read_lvl(l);
        out.push_back(l);
        l = Line{};
        l.name = "rt_texture_partial_256x192_scissor";
        l.hr = {vp(0, 0, 256, 192), uint32_t(dev->SetRenderState(D3DRS_SCISSORTESTENABLE, TRUE)), sc(0, 0, 512, 512),
                uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, 0xFFFF00FFu, 1.0f, 0))};
        read_lvl(l);
        out.push_back(l);
        l = Line{};
        l.name = "rt_texture_partial_offset";
        l.hr = {vp(100, 200, 64, 48), uint32_t(dev->SetRenderState(D3DRS_SCISSORTESTENABLE, FALSE)),
                uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF2040A0u, 1.0f, 0))};
        read_lvl(l);
        out.push_back(l);
        // Back to the back buffer: its viewport again, and a partial clear there.
        l = Line{};
        l.name = "back_to_back_buffer";
        l.hr = {uint32_t(dev->SetRenderTarget(0, bb)), uint32_t(dev->SetDepthStencilSurface(ds)), vp(10, 20, 30, 40),
                uint32_t(dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFFC0C000u, 1.0f, 0))};
        read_bb(l);
        out.push_back(l);
        // A size mismatch and a NULL are refused; the same surface answers D3D_OK.
        add("get_render_target_data_answers",
            {uint32_t(dev->GetRenderTargetData(bb, sys512)), uint32_t(dev->GetRenderTargetData(nullptr, sys)),
             uint32_t(dev->GetRenderTargetData(bb, bb))}, false);
    } else {
        add("rt_texture_missing", {hr_tex, hr_lvl, hr_ds}, false);
    }
    add("set_render_target_answers",
        {uint32_t(dev->SetRenderTarget(0, nullptr)), uint32_t(dev->SetRenderTarget(4, bb)),
         uint32_t(dev->SetRenderTarget(1, nullptr)), uint32_t(dev->SetRenderTarget(0, bb))}, false);
    {
        const uint32_t a = uint32_t(dev->BeginScene()), b = uint32_t(dev->BeginScene()), c = uint32_t(dev->EndScene()),
                       d = uint32_t(dev->EndScene());
        add("scene_answers", {a, b, c, d}, false);
    }
    if (lvl) lvl->Release();
    if (rtt) rtt->Release();
    if (rtds) rtds->Release();
    if (sys512) sys512->Release();
    if (sys) sys->Release();
    if (ds) ds->Release();
    if (bb) bb->Release();
    return out;
}

inline void print(const std::vector<Line>& lines, FILE* f) {
    for (const Line& l : lines) {
        std::fprintf(f, "%s\t", l.name.c_str());
        for (size_t i = 0; i < l.hr.size(); i++) std::fprintf(f, "%s%08x", i ? "," : "", l.hr[i]);
        std::fprintf(f, "\t%016llx\n", (unsigned long long)l.hash);
    }
}

}  // namespace clear_matrix
