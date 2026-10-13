// ref_dxvk.h: the DXVK side of the second host's state check (test/ref_dxvk.cpp). It includes
// no DXVK header, so test/state_check.cpp (which includes the install's d3d9.h) and the DXVK
// translation unit (which includes DXVK Native's own) never meet; they share d9g_state.h and
// ref_facts.h only.
#pragma once

#include "d9g_state.h"
#include "ref_facts.h"

#include <array>
#include <bitset>
#include <map>
#include <string>
#include <vector>

namespace d9g_ref {

// DXVK's device, as the state check is told it: from DXVK's own log of a run on the same
// machine (the enabled features, the D24S8 remap) and the Vulkan property query the second
// host's report carries (vendor ID, point size range). No member has a default: a value with
// no recorded source refuses the check (the critic's M7).
struct DxvkParams {
    uint32_t vendor_id;
    float point_size_max;
    bool d24s8_supported;                   // D3D9VkFormatTable's CheckImageFormatSupport, from DXVK's log
    bool d16s8_supported;                   // (its "D24_UNORM_S8_UINT ->" and "D16_UNORM_S8_UINT ->" remap lines)
    std::map<std::string, bool> features;   // "extDepthBiasControl.depthBiasExact" -> value
    std::string source;                     // where the values were read
};

// A vertex shader's input signature as DXVK reads it (D3D9InputSignature,
// d3d9_shader_analysis.h:80-166): each input register's dcl semantic, in dcl order (HandleDcl,
// d3d9_shader_analysis.cpp:310-312), as {usage, usage index}. Recorded from the bytecode and
// from dxbc-spirv's disassembly by test/shader_record.py (the review's checker-logic finding 1).
struct VsSignature {
    std::vector<std::pair<uint8_t, uint8_t>> inputs;
};

// The shaders' reflection DXVK's code reads (D3D9ShaderMasks, d3d9_shader.h:121; the input
// signature), as the census takes it: test/shader_record.py's record of the bytecode, which the
// census holds equal to the device's own (R1's translator, U9; d9g_state.h PipelineInput).
struct RecordedReflection {
    VsSignature vs_signature;
    uint32_t ps_sampler_mask;   // GetSamplerMask
    uint32_t ps_rt_mask;        // GetRenderTargetMask
};

// The shader metadata DXVK's code reads and the pipeline DXVK draws with (the critic's M3). The
// first three are the shaders' reflection: a recorded one where the census gives it, the input's own
// (d9g_state.h PipelineInput, the device's reflection) where the sweep runs. The rest are named
// stand-ins, metadata the reflection does not carry: the check evaluates DXVK with the chosen values
// and once more per stand-in with that one's alternative, and counts, per stand-in, every fact its
// choice decides.
struct Standins {
    uint32_t fs_output_mask;        // reflection: the PS's written render targets (GetRenderTargetMask)
    VsSignature vs_signature;       // reflection: the VS's input signature (BindInputLayout)
    uint32_t ps_sampler_mask;       // reflection: the PS's sampled slots (UpdateActiveHazardsRT, BindFramebuffer)
    bool fs_sample_rate_shading;    // stand-in: the PS reads SV_SampleIndex-like inputs
    bool fs_exports_sample_mask;    // stand-in: the PS writes the sample mask
    bool gp_base_pipeline;          // stand-in: DXVK draws with the base pipeline it links from graphics
                                    // pipeline libraries (while the optimized one compiles), not the optimized one
    bool can_use_sample_locations;  // stand-in: DxvkDevice::canUseSampleLocations(0), a device property DXVK's
                                    // log does not print; only the sample-location flags read it
};
// The chosen stand-ins, with the recorded reflection where given, else the input's.
Standins standins_chosen(const d9g::PipelineInput& in, const RecordedReflection* recorded = nullptr);
// The stand-ins' names, in the order standins_alternative numbers them.
const std::vector<std::string>& standin_names();
// The chosen stand-ins with stand-in `which` alone at its alternative value.
Standins standins_alternative(const d9g::PipelineInput& in, const RecordedReflection* recorded, size_t which);
// DXVK's null-stream binding (NullStreamIdx = caps::MaxStreams, d3d9_device.h:220), from DXVK's own
// constant: the binding BindInputLayout gives an input the declaration lacks.
uint32_t dxvk_null_stream_binding();

struct PipelineResult {
    D9gPipelineFacts facts{};
    std::bitset<256> rs_read;       // render states DXVK's extracted functions read
    bool rt_feedback = false;       // an unresolvable render-target hazard (unresolvableHazardRT != 0, M4)
    bool history_standin = false;   // a history-dependent member not derivable from the input (M6)
    bool has_ds = false, ds_stencil = false;   // the depth-stencil attachment DXVK binds, and its stencil aspect
    // A state the reference does not model (a path transcribed nowhere, or one DXVK itself
    // refuses): d9g must leave it unmapped, and the check fails a mapped tuple with this reason
    std::string refused;
    std::string notes;
};

// The Vulkan state DXVK builds for a draw. `rs_full` is the whole render-state array (the
// input's pipeline states over DXVK's defaults, or a sweep's perturbation of them).
PipelineResult dxvk_pipeline(const d9g::PipelineInput& in, const uint32_t rs_full[256], const DxvkParams& p,
                             const Standins& st);

struct SamplerResult {
    D9gSamplerFacts facts{};
    bool has_border = false;        // DXVK's key has a CLAMP_TO_BORDER axis
    // the depth-compare bit of a slot with no texture is the last bound texture's (DXVK never
    // clears m_textureSlotTracking.depth on an unbind, UpdateTextureBitmasks
    // d3d9_device.cpp:6225-6283): history, evaluated at its initial 0 (the critic's M6)
    bool history = false;
    std::string refused;            // a texture format DXVK's table maps to nothing, or a conversion format
    // The VkSamplerBorderColorComponentMappingCreateInfoEXT DXVK chains (sm_pNextBorderSwizzle 1):
    // its components (VkComponentSwizzle values r, g, b, a), whether each is the identity
    // (VK_COMPONENT_SWIZZLE_IDENTITY or its own channel), and its srgb member
    std::array<uint32_t, 4> border_swizzle{};
    bool border_swizzle_identity = false, border_swizzle_srgb = false;
    // DXVK's view of the texture as its sampler key carries it on a BORDER sampler (BindSampler's
    // setViewProperties, d3d9_device.cpp:7263-7268; the defaults where no texture is bound): the
    // component mapping, whether it is the identity, and whether the view format is sRGB. And
    // whether DXVK's border colour (the key's, through the view's inverse swizzle) is one of the
    // three standard colours by an exact match: its own determineBorderColorType asked on a device
    // with custom border colours, where no exact match is CUSTOM (a device without them takes the
    // nearest standard colour, which is no exact match). The state check decides from these alone
    // where the SDL patch builds DXVK's border (ruling 3), whatever d9g decides.
    std::array<uint32_t, 4> view_swizzle{};
    bool view_identity = true, view_srgb = false, border_exact_standard = false;
};
SamplerResult dxvk_sampler(const d9g::SamplerInput& in, const DxvkParams& p);

// DXVK's ResetState, evaluated (the extracted assignment lines): `set[i]` says the line sets rs[i].
void dxvk_defaults(uint32_t rs[256], bool set[256], uint32_t ss[14], const DxvkParams& p, bool auto_depth);
// DXVK's d3d9 sampler options at their getOption defaults (the extracted d3d9_options.cpp lines).
d9g::SamplerOptions dxvk_sampler_options();

// Compiled DXVK rules the draw tests hold d9g to.
uint32_t dxvk_vertex_count(uint32_t prim, uint32_t count);         // d3d9_util.cpp GetVertexCount
uint32_t dxvk_decl_size0(const d9g::PipelineInput& in);            // D3D9VertexDecl::GetSize(0) (transcribed)
uint32_t dxvk_up_data_size(uint32_t vertices, uint32_t stride);    // d3d9_device.h GetUPDataSize
uint32_t dxvk_up_buffer_size(uint32_t vertices, uint32_t stride, const d9g::PipelineInput& decl);   // GetUPBufferSize
void dxvk_fill_up(void* buffer, const void* data, uint32_t data_size, uint32_t buffer_size);       // FillUPVertexBuffer
uint64_t dxvk_up_aligned(uint64_t size);                           // AllocUPBuffer's align(size, CACHE_LINE_SIZE)
uint32_t dxvk_decltype_format(uint8_t type);                       // d3d9_util.cpp DecodeDecltype
void dxvk_enum_values(uint32_t* out, int n);                       // DXVK's vulkan_core.h values (the pytest)

// The state check's liveness control (the critic's S1): 1 moves DXVK's dynamic depth compare op
// after its span ran; 0 leaves DXVK alone.
void set_poke(int which);

// The DXVK device features the extracted spans and ref_dxvk.cpp read: the extractor's generated
// X-list (dxvk_extract.inc section FEATURES, the manifest's "device_features_read").
std::vector<std::string> dxvk_features_read();

}  // namespace d9g_ref
