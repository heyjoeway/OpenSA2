// ref_dxvk.cpp: the DXVK side of the second host's state check (PLAN.md sections 8 and 9,
// private/generated/s5/r2b/PLAN.md): for a draw's D3D9 state, the Vulkan state DXVK 3.1.1
// builds, computed by DXVK's own code.
//
//   * Six DXVK translation units are linked unmodified from the pinned checkout (d3d9_util.cpp,
//     d3d9_format.cpp, dxvk_constant_state.cpp, dxvk_format.cpp, dxvk_util.cpp, vulkan_names.cpp;
//     host/d3d9gpu/build.sh): the decoders, the blend and depth-stencil normalisation, the format
//     tables, the Vulkan enum names DXVK's log lines print.
//   * DXVK's own functions, extracted by span (test/extract_refs.py, dxvk_extract.inc), run as
//     members of the shims below: the D3D9 front end's Bind* functions, the format table, the
//     context's setters, its choice of dynamic state and its dynamic-state blocks, the command
//     list's dynamic-state setters, the pipeline's fragment-output, pre-rasterisation and
//     fragment-shader state, the sampler object. Their #line directives name the DXVK source line.
//   * What is transcribed, not extracted, carries a D9G_TRANSCRIBED tag (its name and the DXVK
//     lines it cites) at each place; test/extract_refs.py lists every tag in the manifest ("transcriptions"), holding
//     each citation to the pinned file's lines (the review's extraction finding 4). Each has its
//     own reviewer (PLAN.md section 8.3).
//
// The shims hold the names the spans use and nothing more; every value a shim gives that is not
// the draw's input is the shaders' reflection or a stand-in (Standins) or a device parameter (DxvkParams, from a
// recorded source), or a history-dependent member derived from the input (the critic's M6, each
// derivation cited below, and reported where it is not exact). A state the transcriptions do
// not model is refused (PipelineResult::refused), never approximated.
//
// The transcribed lines are DXVK's, altered (zlib clause 2: plainly marked by the tags above);
// DXVK's notice (zlib clause 3), as its LICENSE file at the pinned commit gives it, byte for byte
// after each line's "// " (test/extract_refs.py refuses an extraction where it is not):
//
//                   Copyright (c) 2017 Philip Rebohle
//                   Copyright (c) 2019 Joshua Ashton
//                   Copyright (c) 2019 Robin Kertels
//                   Copyright (c) 2023 Jeffrey Ellison
//
//                           zlib/libpng license
//
// This software is provided 'as-is', without any express or implied
// warranty. In no event will the authors be held liable for any damages
// arising from the use of this software.
//
// Permission is granted to anyone to use this software for any purpose,
// including commercial applications, and to alter it and redistribute it
// freely, subject to the following restrictions:
//
// – The origin of this software must not be misrepresented; you must not
//   claim that you wrote the original software. If you use this software
//   in a product, an acknowledgment in the product documentation would be
//   appreciated but is not required.
//
// – Altered source versions must be plainly marked as such, and must not
//   be misrepresented as being the original software.
//
// – This notice may not be removed or altered from any source distribution.
//
// The extracted code's own copy of the notice heads dxvk_extract.inc, with each alteration.
#include "d3d9_util.h"
#include "d3d9_format.h"
#include "../dxvk/dxvk_graphics.h"
#include "../dxvk/dxvk_context_state.h"
#include "../dxvk/dxvk_sampler.h"
#include "../vulkan/vulkan_util.h"

#include "ref_dxvk.h"

#include <algorithm>
#include <atomic>
#include <cstring>
#include <map>
#include <stdexcept>
#include <tuple>

namespace dxvk {

#define D9G_SECTION_TYPES
#include "dxvk_extract.inc"
#undef D9G_SECTION_TYPES

// ------------------------------------------------------------------ the device features
// The DxvkDeviceFeatures the spans and this file read: the extractor's generated X-list
// (dxvk_extract.inc section FEATURES, extract_manifest.json "device_features_read"), filled from
// DxvkParams; a member with no recorded value refuses (M7; the review's extraction finding 3).
static DxvkDeviceFeatures g_features;
static const DxvkDeviceFeatures& d9g_features() { return g_features; }

static void set_features(const d9g_ref::DxvkParams& p) {
    g_features = DxvkDeviceFeatures();
    auto need = [&](const char* name) -> VkBool32 {
        auto it = p.features.find(name);
        if (it == p.features.end())
            throw std::runtime_error(std::string("DXVK device feature ") + name + " has no recorded value (" + p.source + ")");
        return it->second ? VK_TRUE : VK_FALSE;
    };
#define D9G_FEATURE(member, name) g_features.member = need(name);
#define D9G_SECTION_FEATURES
#include "dxvk_extract.inc"
#undef D9G_SECTION_FEATURES
#undef D9G_FEATURE
}

// ------------------------------------------------------------------ the command recorder
// The Vulkan dynamic-state calls DXVK records at a draw, kept as facts. The command list's
// own setters (section "cmd") call these through m_vkd.
struct D9gVkRec {
    D9gPipelineFacts* f = nullptr;
    bool ds_test = false, st_test = false;
    void vkCmdSetAlphaToCoverageEnableEXT(VkCommandBuffer, VkBool32 v) { f->v[PF_ms_alphaToCoverageEnable] = v; }
    void vkCmdSetDepthClipEnableEXT(VkCommandBuffer, VkBool32 v) { f->v[PF_rs_depthClipEnable] = v; }
    void vkCmdSetDepthBiasEnable(VkCommandBuffer, VkBool32 v) { f->v[PF_rs_depthBiasEnable] = v; }
    void vkCmdSetDepthBias(VkCommandBuffer, float c, float cl, float s) {
        f->v[PF_rs_depthBiasConstantFactor] = d9g_fbits(c);
        f->v[PF_rs_depthBiasClamp] = d9g_fbits(cl);
        f->v[PF_rs_depthBiasSlopeFactor] = d9g_fbits(s);
        f->v[PF_rs_depthBiasRepresentation] = VK_DEPTH_BIAS_REPRESENTATION_LEAST_REPRESENTABLE_VALUE_FORMAT_EXT;
        f->v[PF_rs_depthBiasExact] = VK_FALSE;
    }
    void vkCmdSetDepthBias2EXT(VkCommandBuffer, const VkDepthBiasInfoEXT* i) {
        f->v[PF_rs_depthBiasConstantFactor] = d9g_fbits(i->depthBiasConstantFactor);
        f->v[PF_rs_depthBiasClamp] = d9g_fbits(i->depthBiasClamp);
        f->v[PF_rs_depthBiasSlopeFactor] = d9g_fbits(i->depthBiasSlopeFactor);
        f->v[PF_rs_depthBiasRepresentation] = VK_DEPTH_BIAS_REPRESENTATION_LEAST_REPRESENTABLE_VALUE_FORMAT_EXT;
        f->v[PF_rs_depthBiasExact] = VK_FALSE;
        for (auto* n = static_cast<const VkBaseInStructure*>(i->pNext); n; n = n->pNext)
            if (n->sType == VK_STRUCTURE_TYPE_DEPTH_BIAS_REPRESENTATION_INFO_EXT) {
                auto* r = reinterpret_cast<const VkDepthBiasRepresentationInfoEXT*>(n);
                f->v[PF_rs_depthBiasRepresentation] = r->depthBiasRepresentation;
                f->v[PF_rs_depthBiasExact] = r->depthBiasExact;
            }
    }
    void vkCmdSetDepthBoundsTestEnable(VkCommandBuffer, VkBool32 v) { f->v[PF_ds_depthBoundsTestEnable] = v; }
    void vkCmdSetDepthBounds(VkCommandBuffer, float, float) {}
    void vkCmdSetDepthTestEnable(VkCommandBuffer, VkBool32 v) { f->v[PF_ds_depthTestEnable] = v; }
    void vkCmdSetDepthWriteEnable(VkCommandBuffer, VkBool32 v) { f->v[PF_ds_depthWriteEnable] = v; }
    void vkCmdSetDepthCompareOp(VkCommandBuffer, VkCompareOp v) { f->v[PF_ds_depthCompareOp] = v; }
    void vkCmdSetRasterizationSamplesEXT(VkCommandBuffer, VkSampleCountFlagBits v) { f->v[PF_ms_rasterizationSamples] = v; }
    void vkCmdSetSampleMaskEXT(VkCommandBuffer, VkSampleCountFlagBits, const VkSampleMask* m) { f->v[PF_ms_sampleMask] = *m; }
    void vkCmdSetCullMode(VkCommandBuffer, VkCullModeFlags v) { f->v[PF_rs_cullMode] = v; }
    void vkCmdSetFrontFace(VkCommandBuffer, VkFrontFace v) { f->v[PF_rs_frontFace] = v; }
    void vkCmdSetStencilTestEnable(VkCommandBuffer, VkBool32 v) { f->v[PF_ds_stencilTestEnable] = v; }
    void face(VkStencilFaceFlags m, int field_front, int field_back, uint32_t v) {
        if (m & VK_STENCIL_FACE_FRONT_BIT) f->v[field_front] = v;
        if (m & VK_STENCIL_FACE_BACK_BIT) f->v[field_back] = v;
    }
    void vkCmdSetStencilOp(VkCommandBuffer, VkStencilFaceFlags m, VkStencilOp fail, VkStencilOp pass, VkStencilOp dfail,
                           VkCompareOp cmp) {
        face(m, PF_ds_front_failOp, PF_ds_back_failOp, fail);
        face(m, PF_ds_front_passOp, PF_ds_back_passOp, pass);
        face(m, PF_ds_front_depthFailOp, PF_ds_back_depthFailOp, dfail);
        face(m, PF_ds_front_compareOp, PF_ds_back_compareOp, cmp);
    }
    void vkCmdSetStencilCompareMask(VkCommandBuffer, VkStencilFaceFlags m, uint32_t v) {
        face(m, PF_ds_front_compareMask, PF_ds_back_compareMask, v);
    }
    void vkCmdSetStencilWriteMask(VkCommandBuffer, VkStencilFaceFlags m, uint32_t v) {
        face(m, PF_ds_front_writeMask, PF_ds_back_writeMask, v);
    }
};

struct D9gCmdRec {
    D9gVkRec vk;
    D9gVkRec* m_vkd = &vk;
    VkCommandBuffer getCmdBuffer() { return VK_NULL_HANDLE; }
#define D9G_SECTION_CMD
#include "dxvk_extract.inc"
#undef D9G_SECTION_CMD
};

// ------------------------------------------------------------------ the context shim
struct D9gDevice {};   // the backend constructors take one and read nothing from it but features

struct D9gShaders {
    // the shader metadata the spans read (the critic's M3: Standins); pointers only test presence.
    const void* vs = nullptr;
    const void* tcs = nullptr;
    const void* tes = nullptr;
    const void* gs = nullptr;
    const void* fs = nullptr;
    uint32_t fs_output_mask = 0;
    bool fs_sample_rate_shading = false, fs_exports_sample_mask = false;
    bool tcs_tessellation_points = false, gs_exports_position = true;
    VkPrimitiveTopology tes_output_topology = VK_PRIMITIVE_TOPOLOGY_MAX_ENUM;
    VkPrimitiveTopology gs_output_topology = VK_PRIMITIVE_TOPOLOGY_MAX_ENUM;
    int32_t gs_rasterized_stream = 0;
};

struct D9gFoState : DxvkGraphicsPipelineFragmentOutputState {
    D9gFoState(const D9gDevice* device, const DxvkGraphicsPipelineStateInfo& state, const D9gShaders& shaders);
};
struct D9gPrState : DxvkGraphicsPipelinePreRasterizationState {
    D9gPrState(const D9gDevice* device, const DxvkGraphicsPipelineStateInfo& state, const D9gShaders& shaders);
    static bool isLineRendering(const D9gShaders& shaders, const DxvkGraphicsPipelineStateInfo& state);
};
struct D9gFsState : DxvkGraphicsPipelineFragmentShaderState {
    D9gFsState(const D9gDevice* device, const DxvkGraphicsPipelineStateInfo& state);
};
VkPrimitiveTopology determinePreGsTopology(const D9gShaders& shaders, const DxvkGraphicsPipelineStateInfo& state);
VkPrimitiveTopology determinePipelineTopology(const D9gShaders& shaders, const DxvkGraphicsPipelineStateInfo& state);

struct D9gRecCtx {
    struct {
        struct { DxvkGraphicsPipelineStateInfo state; DxvkGraphicsPipelineFlags flags; } gp;
        DxvkDynamicState dyn;
        struct { struct { void trackDepthRead() {} void trackDepthWrite() {} void trackStencilRead() {}
                          void trackStencilWrite() {} } attachmentMask; } om;   // render-pass bookkeeping only
        struct { std::array<uint32_t, MaxNumVertexBindings> vertexExtents = { }; } vi;
    } m_state;
    DxvkContextFlags m_flags;
    Flags<DxvkContextFeature> m_features;
    // the dynamic-flag span's stand-ins (Standins::gp_base_pipeline, can_use_sample_locations)
    bool d9g_base_pipeline = false;
    bool d9g_can_use_sample_locations = false;
    D9gCmdRec cmd;
    D9gCmdRec* m_cmd = &cmd;
    // what the front end bound
    std::vector<std::pair<uint32_t, DxvkSamplerKey>> samplers;
    VkImageAspectFlags feedback_loop = 0;

    void setDepthBias(DxvkDepthBias depthBias);
    void setDepthBiasRepresentation(DxvkDepthBiasRepresentation depthBiasRepresentation);
    void setDepthBounds(DxvkDepthBounds depthBounds);
    void setInputAssemblyState(const DxvkInputAssemblyState& ia);
    void setInputLayout(uint32_t attributeCount, const DxvkVertexInput* attributes, uint32_t bindingCount,
                        const DxvkVertexInput* bindings);
    void setRasterizerState(const DxvkRasterizerState& rs);
    void setMultisampleState(const DxvkMultisampleState& ms);
    void setDepthStencilState(const DxvkDepthStencilState& ds);
    void setBlendMode(uint32_t attachment, const DxvkBlendMode& blendMode);
    static VkStencilOpState convertStencilOp(const DxvkStencilOp& op, bool writable);
    void d9g_dynamic_flags();
    void d9g_dyn_depth_clip();
    void d9g_dyn_multisample();
    void d9g_dyn_rasterizer();
    void d9g_dyn_depth_test();
    void d9g_dyn_stencil_test();
    void d9g_dyn_depth_bias();
    void d9g_dyn_depth_bounds();

    void bindResourceSampler(uint32_t stage, uint32_t slot, const DxvkSamplerKey& key) {
        (void)stage;
        samplers.push_back({slot, key});
    }
    // DxvkContext::bindRenderTargets (dxvk_context.h:146-160) then updateRenderTargets (dxvk_context.cpp:7638-7672), the
    // pipeline-state half, with DxvkFramebufferInfo::getRtInfo (dxvk_framebuffer.cpp:66-84) and
    // getSampleCount: transcribed (views are formats and layouts here).
    // D9G_TRANSCRIBED(bindRenderTargets, "src/dxvk/dxvk_context.h:146-160, src/dxvk/dxvk_context.cpp:7638-7672, src/dxvk/dxvk_framebuffer.cpp:10-26, src/dxvk/dxvk_framebuffer.cpp:66-84")
    struct View { VkFormat format = VK_FORMAT_UNDEFINED; VkImageLayout layout = VK_IMAGE_LAYOUT_UNDEFINED;
                  VkComponentMapping swizzle = VkComponentMapping(); VkSampleCountFlagBits samples = VK_SAMPLE_COUNT_1_BIT; };
    struct Targets { std::array<View, MaxNumRenderTargets> color; View depth; };
    void bindRenderTargets(const Targets& rt, VkImageAspectFlags feedbackLoopAspects) {
        // dxvk_context.cpp bindRenderTargets: m_state.gp.state.om feedback loop
        m_state.gp.state.om.setFeedbackLoop(feedbackLoopAspects);
        feedback_loop = feedbackLoopAspects;
        // DxvkFramebufferInfo's constructor (dxvk_framebuffer.cpp:10-26): m_sampleCount starts at 0
        // (dxvk_framebuffer.h:232), each colour target in order sets it, then the depth target
        VkSampleCountFlagBits samples = VkSampleCountFlagBits(0);
        std::array<VkFormat, MaxNumRenderTargets> colorFormats = { };
        for (uint32_t i = 0; i < MaxNumRenderTargets; i++) {
            colorFormats[i] = rt.color[i].format;   // getRtInfo :76-80
            if (rt.color[i].format) samples = rt.color[i].samples;   // :15-20
        }
        VkFormat depthStencilFormat = VK_FORMAT_UNDEFINED;
        VkImageAspectFlags depthStencilReadOnlyAspects = 0;
        if (rt.depth.format) {
            depthStencilFormat = rt.depth.format;
            depthStencilReadOnlyAspects = lookupFormatInfo(rt.depth.format)->aspectMask &
                                          ~vk::getWritableAspectsForLayout(rt.depth.layout);   // getRtInfo :70-73
            samples = rt.depth.samples;   // :22-25
        }
        m_state.gp.state.ms.setSampleCount(samples);                      // :7660
        m_state.gp.state.rt = DxvkRtInfo(MaxNumRenderTargets, colorFormats.data(),
                                         depthStencilFormat, depthStencilReadOnlyAspects);   // :7661, :82-83
        for (uint32_t i = 0; i < MaxNumRenderTargets; i++) {             // :7663-7672
            VkComponentMapping mapping = VkComponentMapping();
            if (rt.color[i].format) mapping = util::invertComponentMapping(rt.color[i].swizzle);
            m_state.gp.state.omSwizzle[i] = DxvkOmAttachmentSwizzle(mapping);
        }
    }
};

// ------------------------------------------------------------------ the device shim
enum class D3D9DeviceDirtyFlag : uint32_t {
    Framebuffer, ViewportScissor, MultiSampleState, BlendState, DepthStencilState, RasterizerState, DepthBias,
    AlphaTestState, SpecializationEntries, PointScale, PushDataFfvs, PushDataVs, DepthBounds, InputLayout,
};
enum class D3DCompatibility : uint32_t { D3D8, D3D9Ex };

struct ShimRS {
    uint32_t v[256] = {};
    std::bitset<256> read;
    DWORD& operator[](uint32_t i) { read.set(i & 255); return v[i & 255]; }
};

struct ShimTextureDesc {
    D3D9Format Format = D3D9Format::Unknown;
    UINT MipLevels = 1;
    D3DMULTISAMPLE_TYPE MultiSample = D3DMULTISAMPLE_NONE;
    DWORD MultisampleQuality = 0;
};
struct ShimView {
    struct Info {
        VkComponentMapping swizzle;
        VkFormat format;
        VkComponentMapping unpackSwizzle() const { return swizzle; }
    } inf{};
    const Info& info() const { return inf; }
};
struct ShimViewRef {
    const ShimView* p = nullptr;
    explicit operator bool() const { return p != nullptr; }
    const ShimView* operator->() const { return p; }
};
struct ShimTexture {
    ShimTextureDesc m_desc;
    bool cube = false;
    D3D9_VK_FORMAT_MAPPING mapping{};   // D3D9VkFormatTable::GetFormatMapping's (the device's LookupFormat)
    ShimView views[2];
    const ShimTextureDesc* Desc() const { return &m_desc; }
    BOOL DetermineShadowState() const;   // extracted (d3d9_common_texture.cpp)
    bool IsCube() const { return cube; }
    // D3D9CommonTexture::IsSrgbCompatible (d3d9_common_texture.h:309-311): the mapping has an sRGB
    // format; GetSampleView picks the sRGB view only then (:367-369)
    // D9G_TRANSCRIBED(GetSampleView, "src/d3d9/d3d9_common_texture.h:309-311, src/d3d9/d3d9_common_texture.h:367-369")
    bool IsSrgbCompatible() const { return mapping.FormatSrgb != VK_FORMAT_UNDEFINED; }
    ShimViewRef GetSampleView(bool srgb) const { return ShimViewRef{&views[srgb && IsSrgbCompatible() ? 1 : 0]}; }
    const D3D9_VK_FORMAT_MAPPING& GetFormatMapping() const { return mapping; }
};
struct ShimSurface {
    ShimTexture tex;
    uint32_t level = 0;
    bool texture = false;     // a level of a texture
    const ShimTexture* GetCommonTexture() const { return &tex; }
};
struct ShimSurfRef {
    ShimSurface* s = nullptr;
    ShimSurface* ptr() const { return s; }
};
static const ShimTexture* GetCommonTexture(ShimSurface* s) { return s ? &s->tex : nullptr; }
static const ShimTexture* GetCommonTexture(const ShimTexture* t) { return t; }
struct ShimLimits { float pointSizeRange[2]; };

struct D3D9ShaderResourceMapping {
    static std::pair<uint32_t, uint32_t> getTextureSlotInfo(DWORD slot) { return {0u, uint32_t(slot)}; }
};

struct D9gConfig {
    template <class T> T getOption(const char*, T def) const { return def; }   // dxvk.conf sets none (extract_refs.py)
};
struct D9gD3D9Options {
    int32_t samplerAnisotropy = 0;
    bool seamlessCubes = false;
    float samplerLodBias = 0.0f;
    bool clampNegativeLodBias = false;
    bool supportDFFormats = false;
    bool supportX4R4G4B4 = false;
    bool useD32forD24 = false;
    void d9g_sampler_anisotropy(const D9gConfig& config);
    void d9g_seamless_cubes(const D9gConfig& config);
    void d9g_sampler_lod_bias(const D9gConfig& config);
    void d9g_clamp_negative_lod_bias(const D9gConfig& config);
    void d9g_support_df_formats(const D9gConfig& config);
    void d9g_support_x4r4g4b4(const D9gConfig& config);
    void d9g_use_d32_for_d24(const D9gConfig& config);
    void d9g_sampler_lod_bias_clamp();
    D9gD3D9Options() {
        D9gConfig config;
        d9g_sampler_anisotropy(config);
        d9g_seamless_cubes(config);
        d9g_sampler_lod_bias(config);
        d9g_clamp_negative_lod_bias(config);
        d9g_support_df_formats(config);
        d9g_support_x4r4g4b4(config);
        d9g_use_d32_for_d24(config);
        d9g_sampler_lod_bias_clamp();   // d3d9_options.cpp:87
    }
};

// D3D9VkFormatTable (d3d9_format.h:197-247), its constructor and GetFormatMapping extracted
// (section "fmt"; the review's extraction finding 2): the adapter is the device (a Direct3DCreate9
// device, not Ex: d3d9_interface.cpp:77), CheckImageFormatSupport's two answers are DXVK's log's
// (DxvkParams), the options their getOption defaults.
struct D9gFmtAdapter {
    uint32_t vendor = 0;
    bool IsD3DCompatibile(D3DCompatibility) const { return false; }
    uint32_t GetVendorId() const { return vendor; }
};
struct D9gFmtDxvkAdapter { bool d24s8, d16s8; };
struct D9gVkFormatTable {
    D9gVkFormatTable(D9gFmtAdapter* pParent, const D9gFmtDxvkAdapter& adapter, const D9gD3D9Options& options);
    D3D9_VK_FORMAT_MAPPING GetFormatMapping(D3D9Format Format) const;
    bool CheckImageFormatSupport(const D9gFmtDxvkAdapter& Adapter, VkFormat Format, VkFormatFeatureFlags2 Features) const {
        (void)Features;
        if (Format == VK_FORMAT_D24_UNORM_S8_UINT) return Adapter.d24s8;
        if (Format == VK_FORMAT_D16_UNORM_S8_UINT) return Adapter.d16s8;
        throw std::runtime_error("CheckImageFormatSupport: no recorded answer for VkFormat " + std::to_string(uint32_t(Format)));
    }
    bool m_isExtended;
    bool m_d24s8Support;
    bool m_d16s8Support;
    bool m_dfSupport;
    bool m_x4r4g4b4Support;
    bool m_w11v11u10Support;
    bool m_d16lockableSupport;
    bool m_d32flockableSupport;
    bool m_d24fs8Support;
};

struct ShimDecl { uint32_t size0 = 0; UINT GetSize(UINT) const { return size0; } };   // D3D9VertexDecl::GetSize

struct D9gDxvkShim {
    D9gRecCtx m_ctx;
    template <class F> void EmitCs(F&& f) { f(&m_ctx); }

    struct {
        ShimRS renderStates;
        std::array<std::array<DWORD, SamplerStateCount>, 21> samplerStates = { };
        std::array<const ShimTexture*, 21> textures = { };
        std::array<ShimSurfRef, 4> renderTargets;
        ShimSurfRef depthStencil;
        D3DVIEWPORT9 viewport = { };
        const ShimDecl* vertexDecl = nullptr;
    } m_state;
    ShimDecl decl_;
    D9gDxvkShim() { m_state.vertexDecl = &decl_; }
    Flags<D3D9DeviceDirtyFlag> m_dirty;
    struct { bool test(D3DCompatibility) const { return false; } } m_d3dCompatibility;   // a D3D9 device, not D3D8
    struct ShimAdapter { uint32_t vendor; uint32_t GetVendorId() const { return vendor; } } adapter_{0};
    ShimAdapter* m_adapter = &adapter_;
    struct ShimDxvkDevice {
        struct { struct { struct { uint32_t vendorID; } properties; } core; } props{};
        const decltype(props)& properties() const { return props; }
        DxvkSamplerKey createSampler(const DxvkSamplerKey& k) const { return k; }
        struct Stats { uint64_t liveCount = 0; };
        Stats getSamplerStats() const { return {}; }
    } dxvk_device_;
    ShimDxvkDevice* m_dxvkDevice = &dxvk_device_;
    struct { uint32_t depth = 0; uint32_t hazardRT = 0; } m_textureSlotTracking;   // depth: UpdateTextureBitmasks's, below
    struct { uint32_t hasAlphaSwizzle = 0; } m_rtSlotTracking;
    // the last draw's type (history, M6): none, so ApplyPrimitiveType always sets the topology
    struct { D3DPRIMITIVETYPE primitiveType = D3DPRIMITIVETYPE(0xFFFFFFFEu); } m_iaState;
    struct { D3D9SharedPushData shared; D3D9VsPushData vs; D3D9FfvsPushData ffvs; } m_pushData;
    D3D9SpecData m_specData;
    D9gD3D9Options m_d3d9Options;
    uint64_t m_samplerBindCount = 0;
    std::atomic<uint64_t> m_lastSamplerStats{0};
    constexpr static uint32_t SamplerCountBits = 12u;   // d3d9_device.h:1701
    // history-dependent members (the critic's M6), derived in derive_history()
    bool m_atocEnabled = false;          // d3d9_device.h: initial false
    bool m_alphaTestEnabled = false;
    bool m_validSampleMask = false;
    bool m_nvdbEnabled = false;
    float m_depthBiasScale = 0.0f;       // d3d9_device.h:1645
    DxvkDepthBiasRepresentation m_depthBiasRepresentation = { };
    VkImageLayout m_hazardLayout = VK_IMAGE_LAYOUT_GENERAL;
    // the draw's shader presence
    // D9G_TRANSCRIBED(UseProgrammableVS, "src/d3d9/d3d9_device.cpp:8490-8494")
    bool vs_bound = false, decl_bound = false, decl_has_position_t = false;
    bool UseProgrammableVS() { return vs_bound && decl_bound && !decl_has_position_t; }

#define D9G_SECTION_DEVH
#include "dxvk_extract.inc"
#undef D9G_SECTION_DEVH

    // the extracted members (section "shim")
    void UpdateAlphaToCoverangeAndAlphaTest();
    void BindMultiSampleState();
    void BindBlendState();
    void BindDepthStencilState();
    void BindRasterizerState();
    void BindDepthBias();
    uint32_t GetAlphaTestPrecision(ShimSurface* rt);
    void BindAlphaTestState();
    void BindSampler(DWORD Sampler);
    bool SamplerUsesBorderColor(DWORD Sampler) const;
    void UpdatePointMode(bool pointList);
    void ApplyPrimitiveType(D3DPRIMITIVETYPE PrimType);
    void d9g_prepare_depth_bounds();
    void d9g_depth_bias_representation_init();
    void d9g_depth_bias_representation();
    void d9g_depth_bias_scale(ShimSurface* ds);
    void d9g_reset_render_states(D3DPRESENT_PARAMETERS* pPresentationParameters, ShimRS& rs, const ShimLimits& limits);
    void d9g_reset_sampler_states(std::array<DWORD, SamplerStateCount>& state);
};

struct D9gSamplerPool {
    VkSamplerCreateInfo info{};
    std::vector<VkStructureType> chain;
    // the border colour component mapping DXVK chains (VK_EXT_border_color_swizzle), as recorded
    // while the create info's chain is alive: the state check's BORDER_SWIZZLE rule reads it
    bool swizzle_chained = false;
    VkComponentMapping swizzle{};
    VkBool32 swizzle_srgb = VK_FALSE;
    struct { VkDevice vkd() const { return VK_NULL_HANDLE; } } dev_;
    decltype(dev_)* m_device = &dev_;
    void d9g_record(const VkSamplerCreateInfo& i) {
        info = i;
        for (auto* n = static_cast<const VkBaseInStructure*>(i.pNext); n; n = n->pNext) {
            chain.push_back(n->sType);
            if (n->sType == VK_STRUCTURE_TYPE_SAMPLER_BORDER_COLOR_COMPONENT_MAPPING_CREATE_INFO_EXT) {
                const auto* m = reinterpret_cast<const VkSamplerBorderColorComponentMappingCreateInfoEXT*>(n);
                swizzle_chained = true;
                swizzle = m->components;
                swizzle_srgb = m->srgb;
            }
        }
        info.pNext = nullptr;   // the chain is the constructor's locals: recorded above, never read later
    }
};
struct D9gDxvkSampler {
    D9gSamplerPool* m_pool;
    DxvkSamplerKey m_key;
    D9gDxvkSampler(D9gSamplerPool* pool, const DxvkSamplerKey& key, uint16_t index);
    VkBorderColor determineBorderColorType(const VkSamplerCustomBorderColorCreateInfoEXT& info) const;
    static VkClearColorValue swizzleBorderColor(const VkClearColorValue& color, VkComponentMapping mapping);
    static float mapBorderColorComponent(const VkClearColorValue& color, const VkComponentMapping& mapping, VkComponentSwizzle which);
};

uint64_t d9g_dxvk_up_aligned(VkDeviceSize size);

#define D9G_SECTION_SHIM
#define D9G_SECTION_CTX
#define D9G_SECTION_GFX
#define D9G_SECTION_SAMPLER
#define D9G_SECTION_OPTIONS
#define D9G_SECTION_FREE
#define D9G_SECTION_FMT
#include "dxvk_extract.inc"
#undef D9G_SECTION_SHIM
#undef D9G_SECTION_CTX
#undef D9G_SECTION_GFX
#undef D9G_SECTION_SAMPLER
#undef D9G_SECTION_OPTIONS
#undef D9G_SECTION_FREE
#undef D9G_SECTION_FMT

}  // namespace dxvk

// ======================================================================== the driver
namespace d9g_ref {
using namespace dxvk;

namespace {

constexpr uint32_t kRS_ADAPTIVETESS_Y = 181, kRS_POINTSIZE = 154;

// The format table for these parameters, made once per (vendor, D24S8, D16S8): its constructor
// logs DXVK's remap lines, which the check needs only once.
const D9gVkFormatTable& format_table(const DxvkParams& p) {
    static std::map<std::tuple<uint32_t, bool, bool>, D9gVkFormatTable> tables;
    const auto key = std::make_tuple(p.vendor_id, p.d24s8_supported, p.d16s8_supported);
    auto it = tables.find(key);
    if (it == tables.end()) {
        D9gFmtAdapter a;
        a.vendor = p.vendor_id;
        it = tables.emplace(key, D9gVkFormatTable(&a, D9gFmtDxvkAdapter{p.d24s8_supported, p.d16s8_supported},
                                                  D9gD3D9Options())).first;
    }
    return it->second;
}

// A surface as DXVK holds it: its texture's description and the format table's mapping
// (D3D9CommonTexture's m_mapping, the device's LookupFormat: GetFormatMapping). A format the
// table maps to nothing is one DXVK refuses to create; a conversion format is not modelled.
std::string target(ShimSurface& s, const d9g::TargetDesc& t, const D9gVkFormatTable& table) {
    s.tex.m_desc.Format = D3D9Format(t.fmt);
    s.tex.m_desc.MipLevels = 1;
    s.tex.m_desc.MultiSample = D3DMULTISAMPLE_TYPE(t.ms);
    s.tex.m_desc.MultisampleQuality = t.quality;
    s.tex.mapping = table.GetFormatMapping(D3D9Format(t.fmt));
    s.level = t.level;
    s.texture = t.texture;
    if (!s.tex.mapping.IsValid()) return "format " + std::to_string(t.fmt) + ", which DXVK's format table maps to nothing here";
    if (s.tex.mapping.ConversionFormatInfo.Format != VK_FORMAT_UNDEFINED)
        return "format " + std::to_string(t.fmt) + ", a conversion format (not modelled)";
    return std::string();
}

// The format of a view of a surface's texture.
// D9G_TRANSCRIBED(CreateView.format, "src/d3d9/d3d9_common_texture.cpp:730-732")
VkFormat view_format(const D3D9_VK_FORMAT_MAPPING& m, bool srgb) {
    return PickSRGB(m.Format, m.FormatSrgb, srgb);   // ConversionFormatInfo refused in target()
}

// The sample count of a surface's image (its creation: DecodeMultiSampleType, d3d9_util.cpp,
// linked), or 0 where DXVK refuses the multisample type.
VkSampleCountFlags samples_of(const d9g::TargetDesc& t) {
    VkSampleCountFlagBits n = VK_SAMPLE_COUNT_1_BIT;
    if (FAILED(DecodeMultiSampleType(D3DMULTISAMPLE_TYPE(t.ms), t.quality, &n))) return 0;
    return VkSampleCountFlags(n);
}

// The magic render-state values DXVK's SetRenderState treats as driver hacks (:2511-2560,
// :2584-2630, :2636-2670): the members they move depend on the order of past calls.
bool magic_point_size(uint32_t v) {
    return v == uint32_t(D3D9Format::A2M1) || v == uint32_t(D3D9Format::A2M0) || v == 0x7fa05000u ||
           v == uint32_t(D3D9Format::INST) || v == uint32_t(D3D9Format::CENT);
}
bool magic_tess_y(uint32_t v) {
    return v == uint32_t(D3D9Format::ATOC) || v == uint32_t(D3D9Format::COPM) || v == uint32_t(D3D9Format::SSAA);
}

// D3D9PackedSemantic (d3d9_shader_analysis.h:51-71): usage in bits 0-3, index in bits 4-7.
uint8_t packed_semantic(uint8_t usage, uint8_t index) { return uint8_t((usage & 0xFu) | ((index & 0xFu) << 4)); }

// BindInputLayout (d3d9_device.cpp:7646-7738), the lambda's body: the vertex shader's input
// signature (the recorded one in the census, the input's own in the sweep), each declaration element
// mapped onto the signature entry of its semantic (D3D9InputSignature::find,
// d3d9_shader_analysis.h:139-160: the first entry with the packed semantic; a later element of the
// same semantic wins), an unmatched entry a null-stream dummy.
// Transcribed: the signature's type is dxbc-spirv's, which no compile here may read.
// D9G_TRANSCRIBED(BindInputLayout, "src/d3d9/d3d9_device.cpp:7667-7750, src/d3d9/d3d9_shader_analysis.h:80-166")
void bind_input_layout(D9gRecCtx& ctx, const d9g::PipelineInput& in, const Standins& st, std::string* refused) {
    std::vector<uint8_t> signature;   // packed semantics, D3D9InputSignature's entries in order
    for (const auto& [usage, index] : st.vs_signature.inputs) {
        if (index >= 16u) { *refused = "a vertex-shader input of usage index 16 or more"; return; }   // add()'s assert
        signature.push_back(packed_semantic(usage, index));
    }
    if (signature.size() > D9G_MAX_ATTRS) { *refused = "more than 16 vertex-shader inputs"; return; }
    const uint32_t attrCount = uint32_t(signature.size());                                   // :7668
    const uint32_t elementCount = in.n_elems;
    std::array<uint8_t, caps::InputRegisterCount * 2u> attrMap;                              // :7671-7672
    std::fill(attrMap.begin(), attrMap.end(), 0xffu);
    for (uint32_t i = 0u; i < elementCount; i++) {                                            // :7674-7685
        uint8_t usage = in.elems[i].usage;
        if (usage == D3DDECLUSAGE_POSITIONT) usage = D3DDECLUSAGE_POSITION;
        const uint8_t packed = packed_semantic(usage, in.elems[i].usage_index);
        uint32_t index = attrCount;   // find(): the first entry holding the packed semantic, else out of bounds
        for (uint32_t k = 0; k < attrCount; k++)
            if (signature[k] == packed) { index = k; break; }
        if (index < attrCount) attrMap[index] = uint8_t(i);
    }
    constexpr uint32_t NullStreamIdx = caps::MaxStreams;                                     // d3d9_device.h:220
    std::array<DxvkVertexInput, caps::InputRegisterCount * 2u> attrList = {};                // :7689
    std::array<DxvkVertexInput, caps::MaxStreams + 1u> bindList = {};                        // :7690
    std::array<uint16_t, caps::MaxStreams + 1u> vertexSizes = {};                             // :7691
    uint32_t bindMask = 0;
    for (uint32_t i = 0; i < attrCount; i++) {                                                // :7695-7716
        DxvkVertexAttribute attrib = {};
        attrib.location = i;
        if (attrMap[i] < elementCount) {
            const d9g::VertexElem& element = in.elems[attrMap[i]];
            if (element.stream >= caps::MaxStreams) { *refused = "a vertex element on stream 16 or more"; return; }
            attrib.binding = uint32_t(element.stream);
            attrib.format = DecodeDecltype(D3DDECLTYPE(element.type));
            attrib.offset = element.offset;
        } else {
            attrib.binding = NullStreamIdx;
            attrib.format = VK_FORMAT_R32G32B32A32_SFLOAT;
            attrib.offset = 0;
        }
        attrList[i] = DxvkVertexInput(attrib);
        const DxvkFormatInfo* fi = lookupFormatInfo(attrib.format);
        if (!fi) { *refused = "a vertex element type DXVK decodes to no format"; return; }
        vertexSizes[attrib.binding] = std::max(vertexSizes[attrib.binding], uint16_t(attrib.offset + fi->elementSize));
        bindMask |= 1u << attrib.binding;
    }
    uint32_t bindCount = 0u;
    for (auto i : bit::BitMask(bindMask)) {                                                   // :7722-7745
        DxvkVertexBinding binding = { };
        binding.binding = i;
        binding.extent = vertexSizes[i];
        if (likely(i < NullStreamIdx)) {
            // cStreamFreq[binding % MaxStreams]: stream 0's is the input's freq0, every other stream's
            // its stream_freq (d9g_state.h: the streams the declaration names)
            const uint32_t instanceData = i == 0 ? in.freq0 : in.stream_freq[i % caps::MaxStreams];
            if (instanceData & D3DSTREAMSOURCE_INSTANCEDATA) {
                binding.divisor = instanceData & 0x7fffffu;
                binding.inputRate = VK_VERTEX_INPUT_RATE_INSTANCE;
            } else {
                binding.divisor = 0u;
                binding.inputRate = VK_VERTEX_INPUT_RATE_VERTEX;
            }
        } else {
            binding.divisor = 0u;
            binding.inputRate = VK_VERTEX_INPUT_RATE_INSTANCE;
        }
        if (bindCount >= D9G_MAX_BINDINGS) { *refused = "more than four vertex bindings"; return; }
        bindList[bindCount++] = DxvkVertexInput(binding);
    }
    ctx.setInputLayout(attrCount, attrList.data(), bindCount, bindList.data());           // :7747-7749
}

// UpdateActiveHazardsRT (d3d9_device.cpp:6302-6352), transcribed (it reads PSShaderMasks() and
// D3D9 objects), then BindFramebuffer (:6694-6833), transcribed (PLAN.md 8.3: it reads
// PSShaderMasks(), shader reflection, and view objects).
//
// The hazards are evaluated on the draw's state: DXVK re-runs UpdateActiveHazardsRT whenever an
// input it reads changes (:1743, :6286-6291, :3953-3955, :2347-2365 via :6214-6217). The slots
// it walks are `rt_tex_stages` (the stages where the target's texture is bound: every one has
// render-target usage, rtUsage); `rt_tex_sampled` is canBeSampled with the level-0 and same-texture
// test (:6320-6325). The pixel shader's sampler and output masks are its reflection (Standins).
// wasHazard (the last state's hazardRT) is history (M6): taken as set, and *history marks every
// draw whose result rests on it.
//
// Refused, never approximated (the review's transcription findings 1 and 2): a bound target or
// depth-stencil surface at a mip level other than 0 (DXVK compares the texture's level-0 extent,
// GetImage()->info().extent, :6721-6725, :6750-6753, :6781-6785; the input carries the level's);
// a depth-stencil surface set while its texture is bound at a stage, whether or not it binds
// (UpdateActiveHazardsDS :6361-6413 runs on the surface set, and BindFramebuffer adds the depth
// aspect to the feedback loop whenever its hazard is unresolvable, :6815-6822, bound or not; that,
// the read-only layout of GetDepthStencilView(false), d3d9_subresource.h:107-109, and
// hazardState.DS :6422 are not transcribed); a multisample type DXVK refuses.
// D9G_TRANSCRIBED(UpdateActiveHazardsRT, "src/d3d9/d3d9_device.cpp:6302-6352")
// D9G_TRANSCRIBED(BindFramebuffer, "src/d3d9/d3d9_device.cpp:6694-6833")
void bind_framebuffer(D9gDxvkShim& d, const d9g::PipelineInput& in, const Standins& st, ShimSurface rt[4], ShimSurface* ds,
                      bool* feedback, bool* history, std::string* refused) {
    for (uint32_t i = 0; i < 4; i++)
        if (in.rt[i].bound && in.rt[i].level != 0) { *refused = "a render target at mip level " + std::to_string(in.rt[i].level); return; }
    if (ds && in.ds.level != 0) { *refused = "a depth-stencil surface at mip level " + std::to_string(in.ds.level); return; }
    for (uint32_t i = 0; i < 4; i++)
        if (in.rt[i].bound && !samples_of(in.rt[i])) { *refused = "a render target of a multisample type DXVK refuses"; return; }
    if (ds && !samples_of(in.ds)) { *refused = "a depth-stencil surface of a multisample type DXVK refuses"; return; }
    if (ds && in.ds_tex_sampled) { *refused = "a depth-stencil surface set while its texture is bound at a stage"; return; }
    uint32_t hazardRT = 0, unresolvableHazardRT = 0;   // m_textureSlotTracking, by sampler slot
    uint32_t hazardTargets = 0;                        // the targets whose texture a hazardRT slot holds
    for (uint32_t rtIdx = 0; rtIdx < 4; rtIdx++) {                                    // :6312 canBeSampled
        if (!in.rt[rtIdx].bound || !((in.rt_tex_sampled >> rtIdx) & 1u)) continue;  // :6320-6325
        const bool anyColorWrite = d.m_state.renderStates[ColorWriteIndex(rtIdx)] != 0;   // :6313
        const bool shaderWritesToRt = (st.fs_output_mask & (1u << rtIdx)) != 0;           // :6314
        for (uint32_t samplerIdx = 0; samplerIdx < 21; samplerIdx++) {                    // :6315 texMask & rtUsage
            if (!((in.rt_tex_stages >> samplerIdx) & 1u)) continue;
            const bool sampledInShader = !!(st.ps_sampler_mask & (1u << samplerIdx));     // :6329
            const bool wasHazard = true;                                                  // :6330 (history, M6)
            if (!sampledInShader && ((anyColorWrite && shaderWritesToRt) || !wasHazard))  // :6334-6335
                continue;
            if (!sampledInShader) *history = true;   // the outcome rests on wasHazard
            hazardRT |= 1u << samplerIdx;                                                 // :6338
            hazardTargets |= 1u << rtIdx;
            if (!sampledInShader) continue;                                               // :6341-6342
            if (!anyColorWrite || !shaderWritesToRt) continue;                            // :6345-6346
            unresolvableHazardRT |= 1u << samplerIdx;                                     // :6349
        }
    }
    (void)hazardRT;
    const uint32_t m = in.rt_tex_sampled & 0xFu;
    if (m & (m - 1u)) *history = true;   // the stages per target are not in the input: not exact
    D9gRecCtx::Targets attachments;
    const bool srgb = d.m_state.renderStates[D3DRS_SRGBWRITEENABLE];                 // :6699
    VkSampleCountFlags sampleCount = VK_SAMPLE_COUNT_1_BIT;                          // :6718
    VkExtent2D renderArea = {0u, 0u};
    const bool rt0bound = in.rt[0].bound;
    if (rt0bound) {                                                                   // :6721-6725 (no NULL format here)
        sampleCount = samples_of(in.rt[0]);
        renderArea = {in.rt[0].w, in.rt[0].h};
    }
    if (ds) {                                                                         // :6727
        uint32_t boundRTCount = 0;
        for (uint32_t i = 0; i < 4; i++)
            if (in.rt[i].bound) boundRTCount++;                                      // :6731-6737
        const bool rt0WrittenTo = (st.fs_output_mask & 1u) != 0 && d.m_state.renderStates[D3DRS_COLORWRITEENABLE] != 0;
        const bool noRT0Bound = !rt0bound;                                            // :6743
        const bool ignoreRT0 = boundRTCount == 1 && renderArea.width == 1 && renderArea.height == 1 && !rt0WrittenTo;
        const bool mismatch = in.ds.w < renderArea.width || in.ds.h < renderArea.height ||
                              samples_of(in.ds) != sampleCount;                      // :6750-6752
        const bool bindDS = !mismatch || noRT0Bound || ignoreRT0;                    // :6753
        if (bindDS) {
            if (noRT0Bound || ignoreRT0) {
                renderArea = {in.ds.w, in.ds.h};
                sampleCount = samples_of(in.ds);
            }
            // :6762-6774: hazardDS needs the DS texture sampled, refused above; so the writable view,
            // GetDepthStencilView(true) (d3d9_subresource.h:106-108), and no hazard layout
            attachments.depth.format = view_format(ds->tex.mapping, false);
            attachments.depth.layout = VK_IMAGE_LAYOUT_DEPTH_STENCIL_ATTACHMENT_OPTIMAL;
            attachments.depth.samples = VkSampleCountFlagBits(samples_of(in.ds));
        }
    }
    for (uint32_t i = 0u; i < 4; i++) {                                               // :6776
        if (!in.rt[i].bound) continue;                                                // HasRenderTargetBound
        if (in.rt[i].w != renderArea.width || in.rt[i].h != renderArea.height || samples_of(in.rt[i]) != sampleCount)
            continue;                                                                 // :6781-6784
        // :6789-6801 a hazardRT slot holds this RT's texture, at level 0
        const bool hasHazard = (hazardTargets >> i) & 1u;
        const bool writtenTo = ((st.fs_output_mask >> i) & 1u) && d.m_state.renderStates[ColorWriteIndex(i)] != 0;
        if (hasHazard && !writtenTo) continue;                                        // :6807-6808
        // :6812 GetRenderTargetView(srgb): an sRGB view only where the texture is sRGB-compatible
        // (Srgb && m_isSrgbCompatible, d3d9_subresource.h:27, :86-88; the transcription review's finding 1)
        // D9G_TRANSCRIBED(GetRenderTargetView, "src/d3d9/d3d9_subresource.h:27, src/d3d9/d3d9_subresource.h:79-90")
        attachments.color[i].format = view_format(rt[i].tex.mapping, srgb && rt[i].tex.IsSrgbCompatible());
        attachments.color[i].layout = VK_IMAGE_LAYOUT_COLOR_ATTACHMENT_OPTIMAL;
        attachments.color[i].swizzle = rt[i].tex.mapping.Swizzle;
        attachments.color[i].samples = VkSampleCountFlagBits(samples_of(in.rt[i]));
    }
    VkImageAspectFlags feedbackLoopAspects = 0u;                                      // :6814-6820
    if (d.m_hazardLayout == VK_IMAGE_LAYOUT_ATTACHMENT_FEEDBACK_LOOP_OPTIMAL_EXT && unresolvableHazardRT != 0)
        feedbackLoopAspects |= VK_IMAGE_ASPECT_COLOR_BIT;
    *feedback = unresolvableHazardRT != 0;
    d.m_ctx.bindRenderTargets(attachments, feedbackLoopAspects);                     // :6823-6830
}

}  // namespace

Standins standins_chosen(const d9g::PipelineInput& in, const RecordedReflection* recorded) {
    Standins s;
    if (recorded) {
        s.fs_output_mask = recorded->ps_rt_mask;
        s.vs_signature = recorded->vs_signature;
        s.ps_sampler_mask = recorded->ps_sampler_mask;
    } else {
        s.fs_output_mask = in.ps_rt_mask;
        for (uint32_t i = 0; i < in.vs_n_inputs && i < in.vs_inputs.size(); i++)
            s.vs_signature.inputs.emplace_back(in.vs_inputs[i].usage, in.vs_inputs[i].usage_index);
        s.ps_sampler_mask = in.ps_sampler_mask;
    }
    s.fs_sample_rate_shading = false;     // SM2/SM3 pixel shaders have no sample-rate input
    s.fs_exports_sample_mask = false;     // nor a sample-mask output
    s.gp_base_pipeline = false;           // the optimized pipeline, which DXVK draws with once compiled
    s.can_use_sample_locations = false;
    return s;
}

const std::vector<std::string>& standin_names() {
    static const std::vector<std::string> n = {"fs_sample_rate_shading", "fs_exports_sample_mask", "gp_base_pipeline",
                                               "can_use_sample_locations"};
    return n;
}

Standins standins_alternative(const d9g::PipelineInput& in, const RecordedReflection* recorded, size_t which) {
    Standins s = standins_chosen(in, recorded);
    switch (which) {
    case 0: s.fs_sample_rate_shading = !s.fs_sample_rate_shading; break;
    case 1: s.fs_exports_sample_mask = !s.fs_exports_sample_mask; break;
    case 2: s.gp_base_pipeline = !s.gp_base_pipeline; break;
    case 3: s.can_use_sample_locations = !s.can_use_sample_locations; break;
    default: throw std::runtime_error("no stand-in " + std::to_string(which));
    }
    return s;
}

uint32_t dxvk_null_stream_binding() { return caps::MaxStreams; }   // NullStreamIdx (d3d9_device.h:220)

static int g_poke = 0;
void set_poke(int which) { g_poke = which; }

std::vector<std::string> dxvk_features_read() {
    return {
#define D9G_FEATURE(member, name) name,
#define D9G_SECTION_FEATURES
#include "dxvk_extract.inc"
#undef D9G_SECTION_FEATURES
#undef D9G_FEATURE
    };
}

PipelineResult dxvk_pipeline(const d9g::PipelineInput& in, const uint32_t rs_full[256], const DxvkParams& p,
                             const Standins& st) {
    set_features(p);
    PipelineResult res;
    const D9gVkFormatTable& table = format_table(p);
    D9gDxvkShim d;
    D9gPipelineFacts& f = res.facts;
    d.m_ctx.cmd.vk.f = &f;
    std::memcpy(d.m_state.renderStates.v, rs_full, sizeof d.m_state.renderStates.v);
    d.adapter_.vendor = p.vendor_id;
    d.dxvk_device_.props.core.properties.vendorID = p.vendor_id;
    d.vs_bound = in.vs_id != 0;
    d.decl_bound = in.decl_id != 0 || in.fvf_decl;
    for (uint32_t e = 0; e < in.n_elems; e++)
        if (in.elems[e].usage == D3DDECLUSAGE_POSITIONT) d.decl_has_position_t = true;
    // an FVF declaration's elements (and an overflowing one's) are not in the input (the review's
    // transcription finding 7)
    if (in.fvf_decl) res.refused = "an FVF declaration (its elements are not in the input)";
    if (in.decl_overflow) res.refused = "a declaration of more than 16 elements";
    // Fixed-function processing is refused, never approximated (the transcription review's NIT
    // 3): DXVK's fixed-function vertex shader's input signature is g_ffIsgn
    // (d3d9_fixed_function.cpp:127-140, read at d3d9_device.cpp:7657-7659), and with no
    // declaration BindInputLayout binds nothing (:7759-7762); without a pixel shader DXVK binds its
    // fixed-function pixel shader (:3942-3948), whose masks are {ffpsTextures, 0x1}
    // (d3d9_device.h:1520-1523). None is transcribed, and d9g maps no such draw: one it mapped
    // fails as mapped where the reference does not model the state (vs_fixedFunction is 0 on
    // every tuple the reference does model).
    if (!d.UseProgrammableVS()) res.refused = "fixed-function vertex processing (g_ffIsgn and the FF vertex shader not transcribed)";
    if (!in.ps_id) res.refused = "fixed-function pixel processing (DXVK's FF pixel shader not transcribed)";
    // m_hazardLayout (the device constructor)
    // D9G_TRANSCRIBED(m_hazardLayout, "src/d3d9/d3d9_device.cpp:122-124")
    d.m_hazardLayout = d9g_features().extAttachmentFeedbackLoopLayout.attachmentFeedbackLoopLayout
        ? VK_IMAGE_LAYOUT_ATTACHMENT_FEEDBACK_LOOP_OPTIMAL_EXT : VK_IMAGE_LAYOUT_GENERAL;

    ShimSurface rt[4], ds;
    for (uint32_t i = 0; i < 4; i++) {
        if (!in.rt[i].bound) continue;
        const std::string why = target(rt[i], in.rt[i], table);
        if (!why.empty()) res.refused = "a render target of " + why;
        d.m_state.renderTargets[i].s = &rt[i];
        // SetRenderTargetInternal's alpha-swizzle bit
        // D9G_TRANSCRIBED(SetRenderTargetInternal.hasAlphaSwizzle, "src/d3d9/d3d9_device.cpp:1736")
        d.m_rtSlotTracking.hasAlphaSwizzle |= uint8_t(rt[i].tex.mapping.Swizzle.a == VK_COMPONENT_SWIZZLE_ONE) << i;
    }
    if (in.ds.bound) {
        const std::string why = target(ds, in.ds, table);
        if (!why.empty()) res.refused = "a depth-stencil surface of " + why;
        d.m_state.depthStencil.s = &ds;
    }
    // ---- history-dependent members, derived (the critic's M6)
    // the device constructor's depth-bias representation (:99-110, extracted), emitted to the
    // context (:112-116, transcribed: the EmitCs lambda's one call)
    d.d9g_depth_bias_representation_init();
    d.d9g_depth_bias_representation();
    // D9G_TRANSCRIBED(D3D9DeviceEx.DepthBiasRepresentation.emit, "src/d3d9/d3d9_device.cpp:112-116")
    d.m_ctx.setDepthBiasRepresentation(d.m_depthBiasRepresentation);
    // SetDepthStencilSurface's scale (:1814-1823): exact with a DS bound; without one DXVK keeps
    // the scale of the last DS set, which no draw input says (and no depth attachment uses it).
    if (in.ds.bound) d.d9g_depth_bias_scale(&ds);
    // SetRenderTargetInternal(0): the alpha precision (:1707) and the valid sample mask (:1761-1768)
    // D9G_TRANSCRIBED(SetRenderTargetInternal.rt0, "src/d3d9/d3d9_device.cpp:1707, src/d3d9/d3d9_device.cpp:1761-1768")
    if (in.rt[0].bound) {
        d.m_specData.setAlphaPrecision(d.GetAlphaTestPrecision(&rt[0]));
        d.m_validSampleMask = rt[0].tex.m_desc.MultiSample > D3DMULTISAMPLE_NONMASKABLE;
    }
    // m_nvdbEnabled: SetRenderState(ADAPTIVETESS_X) (:2637-2665) keeps vendor == NVIDIA && X == NVDB
    // D9G_TRANSCRIBED(m_nvdbEnabled, "src/d3d9/d3d9_device.cpp:2637-2665")
    d.m_nvdbEnabled = p.vendor_id == uint32_t(DxvkGpuVendor::Nvidia) && d.IsNVDepthBoundsTestEnabled();
    // m_atocEnabled, m_alphaTestEnabled: UpdateAlphaToCoverangeAndAlphaTest runs on every change of
    // its inputs except the magic values' own orders (:2504-2553, :2595-2633). It does not run
    // again when POINTSIZE (AMD) or ADAPTIVETESS_Y (other vendors) moves off an ATOC magic value
    // to an ordinary non-zero one, so m_atocEnabled can stay true from a past draw while this
    // draw's state holds no magic value (the transcription review's NIT 5). It reaches a fact only
    // through a multisampled RT0 (UpdateAlphaToCoverangeAndAlphaTest's sample-count test), so a
    // multisampled RT0 marks the result history, as the magic values themselves do.
    d.UpdateAlphaToCoverangeAndAlphaTest();
    // D9G_TRANSCRIBED(SetRenderState.magic, "src/d3d9/d3d9_device.cpp:2504-2553, src/d3d9/d3d9_device.cpp:2595-2633")
    if (magic_point_size(rs_full[kRS_POINTSIZE]) || magic_tess_y(rs_full[kRS_ADAPTIVETESS_Y])) res.history_standin = true;
    if (in.rt[0].bound && samples_of(in.rt[0]) > VK_SAMPLE_COUNT_1_BIT) res.history_standin = true;
    // the push data as SetRenderState and ResetState leave it (alphaRef, a uint8_t; the point
    // sizes, skipped for the magic values above)
    // D9G_TRANSCRIBED(SetRenderState.pushData, "src/d3d9/d3d9_device.cpp:2460-2462, src/d3d9/d3d9_device.cpp:2504-2562")
    d.m_pushData.shared.alphaRef = d.m_state.renderStates[D3DRS_ALPHAREF];
    d.m_pushData.vs.pointSize = d.EncodePointSize(d.m_state.renderStates[D3DRS_POINTSIZE]);
    d.m_pushData.vs.pointSizeMin = d.EncodePointSize(d.m_state.renderStates[D3DRS_POINTSIZE_MIN]);
    d.m_pushData.vs.pointSizeMax = d.EncodePointSize(d.m_state.renderStates[D3DRS_POINTSIZE_MAX]);
    // the viewport SetRenderTarget(0) leaves (RT0's extent, depth 0-1), which the point scale
    // reads; a later SetViewport is not in the input (and no compared fact reads the viewport)
    // D9G_TRANSCRIBED(SetRenderTargetInternal.viewport, "src/d3d9/d3d9_device.cpp:1669-1700")
    d.m_state.viewport = D3DVIEWPORT9{0, 0, in.rt[0].w, in.rt[0].h, 0.0f, 1.0f};

    // ---- PrepareDraw (:7365-7616), the pipeline half, in its order
    bool hazard_history = false;
    std::string fb_refused, il_refused;
    bind_framebuffer(d, in, st, rt, in.ds.bound ? &ds : nullptr, &res.rt_feedback, &hazard_history, &fb_refused);
    if (hazard_history) res.history_standin = true;
    d.BindBlendState();
    d.BindDepthStencilState();
    d.BindRasterizerState();
    d.BindDepthBias();
    d.BindMultiSampleState();
    d.BindAlphaTestState();
    d.m_dirty.set(D3D9DeviceDirtyFlag::PointScale);
    d.UpdatePointMode(in.prim == D3DPT_POINTLIST);
    bind_input_layout(d.m_ctx, in, st, &il_refused);
    d.m_dirty.set(D3D9DeviceDirtyFlag::DepthBounds);
    d.d9g_prepare_depth_bounds();
    d.ApplyPrimitiveType(D3DPRIMITIVETYPE(in.prim));
    res.rs_read = d.m_state.renderStates.read;
    if (res.refused.empty()) res.refused = !fb_refused.empty() ? fb_refused : il_refused;
    if (!res.refused.empty()) return res;

    // ---- the pipeline DXVK builds from its state vector (dxvk_graphics.cpp)
    D9gDevice dev;
    D9gShaders sh;
    sh.vs = &sh;
    sh.fs = in.ps_id ? &sh : nullptr;
    sh.fs_output_mask = st.fs_output_mask;
    sh.fs_sample_rate_shading = st.fs_sample_rate_shading;
    sh.fs_exports_sample_mask = st.fs_exports_sample_mask;
    const DxvkGraphicsPipelineStateInfo& gs = d.m_ctx.m_state.gp.state;
    D9gFoState fo(&dev, gs, sh);
    D9gPrState pr(&dev, gs, sh);
    D9gFsState fsst(&dev, gs);

    // ---- the static facts: the pipeline's create info
    f.v[PF_ia_topology] = gs.ia.primitiveTopology();                            // dxvk_graphics.cpp:80
    f.v[PF_ia_primitiveRestartEnable] = gs.ia.primitiveRestart();                // :81
    f.v[PF_vi_attributeCount] = gs.il.attributeCount();
    for (uint32_t i = 0; i < gs.il.attributeCount() && i < D9G_MAX_ATTRS; i++) {
        const DxvkIlAttribute& a = gs.ilAttributes[i];
        f.v[PF_vi_attr0_binding + 3 * i] = a.binding();
        f.v[PF_vi_attr0_format + 3 * i] = a.format();
        f.v[PF_vi_attr0_offset + 3 * i] = a.offset();
        f.v[PF_vi_attr0_location + i] = a.location();
    }
    f.v[PF_vi_bindingCount] = gs.il.bindingCount();
    for (uint32_t i = 0; i < gs.il.bindingCount() && i < D9G_MAX_BINDINGS; i++) {
        const DxvkIlBinding& b = gs.ilBindings[i];
        f.v[PF_vi_bind0_binding + 4 * i] = b.binding();
        // the pipeline's stride is 0 (setInputLayout) and so dynamic (useDynamicVertexStrides):
        // the draw binds its stream's stride; the null stream has no buffer bound
        // D9G_TRANSCRIBED(BindVertexBuffer.stride, "src/d3d9/d3d9_device.cpp:3172, src/d3d9/d3d9_device.cpp:7767-7792")
        f.v[PF_vi_bind0_stride + 4 * i] = b.stride() ? b.stride()
                                        : b.binding() == 0 ? in.stride0
                                        : b.binding() < caps::MaxStreams ? in.stream_stride[b.binding()] : 0u;
        f.v[PF_vi_bind0_inputRate + 4 * i] = b.inputRate();
        f.v[PF_vi_bind0_divisor + 4 * i] = b.divisor();
    }
    f.v[PF_rs_depthClampEnable] = pr.rsInfo.depthClampEnable;
    f.v[PF_rs_depthClipEnable] = pr.rsDepthClipInfo.depthClipEnable;
    f.v[PF_rs_rasterizerDiscardEnable] = pr.rsInfo.rasterizerDiscardEnable;
    f.v[PF_rs_polygonMode] = pr.rsInfo.polygonMode;
    f.v[PF_rs_cullMode] = pr.rsInfo.cullMode;
    f.v[PF_rs_frontFace] = pr.rsInfo.frontFace;
    f.v[PF_rs_lineWidth] = d9g_fbits(pr.rsInfo.lineWidth);
    f.v[PF_rs_depthBiasEnable] = pr.rsInfo.depthBiasEnable;
    f.v[PF_ms_rasterizationSamples] = fo.msInfo.rasterizationSamples;
    f.v[PF_ms_sampleShadingEnable] = fo.msInfo.sampleShadingEnable;
    f.v[PF_ms_minSampleShading] = d9g_fbits(fo.msInfo.minSampleShading);
    f.v[PF_ms_sampleMask] = fo.msSampleMask;
    f.v[PF_ms_alphaToCoverageEnable] = fo.msInfo.alphaToCoverageEnable;
    f.v[PF_ms_alphaToOneEnable] = fo.msInfo.alphaToOneEnable;
    // the fragment-shader state's depth-stencil create info (extracted: its members' initialisers)
    f.v[PF_ds_depthTestEnable] = fsst.dsInfo.depthTestEnable;
    f.v[PF_ds_depthWriteEnable] = fsst.dsInfo.depthWriteEnable;
    f.v[PF_ds_depthCompareOp] = fsst.dsInfo.depthCompareOp;
    f.v[PF_ds_depthBoundsTestEnable] = fsst.dsInfo.depthBoundsTestEnable;
    f.v[PF_ds_stencilTestEnable] = fsst.dsInfo.stencilTestEnable;
    const VkStencilOpState* faces[2] = {&fsst.dsInfo.front, &fsst.dsInfo.back};
    for (int k = 0; k < 2; k++) {
        const int o = k * (PF_ds_back_failOp - PF_ds_front_failOp);
        f.v[PF_ds_front_failOp + o] = faces[k]->failOp;
        f.v[PF_ds_front_passOp + o] = faces[k]->passOp;
        f.v[PF_ds_front_depthFailOp + o] = faces[k]->depthFailOp;
        f.v[PF_ds_front_compareOp + o] = faces[k]->compareOp;
        f.v[PF_ds_front_compareMask + o] = faces[k]->compareMask;
        f.v[PF_ds_front_writeMask + o] = faces[k]->writeMask;
    }
    f.v[PF_cb_logicOpEnable] = fo.cbInfo.logicOpEnable;
    f.v[PF_cb_logicOp] = fo.cbInfo.logicOp;
    f.v[PF_cb_attachmentCount] = fo.cbInfo.attachmentCount;
    for (uint32_t i = 0; i < 4; i++) {
        const VkPipelineColorBlendAttachmentState& a = fo.cbAttachments[i];
        const uint32_t o = (PF_cb1_blendEnable - PF_cb0_blendEnable) * i;
        f.v[PF_cb0_blendEnable + o] = a.blendEnable;
        f.v[PF_cb0_srcColorBlendFactor + o] = a.srcColorBlendFactor;
        f.v[PF_cb0_dstColorBlendFactor + o] = a.dstColorBlendFactor;
        f.v[PF_cb0_colorBlendOp + o] = a.colorBlendOp;
        f.v[PF_cb0_srcAlphaBlendFactor + o] = a.srcAlphaBlendFactor;
        f.v[PF_cb0_dstAlphaBlendFactor + o] = a.dstAlphaBlendFactor;
        f.v[PF_cb0_alphaBlendOp + o] = a.alphaBlendOp;
        f.v[PF_cb0_colorWriteMask + o] = a.colorWriteMask;
        f.v[PF_rt0_colorFormat + o] = i < fo.rtInfo.colorAttachmentCount ? fo.rtColorFormats[i] : 0;
    }
    f.v[PF_rt_colorAttachmentCount] = fo.rtInfo.colorAttachmentCount;
    f.v[PF_rt_depthAttachmentFormat] = fo.rtInfo.depthAttachmentFormat;
    f.v[PF_rt_stencilAttachmentFormat] = fo.rtInfo.stencilAttachmentFormat;
    f.v[PF_rt_feedbackLoop] = fo.feedbackLoop;

    // ---- the dynamic state DXVK records at the draw over them: the pipeline's flags (the
    // shaders' metadata, stand-ins), updateGraphicsPipelineState's choice of dynamic state
    // (extracted), every state dirty (as after any change), then updateDynamicState's blocks
    // (extracted) in their order, each recording only where its state is dynamic
    D9gRecCtx& c = d.m_ctx;
    // D9G_TRANSCRIBED(DxvkGraphicsPipeline.flags, "src/dxvk/dxvk_graphics.cpp:1055-1060")
    if (st.fs_sample_rate_shading) c.m_state.gp.flags.set(DxvkGraphicsPipelineFlag::HasSampleRateShading);
    if (st.fs_exports_sample_mask) c.m_state.gp.flags.set(DxvkGraphicsPipelineFlag::HasSampleMaskExport);
    c.d9g_base_pipeline = st.gp_base_pipeline;
    c.d9g_can_use_sample_locations = st.can_use_sample_locations;
    c.d9g_dynamic_flags();
    c.m_flags.set(DxvkContextFlag::GpDirtyDepthClip, DxvkContextFlag::GpDirtyMultisampleState,
                  DxvkContextFlag::GpDirtyRasterizerState, DxvkContextFlag::GpDirtyDepthTest,
                  DxvkContextFlag::GpDirtyStencilTest, DxvkContextFlag::GpDirtyDepthBias,
                  DxvkContextFlag::GpDirtyDepthBounds);
    if (g_poke == 1) {   // the liveness control: the emission must carry DXVK's value
        DxvkDepthStencilState& dss = c.m_state.dyn.depthStencilState;
        dss.setDepthCompareOp(dss.depthCompareOp() == VK_COMPARE_OP_NEVER ? VK_COMPARE_OP_ALWAYS : VK_COMPARE_OP_NEVER);
    }
    c.d9g_dyn_depth_clip();
    c.d9g_dyn_multisample();
    c.d9g_dyn_rasterizer();
    c.d9g_dyn_depth_test();
    c.d9g_dyn_stencil_test();
    c.d9g_dyn_depth_bias();
    c.d9g_dyn_depth_bounds();

    // ---- the facts that are no Vulkan state
    f.v[PF_rt_unresolvableHazard] = res.rt_feedback;
    f.v[PF_vs_fixedFunction] = !d.UseProgrammableVS();
    res.has_ds = fo.rtInfo.depthAttachmentFormat != VK_FORMAT_UNDEFINED;
    res.ds_stencil = fo.rtInfo.stencilAttachmentFormat != VK_FORMAT_UNDEFINED;
    // shader-side values (spec data and push data; their layout is the shader route's, OPEN-1)
    f.v[PF_lg_alphaCompareOp] = d.m_specData.alphaTest & 0xFu;                    // setAlphaCompareOp: bits 0-3
    f.v[PF_lg_alphaPrecision] = d.m_specData.alphaTest >> 4;                      // setAlphaPrecision: bits 4-7
    f.v[PF_lg_alphaRef] = d.m_pushData.shared.alphaRef;
    f.v[PF_lg_pointSize] = d.m_pushData.vs.pointSize;
    f.v[PF_lg_pointSizeMin] = d.m_pushData.vs.pointSizeMin;
    f.v[PF_lg_pointSizeMax] = d.m_pushData.vs.pointSizeMax;
    f.v[PF_lg_pointSprite] = d.m_specData.enablePointSprite;
    f.v[PF_lg_pointScale] = d.m_specData.enablePointScale;
    f.v[PF_lg_flatShading] = gs.rs.flatShading();
    return res;
}

SamplerResult dxvk_sampler(const d9g::SamplerInput& in, const DxvkParams& p) {
    set_features(p);
    SamplerResult res;
    const D9gVkFormatTable& table = format_table(p);
    D9gDxvkShim d;
    ShimTexture tex;
    tex.m_desc.Format = D3D9Format(in.tex_fmt);
    tex.m_desc.MipLevels = in.levels;
    tex.cube = in.cube;
    tex.mapping = table.GetFormatMapping(D3D9Format(in.tex_fmt));
    if (in.tex && !tex.mapping.IsValid()) res.refused = "a texture of a format DXVK's format table maps to nothing here";
    if (in.tex && tex.mapping.ConversionFormatInfo.Format != VK_FORMAT_UNDEFINED) res.refused = "a conversion-format texture";
    for (int s = 0; s < 2; s++) {
        // GetSampleView(srgb): CreateView's format (view_format) and the mapping's swizzle
        tex.views[s].inf.format = view_format(tex.mapping, s != 0);
        tex.views[s].inf.swizzle = tex.mapping.Swizzle;
    }
    for (uint32_t i = 0; i < 14; i++) d.m_state.samplerStates[0][i] = in.ss[i];
    d.m_state.textures[0] = in.tex ? &tex : nullptr;
    // the slot's depth-compare bit: UpdateTextureBitmasks copies the bound texture's IsShadow()
    // (m_shadow = DetermineShadowState(), extracted); an unbind keeps the last texture's (history)
    // D9G_TRANSCRIBED(UpdateTextureBitmasks.depth, "src/d3d9/d3d9_device.cpp:6266-6273, src/d3d9/d3d9_common_texture.cpp:41")
    if (in.tex) d.m_textureSlotTracking.depth = tex.DetermineShadowState() ? 1u : 0u;
    else res.history = true;
    d.BindSampler(0);
    if (d.m_ctx.samplers.empty()) throw std::runtime_error("BindSampler bound no sampler");
    const DxvkSamplerKey& key = d.m_ctx.samplers.back().second;
    res.has_border = key.u.p.hasBorder;
    D9gSamplerPool pool;
    D9gDxvkSampler smp(&pool, key, 0);
    const VkSamplerCreateInfo& s = pool.info;
    D9gSamplerFacts& f = res.facts;
    f.v[SF_sm_flags] = s.flags;
    f.v[SF_sm_magFilter] = s.magFilter;
    f.v[SF_sm_minFilter] = s.minFilter;
    f.v[SF_sm_mipmapMode] = s.mipmapMode;
    f.v[SF_sm_addressModeU] = s.addressModeU;
    f.v[SF_sm_addressModeV] = s.addressModeV;
    f.v[SF_sm_addressModeW] = s.addressModeW;
    f.v[SF_sm_mipLodBias] = d9g_fbits(s.mipLodBias);
    f.v[SF_sm_anisotropyEnable] = s.anisotropyEnable;
    f.v[SF_sm_maxAnisotropy] = d9g_fbits(s.maxAnisotropy);
    f.v[SF_sm_compareEnable] = s.compareEnable;
    f.v[SF_sm_compareOp] = s.compareOp;
    f.v[SF_sm_minLod] = d9g_fbits(s.minLod);
    f.v[SF_sm_maxLod] = d9g_fbits(s.maxLod);
    f.v[SF_sm_borderColor] = s.borderColor;
    f.v[SF_sm_unnormalizedCoordinates] = s.unnormalizedCoordinates;
    for (VkStructureType t : pool.chain) {
        if (t == VK_STRUCTURE_TYPE_SAMPLER_REDUCTION_MODE_CREATE_INFO) f.v[SF_sm_pNextReduction] = 1;
        if (t == VK_STRUCTURE_TYPE_SAMPLER_CUSTOM_BORDER_COLOR_CREATE_INFO_EXT) f.v[SF_sm_pNextCustomBorder] = 1;
        if (t == VK_STRUCTURE_TYPE_SAMPLER_BORDER_COLOR_COMPONENT_MAPPING_CREATE_INFO_EXT) f.v[SF_sm_pNextBorderSwizzle] = 1;
    }
    // the identity mapping: each component VK_COMPONENT_SWIZZLE_IDENTITY or its own channel
    auto identity = [](const VkComponentMapping& m) {
        auto own = [](VkComponentSwizzle s, VkComponentSwizzle self) { return s == VK_COMPONENT_SWIZZLE_IDENTITY || s == self; };
        return own(m.r, VK_COMPONENT_SWIZZLE_R) && own(m.g, VK_COMPONENT_SWIZZLE_G) && own(m.b, VK_COMPONENT_SWIZZLE_B) &&
               own(m.a, VK_COMPONENT_SWIZZLE_A);
    };
    if (pool.swizzle_chained) {
        const VkComponentMapping& m = pool.swizzle;
        res.border_swizzle_identity = identity(m);
        res.border_swizzle_srgb = pool.swizzle_srgb != VK_FALSE;
        res.border_swizzle = {uint32_t(m.r), uint32_t(m.g), uint32_t(m.b), uint32_t(m.a)};
    }
    if (key.u.p.hasBorder) {
        // DXVK's view of the texture, from the sampler key (the defaults, IDENTITY and no format,
        // where BindSampler had no view to set)
        const VkComponentMapping view = {VkComponentSwizzle(key.u.p.viewSwizzleR), VkComponentSwizzle(key.u.p.viewSwizzleG),
                                         VkComponentSwizzle(key.u.p.viewSwizzleB), VkComponentSwizzle(key.u.p.viewSwizzleA)};
        res.view_swizzle = {uint32_t(view.r), uint32_t(view.g), uint32_t(view.b), uint32_t(view.a)};
        res.view_identity = identity(view);
        const DxvkFormatInfo* vf = lookupFormatInfo(VkFormat(key.u.p.viewFormat));
        res.view_srgb = vf && vf->flags.test(DxvkFormatFlag::ColorSpaceSrgb);
        // DXVK's colour as its constructor makes it (the view's inverse swizzle), and its own type
        // decision on a device with custom border colours: standard only by an exact match
        VkSamplerCustomBorderColorCreateInfoEXT colour = {VK_STRUCTURE_TYPE_SAMPLER_CUSTOM_BORDER_COLOR_CREATE_INFO_EXT};
        colour.customBorderColor = D9gDxvkSampler::swizzleBorderColor(key.borderColor, view);
        colour.format = VkFormat(key.u.p.viewFormat);
        g_features.extCustomBorderColor.customBorderColors = VK_TRUE;
        g_features.extCustomBorderColor.customBorderColorWithoutFormat = VK_TRUE;
        const VkBorderColor type = smp.determineBorderColorType(colour);
        set_features(p);
        res.border_exact_standard = type == VK_BORDER_COLOR_FLOAT_TRANSPARENT_BLACK || type == VK_BORDER_COLOR_FLOAT_OPAQUE_BLACK ||
                                    type == VK_BORDER_COLOR_FLOAT_OPAQUE_WHITE;
    }
    return res;
}

void dxvk_defaults(uint32_t rs[256], bool set[256], uint32_t ss[14], const DxvkParams& p, bool auto_depth) {
    D9gDxvkShim d;
    ShimRS r;
    for (uint32_t i = 0; i < 256; i++) r.v[i] = 0xDEADBEEFu;
    ShimLimits limits{{1.0f, p.point_size_max}};
    D3DPRESENT_PARAMETERS pp{};
    pp.EnableAutoDepthStencil = auto_depth;
    d.d9g_reset_render_states(&pp, r, limits);
    for (uint32_t i = 0; i < 256; i++) {
        set[i] = r.v[i] != 0xDEADBEEFu;
        rs[i] = set[i] ? r.v[i] : 0u;
    }
    std::array<DWORD, SamplerStateCount> st{};
    d.d9g_reset_sampler_states(st);
    for (uint32_t i = 0; i < 14; i++) ss[i] = st[i];
}

d9g::SamplerOptions dxvk_sampler_options() {
    D9gD3D9Options o;
    d9g::SamplerOptions s;
    s.sampler_anisotropy = o.samplerAnisotropy;
    s.seamless_cubes = o.seamlessCubes;
    s.sampler_lod_bias = o.samplerLodBias;
    s.clamp_negative_lod_bias = o.clampNegativeLodBias;
    return s;
}

uint32_t dxvk_vertex_count(uint32_t prim, uint32_t count) { return GetVertexCount(D3DPRIMITIVETYPE(prim), count); }

// D3D9VertexDecl::Classify's per-stream size, transcribed: GetSize(0) is the largest
// Offset + GetDecltypeSize(Type) of stream 0's elements.
// D9G_TRANSCRIBED(D3D9VertexDecl.GetSize, "src/d3d9/d3d9_vertex_declaration.cpp:355-359")
uint32_t dxvk_decl_size0(const d9g::PipelineInput& in) {
    uint32_t size = 0;
    for (uint32_t e = 0; e < in.n_elems; e++) {
        const d9g::VertexElem& el = in.elems[e];
        if (el.stream == 0 && el.type != D3DDECLTYPE_UNUSED)
            size = std::max<uint32_t>(size, el.offset + GetDecltypeSize(D3DDECLTYPE(el.type)));
    }
    return size;
}

uint32_t dxvk_up_data_size(uint32_t vertices, uint32_t stride) {
    D9gDxvkShim d;
    return d.GetUPDataSize(vertices, stride);
}

uint32_t dxvk_up_buffer_size(uint32_t vertices, uint32_t stride, const d9g::PipelineInput& decl) {
    D9gDxvkShim d;
    d.decl_.size0 = dxvk_decl_size0(decl);   // the declaration GetUPBufferSize reads (m_state.vertexDecl)
    return d.GetUPBufferSize(vertices, stride);
}

void dxvk_fill_up(void* buffer, const void* data, uint32_t data_size, uint32_t buffer_size) {
    D9gDxvkShim d;
    d.FillUPVertexBuffer(buffer, data, data_size, buffer_size);
}

uint64_t dxvk_up_aligned(uint64_t size) { return d9g_dxvk_up_aligned(size); }

uint32_t dxvk_decltype_format(uint8_t type) { return uint32_t(DecodeDecltype(D3DDECLTYPE(type))); }

// The same enumerants as ref_sdl.c's d9g_sdl_enum_values, from DXVK's bundled vulkan_core.h:
// the pytest holds the two headers to equal values for every enumerant the facts use.
void dxvk_enum_values(uint32_t* out, int n) {
    const uint32_t vals[] = {
        VK_FORMAT_B8G8R8A8_UNORM, VK_FORMAT_R8G8B8A8_UNORM, VK_FORMAT_D24_UNORM_S8_UINT, VK_FORMAT_D32_SFLOAT_S8_UINT,
        VK_FORMAT_R32G32B32_SFLOAT, VK_FORMAT_R32G32_SFLOAT, VK_FORMAT_R32_SFLOAT, VK_FORMAT_R32G32B32A32_SFLOAT,
        VK_BLEND_FACTOR_SRC_ALPHA, VK_BLEND_FACTOR_ONE_MINUS_SRC_ALPHA, VK_BLEND_FACTOR_CONSTANT_ALPHA,
        VK_COMPARE_OP_LESS_OR_EQUAL, VK_COMPARE_OP_ALWAYS, VK_STENCIL_OP_KEEP, VK_CULL_MODE_BACK_BIT,
        VK_FRONT_FACE_CLOCKWISE, VK_POLYGON_MODE_LINE, VK_PRIMITIVE_TOPOLOGY_TRIANGLE_FAN,
        VK_PRIMITIVE_TOPOLOGY_TRIANGLE_LIST, VK_SAMPLER_ADDRESS_MODE_CLAMP_TO_BORDER,
        VK_SAMPLER_ADDRESS_MODE_MIRROR_CLAMP_TO_EDGE, VK_BORDER_COLOR_FLOAT_TRANSPARENT_BLACK,
        VK_DEPTH_BIAS_REPRESENTATION_LEAST_REPRESENTABLE_VALUE_FORMAT_EXT, VK_SAMPLE_COUNT_1_BIT,
        VK_VERTEX_INPUT_RATE_INSTANCE, VK_SAMPLER_MIPMAP_MODE_LINEAR, VK_FILTER_LINEAR,
        VK_BORDER_COLOR_FLOAT_OPAQUE_BLACK, VK_BORDER_COLOR_FLOAT_OPAQUE_WHITE,
    };
    for (int k = 0; k < n && k < int(sizeof vals / sizeof vals[0]); k++) out[k] = vals[k];
}

}  // namespace d9g_ref
