// d9g_state.h: the Direct3D 9 state a draw's pipeline and samplers are a function of
// (R2b-prep, private/generated/s5/r2b/PLAN.md section 3), as plain numbers so that both the
// device and the state check's DXVK translation unit (test/ref_dxvk.cpp, which includes DXVK
// Native's own d3d9 headers) can include it.
//
// Rules mirrored from DXVK 3.1.1 (zlib licence; Copyright Philip Rebohle, Joshua Ashton et al.;
// private/s5-tools/src/dxvk-3.1.1/LICENSE), cited per rule.
#pragma once

#include <array>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace d9g {

// Every render state the pipeline mapping (d9g_pipeline) and the shader-side values
// (LegacyData) read, in key order. test/state_check.cpp holds DXVK's read set to this list
// plus kDynamicRenderStates, and every D3DRS token of DXVK's SetRenderState switch to a class
// (the critic's M5).
inline constexpr uint16_t kPipelineRenderStates[] = {
    7,    // ZENABLE
    8,    // FILLMODE
    9,    // SHADEMODE
    14,   // ZWRITEENABLE
    15,   // ALPHATESTENABLE
    19,   // SRCBLEND
    20,   // DESTBLEND
    22,   // CULLMODE
    23,   // ZFUNC
    24,   // ALPHAREF
    25,   // ALPHAFUNC
    27,   // ALPHABLENDENABLE
    52,   // STENCILENABLE
    53,   // STENCILFAIL
    54,   // STENCILZFAIL
    55,   // STENCILPASS
    56,   // STENCILFUNC
    58,   // STENCILMASK
    59,   // STENCILWRITEMASK
    154,  // POINTSIZE (and DXVK's A2M1/A2M0/RESZ/INST/CENT values)
    155,  // POINTSIZE_MIN
    156,  // POINTSPRITEENABLE
    157,  // POINTSCALEENABLE
    161,  // MULTISAMPLEANTIALIAS
    162,  // MULTISAMPLEMASK
    166,  // POINTSIZE_MAX
    168,  // COLORWRITEENABLE
    171,  // BLENDOP
    175,  // SLOPESCALEDEPTHBIAS
    180,  // ADAPTIVETESS_X (DXVK's NVDB depth-bounds value)
    181,  // ADAPTIVETESS_Y (DXVK's ATOC/COPM/SSAA values)
    182,  // ADAPTIVETESS_Z (NVDB minimum)
    183,  // ADAPTIVETESS_W (NVDB maximum)
    185,  // TWOSIDEDSTENCILMODE
    186,  // CCW_STENCILFAIL
    187,  // CCW_STENCILZFAIL
    188,  // CCW_STENCILPASS
    189,  // CCW_STENCILFUNC
    190,  // COLORWRITEENABLE1
    191,  // COLORWRITEENABLE2
    192,  // COLORWRITEENABLE3
    194,  // SRGBWRITEENABLE
    195,  // DEPTHBIAS
    206,  // SEPARATEALPHABLENDENABLE
    207,  // SRCBLENDALPHA
    208,  // DESTBLENDALPHA
    209,  // BLENDOPALPHA
};
inline constexpr size_t kNumPipelineRS = sizeof(kPipelineRenderStates) / sizeof(kPipelineRenderStates[0]);

// Read by DXVK at draw time but dynamic state in both hosts (blend constants, stencil
// reference, the scissor), so not in the pipeline key.
inline constexpr uint16_t kDynamicRenderStates[] = {193 /* BLENDFACTOR */, 57 /* STENCILREF */,
                                                     174 /* SCISSORTESTENABLE */};

// The render states the stream census (private/generated/s5/next/D/scan/scan.cpp, pk_states)
// keys its draw tuples on beyond kPipelineRenderStates (the scissor enable among them):
// recorded in the keys file only so its tuples project onto the census
// (test/scan_projection.py, which reads scan.cpp's list and refuses one this misses). They
// change no pipeline (key sufficiency, test/state_check.cpp).
inline constexpr uint16_t kCensusExtraRenderStates[] = {16, 26, 28, 29, 35, 48, 136, 137, 140, 152, 174};

// Index of a render state in kPipelineRenderStates, or -1: a table, since the mapping reads
// render states by number at every draw.
namespace detail {
constexpr std::array<int16_t, 256> pipeline_rs_table() {
    std::array<int16_t, 256> t{};
    for (auto& v : t) v = -1;
    for (size_t i = 0; i < kNumPipelineRS; i++)
        if (t[kPipelineRenderStates[i]] < 0) t[kPipelineRenderStates[i]] = int16_t(i);
    return t;
}
inline constexpr std::array<int16_t, 256> kPipelineRsIndex = pipeline_rs_table();
}  // namespace detail
constexpr int pipeline_rs_index(uint32_t state) {
    return state < detail::kPipelineRsIndex.size() ? detail::kPipelineRsIndex[state] : -1;
}

struct VertexElem {
    uint16_t stream = 0, offset = 0;
    uint8_t type = 0, method = 0, usage = 0, usage_index = 0;
};

// A render target or depth-stencil surface as the draw sees it.
struct TargetDesc {
    uint32_t fmt = 0;            // D3DFORMAT; 0 with bound false
    uint32_t w = 0, h = 0;
    uint32_t ms = 0, quality = 0;   // D3DMULTISAMPLE_TYPE and quality
    uint32_t level = 0;          // the mip level, for a texture's surface
    bool bound = false;
    bool texture = false;        // a level of a texture (it can be sampled)
};

enum DrawMethod : uint8_t { M_DP = 0, M_DIP = 1, M_DPUP = 2, M_DIPUP = 3 };

// One input of a vertex shader's input signature: the D3DDECLUSAGE and usage index its dcl names.
struct VsInput {
    uint8_t usage = 0, usage_index = 0;
    bool operator==(const VsInput& o) const { return usage == o.usage && usage_index == o.usage_index; }
};

// The D3D9 state at a draw that a pipeline is a function of, before any mapping.
struct PipelineInput {
    std::array<uint32_t, kNumPipelineRS> rs{};
    uint32_t prim = 0;                   // D3DPRIMITIVETYPE
    uint8_t method = M_DP;
    uint32_t stride0 = 0;                // SetStreamSource(0)'s stride, or the UP stride
    uint32_t freq0 = 1;                  // SetStreamSourceFreq(0)
    // SetStreamSource's stride and SetStreamSourceFreq's setting of each stream 1-15 a declaration
    // element names (DXVK's BindInputLayout reads cStreamFreq per binding, :7726-7737, and binds
    // each stream's stride at the draw); 0 for every other stream and for index 0 (stride0, freq0).
    std::array<uint32_t, 16> stream_stride{};
    std::array<uint32_t, 16> stream_freq{};
    uint8_t n_elems = 0;                 // declaration elements (up to the D3DDECL_END)
    std::array<VertexElem, 16> elems{};
    bool decl_overflow = false;          // more than 16 elements
    bool fvf_decl = false;               // the declaration is SetFVF's (not converted to elements here)
    std::array<TargetDesc, 4> rt{};
    TargetDesc ds{};
    uint32_t rt_tex_sampled = 0;         // bit i: RT i is level 0 of a texture bound at some stage
    uint32_t rt_tex_stages = 0;          // the stages (d9g's 0-20, DXVK's slots) where such a texture is
                                         // bound: exact per target only while one bit of rt_tex_sampled is
                                         // set (more are not mapped, d9g_pipeline.cpp)
    uint32_t ds_tex_sampled = 0;         // the DS surface's texture bound at some stage
    // The shaders' reflection (R1's translator, d9g_shader.h Reflection: DXVK's own
    // D3D9ShaderAnalysis), which the state check holds to test/shader_record.py's two readings of the
    // same bytecode. The pixel shader's masks are DXVK's D3D9ShaderMasks (d3d9_shader.h:121,
    // PSShaderMasks d3d9_device.h:1520-1523): the render targets it writes (GetRenderTargetMask) and
    // the sampler slots it reads (GetSamplerMask), which decide the colour attachments and
    // UpdateActiveHazardsRT (d3d9_device.cpp:6302-6352); 0 without a pixel shader (a fixed-function
    // draw, which this device leaves unmapped).
    uint32_t ps_rt_mask = 0;
    uint32_t ps_sampler_mask = 0;
    // The vertex shader's input signature (D3D9ShaderAnalysis::GetInputSignature,
    // d3d9_shader_analysis.h:80-166): the semantic of input location i, in dcl order, which
    // BindInputLayout (d3d9_device.cpp:7666-7750) builds the vertex input from. Empty without a
    // vertex shader; vs_inputs_overflow where it has more entries than vs_inputs holds.
    uint8_t vs_n_inputs = 0;
    std::array<VsInput, 16> vs_inputs{};
    bool vs_inputs_overflow = false;
    uint32_t vs_id = 0, ps_id = 0, decl_id = 0;   // creation indices, 1-based; 0 none (fixed function)
};

// One sampler stage at a draw.
struct SamplerInput {
    std::array<uint32_t, 14> ss{};       // D3DSAMPLERSTATETYPE 0..13 (index 0 unused)
    bool tex = false;                    // a texture bound
    uint32_t levels = 0;                 // its mip levels
    bool cube = false, depth = false;    // a cube texture; a depth texture (m_textureSlotTracking.depth)
    uint32_t tex_fmt = 0;                // D3DFORMAT
};

// What the device knows of its GPU (its report's "sdl_gpu"); no member has a default, so a
// caller cannot use one without a source (the critic's M7).
struct DeviceParams {
    bool d24s8_supported;                // SDL_GPUTextureSupportsFormat(D24_UNORM_S8_UINT, depth target)
    float point_size_max;                // VkPhysicalDeviceLimits::pointSizeRange[1] (d9g_gpu_sdl.cpp's query)
    // The SDL patch's two capabilities as the device uses them (patches/sdl3; d9g_gpu.h GpuInfo):
    // false on a stock or system SDL3, and where a test run forces one off (SA2_D3D9GPU_CAPS_OFF).
    // A false one makes its feature the shader route's fallback obligation (ruling 3: the patch
    // where the device supports it, the shader emulation where it does not): fallback_border
    // (d9g_sampler.h), R_DEPTH_CLAMP (d9g_pipeline.h).
    bool sampler_clamp_to_border;        // SDL_PROP_GPU_DEVICE_SA2RECOMP_SAMPLER_CLAMP_TO_BORDER_BOOLEAN
    bool depth_clip_and_clamp;           // SDL_PROP_GPU_DEVICE_SA2RECOMP_DEPTH_CLIP_AND_CLAMP_BOOLEAN
};

// DXVK's ResetState defaults (d3d9_device.cpp:8633-8857) for every render state it sets and
// every sampler state, held by test/state_check.cpp to the extracted ResetState lines.
// `auto_depth` is D3DPRESENT_PARAMETERS::EnableAutoDepthStencil.
void state_defaults(uint32_t rs[256], uint32_t ss[14], const DeviceParams& dp, bool auto_depth);

// DXVK's d3d9 sampler options (d3d9_options.cpp:52,72,75,76,87) at their getOption defaults
// (host/player/dxvk.conf sets none of them; test/extract_refs.py refuses one that does).
struct SamplerOptions {
    int32_t sampler_anisotropy = -1;
    bool seamless_cubes = false;
    float sampler_lod_bias = 0.0f;
    bool clamp_negative_lod_bias = false;
};

// FNV-1a 64 over bytes (the device's keys, test/state_check.cpp's ids).
inline uint64_t fnv1a64(const void* p, size_t n, uint64_t h = 0xcbf29ce484222325ull) {
    const uint8_t* b = static_cast<const uint8_t*>(p);
    for (size_t i = 0; i < n; i++) h = (h ^ b[i]) * 0x100000001b3ull;
    return h;
}

// Whether n bytes at a and b are equal. Windows: eight at a time, since the memcmp the zig-built
// player links compares a byte at a time (it was an eighth of the player's time under Wine).
inline bool same_bytes(const void* a, const void* b, size_t n) {
#ifdef _WIN32
    const uint8_t* p = static_cast<const uint8_t*>(a);
    const uint8_t* q = static_cast<const uint8_t*>(b);
    size_t i = 0;
    for (; i + 8 <= n; i += 8) {
        uint64_t x, y;
        std::memcpy(&x, p + i, 8);
        std::memcpy(&y, q + i, 8);
        if (x != y) return false;
    }
    for (; i < n; i++)
        if (p[i] != q[i]) return false;
    return true;
#else
    return std::memcmp(a, b, n) == 0;
#endif
}

// A hash of bytes, eight at a time, for an in-memory table's index only: never recorded, and the
// bytes themselves decide equality (the per-draw memo tables and the SDL object caches).
inline uint64_t memo_hash(const void* data, size_t n) {
    const uint8_t* b = static_cast<const uint8_t*>(data);
    uint64_t h = 0x9e3779b97f4a7c15ull ^ n;
    size_t i = 0;
    for (; i + 8 <= n; i += 8) {
        uint64_t w;
        std::memcpy(&w, b + i, 8);
        h = (h ^ w) * 0xff51afd7ed558ccdull;
        h ^= h >> 32;
    }
    for (; i < n; i++) h = (h ^ b[i]) * 0x100000001b3ull;
    return h ^ (h >> 29);
}

}  // namespace d9g
