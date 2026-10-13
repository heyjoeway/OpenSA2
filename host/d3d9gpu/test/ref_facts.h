/* ref_facts.h: the Vulkan state the second host's state check compares (PLAN.md section 9.1,
 * private/generated/s5/r2b/PLAN.md), as named 32-bit slots. Both reference sides fill it:
 *   test/ref_dxvk.cpp  the state DXVK 3.1.1 builds, from DXVK's own code;
 *   test/ref_sdl.c     the state SDL 3.4.16 builds from d9g's SDL_gpu description, from SDL's own code.
 * The two meet only here: plain uint32_t, Vulkan enumerants as their numbers (ABI-stable; the
 * pytest holds the two bundled vulkan_core.h to equal values for every enumerant used), floats
 * as their bit patterns so equality is bitwise.
 *
 * C and C++ both include it. Every slot has a name (the X-lists), so the comparator
 * (test/state_check.cpp) reports and perturbs facts by name.
 *
 * rt_unresolvableHazard is not Vulkan state: it is the hazard decision behind rt_feedbackLoop
 * (DXVK's unresolvableHazardRT != 0; d9g's PipelineDesc::rt_feedback), compared so that the
 * decision is held whether or not the device has the feedback-loop layout (the critic's N10).
 * Like the lg_ slots, the check fills d9g's side from the description, not from SDL.
 * vs_fixedFunction is not Vulkan state either: whether DXVK draws with its fixed-function vertex
 * shader (!UseProgrammableVS(), d3d9_device.cpp:8490-8494). d9g maps no fixed-function draw, so
 * its side is always 0 and a mapped tuple DXVK draws fixed-function differs (the review's N7).
 * vi_attrN_location: the check fills the SDL side from the description's attribute locations,
 * which SDL's VULKAN_CreateGraphicsPipeline copies unchanged into its
 * VkVertexInputAttributeDescription (SDL_gpu_vulkan.c:6444 in the pinned tarball, 6466 patched);
 * test/ref_sdl.c's fact fill predates the fact and leaves it 0.
 */
#ifndef D9G_REF_FACTS_H
#define D9G_REF_FACTS_H

#include <stdint.h>

#define D9G_MAX_ATTRS 16
#define D9G_MAX_BINDINGS 4

/* One attribute (vertex input, at DXVK's state-vector level: the critic's M2c). Its location is
 * compared too (U9: the vertex input follows the vertex shader's input signature, attribute i at
 * location i), in a block of its own after the bindings (D9G_LOCATION), so the attribute and
 * binding blocks keep their layout. */
#define D9G_ATTR(X, i) X(vi_attr##i##_binding) X(vi_attr##i##_format) X(vi_attr##i##_offset)
#define D9G_LOCATION(X, i) X(vi_attr##i##_location)
#define D9G_BINDING(X, i) X(vi_bind##i##_binding) X(vi_bind##i##_stride) X(vi_bind##i##_inputRate) X(vi_bind##i##_divisor)
#define D9G_CB(X, i) X(cb##i##_blendEnable) X(cb##i##_srcColorBlendFactor) X(cb##i##_dstColorBlendFactor) \
    X(cb##i##_colorBlendOp) X(cb##i##_srcAlphaBlendFactor) X(cb##i##_dstAlphaBlendFactor) X(cb##i##_alphaBlendOp) \
    X(cb##i##_colorWriteMask) X(rt##i##_colorFormat)
#define D9G_STENCIL(X, f) X(ds_##f##_failOp) X(ds_##f##_passOp) X(ds_##f##_depthFailOp) X(ds_##f##_compareOp) \
    X(ds_##f##_compareMask) X(ds_##f##_writeMask)

#define D9G_PIPELINE_FACTS(X) \
    X(ia_topology) X(ia_primitiveRestartEnable) \
    X(vi_attributeCount) \
    D9G_ATTR(X, 0) D9G_ATTR(X, 1) D9G_ATTR(X, 2) D9G_ATTR(X, 3) D9G_ATTR(X, 4) D9G_ATTR(X, 5) D9G_ATTR(X, 6) \
    D9G_ATTR(X, 7) D9G_ATTR(X, 8) D9G_ATTR(X, 9) D9G_ATTR(X, 10) D9G_ATTR(X, 11) D9G_ATTR(X, 12) D9G_ATTR(X, 13) \
    D9G_ATTR(X, 14) D9G_ATTR(X, 15) \
    X(vi_bindingCount) D9G_BINDING(X, 0) D9G_BINDING(X, 1) D9G_BINDING(X, 2) D9G_BINDING(X, 3) \
    D9G_LOCATION(X, 0) D9G_LOCATION(X, 1) D9G_LOCATION(X, 2) D9G_LOCATION(X, 3) D9G_LOCATION(X, 4) D9G_LOCATION(X, 5) \
    D9G_LOCATION(X, 6) D9G_LOCATION(X, 7) D9G_LOCATION(X, 8) D9G_LOCATION(X, 9) D9G_LOCATION(X, 10) D9G_LOCATION(X, 11) \
    D9G_LOCATION(X, 12) D9G_LOCATION(X, 13) D9G_LOCATION(X, 14) D9G_LOCATION(X, 15) \
    X(rs_depthClampEnable) X(rs_depthClipEnable) X(rs_rasterizerDiscardEnable) X(rs_polygonMode) X(rs_cullMode) \
    X(rs_frontFace) X(rs_lineWidth) \
    X(rs_depthBiasEnable) X(rs_depthBiasConstantFactor) X(rs_depthBiasClamp) X(rs_depthBiasSlopeFactor) \
    X(rs_depthBiasRepresentation) X(rs_depthBiasExact) \
    X(ms_rasterizationSamples) X(ms_sampleShadingEnable) X(ms_minSampleShading) X(ms_sampleMask) \
    X(ms_alphaToCoverageEnable) X(ms_alphaToOneEnable) \
    X(ds_depthTestEnable) X(ds_depthWriteEnable) X(ds_depthCompareOp) X(ds_depthBoundsTestEnable) \
    X(ds_stencilTestEnable) D9G_STENCIL(X, front) D9G_STENCIL(X, back) \
    X(cb_logicOpEnable) X(cb_logicOp) X(cb_attachmentCount) \
    D9G_CB(X, 0) D9G_CB(X, 1) D9G_CB(X, 2) D9G_CB(X, 3) \
    X(rt_colorAttachmentCount) X(rt_depthAttachmentFormat) X(rt_stencilAttachmentFormat) \
    X(rt_feedbackLoop) X(rt_unresolvableHazard) X(vs_fixedFunction) \
    X(lg_alphaCompareOp) X(lg_alphaPrecision) X(lg_alphaRef) X(lg_pointSize) X(lg_pointSizeMin) \
    X(lg_pointSizeMax) X(lg_pointSprite) X(lg_pointScale) X(lg_flatShading)

#define D9G_SAMPLER_FACTS(X) \
    X(sm_flags) X(sm_magFilter) X(sm_minFilter) X(sm_mipmapMode) X(sm_addressModeU) X(sm_addressModeV) \
    X(sm_addressModeW) X(sm_mipLodBias) X(sm_anisotropyEnable) X(sm_maxAnisotropy) X(sm_compareEnable) \
    X(sm_compareOp) X(sm_minLod) X(sm_maxLod) X(sm_borderColor) X(sm_unnormalizedCoordinates) \
    X(sm_pNextReduction) X(sm_pNextCustomBorder) X(sm_pNextBorderSwizzle)

#define D9G_ENUM(n) PF_##n,
enum D9gPipelineFact { D9G_PIPELINE_FACTS(D9G_ENUM) PF_COUNT };
#undef D9G_ENUM
#define D9G_ENUM(n) SF_##n,
enum D9gSamplerFact { D9G_SAMPLER_FACTS(D9G_ENUM) SF_COUNT };
#undef D9G_ENUM

typedef struct D9gPipelineFacts { uint32_t v[PF_COUNT]; } D9gPipelineFacts;
typedef struct D9gSamplerFacts { uint32_t v[SF_COUNT]; } D9gSamplerFacts;

#ifdef __cplusplus
extern "C" {
#endif
extern const char* const d9g_pipeline_fact_names[PF_COUNT];
extern const char* const d9g_sampler_fact_names[SF_COUNT];
#ifdef __cplusplus
}
#endif

static inline uint32_t d9g_fbits(float f) {
    union { float f; uint32_t u; } c;
    c.f = f;
    return c.u;
}
static inline float d9g_bitsf(uint32_t u) {
    union { float f; uint32_t u; } c;
    c.u = u;
    return c.f;
}

#endif
