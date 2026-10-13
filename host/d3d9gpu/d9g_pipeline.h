// d9g_pipeline.h: a draw's D3D9 state as an SDL_gpu graphics pipeline description, without
// shaders (R2b-prep, private/generated/s5/r2b/PLAN.md section 4). Nothing here creates an SDL
// object: the device computes, keys and counts each draw's description, and R2-draws creates the
// pipelines. The vertex input follows the vertex shader's input signature, as R1's translator
// reflects it (U9, private/generated/s5/rulings/r1-shader-translator/PLAN.md section 2.4).
//
// The rule throughout: d9g maps D3D9 directly to SDL's enums and mirrors DXVK 3.1.1's own
// normalisation, so that each Vulkan field SDL builds from the description equals the raw value
// DXVK builds for the same state (test/state_check.cpp holds every field to DXVK's, computed by
// DXVK's own code). Where SDL cannot express DXVK's state, the description says so by name
// (Unmapped) and never carries a wrong value; where the choice belongs to the user it says
// undecided (residues).
//
// Rules mirrored from DXVK 3.1.1 (zlib licence; Copyright Philip Rebohle, Joshua Ashton et al.;
// private/s5-tools/src/dxvk-3.1.1/LICENSE), cited per rule.
#pragma once

#include "d9g_state.h"

#include <SDL3/SDL_gpu.h>

#include <cstdint>
#include <string>
#include <string_view>
#include <vector>

namespace d9g {

// patches/sdl3's pipeline property by name, so a stock SDL3's headers build the same code (the
// device detects the patch at run time: d9g_gpu_sdl.cpp); held to the patched headers where they
// are the ones built against.
inline constexpr char kPropDepthClamp[] = "SDL.gpu.graphicspipeline.create.sa2recomp.depth_clamp";
#ifdef SDL_PROP_GPU_GRAPHICSPIPELINE_CREATE_SA2RECOMP_DEPTH_CLAMP_BOOLEAN
static_assert(std::string_view(SDL_PROP_GPU_GRAPHICSPIPELINE_CREATE_SA2RECOMP_DEPTH_CLAMP_BOOLEAN) == kPropDepthClamp);
#endif

// Residues: states SDL builds differently from DXVK, each with its way out.
enum Residue : uint32_t {
    // DXVK always builds depthClampEnable together with depth clip (dxvk_graphics.cpp:585-592);
    // stock SDL 3.4.16 builds clamp only without clip (SDL_gpu_vulkan.c:6483; SDL_gpu.h:1835-1837).
    // d9g keeps clip on. Ruling 3 (Documentation/decisions/2026-10-04-S5-user-rulings.md; the
    // state check's DEPTH_CLAMP class): where the device reports patches/sdl3's depth clip and
    // clamp, the pipeline sets kPropDepthClamp (PipelineDesc::depth_clamp) and SDL builds DXVK's
    // state; where it does not, this bit is the shader route's obligation, the fallback that
    // clamps fragment depth to the viewport's range (R1's lowering).
    R_DEPTH_CLAMP = 1u << 0,
};

// SDL's slot for DXVK's null stream: SDL's last vertex buffer slot (MAX_VERTEX_BUFFERS 16,
// SDL_sysgpu.h:36). The device maps stream 0 only, so no mapped attribute reads it as a stream.
inline constexpr uint32_t kNullStreamSlot = 15;

struct PipelineDesc {
    // SDL_GPUGraphicsPipelineCreateInfo minus the shaders
    SDL_GPUVertexBufferDescription vbs[4]{};
    uint32_t n_vbs = 0;
    // One attribute per entry of the vertex shader's input signature, attribute i at location i
    // (BindInputLayout, d3d9_device.cpp:7666-7750): DXVK's dxbc-spirv gives a vertex shader's inputs
    // locations in dcl order (sm3_io_map.cpp:291-308), the signature's order.
    SDL_GPUVertexAttribute attrs[16]{};
    uint32_t n_attrs = 0;
    // The signature's D3D9 semantic of each attribute (usage << 8 | usage_index).
    uint16_t attr_semantic[16]{};
    // An input the declaration lacks: DXVK binds its null stream there (binding 16, per instance,
    // R32G32B32A32_SFLOAT, offset 0: :7704-7707, :7738-7742), a VK_NULL_HANDLE buffer with stride 0
    // (dxvk_context.cpp:7865-7870). SDL binds no null buffer, so the description gives each such
    // attribute kNullStreamSlot, a buffer of the device's own at the per-instance rate with pitch 0
    // (every instance reads its first 16 bytes), whose contents are what DXVK's null binding reads on
    // the driver, measured by host/d3d9gpu/test_shaders.sh C7 (zero on NVIDIA and lavapipe). The
    // state check's NULL_STREAM rule holds the two bindings equal.
    bool null_stream = false;
    SDL_GPUPrimitiveType prim = SDL_GPU_PRIMITIVETYPE_TRIANGLELIST;
    bool fan_as_list = false;          // D3DPT_TRIANGLEFAN drawn as a list through the fan cache (d9g_draw)
    SDL_GPURasterizerState rs{};
    // kPropDepthClamp true in the create info's props: depth clamp beside rs.enable_depth_clip
    // (patches/sdl3), set where the device reports DeviceParams::depth_clip_and_clamp. Exactly one
    // of this and residues' R_DEPTH_CLAMP is set on every description (MF_DEPTH_CLAMP_OFF drops this;
    // MF_DEPTH_CLAMP_EXTRA sets both).
    bool depth_clamp = false;
    SDL_GPUMultisampleState ms{};
    SDL_GPUDepthStencilState ds{};
    SDL_GPUColorTargetDescription ct[4]{};
    uint32_t n_ct = 0;
    bool has_ds = false;
    SDL_GPUTextureFormat ds_fmt = SDL_GPU_TEXTUREFORMAT_INVALID;
    // Component-order obligation (the critic's S4): bit i, the attribute at location i is a
    // D3DCOLOR, which DXVK fetches as B8G8R8A8_UNORM and SDL as R8G8B8A8_UNORM; the vertex shader's
    // BGRA variant (d9g_shader.h Variant::bgra_mask, by the same locations) reads it .zyxw.
    uint32_t bgra_mask = 0;
    uint32_t residues = 0;
    // An unresolvable render-target hazard: a target the draw writes is level 0 of a texture
    // bound at a stage the pixel shader samples (PipelineInput::ps_sampler_mask, the shader's
    // reflection). DXVK renders it in its feedback-loop layout (d3d9_device.cpp:6814-6820), which
    // SDL cannot express: undecided, the user's (the critic's M4).
    bool rt_feedback = false;
    std::vector<std::string> unmapped;   // empty for every census tuple, or the check fails

    // The create info SDL would take, pointing into this description; shaders null. `props`:
    // CreateProps(*this).id() (d9g_props.h), the one maker of the depth-clamp property, which R3's
    // device passes and the state check reads through its model of SDL's property functions.
    SDL_GPUGraphicsPipelineCreateInfo create_info(SDL_PropertiesID props) const;
};

// Shader-side values as DXVK computes them (spec data and push data, d3d9_state.h:37-46 and
// D3D9SpecData); KEEP OUT (OPEN-1): where they live in the shader route.
struct LegacyData {
    uint32_t alpha_compare_vk = 0;     // VkCompareOp; ALWAYS unless ALPHATESTENABLE (:7173-7186, :6932-6937)
    uint32_t alpha_precision = 0;      // GetAlphaTestPrecision of RT0's format (:7137-7170)
    uint8_t alpha_ref = 0;             // D3D9SharedPushData::alphaRef is a uint8_t (:2459-2461)
    uint16_t point_size = 0, point_min = 0, point_max = 0;   // EncodePointSize: 13.3 fixed point (d3d9_device.h:1526-1529)
    bool point_sprite = false, point_scale = false;          // UpdatePointMode (:6574-6607)
    bool flat_shading = false;         // BindRasterizerState setFlatShading (:7103)
    // the semantic forms beside DXVK's encodings (the critic's N7)
    float point_size_f = 0.0f;
    uint32_t alpha_precision_bits = 8;
};

struct PipelineKey {
    std::vector<uint8_t> bytes;        // canonical: field by field, no padding
    uint64_t hash = 0;                 // FNV-1a 64 of bytes
};

// Mapping faults for the state check's controls (SA2_D3D9GPU_FAULT, d9g.h): each maps one
// thing wrongly, and the check must name the field it breaks.
enum MapFault : uint32_t {
    MF_BLEND_SWAP = 1u << 0,          // SRCALPHA <-> INVSRCALPHA
    MF_ZFUNC_SWAP = 1u << 1,          // LESS <-> LESSEQUAL
    MF_NORMALIZE_OFF = 1u << 2,       // DXVK's blend normalisation skipped
    MF_WRITEMASK = 1u << 3,           // COLORWRITEENABLE's R and B bits swapped
    MF_ADDRESS_SWAP = 1u << 4,        // WRAP <-> MIRROR
    MF_BORDER_CLAMP = 1u << 5,        // BORDER silently CLAMP_TO_EDGE
    MF_LOD_UNQUANTISED = 1u << 6,     // max_lod 16.0, not DXVK's 4.8 fixed point
    MF_BGRA_MASK = 1u << 7,           // D3DCOLOR fetched as BGRA (the obligation dropped)
    MF_FAN_ORDER = 1u << 8,           // the fan cache in D3D9's order (0, i+1, i+2)
    MF_DEPTH_CLIP_OFF = 1u << 9,      // enable_depth_clip false
    MF_ALPHA_OP = 1u << 10,           // the alpha compare op ALPHAFUNC even with the test off
    MF_DS_BIND = 1u << 11,            // the depth-stencil bound whatever its extent
    MF_CULL_SWAP = 1u << 12,          // CW <-> CCW
    MF_VI_OFFSET = 1u << 13,          // every vertex attribute's offset 4 bytes late
    MF_BGRA_EXTRA = 1u << 14,         // a BGRA obligation on attribute 0 whatever its type
    MF_FAN_ON_LIST = 1u << 15,        // the fan obligation (fan_as_list) on a draw that is no fan
    MF_BORDER_COLOUR = 1u << 16,      // a mapped BORDER sampler's colour the next of SDL's three (d9g_sampler)
    MF_DEPTH_CLAMP_OFF = 1u << 17,    // kPropDepthClamp dropped on a device with depth clip and clamp, and no
                                      // R_DEPTH_CLAMP either: stock SDL's clip-only state (the depth-clamp
                                      // measurement's run B, sdl-patch PLAN.md section 5)
    MF_DEPTH_BIAS_PUSH = 1u << 18,    // a constant depth bias past MaxZ on depth-tested draws, the measurement's
                                      // positive control: 2^24 units, a whole depth range on D24_UNORM_S8_UINT
                                      // (r = 2^-24); on DXVK's D32_FLOAT_S8_UINT fallback r scales with each
                                      // primitive's exponent, so the push is smaller there and H2 sizes it
    MF_DEPTH_CLAMP_EXTRA = 1u << 19,  // R_DEPTH_CLAMP beside kPropDepthClamp on a device with depth clip and
                                      // clamp: an obligation the shader route does not owe there
    MF_BORDER_SWIZZLED_VIEW = 1u << 20,   // BORDER mapped on a texture whose DXVK view swizzles (X8R8G8B8):
                                          // d9g_format.h dxvk_view_identity ignored
    MF_BORDER_FALLBACK = 1u << 21,    // every BORDER the shader route's fallback, as on a device without
                                      // CLAMP_TO_BORDER, where the patch builds DXVK's border
    // The vertex input by the shader's signature (U9, the R1 plan's section 2.4), each control one
    // wrong rule:
    MF_VI_DECLORDER = 1u << 22,       // R2b's declaration-order stand-in: one attribute per declared element,
                                      // in declaration order, location its index, and no null stream
    MF_VI_NO_NULL = 1u << 23,         // an input the declaration lacks gets no attribute (no null stream)
    MF_NULL_VERTEX_RATE = 1u << 24,   // the null stream's slot at the per-vertex rate
    MF_VI_UNREAD_ATTR = 1u << 25,     // a declaration element the shader does not read gets an attribute too
    MF_BGRA_DECL_INDEX = 1u << 26,    // the BGRA obligation by declaration index, not by signature location
    MF_PS_SAMPLER_SUPERSET = 1u << 27,   // R2b's superset stand-in for the pixel shader's sampler mask
                                         // (every slot 0-15), not its reflection
};
// Name <-> bit for the faults above (the report and SA2_D3D9GPU_FAULT use the names).
const char* map_fault_name(uint32_t bit);
uint32_t map_fault_bit(const std::string& name);

PipelineDesc map_pipeline(const PipelineInput& in, const DeviceParams& dp, uint32_t faults);
LegacyData map_legacy(const PipelineInput& in, uint32_t faults);
// The key: the description without `unmapped`, plus vs_id and ps_id (with_shaders), and a
// version byte (3: the vertex input by the shader's signature, with locations and the null
// stream). LegacyData is not in it (per-draw data in RENDERER.md section 3.3's design).
PipelineKey key_of(const PipelineDesc& d, const PipelineInput& in, bool with_shaders);
// The same bytes into a caller's buffer, without allocating: their length.
inline constexpr size_t kPipelineKeyMaxBytes = 1024;
size_t key_bytes(const PipelineDesc& d, const PipelineInput& in, bool with_shaders, uint8_t (&out)[kPipelineKeyMaxBytes]);
// The bytes of a LegacyData (the device counts distinct ones).
std::vector<uint8_t> legacy_bytes(const LegacyData& l);

// DXVK's decoders, mirrored to SDL_gpu's enums (d3d9_util.cpp:163-247); each returns false where
// SDL has no value DXVK's Vulkan value maps to.
bool sdl_blend_factor(uint32_t d3dblend, bool is_alpha, SDL_GPUBlendFactor* out, std::string* why);
SDL_GPUBlendOp sdl_blend_op(uint32_t d3dblendop);
SDL_GPUCompareOp sdl_compare_op(uint32_t d3dcmp);
SDL_GPUStencilOp sdl_stencil_op(uint32_t d3dstencilop);

}  // namespace d9g
