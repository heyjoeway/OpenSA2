// d9g_gpu.h: the device's GPU backend. The device (d9g_device.cpp, d9g_resources.cpp)
// decides what D3D9 means; a backend only executes it. Two exist:
//   d9g_gpu_sdl.cpp  SDL_gpu (Vulkan), the one libsa2_d3d9gpu.a (and so sa2_player_gpu) is
//                    built with;
//   d9g_gpu_cpu.cpp  a CPU model of the same operations, linked into the unit test only
//                    (host/d3d9gpu/test), never into the archive or a player, so no run can
//                    choose it.
//
// Every texture and buffer a backend makes is zero (DXVK: new images are zero-cleared,
// dxvk_context.cpp initImage; buffers zero-filled, InitDeviceLocalBuffer). There is no
// shader object, pipeline or sampler here yet: R2a draws nothing (the border sampler and the depth
// clamp are ruling 3's, mapped by d9g_sampler and d9g_pipeline with the SDL patch's capabilities
// below). The shader route's device side is R1's: SDL_gpu's device is created with the Vulkan
// version and features the translated modules need, and the facts DXVK's shader options are
// computed from are read from it (GpuInfo's shader_facts); the device translates every shader at
// creation (d9g_device.cpp, d9g_shader.h). Shader objects and pipelines are R2-draws'.
#pragma once

#include "d9g_format.h"
#include "d9g_draw_state.h"
#include "d9g_shader_profile.h"

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>

struct SDL_Window;

namespace d9g {

// A texture's kind: SDL_GPU_TEXTURETYPE_2D, _CUBE (six layers, the D3DCUBEMAP_FACES order) or _3D.
enum class TexType : uint8_t { flat, cube, volume };

struct TexDesc {
    uint32_t w = 0, h = 0, levels = 1;
    GFmt fmt = GFmt::none;
    bool target = false;    // colour or depth-stencil target (by format)
    bool sampled = false;   // read by a shader (R3); still made sampleable now
    TexType type = TexType::flat;
    uint32_t depth = 1;     // a volume's depth at level 0 (1 otherwise)
};

// What one clear writes. The aspects not named keep their contents (LOAD).
struct ClearValue {
    bool colour = false, depth = false, stencil = false;
    float rgba[4] = {0, 0, 0, 0};
    float z = 0.0f;
    uint32_t s = 0;
};

struct GpuInfo {
    std::string driver, device_name, driver_name, driver_version, driver_info;
    std::string depth_format;          // the D24S8 format chosen (DXVK's D32S8 fallback)
    bool debug_mode = false;
    // The Vulkan physical device SDL_gpu runs on, as one vkGetPhysicalDeviceProperties query
    // reads it (d9g_gpu_sdl.cpp; R2b): what DXVK's ResetState reads (pointSizeRange[1], the
    // POINTSIZE_MAX default) and what the state check needs to hold DXVK's vendor rules to the
    // same device. vk_query says how it went; without it the device has no POINTSIZE_MAX
    // default, which the report says.
    bool vk_query_ok = false;
    std::string vk_query;
    uint32_t vk_vendor_id = 0, vk_device_id = 0;
    float vk_point_size_max = 0.0f;
    // The SDL3 that runs (d9g_gpu_sdl.cpp): its revision, patched (marked by the project's
    // patches/sdl3, "(sa2-recomp sdl3-patches <id>)") or not, and the patch's two device
    // capabilities, detected at run time. A stock or system SDL3 has neither property, so both
    // read false and the shader fallback is owed (border, depth clamp); a patched one sets both.
    std::string sdl_revision;
    bool sdl_patched = false;
    bool sdl_capabilities_present = false;
    // As SDL reports them (detected), and as the device uses them: the detected ones less any a
    // test run forces off (capability_override, SA2_D3D9GPU_CAPS_OFF, refused outside a test
    // run: d9g_entry.cpp). The device maps with the used ones (DeviceParams).
    bool sampler_clamp_to_border_detected = false, depth_clip_and_clamp_detected = false;
    bool sampler_clamp_to_border = false, depth_clip_and_clamp = false;
    std::string capability_override;      // the names forced off ("border", "depth-clamp"), "" none
    // R1: the Vulkan facts DXVK's shader options are computed from (d9g_shader_profile.h
    // determine_profile), read from the device SDL_gpu chose by a query of its own
    // (d9g_shader_vulkan.cpp query_device_facts); without them the device is not made, since its
    // translator could not compile as DXVK would on this device. vulkan_options: the API version and
    // features SDL_gpu's device was created with (VulkanFeatures::describe), "" for the CPU model.
    bool shader_facts_ok = false;
    std::string shader_facts_query;
    shader::DeviceFacts shader_facts;
    std::string vulkan_options;
    // Whether the device was created with the sub-dword features (shaderInt8, shaderInt16, 8- and
    // 16-bit buffer access: shader::VulkanFeatures), so shader_facts.sub_dword_access; without them
    // the translator reads the state buffer as 32-bit words. vulkan_lacking: what each physical
    // device lacked of the full request when SDL_gpu refused it ("" when it was accepted), the
    // reason the device was made without them.
    bool sub_dword_access = true;
    std::string vulkan_lacking;
    // Whether the device was created with shaderClipDistance (shader::VulkanFeatures), so
    // shader_facts.clip_distance; without it no vertex module writes ClipDistance
    // (ShaderProfile::no_clip_distance). clip_distance_forced_off: SA2_D3D9GPU_NO_CLIPDIST=1 (or on
    // Android debug.sa2.no_clipdist=1) asked for that on a device that may have it (d9g_gpu_sdl.cpp
    // make_sdl_gpu), for testing.
    bool clip_distance = true;
    bool clip_distance_forced_off = false;
    // The BC formats (DXT1, DXT3, DXT5's) a sampled 2D texture cannot be made of on this device
    // (SDL_GPUTextureSupportsFormat; Mali GPUs have none, the user's Pixel 9 Pro's Mali-G715
    // 2026-10-12), "" when it has all three; and bc_forced_off: SA2_D3D9GPU_NO_BC=1 (on Android also
    // the property debug.sa2.no_bc=1) refuses them on any device (d9g_gpu_sdl.cpp make_sdl_gpu), for
    // testing. The device makes such a texture BGRA8 and decodes its blocks on the CPU at each
    // upload (d9g_device.cpp make_gpu_image).
    std::string bc_unsupported;
    bool bc_forced_off = false;
};

// A test run's capability override (SA2_D3D9GPU_CAPS_OFF, d9g_entry.cpp): the patch's
// capabilities forced off, so the shader fallback's leg runs on a device that supports both. It
// is refused outside a test run (SA2_D3D9GPU_TEST_RUN=1); its report names it
// (sdl_gpu.capability_override), and so does the keys file, whose state check reads it as a test
// leg that never reads OK. tools/s5/compare_hosts.py strips every SA2_D3D9GPU_ variable from the
// players it runs, so no host comparison runs under one; it does not read the field itself.
// "sub-dword" makes the device without the sub-dword features (as on a GPU that lacks them), so the
// translator's 32-bit state words run on a device that has them.
struct CapsOff {
    bool border = false, depth_clamp = false, sub_dword = false;
    std::string names;                     // canonical: the names in the order border, depth-clamp, sub-dword,
                                           // comma-separated; "" none
    std::string error;                     // an unknown name, or the override outside a test run
};
CapsOff parse_caps_off(const char* names, const char* test_run);
// The detected capabilities less the override, into `g` (its used values and override name).
void apply_caps_off(GpuInfo& g, const CapsOff& off);

// The largest rectangle of a bw x bh image's aspect inside a w x h one, centred: Present's
// letterbox (bars above and below) or pillarbox (bars left and right) of the back buffer on a
// swapchain of another aspect; the whole w x h when the aspects agree to within kFitWholeSlack
// pixels on both axes. Play sizes the game's back buffer at the screen's aspect
// (runtime/sa2_widescreen.c, SA2_PLAY_RES or SA2_PLAY_ASPECT), rounded to whole pixels, so a
// near-match is that rounding and is stretched over the whole swapchain (at most 2 px, under 0.2 %
// at 1080 lines) rather than shown with a 1 px bar; a real mismatch (play's switch off, a 16:9
// back buffer on a 19.5:9 phone or in a 21:9 window) keeps its bars.
constexpr uint32_t kFitWholeSlack = 2;
struct FitRect { uint32_t x = 0, y = 0, w = 0, h = 0; };
inline FitRect fit_rect(uint32_t bw, uint32_t bh, uint32_t w, uint32_t h) {
    FitRect r{0, 0, w, h};
    if (!bw || !bh || !w || !h) return r;
    if (uint64_t(w) * bh > uint64_t(h) * bw)        // wider than the image
        r.w = uint32_t((uint64_t(h) * bw + bh / 2) / bh);
    else                                            // taller, or the same
        r.h = uint32_t((uint64_t(w) * bh + bw / 2) / bw);
    r.w = r.w < 1 ? 1 : r.w > w ? w : r.w;
    r.h = r.h < 1 ? 1 : r.h > h ? h : r.h;
    if (w - r.w <= kFitWholeSlack && h - r.h <= kFitWholeSlack) return FitRect{0, 0, w, h};
    r.x = (w - r.w) / 2;
    r.y = (h - r.h) / 2;
    return r;
}

struct PresentInfo {
    bool presented = false;            // the back buffer reached a swapchain texture
    bool no_swapchain_texture = false; // SDL gave none (hidden, occluded, minimised)
    uint32_t sw_w = 0, sw_h = 0;
};

struct GpuCounters {
    uint64_t command_buffers = 0, submits = 0, waits = 0, ring_cycles = 0, dedicated_transfers = 0;
    uint64_t render_passes = 0, copies = 0, uploads = 0, upload_bytes = 0, downloads = 0, blits = 0;
};

// One texture's bytes as made (every level, face and slice; BC as blocks, D32S8 as 8 bytes a texel):
// the device's count of the GPU textures it holds (Report::gpu_texture_bytes).
uint64_t texture_bytes(const TexDesc& d);

class Gpu {
public:
    virtual ~Gpu() = default;
    virtual draw::Outcome draw(const draw::DrawRequest& request) = 0;
    // 0 on failure (error() says why).
    virtual uint32_t create_texture(const TexDesc& d) = 0;
    virtual void release_texture(uint32_t tex) = 0;
    virtual uint32_t create_buffer(uint32_t bytes, bool index) = 0;
    virtual void release_buffer(uint32_t buf) = 0;
    // A whole level, packed rows (d9g_format.h level_pitch), `bytes` = level_bytes.
    bool upload_level(uint32_t tex, uint32_t level, const uint8_t* data, size_t bytes) {
        return upload_subresource(tex, 0, level, data, bytes);
    }
    // A whole level of layer `layer` (a cube's face; 0 for 2D and volume textures), packed rows; a
    // volume's level is its level's depth of slices, `bytes` = level_bytes times that depth.
    virtual bool upload_subresource(uint32_t tex, uint32_t layer, uint32_t level, const uint8_t* data, size_t bytes) = 0;
    virtual bool upload_buffer(uint32_t buf, uint32_t offset, const uint8_t* data, uint32_t bytes) = 0;
    // Clears a whole subresource: the aspects `v` names are cleared, the others loaded.
    bool clear(uint32_t tex, uint32_t level, const ClearValue& v) { return clear_layer(tex, 0, level, v); }
    // The same of layer `layer` (a render-target cube's face; 0 for a 2D texture).
    virtual bool clear_layer(uint32_t tex, uint32_t layer, uint32_t level, const ClearValue& v) = 0;
    // Copies a w x h rectangle between subresources of one format (both aspects of a
    // depth-stencil format).
    bool copy(uint32_t src, uint32_t src_level, uint32_t sx, uint32_t sy, uint32_t dst, uint32_t dst_level, uint32_t dx,
              uint32_t dy, uint32_t w, uint32_t h) {
        return copy_layers(src, 0, src_level, sx, sy, dst, 0, dst_level, dx, dy, w, h);
    }
    // The same between layers (a cube's faces; 0 for a 2D texture).
    virtual bool copy_layers(uint32_t src, uint32_t src_layer, uint32_t src_level, uint32_t sx, uint32_t sy, uint32_t dst,
                             uint32_t dst_layer, uint32_t dst_level, uint32_t dx, uint32_t dy, uint32_t w, uint32_t h) = 0;
    // A BGRA8 subresource into `out` (packed, w * 4 per row); everything recorded
    // before it has run when it returns.
    virtual bool download(uint32_t tex, uint32_t level, uint8_t* out, size_t bytes) = 0;
    // The back buffer to the window (or, with no window, only submitted).
    virtual bool present(uint32_t back_buffer, PresentInfo* out) = 0;
    // Everything recorded submitted, and waited for.
    virtual bool finish() = 0;
    virtual const GpuInfo& info() const = 0;
    virtual const GpuCounters& counters() const = 0;
    virtual const std::string& error() const = 0;
    virtual GFmt depth_format() const = 0;   // what D24S8 is made as
    // Whether a sampled texture of `f` (not a target) can be made with type `t`: false for a BC
    // format the device cannot sample (or one the forced switch refuses: GpuInfo::bc_forced_off).
    virtual bool samples(GFmt f, TexType t) const = 0;
    // Present to `window` from now on (SDL_ClaimWindowForGPUDevice), with VSYNC when
    // `vsync`, else the first of IMMEDIATE, MAILBOX, VSYNC the window supports; *mode
    // names the one chosen. The CPU model has no window.
    virtual bool attach_window(SDL_Window* window, bool vsync, std::string* mode) = 0;
};

// d9g_gpu_sdl.cpp: SDL_CreateGPUDeviceWithProperties(SPIR-V, debug, "vulkan", SDL_GPUVulkanOptions:
// Vulkan 1.3 and shader::VulkanFeatures); nullptr with *err set. `off`: the test run's capability
// override, applied after the patch's detection. SA2_D3D9GPU_NO_CLIPDIST=1 (on Android also the
// property debug.sa2.no_clipdist=1) makes the device without shaderClipDistance on any device (the
// fallback a Mali GPU takes, forced for testing); SA2_D3D9GPU_NO_BC=1 (on Android also the property
// debug.sa2.no_bc=1) samples no BC format (GpuInfo::bc_forced_off), the CPU decode a Mali GPU takes.
std::unique_ptr<Gpu> make_sdl_gpu(bool debug, const CapsOff& off, std::string* err);
// d9g_gpu_cpu.cpp (tests only): every texture a byte array per level. The CPU model reports the
// patch's two capabilities as given (a test parameter, as its device name is a test value),
// shaderClipDistance as given (false: a Mali GPU's profile, ShaderProfile::no_clip_distance), and
// the BC formats as sampled or not (false: a Mali GPU's, GpuInfo::bc_unsupported).
std::unique_ptr<Gpu> make_cpu_gpu(bool clamp_to_border = false, bool clip_and_clamp = false, bool clip_distance = true,
                                  bool bc = true);
// The CPU model's bytes of a subresource (tests read depth-stencil through it too):
// BGRA8 as BGRA8; D24S8 as one uint32 per pixel, depth in bits 0-23 (unorm24) and
// stencil in bits 24-31; a volume's level slice after slice. `layer`: a cube's face.
bool cpu_gpu_bytes(Gpu* g, uint32_t tex, uint32_t level, std::string* out, uint32_t layer = 0);
// The CPU model's description of a texture (its type, depth and layers as created), false for none.
bool cpu_gpu_desc(Gpu* g, uint32_t tex, TexDesc* out);
// Test-only completed request values, never a rasterization result.
const std::vector<draw::Values>* cpu_gpu_draws(Gpu* g);

}  // namespace d9g
