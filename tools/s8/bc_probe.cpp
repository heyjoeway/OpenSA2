// bc_probe.cpp: what this machine's GPU samples BC1, BC2 and BC3 blocks as, against host/player/texcodec's
// two CPU decodes: decode_level_sampled (host/d3d9gpu's decode on a GPU without BC formats, a Mali's:
// d9g_device.cpp make_gpu_image) and decode_level (D3DX's arithmetic). texcodec.cpp's
// decode_block_sampled cites it.
//
// Subtitle: one command that says whether the renderer's CPU decode of DXT textures equals this GPU's.
// For each format a 1024x1024 texture of 65,536 blocks: BC3's alpha halves are every (a0, a1) pair,
// each with the codes 0..7 twice; the colour halves (and BC1's, BC2's blocks) are 4,096 blocks whose
// rows hold the indices 0..3 for every 6-bit (c0, c1) pair (red and blue its low five bits), then
// random blocks (a fixed seed). The texture is blitted with NEAREST filtering to an RGBA32F target of
// its size, read back, and every texel's channels are compared as 8-bit values; a value off the 8-bit
// grid is reported too (the decode would then be wider than BGRA8 holds).
//
//   source tools/env.sh; I=$PWD/private/s5-tools/install; OUT=private/generated/s8/bc-probe; mkdir -p $OUT
//   g++ -std=c++17 -O2 -Ihost/player -I$I/include -o $OUT/bc_probe tools/s8/bc_probe.cpp host/player/texcodec.cpp
//       -L$I/lib -Wl,--disable-new-dtags -Wl,-rpath,$I/lib -lSDL3 -lpng        (one command line)
//   SDL_AUDIO_DRIVER=dummy VK_LOADER_LAYERS_DISABLE='~implicit~' $OUT/bc_probe
//
// Prints one line per format and BC_PROBE_EXACT when decode_level_sampled equals the GPU everywhere,
// BC_PROBE_DIFFERS otherwise (exit 1), BC_PROBE_NO_BC where the GPU samples no BC format (exit 3).
// On this project's RTX 5090 (NVIDIA driver 595.91, 2026-10-12): exact, 3 x 1,048,576 texels.
#include "texcodec.h"

#include <SDL3/SDL.h>

#include <cmath>
#include <cstdio>
#include <cstring>
#include <random>
#include <string>
#include <vector>

namespace {

constexpr uint32_t kW = 1024, kH = 1024, kBX = kW / 4, kBY = kH / 4;

struct Format { const char* name; SDL_GPUTextureFormat sdl; uint32_t d3d, block; };

std::vector<uint8_t> blocks_for(const Format& f) {
    std::vector<uint8_t> b(size_t(kBX) * kBY * f.block);
    std::mt19937_64 rng(20261012);
    for (auto& v : b) v = uint8_t(rng());
    const uint32_t colour = f.block == 8 ? 0 : 8;
    for (uint32_t n = 0; n < 4096; n++) {
        const uint32_t x = n / 64, y = n % 64;
        const uint16_t c0 = uint16_t((x & 31) << 11 | x << 5 | (x & 31)), c1 = uint16_t((y & 31) << 11 | y << 5 | (y & 31));
        uint8_t* c = &b[size_t(n) * f.block + colour];
        c[0] = uint8_t(c0); c[1] = uint8_t(c0 >> 8); c[2] = uint8_t(c1); c[3] = uint8_t(c1 >> 8);
        c[4] = c[5] = c[6] = c[7] = 0xE4;   // each row: indices 0, 1, 2, 3
    }
    if (f.d3d == tex::FMT_DXT5) {
        uint64_t bits = 0;
        for (int t = 0; t < 16; t++) bits |= uint64_t(t & 7) << (3 * t);
        for (uint32_t n = 0; n < 65536; n++) {
            uint8_t* a = &b[size_t(n) * 16];
            a[0] = uint8_t(n >> 8);
            a[1] = uint8_t(n);
            for (int k = 0; k < 6; k++) a[2 + k] = uint8_t(bits >> (8 * k));
        }
    }
    return b;
}

// The GPU's RGBA32F texels of the blocks, row-major.
bool sample(SDL_GPUDevice* dev, const Format& f, const std::vector<uint8_t>& blocks, std::vector<float>* out) {
    SDL_GPUTextureCreateInfo ci{};
    ci.type = SDL_GPU_TEXTURETYPE_2D;
    ci.format = f.sdl;
    ci.usage = SDL_GPU_TEXTUREUSAGE_SAMPLER;
    ci.width = kW; ci.height = kH; ci.layer_count_or_depth = 1; ci.num_levels = 1;
    SDL_GPUTexture* src = SDL_CreateGPUTexture(dev, &ci);
    ci.format = SDL_GPU_TEXTUREFORMAT_R32G32B32A32_FLOAT;
    ci.usage = SDL_GPU_TEXTUREUSAGE_COLOR_TARGET | SDL_GPU_TEXTUREUSAGE_SAMPLER;
    SDL_GPUTexture* dst = SDL_CreateGPUTexture(dev, &ci);
    SDL_GPUTransferBufferCreateInfo ui{SDL_GPU_TRANSFERBUFFERUSAGE_UPLOAD, uint32_t(blocks.size()), 0};
    SDL_GPUTransferBufferCreateInfo di{SDL_GPU_TRANSFERBUFFERUSAGE_DOWNLOAD, kW * kH * 16, 0};
    SDL_GPUTransferBuffer* up = SDL_CreateGPUTransferBuffer(dev, &ui);
    SDL_GPUTransferBuffer* down = SDL_CreateGPUTransferBuffer(dev, &di);
    if (!src || !dst || !up || !down) { std::fprintf(stderr, "bc_probe: %s\n", SDL_GetError()); return false; }
    std::memcpy(SDL_MapGPUTransferBuffer(dev, up, false), blocks.data(), blocks.size());
    SDL_UnmapGPUTransferBuffer(dev, up);
    SDL_GPUCommandBuffer* cb = SDL_AcquireGPUCommandBuffer(dev);
    SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
    SDL_GPUTextureTransferInfo from{up, 0, 0, 0};
    SDL_GPUTextureRegion to{src, 0, 0, 0, 0, 0, kW, kH, 1};
    SDL_UploadToGPUTexture(cp, &from, &to, false);
    SDL_EndGPUCopyPass(cp);
    SDL_GPUBlitInfo bi{};
    bi.source.texture = src; bi.source.w = kW; bi.source.h = kH;
    bi.destination.texture = dst; bi.destination.w = kW; bi.destination.h = kH;
    bi.load_op = SDL_GPU_LOADOP_DONT_CARE;
    bi.filter = SDL_GPU_FILTER_NEAREST;
    SDL_BlitGPUTexture(cb, &bi);
    cp = SDL_BeginGPUCopyPass(cb);
    SDL_GPUTextureRegion back{dst, 0, 0, 0, 0, 0, kW, kH, 1};
    SDL_GPUTextureTransferInfo into{down, 0, 0, 0};
    SDL_DownloadFromGPUTexture(cp, &back, &into);
    SDL_EndGPUCopyPass(cp);
    SDL_GPUFence* fence = SDL_SubmitGPUCommandBufferAndAcquireFence(cb);
    if (!fence || !SDL_WaitForGPUFences(dev, true, &fence, 1)) { std::fprintf(stderr, "bc_probe: %s\n", SDL_GetError()); return false; }
    SDL_ReleaseGPUFence(dev, fence);
    out->resize(size_t(kW) * kH * 4);
    std::memcpy(out->data(), SDL_MapGPUTransferBuffer(dev, down, false), out->size() * sizeof(float));
    SDL_UnmapGPUTransferBuffer(dev, down);
    SDL_ReleaseGPUTransferBuffer(dev, up);
    SDL_ReleaseGPUTransferBuffer(dev, down);
    SDL_ReleaseGPUTexture(dev, src);
    SDL_ReleaseGPUTexture(dev, dst);
    return true;
}

struct Diff { uint64_t texels = 0, channels = 0, max_delta = 0; };

// The GPU's texels against a BGRA8 decode.
Diff compare(const std::vector<float>& gpu, const std::vector<uint8_t>& bgra) {
    Diff d;
    for (size_t i = 0; i < size_t(kW) * kH; i++) {
        const uint8_t want[4] = {bgra[i * 4 + 2], bgra[i * 4 + 1], bgra[i * 4 + 0], bgra[i * 4 + 3]};   // RGBA
        bool any = false;
        for (int c = 0; c < 4; c++) {
            const long got = std::lround(double(gpu[i * 4 + c]) * 255.0);
            const uint64_t delta = uint64_t(std::labs(got - long(want[c])));
            if (delta) { d.channels++; any = true; if (delta > d.max_delta) d.max_delta = delta; }
        }
        d.texels += any;
    }
    return d;
}

}  // namespace

int main() {
    if (!SDL_Init(SDL_INIT_VIDEO)) { std::fprintf(stderr, "bc_probe: SDL_Init: %s\n", SDL_GetError()); return 2; }
    SDL_GPUDevice* dev = SDL_CreateGPUDevice(SDL_GPU_SHADERFORMAT_SPIRV, false, "vulkan");
    if (!dev) { std::fprintf(stderr, "bc_probe: no Vulkan device: %s\n", SDL_GetError()); return 2; }
    const SDL_PropertiesID p = SDL_GetGPUDeviceProperties(dev);
    std::printf("device: %s, driver %s %s\n", SDL_GetStringProperty(p, SDL_PROP_GPU_DEVICE_NAME_STRING, "?"),
                SDL_GetStringProperty(p, SDL_PROP_GPU_DEVICE_DRIVER_NAME_STRING, "?"),
                SDL_GetStringProperty(p, SDL_PROP_GPU_DEVICE_DRIVER_VERSION_STRING, "?"));
    const Format formats[] = {{"BC1 (DXT1)", SDL_GPU_TEXTUREFORMAT_BC1_RGBA_UNORM, tex::FMT_DXT1, 8},
                              {"BC2 (DXT3)", SDL_GPU_TEXTUREFORMAT_BC2_RGBA_UNORM, tex::FMT_DXT3, 16},
                              {"BC3 (DXT5)", SDL_GPU_TEXTUREFORMAT_BC3_RGBA_UNORM, tex::FMT_DXT5, 16}};
    bool exact = true;
    for (const Format& f : formats) {
        if (!SDL_GPUTextureSupportsFormat(dev, f.sdl, SDL_GPU_TEXTURETYPE_2D, SDL_GPU_TEXTUREUSAGE_SAMPLER)) {
            std::printf("BC_PROBE_NO_BC: the GPU samples no %s\n", f.name);
            return 3;
        }
        const std::vector<uint8_t> blocks = blocks_for(f);
        std::vector<float> gpu;
        if (!sample(dev, f, blocks, &gpu)) return 2;
        double off_grid = 0;
        for (float v : gpu) off_grid = std::fmax(off_grid, std::fabs(double(v) * 255.0 - std::round(double(v) * 255.0)));
        std::vector<uint8_t> sampled, d3dx;
        tex::decode_level_sampled(blocks.data(), kW, kH, f.d3d, 0, &sampled);
        tex::decode_level(blocks.data(), kW, kH, f.d3d, 0, &d3dx);
        const Diff a = compare(gpu, sampled), b = compare(gpu, d3dx);
        std::printf("%s: %u texels; decode_level_sampled differs in %llu texels (%llu channels, max %llu); decode_level "
                    "in %llu (%llu channels, max %llu); largest distance from the 8-bit grid %.3g\n",
                    f.name, kW * kH, (unsigned long long)a.texels, (unsigned long long)a.channels,
                    (unsigned long long)a.max_delta, (unsigned long long)b.texels, (unsigned long long)b.channels,
                    (unsigned long long)b.max_delta, off_grid);
        exact &= a.texels == 0 && off_grid < 1e-3;
    }
    SDL_DestroyGPUDevice(dev);
    SDL_Quit();
    std::printf("%s\n", exact ? "BC_PROBE_EXACT" : "BC_PROBE_DIFFERS");
    return exact ? 0 : 1;
}
