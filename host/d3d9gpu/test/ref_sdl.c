/* ref_sdl.c: the SDL side of the second host's state check (PLAN.md sections 8 and 9,
 * private/generated/s5/r2b/PLAN.md): for an SDL_gpu pipeline or sampler description (d9g's,
 * from host/d3d9gpu/d9g_pipeline.cpp and d9g_sampler.cpp), the Vulkan state SDL 3.4.16 builds
 * from it, computed by SDL's own code.
 *
 * SDL's enum tables, SDLToVK_PolygonMode, the state-filling span of VULKAN_CreateGraphicsPipeline
 * (from "Vertex input" through the colour-blend state, with the function's own locals) and the
 * VkSamplerCreateInfo span of VULKAN_CreateSampler are extracted from the pinned tarball by
 * test/extract_refs.py (sdl_extract.inc), each under a #line naming its source line, and so is
 * the debug-mode validation of SDL_CreateGPUGraphicsPipeline and SDL_CreateGPUSampler
 * (SDL_gpu.c), which d9g's descriptions must pass before SDL builds anything (the confirm's
 * residual 3: SDL's own checks, a duplicate attribute location among them). This file
 * compiles against the tarball's own headers (sdl/include, sdl/khronos with VK_NO_PROTOTYPES)
 * and links no SDL: the few SDL functions the spans call (SDL_malloc, SDL_free, SDL_LogWarn)
 * are defined here, and so are the property functions d9g's own maker of a description's
 * properties calls (d9g_props.cpp CreateProps; below), and the renderer is a shim holding the
 * two fields the spans read.
 *
 * The compile context (the extraction review's NIT 10): host/d3d9gpu/build.sh builds this file
 * with -std=c11 -O2 against those headers only. SDL's own build uses -O3 -DNDEBUG
 * -fno-strict-aliasing, its precompiled SDL_internal.h and VK_ENABLE_BETA_EXTENSIONS. The spans
 * compiled here fill tables and create-info structures from their arguments, and none reads a
 * beta extension's type or depends on aliasing or NDEBUG; SDL_assert_release is redefined below
 * (it records the message where SDL's handler would raise it, then the span returns NULL as
 * SDL's does), and the renderer, pipeline and device structures are shims holding the fields
 * the spans read. These are the alterations beside the per-span ones sdl_extract.inc lists.
 *
 * The SDL configuration is the install's (test/extract_refs.py): with the project's patches
 * (patches/sdl3: border addressing and depth clamp, not from the SDL project) the spans are the
 * patched SDL's, and read a description's props through SDL's property getters, answered below
 * from a model of the two property sets a span can read:
 *   - a description's: the set d9g itself makes (d9g_props.cpp CreateProps) through SDL's
 *     property functions, which this file defines as that model: SDL_CreateProperties makes the
 *     one live set, SDL_SetBooleanProperty and SDL_SetNumberProperty record the patch's
 *     depth-clamp boolean (d9g_pipeline.h kPropDepthClamp) and border colour number
 *     (d9g_sampler.h kPropSamplerBorderColour), and SDL_DestroyProperties ends it; a name it does
 *     not set reads SDL's default, as an unset property does; props 0 is the empty set. So what
 *     the spans read is what d9g's maker set, never a set the check builds itself (the I6 device
 *     review's finding 1);
 *   - the device's (SDL_GetGPUDeviceProperties): the patch's two capabilities as the state
 *     check's parameters give them (d9g_sdl_set_device_caps), which SDL's debug-mode validation
 *     holds a description's CLAMP_TO_BORDER and depth clamp to;
 * a property name the model does not know, a second live set, or a read of a destroyed one stops
 * the check. With the stock tarball no span reads them, and the model refuses a capability or a
 * set property (d9g_sdl_patched is 0).
 *
 * Phase B (sdl-patch PLAN.md section 3) made these branches reachable: d9g sets the depth-clamp
 * property on a device with depth clip and clamp, and maps BORDER to CLAMP_TO_BORDER with a
 * border colour on a device with that capability, so the clamp-with-clip chain read below, the
 * border colour table and SDL's validation of both are exercised by every census and sweep tuple
 * that reaches them (the state check's map-depth-clamp-off and map-border-colour controls).
 *
 * Copyright of the extracted code: SDL's (zlib); see the header of sdl_extract.inc.
 */
#define VK_NO_PROTOTYPES
#include "vulkan/vulkan.h"
#include <SDL3/SDL.h>

#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ref_facts.h"

/* SDL_sysgpu.h's opaque renderer type and the Vulkan renderer's two fields the spans read. */
typedef struct SDL_GPURenderer SDL_GPURenderer;
typedef struct VulkanRenderer {
    bool supportsFillModeNonSolid;     /* VkPhysicalDeviceFeatures::fillModeNonSolid (SDL_gpu_vulkan.c) */
    bool fillModeOnlyWarning;
} VulkanRenderer;
typedef struct VulkanGraphicsPipeline {
    SDL_GPUPrimitiveType primitiveType; /* the only member the span writes */
} VulkanGraphicsPipeline;

/* SDL's allocator and logger, for the spans (no libSDL3 is linked: the critic's N5). */
static int g_log_warnings = 0;
void *SDL_malloc(size_t size) { return malloc(size ? size : 1); }
void SDL_free(void *mem) { free(mem); }
void SDL_LogWarn(int category, SDL_PRINTF_FORMAT_STRING const char *fmt, ...) { (void)category; (void)fmt; g_log_warnings++; }

#define D9G_SECTION_TABLES
#include "sdl_extract.inc"
#undef D9G_SECTION_TABLES

int d9g_sdl_log_warnings(void) { return g_log_warnings; }

/* SDL's property functions, for d9g's maker and the patched spans (see the header): two fixed
 * IDs, the shim device's properties and the description's one live set, each answered from its
 * model. */
#define D9G_SDL_DEVICE_PROPS ((SDL_PropertiesID)0x5d9d9u)
#define D9G_SDL_DESC_PROPS ((SDL_PropertiesID)0x5d9dau)
#ifdef SDL_PROP_GPU_DEVICE_SA2RECOMP_SAMPLER_CLAMP_TO_BORDER_BOOLEAN
#define D9G_SDL_PATCHED 1
#define D9G_PROP_DEPTH_CLAMP SDL_PROP_GPU_GRAPHICSPIPELINE_CREATE_SA2RECOMP_DEPTH_CLAMP_BOOLEAN
#define D9G_PROP_BORDER_COLOUR SDL_PROP_GPU_SAMPLER_CREATE_SA2RECOMP_BORDER_COLOR_NUMBER
#else
#define D9G_SDL_PATCHED 0
#define D9G_PROP_DEPTH_CLAMP NULL           /* the stock SDL has neither: a set one is unmodelled */
#define D9G_PROP_BORDER_COLOUR NULL
#endif
static struct {
    int border, clip_and_clamp;            /* the device's capabilities (d9g_sdl_set_device_caps) */
} g_device_caps = { 0, 0 };
static struct {
    int live;                              /* made by SDL_CreateProperties, not yet destroyed */
    int has_depth_clamp;                   /* the depth-clamp boolean, set */
    bool depth_clamp;
    int has_border_colour;                 /* the border colour number, set */
    Sint64 border_colour;
} g_desc = { 0, 0, false, 0, 0 };
static void d9g_sdl_unmodelled(SDL_PropertiesID props, const char *name)
{
    fprintf(stderr, "ref_sdl.c: property %s of props %u, which the state check does not model\n",
            name ? name : "(none)", (unsigned)props);
    abort();
}
static inline int d9g_sdl_is(const char *name, const char *want) { return name && want && strcmp(name, want) == 0; }
/* The description's set, live: anything else is a set the model never made, or one destroyed. */
static void d9g_sdl_desc_live(SDL_PropertiesID props, const char *name)
{
    if (props != D9G_SDL_DESC_PROPS || !g_desc.live) d9g_sdl_unmodelled(props, name);
}

SDL_PropertiesID SDL_CreateProperties(void)
{
    if (g_desc.live) {
        fprintf(stderr, "ref_sdl.c: a description's property set made while another lives (the model holds one)\n");
        abort();
    }
    memset(&g_desc, 0, sizeof g_desc);
    g_desc.live = 1;
    return D9G_SDL_DESC_PROPS;
}
bool SDL_SetBooleanProperty(SDL_PropertiesID props, const char *name, bool value)
{
    d9g_sdl_desc_live(props, name);
    if (!d9g_sdl_is(name, D9G_PROP_DEPTH_CLAMP)) d9g_sdl_unmodelled(props, name);
    g_desc.has_depth_clamp = 1;
    g_desc.depth_clamp = value;
    return true;
}
bool SDL_SetNumberProperty(SDL_PropertiesID props, const char *name, Sint64 value)
{
    d9g_sdl_desc_live(props, name);
    if (!d9g_sdl_is(name, D9G_PROP_BORDER_COLOUR)) d9g_sdl_unmodelled(props, name);
    g_desc.has_border_colour = 1;
    g_desc.border_colour = value;
    return true;
}
void SDL_DestroyProperties(SDL_PropertiesID props)
{
    d9g_sdl_desc_live(props, "(SDL_DestroyProperties)");
    g_desc.live = 0;
}
const char *SDL_GetError(void) { return "(the state check's model of SDL's properties sets no error)"; }

static inline bool d9g_sdl_get_boolean_property(SDL_PropertiesID props, const char *name, bool default_value)
{
    if (props == 0) return default_value;   /* none set: SDL's default */
#if D9G_SDL_PATCHED
    if (props == D9G_SDL_DEVICE_PROPS) {
        if (d9g_sdl_is(name, SDL_PROP_GPU_DEVICE_SA2RECOMP_SAMPLER_CLAMP_TO_BORDER_BOOLEAN)) return g_device_caps.border != 0;
        if (d9g_sdl_is(name, SDL_PROP_GPU_DEVICE_SA2RECOMP_DEPTH_CLIP_AND_CLAMP_BOOLEAN)) return g_device_caps.clip_and_clamp != 0;
        d9g_sdl_unmodelled(props, name);
    }
    d9g_sdl_desc_live(props, name);
    if (d9g_sdl_is(name, D9G_PROP_DEPTH_CLAMP) && g_desc.has_depth_clamp) return g_desc.depth_clamp;
    return default_value;                   /* a property the description does not set */
#else
    if (props == D9G_SDL_DEVICE_PROPS) return default_value;   /* the stock SDL's device sets none of the patch's */
    d9g_sdl_unmodelled(props, name);
    return default_value;
#endif
}
static inline Sint64 d9g_sdl_get_number_property(SDL_PropertiesID props, const char *name, Sint64 default_value)
{
    if (props == 0) return default_value;
#if D9G_SDL_PATCHED
    d9g_sdl_desc_live(props, name);
    if (d9g_sdl_is(name, D9G_PROP_BORDER_COLOUR) && g_desc.has_border_colour) return g_desc.border_colour;
    return default_value;
#else
    d9g_sdl_unmodelled(props, name);
    return default_value;
#endif
}
static inline bool d9g_sdl_has_property(SDL_PropertiesID props, const char *name)
{
    if (props == 0) return false;
#if D9G_SDL_PATCHED
    d9g_sdl_desc_live(props, name);
    if (d9g_sdl_is(name, D9G_PROP_BORDER_COLOUR)) return g_desc.has_border_colour != 0;
    if (d9g_sdl_is(name, D9G_PROP_DEPTH_CLAMP)) return g_desc.has_depth_clamp != 0;
    return false;
#else
    d9g_sdl_unmodelled(props, name);
    return false;
#endif
}

/* 1 when the extracted SDL is the patched one (its headers define the patch's properties). */
int d9g_sdl_patched(void) { return D9G_SDL_PATCHED; }

/* The shim device's capabilities: 0 when it was set as asked, -1 when the extracted SDL is the
 * stock one and a capability was asked for (its device can report neither). */
int d9g_sdl_set_device_caps(int border, int clip_and_clamp)
{
    if (!D9G_SDL_PATCHED && (border || clip_and_clamp)) return -1;
    g_device_caps.border = border != 0;
    g_device_caps.clip_and_clamp = clip_and_clamp != 0;
    return 0;
}

/* Whether a description's set lives (the check holds every one destroyed between tuples). */
int d9g_sdl_desc_props_live(void) { return g_desc.live; }
#define SDL_GetBooleanProperty(props, name, default_value) d9g_sdl_get_boolean_property(props, name, default_value)
#define SDL_GetNumberProperty(props, name, default_value) d9g_sdl_get_number_property(props, name, default_value)
#define SDL_HasProperty(props, name) d9g_sdl_has_property(props, name)
#define SDL_GetGPUDeviceProperties(device) ((void)(device), D9G_SDL_DEVICE_PROPS)

/* ---- SDL's debug-mode validation (SDL_gpu.c), with its helpers (the CHECK_*_ENUM_INVALID
 * macros, MAX_VERTEX_BUFFERS and MAX_VERTEX_ATTRIBUTES, IsDepthFormat, FormatHasAlpha). Each
 * SDL_assert_release it reaches is recorded here (its message) instead of raising SDL's
 * assertion handler, and the span then returns NULL as SDL's does. The device here is in debug
 * mode with neither feature check disabled (SDL_gpu.c:723-740): stricter than the device d9g
 * makes, which turns debug mode on only with SA2_D3D9GPU_DEBUG=1 (d9g_entry.cpp,
 * d9g_gpu_sdl.cpp), so every description is held to the checks SDL makes in debug mode. */
typedef struct D9gSdlDevice {
    bool debug_mode;
    bool validate_feature_depth_clamp_disabled;
    bool validate_feature_anisotropy_disabled;
    bool d24s8_supported;    /* DeviceParams::d24s8_supported: the device's SDL_GPUTextureSupportsFormat answer */
} D9gSdlDevice;
static const char *g_assert_message = NULL;
static void d9g_sdl_assert_failed(const char *what) { g_assert_message = what; }
#undef SDL_assert_release
#define SDL_assert_release(condition) d9g_sdl_assert_failed(#condition)
#define D9G_SECTION_VALIDATE_HELPERS
#include "sdl_extract.inc"
#undef D9G_SECTION_VALIDATE_HELPERS

/* SDL_GPUTextureSupportsFormat, answered for the formats d9g's descriptions name: a 2D colour
 * target of B8G8R8A8_UNORM (which every Vulkan device supports: the specification's mandatory
 * format support), a depth-stencil target of D24_UNORM_S8_UINT as the device's own query said
 * (DeviceParams), and of D32_FLOAT_S8_UINT where D24 is not supported (Vulkan requires one of
 * the two). Any other question has no recorded answer here: unsupported, so the check fails. */
static bool d9g_sdl_texture_supports_format(const D9gSdlDevice *device, SDL_GPUTextureFormat format,
                                            SDL_GPUTextureType type, SDL_GPUTextureUsageFlags usage)
{
    if (type != SDL_GPU_TEXTURETYPE_2D) return false;
    if (usage == SDL_GPU_TEXTUREUSAGE_COLOR_TARGET) return format == SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM;
    if (usage == SDL_GPU_TEXTUREUSAGE_DEPTH_STENCIL_TARGET)
        return format == (device->d24s8_supported ? SDL_GPU_TEXTUREFORMAT_D24_UNORM_S8_UINT : SDL_GPU_TEXTUREFORMAT_D32_FLOAT_S8_UINT);
    return false;
}

/* SDL_CreateGPUGraphicsPipeline's validation of a create info: non-NULL when it passes, NULL
 * (with d9g_sdl_last_assert naming why) when SDL would refuse it. */
const void *d9g_sdl_validate_pipeline(const SDL_GPUGraphicsPipelineCreateInfo *graphicsPipelineCreateInfo, int d24s8_supported)
{
    const D9gSdlDevice dev = { true, false, false, d24s8_supported != 0 };
    const D9gSdlDevice *device = &dev;
    g_assert_message = NULL;
#define D9G_SECTION_PIPELINE_VALIDATE
#include "sdl_extract.inc"
#undef D9G_SECTION_PIPELINE_VALIDATE
    return graphicsPipelineCreateInfo;
}

/* SDL_CreateGPUSampler's validation of a create info, the same way. */
const void *d9g_sdl_validate_sampler(const SDL_GPUSamplerCreateInfo *createinfo)
{
    const D9gSdlDevice dev = { true, false, false, true };
    const D9gSdlDevice *device = &dev;
    g_assert_message = NULL;
#define D9G_SECTION_SAMPLER_VALIDATE
#include "sdl_extract.inc"
#undef D9G_SECTION_SAMPLER_VALIDATE
    return createinfo;
}

const char *d9g_sdl_last_assert(void) { return g_assert_message ? g_assert_message : ""; }

/* The state check's liveness control (the critic's S1): 1 swaps the table's SRC_ALPHA and
 * ONE_MINUS_SRC_ALPHA entries, 0 restores them; the comparator must then name the field. */
void d9g_sdl_poke(int which)
{
    static int poked = 0;
    if ((which != 0) != (poked != 0)) {
        VkBlendFactor t = SDLToVK_BlendFactor[SDL_GPU_BLENDFACTOR_SRC_ALPHA];
        SDLToVK_BlendFactor[SDL_GPU_BLENDFACTOR_SRC_ALPHA] = SDLToVK_BlendFactor[SDL_GPU_BLENDFACTOR_ONE_MINUS_SRC_ALPHA];
        SDLToVK_BlendFactor[SDL_GPU_BLENDFACTOR_ONE_MINUS_SRC_ALPHA] = t;
        poked = which;
    }
}

/* VULKAN_CreateGraphicsPipeline's state, filled by SDL's span, into facts. */
void d9g_sdl_pipeline_facts(const SDL_GPUGraphicsPipelineCreateInfo *createinfo, int supports_fill_mode_non_solid,
                            D9gPipelineFacts *out)
{
    VulkanRenderer shim_renderer = { supports_fill_mode_non_solid != 0, false };
    SDL_GPURenderer *driverData = (SDL_GPURenderer *)&shim_renderer;
#define D9G_SECTION_PIPELINE_LOCALS
#include "sdl_extract.inc"
#undef D9G_SECTION_PIPELINE_LOCALS
    (void)vulkanResult;
    (void)vkPipelineCreateInfo;
    (void)shaderStageCreateInfos;
    (void)viewportStateCreateInfo;
    (void)dynamicStateCreateInfo;
    (void)dynamicStates;
#define D9G_SECTION_PIPELINE_STATE
#include "sdl_extract.inc"
#undef D9G_SECTION_PIPELINE_STATE

    memset(out, 0, sizeof *out);
    uint32_t *v = out->v;
    v[PF_ia_topology] = inputAssemblyStateCreateInfo.topology;
    v[PF_ia_primitiveRestartEnable] = inputAssemblyStateCreateInfo.primitiveRestartEnable;
    v[PF_vi_attributeCount] = vertexInputStateCreateInfo.vertexAttributeDescriptionCount;
    for (uint32_t k = 0; k < vertexInputStateCreateInfo.vertexAttributeDescriptionCount && k < D9G_MAX_ATTRS; k++) {
        v[PF_vi_attr0_binding + 3 * k] = vertexInputAttributeDescriptions[k].binding;
        v[PF_vi_attr0_format + 3 * k] = vertexInputAttributeDescriptions[k].format;
        v[PF_vi_attr0_offset + 3 * k] = vertexInputAttributeDescriptions[k].offset;
    }
    v[PF_vi_bindingCount] = vertexInputStateCreateInfo.vertexBindingDescriptionCount;
    for (uint32_t k = 0; k < vertexInputStateCreateInfo.vertexBindingDescriptionCount && k < D9G_MAX_BINDINGS; k++) {
        v[PF_vi_bind0_binding + 4 * k] = vertexInputBindingDescriptions[k].binding;
        v[PF_vi_bind0_stride + 4 * k] = vertexInputBindingDescriptions[k].stride;
        v[PF_vi_bind0_inputRate + 4 * k] = vertexInputBindingDescriptions[k].inputRate;
        /* no VkVertexInputBindingDivisorDescription: an instanced binding steps by one */
        v[PF_vi_bind0_divisor + 4 * k] = vertexInputBindingDescriptions[k].inputRate == VK_VERTEX_INPUT_RATE_INSTANCE ? 1 : 0;
    }
    v[PF_rs_depthClampEnable] = rasterizationStateCreateInfo.depthClampEnable;
    /* No VkPipelineRasterizationDepthClipStateCreateInfoEXT in the chain (pNext NULL): Vulkan clips
     * depth exactly when depth clamping is off (VK_EXT_depth_clip_enable). The patched SDL chains
     * that one structure for clip with clamp (a description with the depth-clamp property): its
     * depthClipEnable is then the answer. Anything else in the chain is not modelled (0xFFFFFFFF,
     * which no comparison accepts). */
    const VkPipelineRasterizationDepthClipStateCreateInfoEXT *clip =
        (const VkPipelineRasterizationDepthClipStateCreateInfoEXT *)rasterizationStateCreateInfo.pNext;
    if (clip == NULL)
        v[PF_rs_depthClipEnable] = !rasterizationStateCreateInfo.depthClampEnable;
    else if (clip->sType == VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_DEPTH_CLIP_STATE_CREATE_INFO_EXT && clip->pNext == NULL)
        v[PF_rs_depthClipEnable] = clip->depthClipEnable;
    else
        v[PF_rs_depthClipEnable] = 0xFFFFFFFFu;
    v[PF_rs_rasterizerDiscardEnable] = rasterizationStateCreateInfo.rasterizerDiscardEnable;
    v[PF_rs_polygonMode] = rasterizationStateCreateInfo.polygonMode;
    v[PF_rs_cullMode] = rasterizationStateCreateInfo.cullMode;
    v[PF_rs_frontFace] = rasterizationStateCreateInfo.frontFace;
    v[PF_rs_lineWidth] = d9g_fbits(rasterizationStateCreateInfo.lineWidth);
    v[PF_rs_depthBiasEnable] = rasterizationStateCreateInfo.depthBiasEnable;
    v[PF_rs_depthBiasConstantFactor] = d9g_fbits(rasterizationStateCreateInfo.depthBiasConstantFactor);
    v[PF_rs_depthBiasClamp] = d9g_fbits(rasterizationStateCreateInfo.depthBiasClamp);
    v[PF_rs_depthBiasSlopeFactor] = d9g_fbits(rasterizationStateCreateInfo.depthBiasSlopeFactor);
    /* no VkDepthBiasRepresentationInfoEXT: Vulkan's default representation, not exact */
    v[PF_rs_depthBiasRepresentation] = VK_DEPTH_BIAS_REPRESENTATION_LEAST_REPRESENTABLE_VALUE_FORMAT_EXT;
    v[PF_rs_depthBiasExact] = VK_FALSE;
    v[PF_ms_rasterizationSamples] = multisampleStateCreateInfo.rasterizationSamples;
    v[PF_ms_sampleShadingEnable] = multisampleStateCreateInfo.sampleShadingEnable;
    v[PF_ms_minSampleShading] = d9g_fbits(multisampleStateCreateInfo.minSampleShading);
    v[PF_ms_sampleMask] = multisampleStateCreateInfo.pSampleMask ? *multisampleStateCreateInfo.pSampleMask : 0xFFFFFFFFu;
    v[PF_ms_alphaToCoverageEnable] = multisampleStateCreateInfo.alphaToCoverageEnable;
    v[PF_ms_alphaToOneEnable] = multisampleStateCreateInfo.alphaToOneEnable;
    v[PF_ds_depthTestEnable] = depthStencilStateCreateInfo.depthTestEnable;
    v[PF_ds_depthWriteEnable] = depthStencilStateCreateInfo.depthWriteEnable;
    v[PF_ds_depthCompareOp] = depthStencilStateCreateInfo.depthCompareOp;
    v[PF_ds_depthBoundsTestEnable] = depthStencilStateCreateInfo.depthBoundsTestEnable;
    v[PF_ds_stencilTestEnable] = depthStencilStateCreateInfo.stencilTestEnable;
    const VkStencilOpState *faces[2] = { &depthStencilStateCreateInfo.front, &depthStencilStateCreateInfo.back };
    for (int k = 0; k < 2; k++) {
        const int o = k * (PF_ds_back_failOp - PF_ds_front_failOp);
        v[PF_ds_front_failOp + o] = faces[k]->failOp;
        v[PF_ds_front_passOp + o] = faces[k]->passOp;
        v[PF_ds_front_depthFailOp + o] = faces[k]->depthFailOp;
        v[PF_ds_front_compareOp + o] = faces[k]->compareOp;
        v[PF_ds_front_compareMask + o] = faces[k]->compareMask;
        v[PF_ds_front_writeMask + o] = faces[k]->writeMask;
    }
    v[PF_cb_logicOpEnable] = colorBlendStateCreateInfo.logicOpEnable;
    v[PF_cb_logicOp] = colorBlendStateCreateInfo.logicOp;
    v[PF_cb_attachmentCount] = colorBlendStateCreateInfo.attachmentCount;
    for (uint32_t k = 0; k < colorBlendStateCreateInfo.attachmentCount && k < 4; k++) {
        const VkPipelineColorBlendAttachmentState *a = &colorBlendAttachmentStates[k];
        const uint32_t o = (PF_cb1_blendEnable - PF_cb0_blendEnable) * k;
        v[PF_cb0_blendEnable + o] = a->blendEnable;
        v[PF_cb0_srcColorBlendFactor + o] = a->srcColorBlendFactor;
        v[PF_cb0_dstColorBlendFactor + o] = a->dstColorBlendFactor;
        v[PF_cb0_colorBlendOp + o] = a->colorBlendOp;
        v[PF_cb0_srcAlphaBlendFactor + o] = a->srcAlphaBlendFactor;
        v[PF_cb0_dstAlphaBlendFactor + o] = a->dstAlphaBlendFactor;
        v[PF_cb0_alphaBlendOp + o] = a->alphaBlendOp;
        v[PF_cb0_colorWriteMask + o] = a->colorWriteMask;
        /* the transient render pass's attachment formats (VULKAN_INTERNAL_CreateTransientRenderPass,
         * SDL_gpu_vulkan.c:6268-6284), through the extracted table */
        v[PF_rt0_colorFormat + o] = SDLToVK_TextureFormat[createinfo->target_info.color_target_descriptions[k].format];
    }
    v[PF_rt_colorAttachmentCount] = createinfo->target_info.num_color_targets;
    if (createinfo->target_info.has_depth_stencil_target) {   /* :6297-6313 */
        const VkFormat dsf = SDLToVK_TextureFormat[createinfo->target_info.depth_stencil_format];
        v[PF_rt_depthAttachmentFormat] = dsf;
        /* the format's stencil aspect: SDL's depth-stencil formats with stencil (SDL_gpu.h) */
        if (dsf == VK_FORMAT_D24_UNORM_S8_UINT || dsf == VK_FORMAT_D32_SFLOAT_S8_UINT)
            v[PF_rt_stencilAttachmentFormat] = dsf;
    }
    v[PF_rt_feedbackLoop] = 0;   /* a render pass with no feedback-loop layout */
    SDL_free(graphicsPipeline);
}

/* VULKAN_CreateSampler's VkSamplerCreateInfo, filled by SDL's span, into facts. */
void d9g_sdl_sampler_facts(const SDL_GPUSamplerCreateInfo *createinfo, D9gSamplerFacts *out)
{
#define D9G_SECTION_SAMPLER_STATE
#include "sdl_extract.inc"
#undef D9G_SECTION_SAMPLER_STATE
    memset(out, 0, sizeof *out);
    uint32_t *v = out->v;
    v[SF_sm_flags] = vkSamplerCreateInfo.flags;
    v[SF_sm_magFilter] = vkSamplerCreateInfo.magFilter;
    v[SF_sm_minFilter] = vkSamplerCreateInfo.minFilter;
    v[SF_sm_mipmapMode] = vkSamplerCreateInfo.mipmapMode;
    v[SF_sm_addressModeU] = vkSamplerCreateInfo.addressModeU;
    v[SF_sm_addressModeV] = vkSamplerCreateInfo.addressModeV;
    v[SF_sm_addressModeW] = vkSamplerCreateInfo.addressModeW;
    v[SF_sm_mipLodBias] = d9g_fbits(vkSamplerCreateInfo.mipLodBias);
    v[SF_sm_anisotropyEnable] = vkSamplerCreateInfo.anisotropyEnable;
    v[SF_sm_maxAnisotropy] = d9g_fbits(vkSamplerCreateInfo.maxAnisotropy);
    v[SF_sm_compareEnable] = vkSamplerCreateInfo.compareEnable;
    v[SF_sm_compareOp] = vkSamplerCreateInfo.compareOp;
    v[SF_sm_minLod] = d9g_fbits(vkSamplerCreateInfo.minLod);
    v[SF_sm_maxLod] = d9g_fbits(vkSamplerCreateInfo.maxLod);
    v[SF_sm_borderColor] = vkSamplerCreateInfo.borderColor;
    v[SF_sm_unnormalizedCoordinates] = vkSamplerCreateInfo.unnormalizedCoordinates;
    v[SF_sm_pNextReduction] = v[SF_sm_pNextCustomBorder] = v[SF_sm_pNextBorderSwizzle] = 0;
    if (vkSamplerCreateInfo.pNext) v[SF_sm_pNextReduction] = 0xFFFFFFFFu;   /* SDL chains nothing (pNext NULL) */
}

/* The Vulkan enumerant values this side's header gives, for the pytest's check that the two
 * bundled vulkan_core.h agree (ref_dxvk's are printed by the harness from DXVK's). */
void d9g_sdl_enum_values(uint32_t *out, int n)
{
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
    for (int k = 0; k < n && k < (int)(sizeof vals / sizeof vals[0]); k++) out[k] = vals[k];
}
