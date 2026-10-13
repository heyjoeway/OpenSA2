// border_clamp: the behaviour check of the local SDL patch
// (0001-gpu-clamp-to-border-and-depth-clamp-with-clip.patch): clamp-to-border sampling with a
// border colour, and depth clamp beside depth clip. It draws offscreen through SDL_gpu on one
// Vulkan driver (VK_DRIVER_FILES names it), reads every target back and compares each pixel
// with a literal expected value. No window, nothing presented, no sound.
//
//   border_clamp <shader-dir> [--neutral-only | --stock-detect]
//
// <shader-dir> holds quad.vert.spv, textured.frag.spv, solid.frag.spv and solid_depth.frag.spv,
// compiled from this directory's GLSL by the pinned glslang. Each result is one JSON line on
// stdout; the last line is BORDER_CLAMP_OK, or BORDER_CLAMP_FAIL <count>, and the exit status
// is 0 only for OK.
//
// --neutral-only runs the stock-API draws alone (no new enum value, property or query) and
// prints their FNV-1a 64 hashes, so the same binary can be run against the stock and the
// patched library and the two outputs compared byte for byte.
//
// --stock-detect checks only that the patch's two device capabilities are absent (so read
// false), which is true of a stock SDL3 and false of the patched one.
#include <SDL3/SDL.h>
#include <vulkan/vulkan.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

// The patch's API, spelled out so that the stock headers also compile this file: the values and
// property names are the patch's own, and nothing else here depends on the patched header.
#ifndef SDL_PROP_GPU_SAMPLER_CREATE_SA2RECOMP_BORDER_COLOR_NUMBER
#define SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_BORDER ((SDL_GPUSamplerAddressMode)3)
#define SDL_GPU_SAMPLERBORDERCOLOR_TRANSPARENT_BLACK 0
#define SDL_GPU_SAMPLERBORDERCOLOR_OPAQUE_BLACK 1
#define SDL_GPU_SAMPLERBORDERCOLOR_OPAQUE_WHITE 2
#define SDL_PROP_GPU_SAMPLER_CREATE_SA2RECOMP_BORDER_COLOR_NUMBER "SDL.gpu.sampler.create.sa2recomp.border_color"
#define SDL_PROP_GPU_GRAPHICSPIPELINE_CREATE_SA2RECOMP_DEPTH_CLAMP_BOOLEAN "SDL.gpu.graphicspipeline.create.sa2recomp.depth_clamp"
#define SDL_PROP_GPU_DEVICE_SA2RECOMP_SAMPLER_CLAMP_TO_BORDER_BOOLEAN "SDL.gpu.device.sa2recomp.sampler_clamp_to_border"
#define SDL_PROP_GPU_DEVICE_SA2RECOMP_DEPTH_CLIP_AND_CLAMP_BOOLEAN "SDL.gpu.device.sa2recomp.depth_clip_and_clamp"
#endif

#define W 64
#define H 64

typedef struct { float x, y, z, w, u, v; } Vertex;
typedef struct { float color[4]; float depth[4]; } Solid;

static const char *shader_dir;
static int failures;
static int assertions;  // SDL_assert_release calls seen (debug-mode validation)
static char last_assertion[256];

static SDL_AssertState SDLCALL count_assertion(const SDL_AssertData *data, void *userdata)
{
    (void)userdata;
    assertions++;
    SDL_strlcpy(last_assertion, data->condition, sizeof(last_assertion));
    return SDL_ASSERTION_IGNORE;
}

static void result(const char *name, bool ok, const char *detail)
{
    char escaped[512];
    size_t n = 0;
    for (const char *c = detail; *c && n + 2 < sizeof(escaped); c++) {
        if (*c == '"' || *c == '\\') {
            escaped[n++] = '\\';
        }
        escaped[n++] = (*c >= 0 && *c < 0x20) ? ' ' : *c;
    }
    escaped[n] = '\0';
    printf("{\"test\": \"%s\", \"ok\": %s, \"detail\": \"%s\"}\n", name, ok ? "true" : "false", escaped);
    if (!ok) {
        failures++;
    }
    fflush(stdout);
}

static uint64_t fnv(const uint8_t *p, size_t n)
{
    uint64_t h = 1469598103934665603ull;
    for (size_t i = 0; i < n; i++) {
        h ^= p[i];
        h *= 1099511628211ull;
    }
    return h;
}

// --- device -------------------------------------------------------------------------------------

typedef struct
{
    bool debug;
    bool depth_clamping;      // SDL_PROP_GPU_DEVICE_CREATE_FEATURE_DEPTH_CLAMPING_BOOLEAN
    bool vulkan13_options;    // SDL_GPUVulkanOptions at 1.3, with a depth-clip struct in feature_list
} DeviceSpec;

static SDL_GPUDevice *create_device(DeviceSpec spec)
{
    SDL_PropertiesID props = SDL_CreateProperties();
    SDL_SetBooleanProperty(props, SDL_PROP_GPU_DEVICE_CREATE_SHADERS_SPIRV_BOOLEAN, true);
    SDL_SetStringProperty(props, SDL_PROP_GPU_DEVICE_CREATE_NAME_STRING, "vulkan");
    SDL_SetBooleanProperty(props, SDL_PROP_GPU_DEVICE_CREATE_DEBUGMODE_BOOLEAN, spec.debug);
    SDL_SetBooleanProperty(props, SDL_PROP_GPU_DEVICE_CREATE_FEATURE_DEPTH_CLAMPING_BOOLEAN, spec.depth_clamping);

    static VkPhysicalDeviceVulkan12Features vk12;
    static VkPhysicalDeviceDepthClipEnableFeaturesEXT clip;
    static SDL_GPUVulkanOptions options;
    if (spec.vulkan13_options) {
        // An application that already asks for the depth-clip feature itself: SDL builds the
        // device's feature chain from its own structures, so the patch's struct is never a
        // duplicate of the application's (VUID-VkDeviceCreateInfo-sType-unique).
        SDL_zero(vk12);
        vk12.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES;
        vk12.scalarBlockLayout = VK_TRUE;
        SDL_zero(clip);
        clip.sType = VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DEPTH_CLIP_ENABLE_FEATURES_EXT;
        clip.depthClipEnable = VK_TRUE;
        vk12.pNext = &clip;
        SDL_zero(options);
        options.vulkan_api_version = VK_API_VERSION_1_3;
        options.feature_list = &vk12;
        SDL_SetPointerProperty(props, SDL_PROP_GPU_DEVICE_CREATE_VULKAN_OPTIONS_POINTER, &options);
    }
    SDL_GPUDevice *dev = SDL_CreateGPUDeviceWithProperties(props);
    SDL_DestroyProperties(props);
    if (!dev) {
        fprintf(stderr, "SDL_CreateGPUDeviceWithProperties: %s\n", SDL_GetError());
    }
    return dev;
}

static bool device_bool(SDL_GPUDevice *dev, const char *name, bool *present)
{
    SDL_PropertiesID props = SDL_GetGPUDeviceProperties(dev);
    *present = SDL_HasProperty(props, name);
    return SDL_GetBooleanProperty(props, name, false);
}

// --- resources ----------------------------------------------------------------------------------

static void *load_file(const char *name, size_t *size)
{
    char path[1024];
    SDL_snprintf(path, sizeof(path), "%s/%s", shader_dir, name);
    void *data = SDL_LoadFile(path, size);
    if (!data) {
        fprintf(stderr, "cannot read %s: %s\n", path, SDL_GetError());
        exit(2);
    }
    return data;
}

static SDL_GPUShader *shader(SDL_GPUDevice *dev, const char *name, SDL_GPUShaderStage stage,
                             Uint32 samplers, Uint32 uniform_buffers)
{
    size_t size;
    void *code = load_file(name, &size);
    SDL_GPUShaderCreateInfo ci;
    SDL_zero(ci);
    ci.code_size = size;
    ci.code = code;
    ci.entrypoint = "main";
    ci.format = SDL_GPU_SHADERFORMAT_SPIRV;
    ci.stage = stage;
    ci.num_samplers = samplers;
    ci.num_uniform_buffers = uniform_buffers;
    SDL_GPUShader *s = SDL_CreateGPUShader(dev, &ci);
    SDL_free(code);
    if (!s) {
        fprintf(stderr, "SDL_CreateGPUShader(%s): %s\n", name, SDL_GetError());
        exit(1);
    }
    return s;
}

typedef struct
{
    bool textured;
    bool shader_depth;            // solid_depth.frag: gl_FragDepth from the uniform
    bool has_depth;               // a depth target of depth_format
    SDL_GPUTextureFormat depth_format;
    bool depth_test;              // LESS, with writes
    bool enable_depth_clip;
    bool clamp_property;          // SDL_PROP_GPU_GRAPHICSPIPELINE_CREATE_SA2RECOMP_DEPTH_CLAMP_BOOLEAN
    float depth_bias_constant;    // enabled when non-zero
} PipelineSpec;

static SDL_GPUGraphicsPipeline *pipeline(SDL_GPUDevice *dev, PipelineSpec spec)
{
    SDL_GPUShader *vs = shader(dev, "quad.vert.spv", SDL_GPU_SHADERSTAGE_VERTEX, 0, 0);
    SDL_GPUShader *fs = spec.textured
        ? shader(dev, "textured.frag.spv", SDL_GPU_SHADERSTAGE_FRAGMENT, 1, 0)
        : shader(dev, spec.shader_depth ? "solid_depth.frag.spv" : "solid.frag.spv", SDL_GPU_SHADERSTAGE_FRAGMENT, 0, 1);

    SDL_GPUVertexBufferDescription vb = { 0, sizeof(Vertex), SDL_GPU_VERTEXINPUTRATE_VERTEX, 0 };
    SDL_GPUVertexAttribute attrs[2] = {
        { 0, 0, SDL_GPU_VERTEXELEMENTFORMAT_FLOAT4, 0 },
        { 1, 0, SDL_GPU_VERTEXELEMENTFORMAT_FLOAT2, 16 },
    };
    SDL_GPUColorTargetDescription ctd;
    SDL_zero(ctd);
    ctd.format = SDL_GPU_TEXTUREFORMAT_R8G8B8A8_UNORM;

    SDL_GPUGraphicsPipelineCreateInfo pi;
    SDL_zero(pi);
    pi.vertex_shader = vs;
    pi.fragment_shader = fs;
    pi.vertex_input_state.vertex_buffer_descriptions = &vb;
    pi.vertex_input_state.num_vertex_buffers = 1;
    pi.vertex_input_state.vertex_attributes = attrs;
    pi.vertex_input_state.num_vertex_attributes = 2;
    pi.primitive_type = SDL_GPU_PRIMITIVETYPE_TRIANGLESTRIP;
    pi.rasterizer_state.fill_mode = SDL_GPU_FILLMODE_FILL;
    pi.rasterizer_state.cull_mode = SDL_GPU_CULLMODE_NONE;
    pi.rasterizer_state.enable_depth_clip = spec.enable_depth_clip;
    if (spec.depth_bias_constant != 0.0f) {
        pi.rasterizer_state.enable_depth_bias = true;
        pi.rasterizer_state.depth_bias_constant_factor = spec.depth_bias_constant;
    }
    pi.multisample_state.sample_count = SDL_GPU_SAMPLECOUNT_1;
    pi.depth_stencil_state.enable_depth_test = spec.depth_test;
    pi.depth_stencil_state.enable_depth_write = spec.depth_test;
    pi.depth_stencil_state.compare_op = SDL_GPU_COMPAREOP_LESS;
    pi.target_info.color_target_descriptions = &ctd;
    pi.target_info.num_color_targets = 1;
    pi.target_info.has_depth_stencil_target = spec.has_depth;
    pi.target_info.depth_stencil_format = spec.depth_format;
    if (spec.clamp_property) {
        pi.props = SDL_CreateProperties();
        SDL_SetBooleanProperty(pi.props, SDL_PROP_GPU_GRAPHICSPIPELINE_CREATE_SA2RECOMP_DEPTH_CLAMP_BOOLEAN, true);
    }
    SDL_GPUGraphicsPipeline *p = SDL_CreateGPUGraphicsPipeline(dev, &pi);
    if (pi.props) {
        SDL_DestroyProperties(pi.props);
    }
    SDL_ReleaseGPUShader(dev, vs);
    SDL_ReleaseGPUShader(dev, fs);
    return p;
}

static SDL_GPUSampler *sampler(SDL_GPUDevice *dev, SDL_GPUFilter filter, SDL_GPUSamplerAddressMode mode,
                               int border_color /* -1: no property */)
{
    SDL_GPUSamplerCreateInfo si;
    SDL_zero(si);
    si.min_filter = si.mag_filter = filter;
    si.mipmap_mode = SDL_GPU_SAMPLERMIPMAPMODE_NEAREST;
    si.address_mode_u = si.address_mode_v = mode;
    si.address_mode_w = SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_EDGE;
    si.min_lod = 0.0f;
    si.max_lod = 0.0f;
    if (border_color >= 0) {
        si.props = SDL_CreateProperties();
        SDL_SetNumberProperty(si.props, SDL_PROP_GPU_SAMPLER_CREATE_SA2RECOMP_BORDER_COLOR_NUMBER, border_color);
    }
    SDL_GPUSampler *s = SDL_CreateGPUSampler(dev, &si);
    if (si.props) {
        SDL_DestroyProperties(si.props);
    }
    return s;
}

static SDL_GPUTexture *texture(SDL_GPUDevice *dev, int tw, int th, const uint8_t *rgba)
{
    SDL_GPUTextureCreateInfo ti;
    SDL_zero(ti);
    ti.type = SDL_GPU_TEXTURETYPE_2D;
    ti.format = SDL_GPU_TEXTUREFORMAT_R8G8B8A8_UNORM;
    ti.usage = SDL_GPU_TEXTUREUSAGE_SAMPLER;
    ti.width = (Uint32)tw;
    ti.height = (Uint32)th;
    ti.layer_count_or_depth = 1;
    ti.num_levels = 1;
    SDL_GPUTexture *t = SDL_CreateGPUTexture(dev, &ti);
    SDL_GPUTransferBufferCreateInfo bi = { SDL_GPU_TRANSFERBUFFERUSAGE_UPLOAD, (Uint32)(tw * th * 4), 0 };
    SDL_GPUTransferBuffer *tb = SDL_CreateGPUTransferBuffer(dev, &bi);
    SDL_memcpy(SDL_MapGPUTransferBuffer(dev, tb, false), rgba, (size_t)tw * th * 4);
    SDL_UnmapGPUTransferBuffer(dev, tb);
    SDL_GPUCommandBuffer *cb = SDL_AcquireGPUCommandBuffer(dev);
    SDL_GPUCopyPass *cp = SDL_BeginGPUCopyPass(cb);
    SDL_GPUTextureTransferInfo src = { tb, 0, (Uint32)tw, (Uint32)th };
    SDL_GPUTextureRegion dst = { t, 0, 0, 0, 0, 0, (Uint32)tw, (Uint32)th, 1 };
    SDL_UploadToGPUTexture(cp, &src, &dst, false);
    SDL_EndGPUCopyPass(cp);
    SDL_SubmitGPUCommandBuffer(cb);
    SDL_ReleaseGPUTransferBuffer(dev, tb);
    return t;
}

// --- drawing ------------------------------------------------------------------------------------

typedef struct
{
    SDL_GPUGraphicsPipeline *pipe;
    SDL_GPUTexture *tex;          // textured draws
    SDL_GPUSampler *smp;
    Solid solid;                  // solid draws
    float z;                      // clip-space z of the whole quad (w = 1)
    float u0, u1;                 // texture coordinate range across the quad, both axes
    float min_depth, max_depth;   // the viewport's depth range
} Draw;

// Clears colour to (0,0,0,255) and depth to 1, runs the draws in order, and returns W*H RGBA8.
static uint8_t *render(SDL_GPUDevice *dev, const Draw *draws, int count, bool has_depth, SDL_GPUTextureFormat depth_format)
{
    SDL_GPUBufferCreateInfo vbi = { SDL_GPU_BUFFERUSAGE_VERTEX, (Uint32)(sizeof(Vertex) * 4 * count), 0 };
    SDL_GPUBuffer *vb = SDL_CreateGPUBuffer(dev, &vbi);
    SDL_GPUTransferBufferCreateInfo ubi = { SDL_GPU_TRANSFERBUFFERUSAGE_UPLOAD, vbi.size, 0 };
    SDL_GPUTransferBuffer *up = SDL_CreateGPUTransferBuffer(dev, &ubi);
    Vertex *v = (Vertex *)SDL_MapGPUTransferBuffer(dev, up, false);
    for (int i = 0; i < count; i++) {
        const Draw *d = &draws[i];
        const Vertex q[4] = {
            { -1.0f, -1.0f, d->z, 1.0f, d->u0, d->u0 },
            {  1.0f, -1.0f, d->z, 1.0f, d->u1, d->u0 },
            { -1.0f,  1.0f, d->z, 1.0f, d->u0, d->u1 },
            {  1.0f,  1.0f, d->z, 1.0f, d->u1, d->u1 },
        };
        SDL_memcpy(&v[4 * i], q, sizeof(q));
    }
    SDL_UnmapGPUTransferBuffer(dev, up);

    SDL_GPUTextureCreateInfo ri;
    SDL_zero(ri);
    ri.type = SDL_GPU_TEXTURETYPE_2D;
    ri.format = SDL_GPU_TEXTUREFORMAT_R8G8B8A8_UNORM;
    ri.usage = SDL_GPU_TEXTUREUSAGE_COLOR_TARGET;
    ri.width = W;
    ri.height = H;
    ri.layer_count_or_depth = 1;
    ri.num_levels = 1;
    SDL_GPUTexture *rt = SDL_CreateGPUTexture(dev, &ri);
    SDL_GPUTexture *ds = NULL;
    if (has_depth) {
        ri.format = depth_format;
        ri.usage = SDL_GPU_TEXTUREUSAGE_DEPTH_STENCIL_TARGET;
        ds = SDL_CreateGPUTexture(dev, &ri);
    }
    SDL_GPUTransferBufferCreateInfo dbi = { SDL_GPU_TRANSFERBUFFERUSAGE_DOWNLOAD, W * H * 4, 0 };
    SDL_GPUTransferBuffer *down = SDL_CreateGPUTransferBuffer(dev, &dbi);

    SDL_GPUCommandBuffer *cb = SDL_AcquireGPUCommandBuffer(dev);
    SDL_GPUCopyPass *cp = SDL_BeginGPUCopyPass(cb);
    SDL_GPUTransferBufferLocation src = { up, 0 };
    SDL_GPUBufferRegion dst = { vb, 0, vbi.size };
    SDL_UploadToGPUBuffer(cp, &src, &dst, false);
    SDL_EndGPUCopyPass(cp);

    SDL_GPUColorTargetInfo ct;
    SDL_zero(ct);
    ct.texture = rt;
    ct.clear_color = (SDL_FColor){ 0.0f, 0.0f, 0.0f, 1.0f };
    ct.load_op = SDL_GPU_LOADOP_CLEAR;
    ct.store_op = SDL_GPU_STOREOP_STORE;
    SDL_GPUDepthStencilTargetInfo dti;
    SDL_zero(dti);
    dti.texture = ds;
    dti.clear_depth = 1.0f;
    dti.load_op = SDL_GPU_LOADOP_CLEAR;
    dti.store_op = SDL_GPU_STOREOP_DONT_CARE;
    dti.stencil_load_op = SDL_GPU_LOADOP_DONT_CARE;
    dti.stencil_store_op = SDL_GPU_STOREOP_DONT_CARE;
    SDL_GPURenderPass *rp = SDL_BeginGPURenderPass(cb, &ct, 1, ds ? &dti : NULL);
    SDL_Rect sc = { 0, 0, W, H };
    SDL_SetGPUScissor(rp, &sc);
    for (int i = 0; i < count; i++) {
        const Draw *d = &draws[i];
        SDL_BindGPUGraphicsPipeline(rp, d->pipe);
        SDL_GPUViewport vp = { 0.0f, 0.0f, (float)W, (float)H, d->min_depth, d->max_depth };
        SDL_SetGPUViewport(rp, &vp);
        SDL_GPUBufferBinding vbb = { vb, 0 };
        SDL_BindGPUVertexBuffers(rp, 0, &vbb, 1);
        if (d->tex) {
            SDL_GPUTextureSamplerBinding tsb = { d->tex, d->smp };
            SDL_BindGPUFragmentSamplers(rp, 0, &tsb, 1);
        } else {
            SDL_PushGPUFragmentUniformData(cb, 0, &d->solid, sizeof(d->solid));
        }
        SDL_DrawGPUPrimitives(rp, 4, 1, (Uint32)(4 * i), 0);
    }
    SDL_EndGPURenderPass(rp);

    cp = SDL_BeginGPUCopyPass(cb);
    SDL_GPUTextureRegion from = { rt, 0, 0, 0, 0, 0, W, H, 1 };
    SDL_GPUTextureTransferInfo to = { down, 0, W, H };
    SDL_DownloadFromGPUTexture(cp, &from, &to);
    SDL_EndGPUCopyPass(cp);
    SDL_GPUFence *fence = SDL_SubmitGPUCommandBufferAndAcquireFence(cb);
    if (!fence || !SDL_WaitForGPUFences(dev, true, &fence, 1)) {
        fprintf(stderr, "submit/wait failed: %s\n", SDL_GetError());
        exit(1);
    }
    SDL_ReleaseGPUFence(dev, fence);
    uint8_t *px = (uint8_t *)SDL_malloc(W * H * 4);
    SDL_memcpy(px, SDL_MapGPUTransferBuffer(dev, down, false), W * H * 4);
    SDL_UnmapGPUTransferBuffer(dev, down);
    SDL_ReleaseGPUTransferBuffer(dev, down);
    SDL_ReleaseGPUTransferBuffer(dev, up);
    SDL_ReleaseGPUBuffer(dev, vb);
    SDL_ReleaseGPUTexture(dev, rt);
    if (ds) {
        SDL_ReleaseGPUTexture(dev, ds);
    }
    return px;
}

// --- the literal expectations -------------------------------------------------------------------

// A 4x4 texture whose texels differ from each other and from all three border colours.
static uint8_t texels[4 * 4 * 4];
static void make_texels(void)
{
    for (int i = 0; i < 16; i++) {
        texels[4 * i + 0] = (uint8_t)(16 + 12 * i);
        texels[4 * i + 1] = (uint8_t)(200 - 9 * i);
        texels[4 * i + 2] = (uint8_t)(64 + (i % 4) * 40);
        texels[4 * i + 3] = 255;
    }
}
static const uint8_t uniform_texel[4] = { 40, 90, 160, 255 };
static const uint8_t border_rgba[3][4] = { { 0, 0, 0, 0 }, { 0, 0, 0, 255 }, { 255, 255, 255, 255 } };

// The quad's texture coordinates run from -0.5 to 1.5 over the 64 pixels, so the pixel centre at
// x samples u = (2x - 31) / 64, never exactly 0, 1 or a texel boundary. Rows count from the
// bottom: SDL_gpu's clip space has y up, so clip y = -1 (v = -0.5) is the last row read back. NEAREST: inside [0, 1]
// the texel floor(4u), else the border. LINEAR on a uniform texture: both taps inside when
// u is in [1/8, 7/8], both outside when u < -1/8 or u > 9/8; the pixels between are a blend
// and are not compared.
typedef enum { REGION_INSIDE, REGION_OUTSIDE, REGION_BLEND } Region;
static Region region(int x, bool linear, int *texel)
{
    int n = 2 * x - 31;  // u = n / 64
    if (!linear) {
        if (n < 0 || n > 64) {
            return REGION_OUTSIDE;
        }
        *texel = (n * 4) / 64;
        return REGION_INSIDE;
    }
    if (n >= 8 && n <= 56) {
        *texel = 0;
        return REGION_INSIDE;
    }
    if (n < -8 || n > 72) {
        return REGION_OUTSIDE;
    }
    return REGION_BLEND;
}

// Returns the number of compared pixels that differ from the expectation.
static int check_border(const uint8_t *px, bool linear, const uint8_t outside[4], int *compared)
{
    int bad = 0;
    *compared = 0;
    for (int y = 0; y < H; y++) {
        for (int x = 0; x < W; x++) {
            int tx = 0, ty = 0;
            Region rx = region(x, linear, &tx), ry = region(H - 1 - y, linear, &ty);
            const uint8_t *want;
            if (rx == REGION_BLEND || ry == REGION_BLEND) {
                continue;
            }
            if (rx == REGION_OUTSIDE || ry == REGION_OUTSIDE) {
                want = outside;
            } else {
                want = linear ? uniform_texel : &texels[4 * (4 * ty + tx)];
            }
            (*compared)++;
            if (SDL_memcmp(&px[4 * (y * W + x)], want, 4) != 0) {
                bad++;
            }
        }
    }
    return bad;
}

static int count_color(const uint8_t *px, const uint8_t want[4])
{
    int n = 0;
    for (int i = 0; i < W * H; i++) {
        n += SDL_memcmp(&px[4 * i], want, 4) == 0;
    }
    return n;
}

static const uint8_t BLACK[4] = { 0, 0, 0, 255 }, GREEN[4] = { 0, 255, 0, 255 }, RED[4] = { 255, 0, 0, 255 };
static const Solid SOLID_GREEN = { { 0, 1, 0, 1 }, { 0, 0, 0, 0 } };
static const Solid SOLID_RED = { { 1, 0, 0, 1 }, { 0, 0, 0, 0 } };

// --- the tests ----------------------------------------------------------------------------------

static void test_properties(void)
{
    char detail[256];
    bool present_b, present_c;
    DeviceSpec plain = { false, true, false };
    SDL_GPUDevice *dev = create_device(plain);
    if (!dev) {
        result("properties.default", false, "no device");
        return;
    }
    bool border = device_bool(dev, SDL_PROP_GPU_DEVICE_SA2RECOMP_SAMPLER_CLAMP_TO_BORDER_BOOLEAN, &present_b);
    bool clamp = device_bool(dev, SDL_PROP_GPU_DEVICE_SA2RECOMP_DEPTH_CLIP_AND_CLAMP_BOOLEAN, &present_c);
    SDL_snprintf(detail, sizeof(detail), "border %d (present %d), clip_and_clamp %d (present %d), device %s",
                 border, present_b, clamp, present_c,
                 SDL_GetStringProperty(SDL_GetGPUDeviceProperties(dev), SDL_PROP_GPU_DEVICE_NAME_STRING, "?"));
    result("properties.default", border && clamp && present_b && present_c, detail);
    SDL_DestroyGPUDevice(dev);

    DeviceSpec noclamp = { false, false, false };
    dev = create_device(noclamp);
    if (!dev) {
        result("properties.depth_clamping_off", false, "no device");
        return;
    }
    border = device_bool(dev, SDL_PROP_GPU_DEVICE_SA2RECOMP_SAMPLER_CLAMP_TO_BORDER_BOOLEAN, &present_b);
    clamp = device_bool(dev, SDL_PROP_GPU_DEVICE_SA2RECOMP_DEPTH_CLIP_AND_CLAMP_BOOLEAN, &present_c);
    SDL_snprintf(detail, sizeof(detail), "border %d, clip_and_clamp %d (present %d)", border, clamp, present_c);
    result("properties.depth_clamping_off", border && !clamp && present_c, detail);
    SDL_DestroyGPUDevice(dev);

    DeviceSpec v13 = { false, true, true };
    dev = create_device(v13);
    if (!dev) {
        result("properties.vulkan13_options", false, "no device with SDL_GPUVulkanOptions 1.3 and an application depth-clip struct");
        return;
    }
    border = device_bool(dev, SDL_PROP_GPU_DEVICE_SA2RECOMP_SAMPLER_CLAMP_TO_BORDER_BOOLEAN, &present_b);
    clamp = device_bool(dev, SDL_PROP_GPU_DEVICE_SA2RECOMP_DEPTH_CLIP_AND_CLAMP_BOOLEAN, &present_c);
    PipelineSpec ps = { false, false, true, SDL_GPU_TEXTUREFORMAT_D16_UNORM, true, true, true, 0.0f };
    SDL_GPUGraphicsPipeline *p = pipeline(dev, ps);
    SDL_snprintf(detail, sizeof(detail), "border %d, clip_and_clamp %d, clamp pipeline %s", border, clamp, p ? "created" : SDL_GetError());
    result("properties.vulkan13_options", border && clamp && p != NULL, detail);
    if (p) {
        SDL_ReleaseGPUGraphicsPipeline(dev, p);
    }
    SDL_DestroyGPUDevice(dev);
}

// On an SDL without the patch both capabilities are absent, and so read false: the run-time
// detection by which a build on stock or system SDL3 is to take its shader fallback, phase B's
// work and not built yet (ruling of
// 2026-10-04, Documentation/decisions/2026-10-04-S5-user-rulings-2.md). On the patched library
// this row fails, which is what shows it can.
static void test_stock_detect(void)
{
    char detail[256];
    bool present_b, present_c;
    DeviceSpec plain = { false, true, false };
    SDL_GPUDevice *dev = create_device(plain);
    if (!dev) {
        result("properties.stock_absent", false, "no device");
        return;
    }
    bool border = device_bool(dev, SDL_PROP_GPU_DEVICE_SA2RECOMP_SAMPLER_CLAMP_TO_BORDER_BOOLEAN, &present_b);
    bool clamp = device_bool(dev, SDL_PROP_GPU_DEVICE_SA2RECOMP_DEPTH_CLIP_AND_CLAMP_BOOLEAN, &present_c);
    SDL_snprintf(detail, sizeof(detail), "border %d (present %d), clip_and_clamp %d (present %d)", border, present_b, clamp, present_c);
    result("properties.stock_absent", !border && !clamp && !present_b && !present_c, detail);
    SDL_DestroyGPUDevice(dev);
}

static void test_border(SDL_GPUDevice *dev)
{
    static const char *color_names[3] = { "transparent_black", "opaque_black", "opaque_white" };
    char name[128], detail[256];
    PipelineSpec ps = { true, false, false, SDL_GPU_TEXTUREFORMAT_INVALID, false, true, false, 0.0f };
    SDL_GPUGraphicsPipeline *p = pipeline(dev, ps);
    SDL_GPUTexture *tex4 = texture(dev, 4, 4, texels);
    uint8_t uniform[4 * 4 * 4];
    for (int i = 0; i < 16; i++) {
        SDL_memcpy(&uniform[4 * i], uniform_texel, 4);
    }
    SDL_GPUTexture *texu = texture(dev, 4, 4, uniform);
    for (int linear = 0; linear < 2; linear++) {
        SDL_GPUFilter filter = linear ? SDL_GPU_FILTER_LINEAR : SDL_GPU_FILTER_NEAREST;
        SDL_GPUTexture *tex = linear ? texu : tex4;
        for (int c = 0; c < 3; c++) {
            SDL_GPUSampler *smp = sampler(dev, filter, SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_BORDER, c);
            SDL_snprintf(name, sizeof(name), "border.%s.%s", linear ? "linear" : "nearest", color_names[c]);
            if (!smp) {
                result(name, false, "sampler refused");
                continue;
            }
            Draw d = { p, tex, smp, { { 0 } }, 0.5f, -0.5f, 1.5f, 0.0f, 1.0f };
            uint8_t *px = render(dev, &d, 1, false, SDL_GPU_TEXTUREFORMAT_INVALID);
            int compared;
            int bad = check_border(px, linear, border_rgba[c], &compared);
            SDL_snprintf(detail, sizeof(detail), "%d of %d compared pixels differ; fnv %016llx", bad, compared,
                         (unsigned long long)fnv(px, W * H * 4));
            result(name, bad == 0 && compared > 0, detail);
            SDL_free(px);
            SDL_ReleaseGPUSampler(dev, smp);
        }
    }
    // The default border colour, with no property: transparent black.
    SDL_GPUSampler *smp = sampler(dev, SDL_GPU_FILTER_NEAREST, SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_BORDER, -1);
    Draw d = { p, tex4, smp, { { 0 } }, 0.5f, -0.5f, 1.5f, 0.0f, 1.0f };
    uint8_t *px = render(dev, &d, 1, false, SDL_GPU_TEXTUREFORMAT_INVALID);
    int compared;
    int bad = check_border(px, false, border_rgba[0], &compared);
    SDL_snprintf(detail, sizeof(detail), "%d of %d compared pixels differ", bad, compared);
    result("border.default_color", bad == 0, detail);
    SDL_free(px);
    SDL_ReleaseGPUSampler(dev, smp);
    // The control: CLAMP_TO_EDGE must not read as any border colour outside [0, 1].
    smp = sampler(dev, SDL_GPU_FILTER_NEAREST, SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_EDGE, -1);
    d.smp = smp;
    px = render(dev, &d, 1, false, SDL_GPU_TEXTUREFORMAT_INVALID);
    int differing_from_every_border = 0, outside = 0;
    for (int c = 0; c < 3; c++) {
        int b = check_border(px, false, border_rgba[c], &compared);
        if (c == 0) {
            outside = b;
        } else {
            outside = b < outside ? b : outside;
        }
    }
    differing_from_every_border = outside;
    SDL_snprintf(detail, sizeof(detail), "at least %d outside pixels differ from every border colour", differing_from_every_border);
    result("border.control_clamp_to_edge", differing_from_every_border > 0, detail);
    SDL_free(px);
    SDL_ReleaseGPUSampler(dev, smp);
    SDL_ReleaseGPUTexture(dev, tex4);
    SDL_ReleaseGPUTexture(dev, texu);
    SDL_ReleaseGPUGraphicsPipeline(dev, p);
}

// Depth clamp beside depth clip. Draw A covers the target at clip z 0.9 in a viewport whose depth
// range is [0.25, 0.75] (window depth 0.70), pushed by 0.15 by a depth bias (D16_UNORM: r = 2^-16,
// so 9830 units) or by a shader-written depth of 0.85. A probe at window depth 0.80 in a [0, 1]
// viewport then passes LESS only if A's stored depth was not clamped to 0.75.
static void test_clamp(SDL_GPUDevice *dev)
{
    char name[128], detail[256];
    for (int mode = 0; mode < 2; mode++) {
        bool shader_depth = mode == 1;
        SDL_GPUTextureFormat fmt = shader_depth ? SDL_GPU_TEXTUREFORMAT_D32_FLOAT : SDL_GPU_TEXTUREFORMAT_D16_UNORM;
        for (int clamp = 0; clamp < 2; clamp++) {
            PipelineSpec pa = { false, shader_depth, true, fmt, true, true, clamp == 1, shader_depth ? 0.0f : 9830.0f };
            PipelineSpec pp = { false, false, true, fmt, true, true, false, 0.0f };
            SDL_GPUGraphicsPipeline *a = pipeline(dev, pa), *probe = pipeline(dev, pp);
            SDL_snprintf(name, sizeof(name), "clamp.%s.%s", shader_depth ? "frag_depth" : "depth_bias",
                         clamp ? "clip_and_clamp" : "clip_only");
            if (!a || !probe) {
                result(name, false, SDL_GetError());
                continue;
            }
            Solid green = SOLID_GREEN;
            green.depth[0] = 0.85f;
            Draw d[2] = {
                { a, NULL, NULL, green, 0.9f, 0, 0, 0.25f, 0.75f },
                { probe, NULL, NULL, SOLID_RED, 0.8f, 0, 0, 0.0f, 1.0f },
            };
            uint8_t *px = render(dev, d, 2, true, fmt);
            int g = count_color(px, GREEN), r = count_color(px, RED);
            // Clamped: A stored 0.75, the probe (0.80) fails LESS, all green. Clip only: A stored
            // 0.85, the probe passes, all red.
            bool ok = clamp ? (g == W * H) : (r == W * H);
            SDL_snprintf(detail, sizeof(detail), "green %d, red %d of %d", g, r, W * H);
            result(name, ok, detail);
            SDL_free(px);
            SDL_ReleaseGPUGraphicsPipeline(dev, a);
            SDL_ReleaseGPUGraphicsPipeline(dev, probe);
        }
    }
    // Depth clip stays on with the clamp: a quad entirely beyond the far plane (clip z 1.5) is
    // clipped with clip and clamp and with clip only, and drawn with clamp only.
    struct { const char *name; bool clip; bool prop; bool drawn; } cases[3] = {
        { "clip.far_plane.clip_and_clamp", true, true, false },
        { "clip.far_plane.clip_only", true, false, false },
        { "clip.far_plane.clamp_only", false, false, true },
    };
    for (int i = 0; i < 3; i++) {
        PipelineSpec ps = { false, false, false, SDL_GPU_TEXTUREFORMAT_INVALID, false, cases[i].clip, cases[i].prop, 0.0f };
        SDL_GPUGraphicsPipeline *p = pipeline(dev, ps);
        if (!p) {
            result(cases[i].name, false, SDL_GetError());
            continue;
        }
        Draw d = { p, NULL, NULL, SOLID_RED, 1.5f, 0, 0, 0.0f, 1.0f };
        uint8_t *px = render(dev, &d, 1, false, SDL_GPU_TEXTUREFORMAT_INVALID);
        int r = count_color(px, RED), b = count_color(px, BLACK);
        SDL_snprintf(detail, sizeof(detail), "red %d, black %d of %d", r, b, W * H);
        result(cases[i].name, cases[i].drawn ? r == W * H : b == W * H, detail);
        SDL_free(px);
        SDL_ReleaseGPUGraphicsPipeline(dev, p);
    }
}

static void test_validation(void)
{
    char detail[256];
    // Debug mode: SDL's own checks refuse, through SDL_assert_release, before the backend runs.
    DeviceSpec dbg_noclamp = { true, false, false };
    SDL_GPUDevice *dev = create_device(dbg_noclamp);
    if (!dev) {
        result("validation.debug", false, "no device");
        return;
    }
    int before = assertions;
    SDL_GPUSampler *s = sampler(dev, SDL_GPU_FILTER_NEAREST, SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_BORDER, 7);
    SDL_snprintf(detail, sizeof(detail), "sampler %s, assertions %d, last '%s'", s ? "created" : "refused", assertions - before, last_assertion);
    result("validation.debug.border_color_out_of_range", !s && assertions - before == 1, detail);
    if (s) {
        SDL_ReleaseGPUSampler(dev, s);
    }
    before = assertions;
    s = sampler(dev, SDL_GPU_FILTER_NEAREST, (SDL_GPUSamplerAddressMode)4, -1);
    SDL_snprintf(detail, sizeof(detail), "sampler %s, assertions %d, last '%s'", s ? "created" : "refused", assertions - before, last_assertion);
    result("validation.debug.address_mode_out_of_range", !s && assertions - before == 1, detail);
    if (s) {
        SDL_ReleaseGPUSampler(dev, s);
    }
    before = assertions;
    PipelineSpec ps = { false, false, true, SDL_GPU_TEXTUREFORMAT_D16_UNORM, true, true, true, 0.0f };
    SDL_GPUGraphicsPipeline *p = pipeline(dev, ps);
    SDL_snprintf(detail, sizeof(detail), "pipeline %s, assertions %d, last '%s'", p ? "created" : "refused", assertions - before, last_assertion);
    result("validation.debug.clamp_without_capability", !p && assertions - before == 1, detail);
    if (p) {
        SDL_ReleaseGPUGraphicsPipeline(dev, p);
    }
    // The same calls succeed where they are valid, so the refusals are not blanket ones.
    before = assertions;
    s = sampler(dev, SDL_GPU_FILTER_NEAREST, SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_BORDER, SDL_GPU_SAMPLERBORDERCOLOR_OPAQUE_WHITE);
    result("validation.debug.valid_border_sampler", s != NULL && assertions == before, s ? "created" : SDL_GetError());
    if (s) {
        SDL_ReleaseGPUSampler(dev, s);
    }
    SDL_DestroyGPUDevice(dev);

    // Without debug mode the backend still refuses what it cannot build.
    DeviceSpec nodbg_noclamp = { false, false, false };
    dev = create_device(nodbg_noclamp);
    if (!dev) {
        result("validation.backend", false, "no device");
        return;
    }
    before = assertions;
    p = pipeline(dev, ps);
    SDL_snprintf(detail, sizeof(detail), "pipeline %s, error '%s', assertions %d", p ? "created" : "refused", SDL_GetError(), assertions - before);
    result("validation.backend.clamp_without_capability", !p && assertions == before, detail);
    if (p) {
        SDL_ReleaseGPUGraphicsPipeline(dev, p);
    }
    s = sampler(dev, SDL_GPU_FILTER_NEAREST, SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_BORDER, 7);
    SDL_snprintf(detail, sizeof(detail), "sampler %s, error '%s'", s ? "created" : "refused", SDL_GetError());
    result("validation.backend.border_color_out_of_range", !s, detail);
    if (s) {
        SDL_ReleaseGPUSampler(dev, s);
    }
    SDL_DestroyGPUDevice(dev);
}

// Stock-API draws only: a CLAMP_TO_EDGE NEAREST textured quad and a clip-only depth draw. Their
// pixels are literal, and their hashes are printed so that the same binary's runs on the stock
// and the patched library can be compared.
static void test_neutral(SDL_GPUDevice *dev)
{
    char detail[256];
    PipelineSpec ps = { true, false, false, SDL_GPU_TEXTUREFORMAT_INVALID, false, true, false, 0.0f };
    SDL_GPUGraphicsPipeline *p = pipeline(dev, ps);
    SDL_GPUTexture *tex = texture(dev, 4, 4, texels);
    SDL_GPUSampler *smp = sampler(dev, SDL_GPU_FILTER_NEAREST, SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_EDGE, -1);
    Draw d = { p, tex, smp, { { 0 } }, 0.5f, -0.5f, 1.5f, 0.0f, 1.0f };
    uint8_t *px = render(dev, &d, 1, false, SDL_GPU_TEXTUREFORMAT_INVALID);
    // CLAMP_TO_EDGE: every pixel reads texel (clamp(floor(4u), 0, 3), clamp(floor(4v), 0, 3)).
    int bad = 0;
    for (int y = 0; y < H; y++) {
        for (int x = 0; x < W; x++) {
            int nx = 2 * x - 31, ny = 2 * (H - 1 - y) - 31;
            int tx = nx < 0 ? 0 : (nx * 4) / 64, ty = ny < 0 ? 0 : (ny * 4) / 64;
            tx = tx > 3 ? 3 : tx;
            ty = ty > 3 ? 3 : ty;
            bad += SDL_memcmp(&px[4 * (y * W + x)], &texels[4 * (4 * ty + tx)], 4) != 0;
        }
    }
    SDL_snprintf(detail, sizeof(detail), "%d of %d pixels differ; fnv %016llx", bad, W * H, (unsigned long long)fnv(px, W * H * 4));
    result("neutral.clamp_to_edge", bad == 0, detail);
    SDL_free(px);
    SDL_ReleaseGPUSampler(dev, smp);
    SDL_ReleaseGPUTexture(dev, tex);
    SDL_ReleaseGPUGraphicsPipeline(dev, p);

    // Clip only, biased: the stored depth is not clamped and the probe shows (all red).
    PipelineSpec pa = { false, false, true, SDL_GPU_TEXTUREFORMAT_D16_UNORM, true, true, false, 9830.0f };
    PipelineSpec pp = { false, false, true, SDL_GPU_TEXTUREFORMAT_D16_UNORM, true, true, false, 0.0f };
    SDL_GPUGraphicsPipeline *a = pipeline(dev, pa), *probe = pipeline(dev, pp);
    Draw dd[2] = {
        { a, NULL, NULL, SOLID_GREEN, 0.9f, 0, 0, 0.25f, 0.75f },
        { probe, NULL, NULL, SOLID_RED, 0.8f, 0, 0, 0.0f, 1.0f },
    };
    px = render(dev, dd, 2, true, SDL_GPU_TEXTUREFORMAT_D16_UNORM);
    int r = count_color(px, RED);
    SDL_snprintf(detail, sizeof(detail), "red %d of %d; fnv %016llx", r, W * H, (unsigned long long)fnv(px, W * H * 4));
    result("neutral.clip_only_depth", r == W * H, detail);
    SDL_free(px);
    SDL_ReleaseGPUGraphicsPipeline(dev, a);
    SDL_ReleaseGPUGraphicsPipeline(dev, probe);
}

int main(int argc, char **argv)
{
    if (argc < 2) {
        fprintf(stderr, "usage: border_clamp <shader-dir> [--neutral-only | --stock-detect]\n");
        return 2;
    }
    shader_dir = argv[1];
    bool neutral_only = argc > 2 && SDL_strcmp(argv[2], "--neutral-only") == 0;
    bool stock_detect = argc > 2 && SDL_strcmp(argv[2], "--stock-detect") == 0;
    if (argc > 2 && !neutral_only && !stock_detect) {
        fprintf(stderr, "border_clamp: unknown mode %s\n", argv[2]);
        return 2;
    }
    SDL_SetAssertionHandler(count_assertion, NULL);
    if (!SDL_Init(SDL_INIT_VIDEO)) {
        fprintf(stderr, "SDL_Init: %s\n", SDL_GetError());
        return 2;
    }
    printf("{\"sdl_revision\": \"%s\", \"sdl_version\": %d}\n", SDL_GetRevision(), SDL_GetVersion());
    make_texels();

    DeviceSpec plain = { false, true, false };
    SDL_GPUDevice *dev = stock_detect ? NULL : create_device(plain);
    if (stock_detect) {
        test_stock_detect();
    } else if (!dev) {
        result("device", false, SDL_GetError());
    } else {
        test_neutral(dev);
        if (!neutral_only) {
            test_border(dev);
            test_clamp(dev);
        }
        SDL_DestroyGPUDevice(dev);
    }
    if (!neutral_only && !stock_detect) {
        test_properties();
        test_validation();
    }
    SDL_Quit();
    if (failures) {
        printf("BORDER_CLAMP_FAIL %d\n", failures);
        return 1;
    }
    printf("BORDER_CLAMP_OK\n");
    return 0;
}
