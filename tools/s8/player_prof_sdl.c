/* tools/s8/player_prof_sdl.c: the player's SDL GPU calls, counted per frame by kind (LD_PRELOAD).
 *
 * Built by `tools/s8/player_prof.py sdl-counter` and preloaded into a Linux player
 * (LD_PRELOAD=<lib> SA2_SDLCOUNT_OUT=<dir>). Each SDL_gpu function the player's device calls
 * (host/d3d9gpu/d9g_gpu_sdl.cpp) is wrapped: the call is counted, timed with the TSC (SDL's own
 * work and the Vulkan driver's under it), and held to the last call of its kind:
 *   - in the same render pass (SDL_BeginGPURenderPass resets them, as SDL's own state does): the
 *     pipeline, the vertex buffers, the index buffer, the vertex and fragment samplers, the
 *     viewport, the scissor, the blend constants, the stencil reference;
 *   - in the same command buffer: each uniform slot's bytes (SDL keeps a slot's data for every
 *     later draw of the command buffer).
 * A call equal to the last one is redundant: skipping it leaves SDL's state as it was. A draw
 * whose every bind since the draw before it was redundant (or absent) is counted as "same state".
 * One row per frame (a frame ends at SDL_WaitAndAcquireGPUSwapchainTexture, the Present's) in
 * <dir>/sdl-gpu-calls.tsv, and the totals with the ns per call in <dir>/sdl-gpu-calls.json at
 * exit. The wrappers cost the run about 20 ns a call: a counting run is not a speed run.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <x86intrin.h>

#include <SDL3/SDL.h>

enum {
    K_PIPELINE, K_VB, K_IB, K_VSAMP, K_FSAMP, K_VUNI, K_FUNI, K_VIEWPORT, K_SCISSOR, K_BLEND, K_STENCIL,
    K_DRAW_INDEXED, K_DRAW, K_BEGIN_RP, K_END_RP, K_BEGIN_CP, K_END_CP, K_UPLOAD_BUF, K_UPLOAD_TEX, K_MAP, K_UNMAP,
    K_ACQUIRE_CB, K_SUBMIT, K_SUBMIT_FENCE, K_SWAPCHAIN, K_DOWNLOAD, K_BLIT, K_COPY_TEX, K_WAIT_FENCES,
    K_CLEAR_ERROR, K_GET_ERROR, K_CREATE, K_RELEASE, K_COUNT
};
static const char *const kNames[K_COUNT] = {
    "bind_pipeline", "bind_vertex_buffers", "bind_index_buffer", "bind_vertex_samplers", "bind_fragment_samplers",
    "push_vertex_uniforms", "push_fragment_uniforms", "set_viewport", "set_scissor", "set_blend_constants",
    "set_stencil_reference", "draw_indexed", "draw", "begin_render_pass", "end_render_pass", "begin_copy_pass",
    "end_copy_pass", "upload_to_buffer", "upload_to_texture", "map_transfer_buffer", "unmap_transfer_buffer",
    "acquire_command_buffer", "submit", "submit_and_acquire_fence", "acquire_swapchain_texture", "download_from_texture",
    "blit_texture", "copy_texture_to_texture", "wait_for_fences", "clear_error", "get_error", "create_buffer",
    "release_object"};

struct Tally { uint64_t calls, redundant, bytes, ticks; };
static struct Tally g_total[K_COUNT], g_frame[K_COUNT];
static uint64_t g_frames, g_same_state_draws, g_frame_same_state, g_draws_total;
/* draws that, with the redundant calls skipped, would need no new descriptor set from SDL: no
 * pipeline bind, sampler change or uniform change since the draw before (SDL_gpu_vulkan.c's
 * VULKAN_INTERNAL_BindGraphicsDescriptorSets then returns at once, past a vertex-buffer bind) */
static uint64_t g_descriptor_clean_draws, g_frame_descriptor_clean;
static FILE *g_tsv;
static char g_dir[1024];
static uint64_t g_tsc0, g_ns0;

/* the render pass's state, and the command buffer's uniforms */
static struct {
    void *pipeline;
    int has_pipeline;
    SDL_GPUBufferBinding vb[16]; uint32_t vb_first, vb_n; int has_vb;
    SDL_GPUBufferBinding ib; int ib_size; int has_ib;
    SDL_GPUTextureSamplerBinding vs[16]; uint32_t vs_first, vs_n; int has_vs;
    SDL_GPUTextureSamplerBinding fs[16]; uint32_t fs_first, fs_n; int has_fs;
    SDL_GPUViewport viewport; int has_viewport;
    SDL_Rect scissor; int has_scissor;
    SDL_FColor blend; int has_blend;
    Uint8 stencil; int has_stencil;
    int changed_since_draw;
    int descriptors_since_draw;
} P;
static struct { uint8_t bytes[4096]; uint32_t len; int has; } U[2][4];

static uint64_t ns_now(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ull + (uint64_t)ts.tv_nsec;
}

static void *real(const char *name) {
    void *f = dlsym(RTLD_NEXT, name);
    if (!f) { fprintf(stderr, "player_prof_sdl: no %s\n", name); abort(); }
    return f;
}

static void tally(int k, int redundant, uint64_t bytes, uint64_t ticks) {
    g_frame[k].calls++; g_frame[k].redundant += redundant != 0; g_frame[k].bytes += bytes; g_frame[k].ticks += ticks;
}

static void open_out(void) {
    if (g_tsv || !getenv("SA2_SDLCOUNT_OUT")) return;
    snprintf(g_dir, sizeof g_dir, "%s", getenv("SA2_SDLCOUNT_OUT"));
    char p[1100];
    snprintf(p, sizeof p, "%s/sdl-gpu-calls.tsv", g_dir);
    g_tsv = fopen(p, "w");
    if (!g_tsv) return;
    setvbuf(g_tsv, NULL, _IOFBF, 1 << 20);
    fputs("frame\tsame_state_draws", g_tsv);
    for (int k = 0; k < K_COUNT; k++) fprintf(g_tsv, "\t%s\t%s_redundant", kNames[k], kNames[k]);
    fputs("\n", g_tsv);
}

static void end_frame(void) {
    open_out();
    g_frames++;
    if (g_tsv) {
        fprintf(g_tsv, "%llu\t%llu", (unsigned long long)g_frames, (unsigned long long)g_frame_same_state);
        for (int k = 0; k < K_COUNT; k++)
            fprintf(g_tsv, "\t%llu\t%llu", (unsigned long long)g_frame[k].calls, (unsigned long long)g_frame[k].redundant);
        fputs("\n", g_tsv);
    }
    for (int k = 0; k < K_COUNT; k++) {
        g_total[k].calls += g_frame[k].calls; g_total[k].redundant += g_frame[k].redundant;
        g_total[k].bytes += g_frame[k].bytes; g_total[k].ticks += g_frame[k].ticks;
    }
    memset(g_frame, 0, sizeof g_frame);
    g_same_state_draws += g_frame_same_state;
    g_frame_same_state = 0;
    g_descriptor_clean_draws += g_frame_descriptor_clean;
    g_frame_descriptor_clean = 0;
}

__attribute__((constructor)) static void start(void) { g_tsc0 = __rdtsc(); g_ns0 = ns_now(); }

static uint64_t calls_seen(void) {
    uint64_t n = 0;
    for (int k = 0; k < K_COUNT; k++) n += g_total[k].calls + g_frame[k].calls;
    return n;
}

/* Only a process that called SDL writes (the preload reaches the shells and wrappers around the
 * player too, which would otherwise overwrite its files with empty ones). */
__attribute__((destructor)) static void finish(void) {
    if (!getenv("SA2_SDLCOUNT_OUT") || !calls_seen()) return;
    end_frame();
    g_frames--;                                  /* the partial frame at exit is in the totals, not a frame */
    const double ns_per_tick = (double)(ns_now() - g_ns0) / (double)(__rdtsc() - g_tsc0);
    char p[1100];
    snprintf(p, sizeof p, "%s/sdl-gpu-calls.json", g_dir);
    FILE *f = fopen(p, "w");
    if (!f) return;
    fprintf(f, "{\"schema\": \"sa2-player-prof-sdl/v1\", \"frames\": %llu, \"draws\": %llu, \"same_state_draws\": %llu, "
               "\"descriptor_clean_draws\": %llu, \"calls\": {", (unsigned long long)g_frames, (unsigned long long)g_draws_total,
            (unsigned long long)g_same_state_draws, (unsigned long long)g_descriptor_clean_draws);
    for (int k = 0; k < K_COUNT; k++) {
        const struct Tally *t = &g_total[k];
        const double fr = g_frames ? (double)g_frames : 1.0;
        fprintf(f, "%s\n  \"%s\": {\"calls\": %llu, \"redundant\": %llu, \"bytes\": %llu, \"per_frame\": %.1f, "
                   "\"redundant_per_frame\": %.1f, \"ms_per_frame\": %.4f, \"ns_per_call\": %.1f}",
                k ? "," : "", kNames[k], (unsigned long long)t->calls, (unsigned long long)t->redundant,
                (unsigned long long)t->bytes, (double)t->calls / fr, (double)t->redundant / fr,
                (double)t->ticks * ns_per_tick / 1e6 / fr, t->calls ? (double)t->ticks * ns_per_tick / (double)t->calls : 0.0);
    }
    fputs("\n}}\n", f);
    fclose(f);
    if (g_tsv) fclose(g_tsv);
}

#define TIMED(k, red, bytes, call) do { const uint64_t t0_ = __rdtsc(); call; tally((k), (red), (bytes), __rdtsc() - t0_); } while (0)
#define CHANGED(red) do { if (!(red)) P.changed_since_draw = 1; } while (0)
#define DESCRIPTORS(red) do { if (!(red)) P.descriptors_since_draw = 1; } while (0)

void SDL_BindGPUGraphicsPipeline(SDL_GPURenderPass *rp, SDL_GPUGraphicsPipeline *pl) {
    static void (*f)(SDL_GPURenderPass *, SDL_GPUGraphicsPipeline *);
    if (!f) f = real(__func__);
    const int red = P.has_pipeline && P.pipeline == (void *)pl;
    P.pipeline = pl; P.has_pipeline = 1; CHANGED(red); DESCRIPTORS(red);
    TIMED(K_PIPELINE, red, 0, f(rp, pl));
}

void SDL_BindGPUVertexBuffers(SDL_GPURenderPass *rp, Uint32 first, const SDL_GPUBufferBinding *b, Uint32 n) {
    static void (*f)(SDL_GPURenderPass *, Uint32, const SDL_GPUBufferBinding *, Uint32);
    if (!f) f = real(__func__);
    const int fits = n <= 16;
    const int red = fits && P.has_vb && P.vb_first == first && P.vb_n == n && !memcmp(P.vb, b, n * sizeof *b);
    if (fits) { memcpy(P.vb, b, n * sizeof *b); P.vb_first = first; P.vb_n = n; P.has_vb = 1; } else P.has_vb = 0;
    CHANGED(red);
    TIMED(K_VB, red, 0, f(rp, first, b, n));
}

void SDL_BindGPUIndexBuffer(SDL_GPURenderPass *rp, const SDL_GPUBufferBinding *b, SDL_GPUIndexElementSize sz) {
    static void (*f)(SDL_GPURenderPass *, const SDL_GPUBufferBinding *, SDL_GPUIndexElementSize);
    if (!f) f = real(__func__);
    const int red = P.has_ib && !memcmp(&P.ib, b, sizeof *b) && P.ib_size == (int)sz;
    P.ib = *b; P.ib_size = (int)sz; P.has_ib = 1; CHANGED(red);
    TIMED(K_IB, red, 0, f(rp, b, sz));
}

static void samplers(int k, SDL_GPURenderPass *rp, Uint32 first, const SDL_GPUTextureSamplerBinding *b, Uint32 n,
                     void (*f)(SDL_GPURenderPass *, Uint32, const SDL_GPUTextureSamplerBinding *, Uint32)) {
    SDL_GPUTextureSamplerBinding *last = k == K_VSAMP ? P.vs : P.fs;
    uint32_t *lf = k == K_VSAMP ? &P.vs_first : &P.fs_first, *ln = k == K_VSAMP ? &P.vs_n : &P.fs_n;
    int *has = k == K_VSAMP ? &P.has_vs : &P.has_fs;
    const int fits = n <= 16;
    const int red = fits && *has && *lf == first && *ln == n && !memcmp(last, b, n * sizeof *b);
    if (fits) { memcpy(last, b, n * sizeof *b); *lf = first; *ln = n; *has = 1; } else *has = 0;
    CHANGED(red); DESCRIPTORS(red);
    TIMED(k, red, 0, f(rp, first, b, n));
}

void SDL_BindGPUVertexSamplers(SDL_GPURenderPass *rp, Uint32 first, const SDL_GPUTextureSamplerBinding *b, Uint32 n) {
    static void (*f)(SDL_GPURenderPass *, Uint32, const SDL_GPUTextureSamplerBinding *, Uint32);
    if (!f) f = real(__func__);
    samplers(K_VSAMP, rp, first, b, n, f);
}

void SDL_BindGPUFragmentSamplers(SDL_GPURenderPass *rp, Uint32 first, const SDL_GPUTextureSamplerBinding *b, Uint32 n) {
    static void (*f)(SDL_GPURenderPass *, Uint32, const SDL_GPUTextureSamplerBinding *, Uint32);
    if (!f) f = real(__func__);
    samplers(K_FSAMP, rp, first, b, n, f);
}

static void uniforms(int k, SDL_GPUCommandBuffer *cb, Uint32 slot, const void *data, Uint32 len,
                     void (*f)(SDL_GPUCommandBuffer *, Uint32, const void *, Uint32)) {
    const int stage = k == K_VUNI ? 0 : 1;
    int red = 0;
    if (slot < 4 && len <= 4096) {
        red = U[stage][slot].has && U[stage][slot].len == len && !memcmp(U[stage][slot].bytes, data, len);
        memcpy(U[stage][slot].bytes, data, len); U[stage][slot].len = len; U[stage][slot].has = 1;
    }
    CHANGED(red); DESCRIPTORS(red);
    TIMED(k, red, len, f(cb, slot, data, len));
}

void SDL_PushGPUVertexUniformData(SDL_GPUCommandBuffer *cb, Uint32 slot, const void *data, Uint32 len) {
    static void (*f)(SDL_GPUCommandBuffer *, Uint32, const void *, Uint32);
    if (!f) f = real(__func__);
    uniforms(K_VUNI, cb, slot, data, len, f);
}

void SDL_PushGPUFragmentUniformData(SDL_GPUCommandBuffer *cb, Uint32 slot, const void *data, Uint32 len) {
    static void (*f)(SDL_GPUCommandBuffer *, Uint32, const void *, Uint32);
    if (!f) f = real(__func__);
    uniforms(K_FUNI, cb, slot, data, len, f);
}

void SDL_SetGPUViewport(SDL_GPURenderPass *rp, const SDL_GPUViewport *v) {
    static void (*f)(SDL_GPURenderPass *, const SDL_GPUViewport *);
    if (!f) f = real(__func__);
    const int red = P.has_viewport && !memcmp(&P.viewport, v, sizeof *v);
    P.viewport = *v; P.has_viewport = 1; CHANGED(red);
    TIMED(K_VIEWPORT, red, 0, f(rp, v));
}

void SDL_SetGPUScissor(SDL_GPURenderPass *rp, const SDL_Rect *r) {
    static void (*f)(SDL_GPURenderPass *, const SDL_Rect *);
    if (!f) f = real(__func__);
    const int red = P.has_scissor && !memcmp(&P.scissor, r, sizeof *r);
    P.scissor = *r; P.has_scissor = 1; CHANGED(red);
    TIMED(K_SCISSOR, red, 0, f(rp, r));
}

void SDL_SetGPUBlendConstants(SDL_GPURenderPass *rp, SDL_FColor c) {
    static void (*f)(SDL_GPURenderPass *, SDL_FColor);
    if (!f) f = real(__func__);
    const int red = P.has_blend && !memcmp(&P.blend, &c, sizeof c);
    P.blend = c; P.has_blend = 1; CHANGED(red);
    TIMED(K_BLEND, red, 0, f(rp, c));
}

void SDL_SetGPUStencilReference(SDL_GPURenderPass *rp, Uint8 ref) {
    static void (*f)(SDL_GPURenderPass *, Uint8);
    if (!f) f = real(__func__);
    const int red = P.has_stencil && P.stencil == ref;
    P.stencil = ref; P.has_stencil = 1; CHANGED(red);
    TIMED(K_STENCIL, red, 0, f(rp, ref));
}

static void drew(void) {
    g_draws_total++;
    if (!P.changed_since_draw) g_frame_same_state++;
    if (!P.descriptors_since_draw) g_frame_descriptor_clean++;
    P.changed_since_draw = 0;
    P.descriptors_since_draw = 0;
}

void SDL_DrawGPUIndexedPrimitives(SDL_GPURenderPass *rp, Uint32 n, Uint32 inst, Uint32 first, Sint32 off, Uint32 fi) {
    static void (*f)(SDL_GPURenderPass *, Uint32, Uint32, Uint32, Sint32, Uint32);
    if (!f) f = real(__func__);
    drew();
    TIMED(K_DRAW_INDEXED, 0, 0, f(rp, n, inst, first, off, fi));
}

void SDL_DrawGPUPrimitives(SDL_GPURenderPass *rp, Uint32 n, Uint32 inst, Uint32 first, Uint32 fi) {
    static void (*f)(SDL_GPURenderPass *, Uint32, Uint32, Uint32, Uint32);
    if (!f) f = real(__func__);
    drew();
    TIMED(K_DRAW, 0, 0, f(rp, n, inst, first, fi));
}

SDL_GPURenderPass *SDL_BeginGPURenderPass(SDL_GPUCommandBuffer *cb, const SDL_GPUColorTargetInfo *ct, Uint32 n,
                                          const SDL_GPUDepthStencilTargetInfo *ds) {
    static SDL_GPURenderPass *(*f)(SDL_GPUCommandBuffer *, const SDL_GPUColorTargetInfo *, Uint32,
                                   const SDL_GPUDepthStencilTargetInfo *);
    if (!f) f = real(__func__);
    memset(&P, 0, sizeof P);
    P.changed_since_draw = 1;
    P.descriptors_since_draw = 1;
    SDL_GPURenderPass *r;
    TIMED(K_BEGIN_RP, 0, 0, r = f(cb, ct, n, ds));
    return r;
}

void SDL_EndGPURenderPass(SDL_GPURenderPass *rp) {
    static void (*f)(SDL_GPURenderPass *);
    if (!f) f = real(__func__);
    TIMED(K_END_RP, 0, 0, f(rp));
}

SDL_GPUCopyPass *SDL_BeginGPUCopyPass(SDL_GPUCommandBuffer *cb) {
    static SDL_GPUCopyPass *(*f)(SDL_GPUCommandBuffer *);
    if (!f) f = real(__func__);
    SDL_GPUCopyPass *r;
    TIMED(K_BEGIN_CP, 0, 0, r = f(cb));
    return r;
}

void SDL_EndGPUCopyPass(SDL_GPUCopyPass *cp) {
    static void (*f)(SDL_GPUCopyPass *);
    if (!f) f = real(__func__);
    TIMED(K_END_CP, 0, 0, f(cp));
}

void SDL_UploadToGPUBuffer(SDL_GPUCopyPass *cp, const SDL_GPUTransferBufferLocation *s, const SDL_GPUBufferRegion *d, bool cyc) {
    static void (*f)(SDL_GPUCopyPass *, const SDL_GPUTransferBufferLocation *, const SDL_GPUBufferRegion *, bool);
    if (!f) f = real(__func__);
    TIMED(K_UPLOAD_BUF, 0, d ? d->size : 0, f(cp, s, d, cyc));
}

void SDL_UploadToGPUTexture(SDL_GPUCopyPass *cp, const SDL_GPUTextureTransferInfo *s, const SDL_GPUTextureRegion *d, bool cyc) {
    static void (*f)(SDL_GPUCopyPass *, const SDL_GPUTextureTransferInfo *, const SDL_GPUTextureRegion *, bool);
    if (!f) f = real(__func__);
    TIMED(K_UPLOAD_TEX, 0, d ? (uint64_t)d->w * d->h * (d->d ? d->d : 1) : 0, f(cp, s, d, cyc));
}

void *SDL_MapGPUTransferBuffer(SDL_GPUDevice *dev, SDL_GPUTransferBuffer *tb, bool cyc) {
    static void *(*f)(SDL_GPUDevice *, SDL_GPUTransferBuffer *, bool);
    if (!f) f = real(__func__);
    void *r;
    TIMED(K_MAP, 0, 0, r = f(dev, tb, cyc));
    return r;
}

void SDL_UnmapGPUTransferBuffer(SDL_GPUDevice *dev, SDL_GPUTransferBuffer *tb) {
    static void (*f)(SDL_GPUDevice *, SDL_GPUTransferBuffer *);
    if (!f) f = real(__func__);
    TIMED(K_UNMAP, 0, 0, f(dev, tb));
}

SDL_GPUCommandBuffer *SDL_AcquireGPUCommandBuffer(SDL_GPUDevice *dev) {
    static SDL_GPUCommandBuffer *(*f)(SDL_GPUDevice *);
    if (!f) f = real(__func__);
    memset(U, 0, sizeof U);
    SDL_GPUCommandBuffer *r;
    TIMED(K_ACQUIRE_CB, 0, 0, r = f(dev));
    return r;
}

bool SDL_SubmitGPUCommandBuffer(SDL_GPUCommandBuffer *cb) {
    static bool (*f)(SDL_GPUCommandBuffer *);
    if (!f) f = real(__func__);
    bool r;
    TIMED(K_SUBMIT, 0, 0, r = f(cb));
    return r;
}

SDL_GPUFence *SDL_SubmitGPUCommandBufferAndAcquireFence(SDL_GPUCommandBuffer *cb) {
    static SDL_GPUFence *(*f)(SDL_GPUCommandBuffer *);
    if (!f) f = real(__func__);
    SDL_GPUFence *r;
    TIMED(K_SUBMIT_FENCE, 0, 0, r = f(cb));
    return r;
}

bool SDL_WaitAndAcquireGPUSwapchainTexture(SDL_GPUCommandBuffer *cb, SDL_Window *w, SDL_GPUTexture **t, Uint32 *tw, Uint32 *th) {
    static bool (*f)(SDL_GPUCommandBuffer *, SDL_Window *, SDL_GPUTexture **, Uint32 *, Uint32 *);
    if (!f) f = real(__func__);
    bool r;
    TIMED(K_SWAPCHAIN, 0, 0, r = f(cb, w, t, tw, th));
    end_frame();
    return r;
}

bool SDL_WaitForGPUFences(SDL_GPUDevice *dev, bool all, SDL_GPUFence *const *fences, Uint32 n) {
    static bool (*f)(SDL_GPUDevice *, bool, SDL_GPUFence *const *, Uint32);
    if (!f) f = real(__func__);
    bool r;
    TIMED(K_WAIT_FENCES, 0, 0, r = f(dev, all, fences, n));
    return r;
}

void SDL_DownloadFromGPUTexture(SDL_GPUCopyPass *cp, const SDL_GPUTextureRegion *s, const SDL_GPUTextureTransferInfo *d) {
    static void (*f)(SDL_GPUCopyPass *, const SDL_GPUTextureRegion *, const SDL_GPUTextureTransferInfo *);
    if (!f) f = real(__func__);
    TIMED(K_DOWNLOAD, 0, 0, f(cp, s, d));
}

void SDL_BlitGPUTexture(SDL_GPUCommandBuffer *cb, const SDL_GPUBlitInfo *info) {
    static void (*f)(SDL_GPUCommandBuffer *, const SDL_GPUBlitInfo *);
    if (!f) f = real(__func__);
    TIMED(K_BLIT, 0, 0, f(cb, info));
}

void SDL_CopyGPUTextureToTexture(SDL_GPUCopyPass *cp, const SDL_GPUTextureLocation *s, const SDL_GPUTextureLocation *d,
                                 Uint32 w, Uint32 h, Uint32 dep, bool cyc) {
    static void (*f)(SDL_GPUCopyPass *, const SDL_GPUTextureLocation *, const SDL_GPUTextureLocation *, Uint32, Uint32,
                     Uint32, bool);
    if (!f) f = real(__func__);
    TIMED(K_COPY_TEX, 0, 0, f(cp, s, d, w, h, dep, cyc));
}

bool SDL_ClearError(void) {
    static bool (*f)(void);
    if (!f) f = real(__func__);
    bool r;
    TIMED(K_CLEAR_ERROR, 0, 0, r = f());
    return r;
}

const char *SDL_GetError(void) {
    static const char *(*f)(void);
    if (!f) f = real(__func__);
    const char *r;
    TIMED(K_GET_ERROR, 0, 0, r = f());
    return r;
}

#define RELEASE(name, type) \
    void name(SDL_GPUDevice *dev, type *o) { \
        static void (*f)(SDL_GPUDevice *, type *); \
        if (!f) f = (void (*)(SDL_GPUDevice *, type *))real(#name); \
        TIMED(K_RELEASE, 0, 0, f(dev, o)); }
SDL_GPUBuffer *SDL_CreateGPUBuffer(SDL_GPUDevice *dev, const SDL_GPUBufferCreateInfo *ci) {
    static SDL_GPUBuffer *(*f)(SDL_GPUDevice *, const SDL_GPUBufferCreateInfo *);
    if (!f) f = real(__func__);
    SDL_GPUBuffer *r;
    TIMED(K_CREATE, 0, ci ? ci->size : 0, r = f(dev, ci));
    return r;
}

RELEASE(SDL_ReleaseGPUTransferBuffer, SDL_GPUTransferBuffer)
RELEASE(SDL_ReleaseGPUBuffer, SDL_GPUBuffer)
RELEASE(SDL_ReleaseGPUTexture, SDL_GPUTexture)
