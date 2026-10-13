#!/usr/bin/env python3
"""extract_refs.py: the reference code of the second host's state check, extracted from the
pinned DXVK checkout and the pinned SDL tarball (private/generated/s5/r2b/PLAN.md section 8.2).

Nothing third-party is tracked. This script is tracked; what it writes goes under the build
directory (OUT) only:

  dxvk_extract.inc        DXVK's own functions, copied by span from DXVK 3.1.1 at the pinned
                          commit, each under a #line directive naming its source line, with the
                          substitutions the span table lists (counted, each exactly as often as
                          the table says); compiled by test/ref_dxvk.cpp. Its FEATURES section
                          is the X-list of every DxvkDeviceFeatures member the spans and
                          ref_dxvk.cpp read (generated: ref_dxvk.cpp fills and requires exactly
                          these, the review's extraction finding 3)
  sdl_extract.inc         SDL 3.4.16's Vulkan tables, the state-filling spans of
                          VULKAN_CreateGraphicsPipeline and VULKAN_CreateSampler, and the debug
                          validation of SDL_CreateGPUGraphicsPipeline and SDL_CreateGPUSampler,
                          read from the tarball's members, with the project's SDL patches applied
                          when the install is the patched configuration (below); compiled by
                          test/ref_sdl.c
  sdl/include/SDL3/       the tarball's public headers, patched as the install is (ref_sdl.c
                          compiles against these only)
  sdl/khronos/            the tarball's Khronos Vulkan headers (VK_NO_PROTOTYPES)
  extract_manifest.json   per span: source, line range, sha256 of the source text, the
                          substitutions with their counts and what they cut, the pin; the
                          stand-ins (OPEN-1), the device features read, the transcriptions
                          ref_dxvk.cpp tags (each with the DXVK lines it cites), the two Vulkan
                          headers' shared enumerants, where ref_dxvk.cpp carries DXVK's notice,
                          the sections each including file takes, and every file written here
                          at its sha256 ("outputs", which verify-deps holds compiles to)
  render_state_audit.json every D3DRS token of DXVK's SetRenderState switch and PrepareDraw
                          (the critic's M5), for test/state_check.cpp to classify

Provenance is checked before anything is read: DXVK's HEAD is tools/pins.json's commit and
every file read hashes (git's blob hash of the very bytes read) to its blob at that commit
(submodule files at their gitlink commits); the SDL tarball's bytes hash to the pinned sha256
and are read from those bytes; the installed SDL3 headers equal the tarball's and the installed
DXVK Native headers equal the checkout's, byte for byte. Any difference refuses the extraction
(exit 2) with the reason.

The SDL configuration is the install's (tools/s5/build_host_deps.sh): its SDL_revision.h defines
SDL_VENDOR_INFO as tools.sdl3.vendor_info for the patched build, and none for the stock one;
anything else refuses. For the patched build the pinned patches (tools.sdl3.patches, under
patches/sdl3) are applied to a scratch copy of the members they name, strictly, by
tools/s5/sdl3_patches.py (the builder's own check and apply), and every SDL read after that,
the installed headers' comparison and every span, is of the patched members; the patched
configuration's spans (SDL_SPANS_PATCHED) add the patch's tables, its address-mode check and the
two locals its state spans read. The manifest records the configuration and the patches.

  extract_refs.py --out DIR [--dxvk ROOT]
  extract_refs.py verify-deps --deps FILE.d ... [--dxvk ROOT]
      every file a compile read (its -MD dependency file) is accounted for, by the compile's
      source: a DXVK compile (a DXVK file, ref_dxvk.cpp or ref_link_shims.cpp) may read DXVK
      files at their pinned blobs, dxvk_extract.inc at the manifest's hash and the named
      host/d3d9gpu files that make its side (DXVK_HOST_FILES), and must name a DXVK file; the SDL
      compile (ref_sdl.c) may read the extraction's SDL files (sdl_extract.inc, sdl/include,
      sdl/khronos, each at the manifest's hash) and SDL_HOST_FILES, and no DXVK file; both may
      read the system toolchain's headers (/usr/include, /usr/lib/gcc, /usr/lib/linux), never an
      installed Vulkan, SDL, DXVK or SPIR-V header nor /usr/local (SYSTEM_DENIED). Anything else,
      a relative path, or no DXVK file checked at all refuses (the review's extraction finding 1;
      the extraction re-review's findings 1 and 2)
  extract_refs.py dxvk-flags --source SRC [--record FILE]

Licences: DXVK and SDL are zlib-licensed. Each generated file begins with its source's licence
text and, per span, every alteration made to it (zlib clause 2, altered versions plainly
marked; clause 3, the notice kept).
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = Path(__file__).resolve().parents[3]
# The private tree (the pinned checkout, tarball and install): this checkout's, or SA2_REPO's when
# the build runs from another checkout of the repository (host/d3d9gpu/build.sh's REPO).
PRIVATE = Path(os.environ.get("SA2_REPO") or REPO)
REF_DXVK_CPP = HERE / "ref_dxvk.cpp"
REF_SDL_C = HERE / "ref_sdl.c"
# The SDL side's compile context, beside the per-span alterations (the extraction re-review's
# NIT 10): sdl_extract.inc's header carries it.
SDL_CONTEXT = ("Compiled in host/d3d9gpu/test/ref_sdl.c, which also alters the spans' context: SDL_assert_release\n"
               "is redefined to record its message (the span then returns NULL as SDL's does); the renderer,\n"
               "graphics pipeline and device are shim structures holding the fields the spans read; SDL_malloc,\n"
               "SDL_free and SDL_LogWarn are defined there; SDL_GPUTextureSupportsFormat is answered from the\n"
               "device's parameters; SDL's property getters (SDL_GetBooleanProperty, SDL_GetNumberProperty,\n"
               "SDL_HasProperty) answer a description's props as SDL does when it holds none (0: the default),\n"
               "SDL_GetGPUDeviceProperties gives the shim device's, which report neither of the patch's\n"
               "capabilities, and any other props stop the check (abort). It is built with -std=c11 -O2 against\n"
               "the tarball's headers alone, not SDL's own -O3 -DNDEBUG -fno-strict-aliasing, SDL_internal.h or\n"
               "VK_ENABLE_BETA_EXTENSIONS.")

# ----------------------------------------------------------------------------- span table
# A span: `start` is one source line (compared stripped; with match="prefix", the stripped line
# begins with it) that must occur exactly `occurs` times (or, with `after`, the first occurrence
# after the one line `after` names); `end` is "brace" (through the brace matching the first "{"
# at or after the start line), "brace-else" (the same, followed through every "} else {" of an
# if-else chain), "brace+assert" (a table and its SDL_COMPILE_TIME_ASSERT), "line" (the start
# line alone, or through the first line ending with ";"), "self" (the start line alone), or an
# exact end line (inclusive). `subs` are (old, new, count): each must occur exactly `count`
# times in the span. `wrap` puts the span in a generated function (for statement blocks).


@dataclass
class Span:
    name: str
    source: str
    start: str
    end: str = "brace"
    subs: list = field(default_factory=list)
    wrap: str | None = None       # "void D9gRecCtx::d9g_dyn_x() {" ... "}" around the span
    standins: list = field(default_factory=list)   # names of OPEN-1 stand-ins the subs introduce
    occurrence: int = 1           # the n-th occurrence of `start` (1-based); the count must be `occurs`
    occurs: int = 1
    statements: str | None = None  # a regex: keep only the statements whose first line matches
    drop_first: int = 0           # lines dropped from the span's head (a function's signature)
    section: str = ""             # the including file selects spans by section (D9G_SECTION_<NAME>)
    tail: str = ""                # a generated statement before the wrapper's closing brace
    match: str = "exact"          # "exact" or "prefix" (the start line)
    after: str | None = None      # the start is the first occurrence after this unique line
    cuts: list = field(default_factory=list)   # DXVK code a substitution or a shim leaves out, in words


D3D9 = "src/d3d9/d3d9_device.cpp"
D3D9H = "src/d3d9/d3d9_device.h"
STATE = "src/d3d9/d3d9_state.h"
OPTS = "src/d3d9/d3d9_options.cpp"
FMT = "src/d3d9/d3d9_format.cpp"
TEX = "src/d3d9/d3d9_common_texture.cpp"
CTX = "src/dxvk/dxvk_context.cpp"
CTXH = "src/dxvk/dxvk_context.h"
CMD = "src/dxvk/dxvk_cmdlist.h"
GFX = "src/dxvk/dxvk_graphics.cpp"
SMP = "src/dxvk/dxvk_sampler.cpp"
DEV = "D3D9DeviceEx::"
SHIM = "D9gDxvkShim::"
CTXQ = "DxvkContext::"
REC = "D9gRecCtx::"


def option_span(option: str, line: str, method: str) -> Span:
    return Span(f"D3D9Options.{option}", OPTS, line, end="line",
                wrap=f"void D9gD3D9Options::{method}(const D9gConfig& config) {{", section="options")


DXVK_SPANS: list[Span] = [
    # ---- section "types": D3D9 types whose headers cannot be included (d3d9_state.h reaches
    # dxbc-spirv's sm3 parser), at namespace scope
    Span("SamplerStateCount", STATE, "static constexpr uint32_t SamplerStateCount = D3DSAMP_DMAPOFFSET + 1;", end="line",
         section="types"),
    Span("D3D9SharedPushData", STATE, "struct D3D9SharedPushData {", section="types"),
    Span("D3D9VsPushData", STATE, "struct D3D9VsPushData {", section="types"),
    Span("D3D9FfvsPushData", STATE, "struct D3D9FfvsPushData {", section="types"),
    Span("D3D9SpecData", STATE, "struct D3D9SpecData {", section="types"),
    Span("D3D9SamplerInfo", STATE, "struct D3D9SamplerInfo {", section="types"),
    # ---- section "devh": d3d9_device.h's inline members, inside the device shim's class body
    Span("GetUPDataSize", D3D9H, "inline uint32_t GetUPDataSize(uint32_t vertexCount, uint32_t stride) {", section="devh"),
    Span("GetUPBufferSize", D3D9H, "inline uint32_t GetUPBufferSize(uint32_t vertexCount, uint32_t stride) {",
         section="devh"),
    Span("FillUPVertexBuffer", D3D9H,
         "inline void FillUPVertexBuffer(void* buffer, const void* userData, uint32_t dataSize, uint32_t bufferSize) {",
         section="devh"),
    Span("EncodePointSize", D3D9H, "inline static uint16_t EncodePointSize(DWORD Value) {", section="devh"),
    Span("IsNVDepthBoundsTestEnabled", D3D9H, "inline bool IsNVDepthBoundsTestEnabled () {", section="devh"),
    # ---- section "cmd": the command list's dynamic-state setters (the Vulkan calls themselves),
    # inside the command recorder's class body
    *[Span(f"DxvkCommandList.{n}", CMD, f"void {n}(", section="cmd") for n in (
        "cmdSetAlphaToCoverageState", "cmdSetDepthClipState", "cmdSetDepthBias", "cmdSetDepthBias2",
        "cmdSetDepthBounds", "cmdSetDepthTest", "cmdSetDepthWrite", "cmdSetDepthCompareOp",
        "cmdSetMultisampleState", "cmdSetRasterizerState", "cmdSetStencilTest", "cmdSetStencilOp",
        "cmdSetStencilCompareMask", "cmdSetStencilWriteMask")],
    # ---- section "fmt": DXVK's format table (D3D9VkFormatTable), the mapping every texture,
    # render target, depth-stencil surface and sampler view DXVK makes goes through (the review's
    # extraction finding 2): its constructor's support rules, fed from DxvkParams through the
    # shims (the adapter's vendor, CheckImageFormatSupport from DXVK's log), and GetFormatMapping
    Span("D3D9VkFormatTable", FMT, "D3D9VkFormatTable::D3D9VkFormatTable(",
         subs=[("D3D9VkFormatTable::D3D9VkFormatTable(", "D9gVkFormatTable::D9gVkFormatTable(", 1),
               ("D3D9Adapter*     pParent", "D9gFmtAdapter*   pParent", 1),
               ("const Rc<DxvkAdapter>& adapter", "const D9gFmtDxvkAdapter& adapter", 1),
               ("const D3D9Options&     options", "const D9gD3D9Options&  options", 1)], section="fmt"),
    Span("D3D9VkFormatTable.GetFormatMapping", FMT, "D3D9_VK_FORMAT_MAPPING D3D9VkFormatTable::GetFormatMapping(",
         subs=[("D3D9VkFormatTable::GetFormatMapping(", "D9gVkFormatTable::GetFormatMapping(", 1)], section="fmt"),
    # ---- section "shim": the D3D9 front end (D3D9 state to DXVK's state objects), as members
    # of the device shim (test/ref_dxvk.cpp D9gDxvkShim)
    Span("UpdateAlphaToCoverangeAndAlphaTest", D3D9, "void D3D9DeviceEx::UpdateAlphaToCoverangeAndAlphaTest() {",
         subs=[(DEV, SHIM, 1), ("D3D9CommonTexture", "ShimTexture", 1)], section="shim"),
    Span("BindMultiSampleState", D3D9, "void D3D9DeviceEx::BindMultiSampleState() {",
         subs=[(DEV, SHIM, 1), ("] (DxvkContext* ctx) {", "] (D9gRecCtx* ctx) {", 1)], section="shim"),
    Span("BindBlendState", D3D9, "void D3D9DeviceEx::BindBlendState() {",
         subs=[(DEV, SHIM, 1), ("](DxvkContext* ctx) {", "](D9gRecCtx* ctx) {", 1)], section="shim"),
    Span("BindDepthStencilState", D3D9, "void D3D9DeviceEx::BindDepthStencilState() {",
         subs=[(DEV, SHIM, 1), ("] (DxvkContext* ctx) mutable {", "] (D9gRecCtx* ctx) mutable {", 1)], section="shim"),
    Span("BindRasterizerState", D3D9, "void D3D9DeviceEx::BindRasterizerState() {",
         subs=[(DEV, SHIM, 1), ("](DxvkContext* ctx) {", "](D9gRecCtx* ctx) {", 1)], section="shim"),
    Span("BindDepthBias", D3D9, "void D3D9DeviceEx::BindDepthBias() {",
         subs=[(DEV, SHIM, 1), ("](DxvkContext* ctx) {", "](D9gRecCtx* ctx) {", 1)], section="shim"),
    Span("GetAlphaTestPrecision", D3D9, "uint32_t D3D9DeviceEx::GetAlphaTestPrecision(D3D9Surface* rt) {",
         subs=[(DEV, SHIM, 1), ("D3D9Surface* rt", "ShimSurface* rt", 1)], section="shim"),
    Span("BindAlphaTestState", D3D9, "void D3D9DeviceEx::BindAlphaTestState() {", subs=[(DEV, SHIM, 1)], section="shim"),
    Span("BindSampler", D3D9, "void D3D9DeviceEx::BindSampler(DWORD Sampler) {",
         subs=[(DEV, SHIM, 1), ("] (DxvkContext* ctx) {", "] (D9gRecCtx* ctx) {", 1), ("D3D9CommonTexture", "ShimTexture", 1),
               ("Rc<DxvkImageView> imageView;", "ShimViewRef imageView;", 1)],
         section="shim"),
    Span("SamplerUsesBorderColor", D3D9, "bool D3D9DeviceEx::SamplerUsesBorderColor(DWORD Sampler) const {",
         subs=[(DEV, SHIM, 1)], section="shim"),
    Span("UpdatePointMode", D3D9, "void D3D9DeviceEx::UpdatePointMode(bool pointList) {", subs=[(DEV, SHIM, 1)],
         section="shim"),
    Span("ApplyPrimitiveType", D3D9, "void D3D9DeviceEx::ApplyPrimitiveType(D3DPRIMITIVETYPE PrimType) {",
         subs=[(DEV, SHIM, 1), ("] (DxvkContext* ctx) {", "] (D9gRecCtx* ctx) {", 1)], section="shim"),
    # the texture's depth-compare state (m_shadow), which UpdateTextureBitmasks copies into the
    # slot's m_textureSlotTracking.depth bit when the texture is bound (the review's
    # transcription finding 3)
    Span("D3D9CommonTexture.DetermineShadowState", TEX, "BOOL D3D9CommonTexture::DetermineShadowState() const {",
         subs=[("D3D9CommonTexture::DetermineShadowState(", "ShimTexture::DetermineShadowState(", 1)], section="shim"),
    # PrepareDraw's depth-bounds block (NVDB, the critic's M5)
    Span("PrepareDraw.DepthBounds", D3D9, "if (unlikely(m_dirty.test(D3D9DeviceDirtyFlag::DepthBounds))) {",
         subs=[("] (DxvkContext* ctx) {", "] (D9gRecCtx* ctx) {", 1)],
         wrap="void D9gDxvkShim::d9g_prepare_depth_bounds() {", section="shim"),
    # The device constructor's depth-bias representation (VK_EXT_depth_bias_control, the critic's M7)
    Span("D3D9DeviceEx.DepthBiasRepresentation.init", D3D9,
         "m_depthBiasRepresentation = { VK_DEPTH_BIAS_REPRESENTATION_LEAST_REPRESENTABLE_VALUE_FORMAT_EXT, false };",
         end="line", wrap="void D9gDxvkShim::d9g_depth_bias_representation_init() {", section="shim"),
    Span("D3D9DeviceEx.DepthBiasRepresentation", D3D9, "if (dxvkDevice->features().extDepthBiasControl.depthBiasControl) {",
         subs=[("dxvkDevice->features()", "d9g_features()", 4)],
         wrap="void D9gDxvkShim::d9g_depth_bias_representation() {", section="shim"),
    # SetDepthStencilSurface's depth-bias scale (m_depthBiasScale, the critic's M6)
    Span("SetDepthStencilSurface.DepthBiasScale", D3D9,
         "if (ds != nullptr && m_depthBiasRepresentation.depthBiasRepresentation != VK_DEPTH_BIAS_REPRESENTATION_FLOAT_EXT) {",
         wrap="void D9gDxvkShim::d9g_depth_bias_scale(ShimSurface* ds) {", section="shim"),
    # ResetState: every render-state and sampler-state assignment, as statements
    Span("ResetState.renderStates", D3D9, "void D3D9DeviceEx::ResetState(D3DPRESENT_PARAMETERS* pPresentationParameters) {",
         statements=r"^\s*rs\[D3DRS_\w+\]\s*=",
         wrap="void D9gDxvkShim::d9g_reset_render_states(D3DPRESENT_PARAMETERS* pPresentationParameters, ShimRS& rs, "
              "const ShimLimits& limits) {", section="shim"),
    Span("ResetState.samplerStates", D3D9, "void D3D9DeviceEx::ResetState(D3DPRESENT_PARAMETERS* pPresentationParameters) {",
         statements=r"^\s*state\[D3DSAMP_\w+\]\s*=",
         wrap="void D9gDxvkShim::d9g_reset_sampler_states(std::array<DWORD, SamplerStateCount>& state) {", section="shim"),
    # ---- section "free": AllocUPBuffer's per-allocation alignment (the arena layout, N3)
    Span("AllocUPBuffer.align", D3D9, "VkDeviceSize alignedSize = align(size, CACHE_LINE_SIZE);", end="line",
         wrap="uint64_t d9g_dxvk_up_aligned(VkDeviceSize size) {", tail="return alignedSize;", section="free"),
    # ---- section "options": d3d9_options.cpp, the options the extracted code reads at their
    # getOption defaults (getOption's second argument; host/player/dxvk.conf sets none of them)
    option_span("samplerAnisotropy",
                'this->samplerAnisotropy             = config.getOption<int32_t>     ("d3d9.samplerAnisotropy",             -1);',
                "d9g_sampler_anisotropy"),
    option_span("seamlessCubes",
                'this->seamlessCubes                 = config.getOption<bool>        ("d3d9.seamlessCubes",                 false);',
                "d9g_seamless_cubes"),
    option_span("samplerLodBias",
                'this->samplerLodBias                = config.getOption<float>       ("d3d9.samplerLodBias",                0.0f);',
                "d9g_sampler_lod_bias"),
    option_span("clampNegativeLodBias",
                'this->clampNegativeLodBias          = config.getOption<bool>        ("d3d9.clampNegativeLodBias",          false);',
                "d9g_clamp_negative_lod_bias"),
    option_span("supportDFFormats",
                'this->supportDFFormats              = config.getOption<bool>        ("d3d9.supportDFFormats",              true);',
                "d9g_support_df_formats"),
    option_span("supportX4R4G4B4",
                'this->supportX4R4G4B4               = config.getOption<bool>        ("d3d9.supportX4R4G4B4",               true);',
                "d9g_support_x4r4g4b4"),
    option_span("useD32forD24",
                'this->useD32forD24                  = config.getOption<bool>        ("d3d9.useD32forD24",                  false);',
                "d9g_use_d32_for_d24"),
    Span("D3D9Options.samplerLodBiasClamp", OPTS, "this->samplerLodBias = dxvk::fclamp(this->samplerLodBias, -2.0f, 1.0f);",
         end="line", wrap="void D9gD3D9Options::d9g_sampler_lod_bias_clamp() {", section="options"),
    # ---- section "ctx": the context, DXVK's state objects into its pipeline state vector and
    # dynamic state, as members of the context shim (D9gRecCtx)
    *[Span(f"DxvkContext.{n}", CTX, f"void DxvkContext::{n}(", subs=[(CTXQ, REC, 1)], section="ctx") for n in (
        "setDepthBias", "setDepthBiasRepresentation", "setDepthBounds", "setInputLayout", "setBlendMode")],
    Span("DxvkContext.setInputAssemblyState", CTX, "void DxvkContext::setInputAssemblyState(const DxvkInputAssemblyState& ia) {",
         subs=[(CTXQ, REC, 1)], section="ctx"),
    Span("DxvkContext.setRasterizerState", CTX, "void DxvkContext::setRasterizerState(const DxvkRasterizerState& rs) {",
         subs=[(CTXQ, REC, 1)], section="ctx"),
    Span("DxvkContext.setMultisampleState", CTX, "void DxvkContext::setMultisampleState(const DxvkMultisampleState& ms) {",
         subs=[(CTXQ, REC, 1)], section="ctx"),
    Span("DxvkContext.setDepthStencilState", CTX, "void DxvkContext::setDepthStencilState(const DxvkDepthStencilState& ds) {",
         subs=[(CTXQ, REC, 1)], section="ctx"),
    Span("DxvkContext.convertStencilOp", CTX, "VkStencilOpState DxvkContext::convertStencilOp(", subs=[(CTXQ, REC, 1)],
         section="ctx"),
    # updateGraphicsPipelineState's choice of dynamic state (which states the pipeline leaves
    # dynamic, and so which updateDynamicState block records them): the pipeline type
    # (DXVK's optimized pipeline, or the base pipeline it links from graphics pipeline libraries
    # while the optimized one compiles) is an OPEN-1 stand-in, varied by the stand-ins' census;
    # canUseSampleLocations is a device property the log does not print, and only the
    # sample-location flags read it, which no compared fact does (the review's extraction
    # finding 4)
    Span("updateGraphicsPipelineState.dynamicFlags", CTX,
         "if (!m_state.gp.flags.test(DxvkGraphicsPipelineFlag::HasRasterizerDiscard)) {", end="brace-else",
         subs=[("pipelineInfo.type == DxvkGraphicsPipelineType::BasePipeline", "d9g_base_pipeline", 1),
               ("m_device->features()", "d9g_features()", 6),
               ("m_device->canUseSampleLocations(0u)", "d9g_can_use_sample_locations", 2)],
         standins=["gp_base_pipeline", "can_use_sample_locations"],
         wrap="void D9gRecCtx::d9g_dynamic_flags() {", section="ctx"),
    # updateDynamicState's blocks: what DXVK records into the command buffer at a draw
    *[Span(f"updateDynamicState.{n}", CTX, f"if (unlikely(m_flags.all(DxvkContextFlag::GpDirty{flag},",
           wrap=f"void D9gRecCtx::d9g_dyn_{fn}() {{", section="ctx",
           subs=[("m_device->features()", "d9g_features()", nf)] if nf else []) for n, flag, fn, nf in (
        ("DepthClip", "DepthClip", "depth_clip", 0), ("Multisample", "MultisampleState", "multisample", 1),
        ("Rasterizer", "RasterizerState", "rasterizer", 0), ("DepthTest", "DepthTest", "depth_test", 0),
        ("StencilTest", "StencilTest", "stencil_test", 0), ("DepthBias", "DepthBias", "depth_bias", 1),
        ("DepthBounds", "DepthBounds", "depth_bounds", 0))],
    # ---- section "gfx": the backend, DXVK's pipeline state vector into Vulkan create-info structures
    Span("determinePreGsTopology", GFX, "VkPrimitiveTopology determinePreGsTopology(",
         subs=[("const DxvkGraphicsPipelineShaders&", "const D9gShaders&", 1),
               ("shaders.tcs->metadata().flags.test(DxvkShaderFlag::TessellationPoints)", "shaders.tcs_tessellation_points", 1),
               ("shaders.tes->metadata().outputTopology", "shaders.tes_output_topology", 1)],
         standins=["tcs_tessellation_points", "tes_output_topology"], section="gfx"),
    Span("determinePipelineTopology", GFX, "VkPrimitiveTopology determinePipelineTopology(",
         subs=[("const DxvkGraphicsPipelineShaders&", "const D9gShaders&", 1),
               ("shaders.gs->metadata().outputTopology", "shaders.gs_output_topology", 1)],
         standins=["gs_output_topology"], section="gfx"),
    # the state structures' default constructors (their members' initialisers), defined in
    # dxvk_graphics.cpp, which the check does not link
    Span("DxvkGraphicsPipelineFragmentOutputState()", GFX,
         "DxvkGraphicsPipelineFragmentOutputState::DxvkGraphicsPipelineFragmentOutputState() {", section="gfx"),
    Span("DxvkGraphicsPipelinePreRasterizationState()", GFX,
         "DxvkGraphicsPipelinePreRasterizationState::DxvkGraphicsPipelinePreRasterizationState() {", section="gfx"),
    Span("DxvkGraphicsPipelineFragmentShaderState()", GFX,
         "DxvkGraphicsPipelineFragmentShaderState::DxvkGraphicsPipelineFragmentShaderState() {", section="gfx"),
    Span("DxvkGraphicsPipelineFragmentOutputState", GFX,
         "DxvkGraphicsPipelineFragmentOutputState::DxvkGraphicsPipelineFragmentOutputState(",
         subs=[("DxvkGraphicsPipelineFragmentOutputState::DxvkGraphicsPipelineFragmentOutputState(", "D9gFoState::D9gFoState(", 1),
               ("const DxvkDevice*", "const D9gDevice*", 1),
               ("const DxvkGraphicsPipelineShaders&", "const D9gShaders&", 1),
               ("shaders.fs->metadata().outputs.computeMask()", "shaders.fs_output_mask", 1),
               ("shaders.fs->metadata().flags.test(DxvkShaderFlag::HasSampleRateShading)", "shaders.fs_sample_rate_shading", 1),
               ("shaders.fs->metadata().flags.test(DxvkShaderFlag::ExportsSampleMask)", "shaders.fs_exports_sample_mask", 1)],
         standins=["fs_output_mask", "fs_sample_rate_shading", "fs_exports_sample_mask"], section="gfx"),
    Span("DxvkGraphicsPipelinePreRasterizationState", GFX,
         "DxvkGraphicsPipelinePreRasterizationState::DxvkGraphicsPipelinePreRasterizationState(",
         subs=[("DxvkGraphicsPipelinePreRasterizationState::DxvkGraphicsPipelinePreRasterizationState(", "D9gPrState::D9gPrState(", 1),
               ("const DxvkDevice*", "const D9gDevice*", 1),
               ("const DxvkGraphicsPipelineShaders&", "const D9gShaders&", 1),
               ("shaders.gs->metadata().rasterizedStream", "shaders.gs_rasterized_stream", 1),
               ("shaders.gs->metadata().flags.test(DxvkShaderFlag::ExportsPosition)", "shaders.gs_exports_position", 1),
               ("shaders.fs->metadata().flags.test(DxvkShaderFlag::HasSampleRateShading)", "shaders.fs_sample_rate_shading", 1)],
         standins=["gs_rasterized_stream", "gs_exports_position", "fs_sample_rate_shading"], section="gfx"),
    # the fragment-shader state: the pipeline's static depth-stencil create info, which a draw
    # whose depth and stencil tests are not dynamic keeps (replaces the transcribed zeroing)
    Span("DxvkGraphicsPipelineFragmentShaderState", GFX,
         "DxvkGraphicsPipelineFragmentShaderState::DxvkGraphicsPipelineFragmentShaderState(",
         subs=[("DxvkGraphicsPipelineFragmentShaderState::DxvkGraphicsPipelineFragmentShaderState(", "D9gFsState::D9gFsState(", 1),
               ("const DxvkDevice*", "const D9gDevice*", 1)], section="gfx"),
    Span("DxvkGraphicsPipelinePreRasterizationState.isLineRendering", GFX,
         "bool DxvkGraphicsPipelinePreRasterizationState::isLineRendering(",
         subs=[("DxvkGraphicsPipelinePreRasterizationState::isLineRendering(", "D9gPrState::isLineRendering(", 1),
               ("const DxvkGraphicsPipelineShaders&", "const D9gShaders&", 1)], section="gfx"),
    # ---- section "sampler": the sampler object, DXVK's sampler key into VkSamplerCreateInfo
    Span("DxvkSampler", SMP, "DxvkSampler::DxvkSampler(",
         subs=[("DxvkSampler::DxvkSampler(", "D9gDxvkSampler::D9gDxvkSampler(", 1),
               ("DxvkSamplerPool*        pool", "D9gSamplerPool*         pool", 1),
               ("m_pool->m_device->features()", "d9g_features()", 4),
               ("m_descriptor = m_pool->m_descriptorHeap.createSampler(index, &samplerInfo);",
                "m_pool->d9g_record(samplerInfo);", 1)], section="sampler",
         # what the substitution and the device shim leave out (the extraction re-review's NIT 4),
         # re-examined at the SDL patch's phase B, when BORDER became mapped: neither moves a
         # compared fact on any sampler d9g maps. The heap chains its colour index, or rewrites
         # the colour, only where the create info chains a custom border colour (findBorderColorInfo,
         # dxvk_sampler.cpp:237-255, :487-494), and d9g maps BORDER only where DXVK's colour is a
         # standard one, which chains none (:63-65; sm_pNextCustomBorder, compared raw, holds it);
         # the pool's fallback is run-time exhaustion, the same for every sampler
         cuts=["DxvkSamplerDescriptorHeap::createSampler (src/dxvk/dxvk_sampler.cpp:217-289): on a device that can "
               "use a descriptor heap it chains a VkSamplerCustomBorderColorIndexCreateInfoEXT, and where it cannot "
               "allocate a border colour it rewrites borderColor to transparent black (BORDER samplers only)",
               "DxvkSamplerPool::createSampler (src/dxvk/dxvk_sampler.cpp:535-583): its fallback to the default "
               "sampler when the pool is full (run-time exhaustion; the device shim's createSampler returns the key)"]),
    Span("DxvkSampler.determineBorderColorType", SMP,
         "VkBorderColor DxvkSampler::determineBorderColorType(const VkSamplerCustomBorderColorCreateInfoEXT& info) const {",
         subs=[("DxvkSampler::determineBorderColorType(", "D9gDxvkSampler::determineBorderColorType(", 1),
               ("m_pool->m_device->features()", "d9g_features()", 2)], section="sampler"),
    Span("DxvkSampler.mapBorderColorComponent", SMP,
         "float DxvkSampler::mapBorderColorComponent(const VkClearColorValue& color, const VkComponentMapping& mapping, VkComponentSwizzle which) {",
         subs=[("DxvkSampler::mapBorderColorComponent(", "D9gDxvkSampler::mapBorderColorComponent(", 1)], section="sampler"),
    Span("DxvkSampler.swizzleBorderColor", SMP,
         "VkClearColorValue DxvkSampler::swizzleBorderColor(const VkClearColorValue& color, VkComponentMapping mapping) {",
         subs=[("DxvkSampler::swizzleBorderColor(", "D9gDxvkSampler::swizzleBorderColor(", 1)], section="sampler"),
]

# Spans read for the static audit (M5) only, never compiled.
AUDIT_SPANS = [
    Span("SetRenderState", D3D9, "HRESULT STDMETHODCALLTYPE D3D9DeviceEx::SetRenderState(D3DRENDERSTATETYPE State, DWORD Value) {"),
    Span("PrepareDraw", D3D9, "void D3D9DeviceEx::PrepareDraw(D3DPRIMITIVETYPE PrimitiveType, bool UploadVBOs, bool UploadIBO) {"),
]

SDL_ROOT = "SDL3-3.4.16/"
VKC = SDL_ROOT + "src/gpu/vulkan/SDL_gpu_vulkan.c"
SYSGPU = SDL_ROOT + "src/gpu/SDL_sysgpu.h"
SDLGPU = SDL_ROOT + "src/gpu/SDL_gpu.c"
SDL_TABLES = ["TextureFormat", "VertexFormat", "PrimitiveType", "CullMode", "FrontFace", "BlendFactor", "BlendOp",
              "CompareOp", "StencilOp", "SampleCount", "VertexInputRate", "Filter", "SamplerMipmapMode",
              "SamplerAddressMode"]
SDL_TABLE_TYPES = {"TextureFormat": "VkFormat", "VertexFormat": "VkFormat", "PrimitiveType": "VkPrimitiveTopology",
                   "CullMode": "VkCullModeFlags", "FrontFace": "VkFrontFace", "BlendFactor": "VkBlendFactor",
                   "BlendOp": "VkBlendOp", "CompareOp": "VkCompareOp", "StencilOp": "VkStencilOp",
                   "SampleCount": "VkSampleCountFlagBits", "VertexInputRate": "VkVertexInputRate", "Filter": "VkFilter",
                   "SamplerMipmapMode": "VkSamplerMipmapMode", "SamplerAddressMode": "VkSamplerAddressMode"}
SDL_CHECKS = ["TEXTUREFORMAT", "VERTEXELEMENTFORMAT", "COMPAREOP", "STENCILOP", "BLENDOP", "BLENDFACTOR"]

# The patched configuration's spans (patches/sdl3): the patch's two maximum-enum lines close the
# MAX_ENUM_VALUES span; the border-colour table; the address-mode check its sampler validation
# uses; and the two locals its state spans read, each cut as its one line (the backend's own
# refusals between them and the spans, of a border colour out of range and of the clamp
# property on a device without it, are cut: the debug-mode validation spans hold both).
PATCHED_MAX_ENUM_END = "#define SDL_GPU_SAMPLERBORDERCOLOR_MAX_ENUM_VALUE   (SDL_GPU_SAMPLERBORDERCOLOR_OPAQUE_WHITE + 1)"
PATCHED_TABLES = {"SamplerBorderColor": "VkBorderColor"}
PATCHED_CHECKS = ["SAMPLERADDRESSMODE"]
PATCHED_CLAMP_LOCAL = ("const bool clampWithClip = SDL_GetBooleanProperty(createinfo->props, "
                       "SDL_PROP_GPU_GRAPHICSPIPELINE_CREATE_SA2RECOMP_DEPTH_CLAMP_BOOLEAN, false);")
PATCHED_BORDER_LOCAL = ("Sint64 borderColor = SDL_GetNumberProperty(createinfo->props, "
                        "SDL_PROP_GPU_SAMPLER_CREATE_SA2RECOMP_BORDER_COLOR_NUMBER, "
                        "SDL_GPU_SAMPLERBORDERCOLOR_TRANSPARENT_BLACK);")


def sdl_spans(patched: bool) -> list[Span]:
    """The SDL side's spans, for the stock tarball or the patched one."""
    tables = dict(SDL_TABLE_TYPES, **(PATCHED_TABLES if patched else {}))
    names = SDL_TABLES + (list(PATCHED_TABLES) if patched else [])
    checks = SDL_CHECKS + (PATCHED_CHECKS if patched else [])
    max_end = PATCHED_MAX_ENUM_END if patched else \
        "#define SDL_GPU_PRESENTMODE_MAX_ENUM_VALUE          (SDL_GPU_PRESENTMODE_MAILBOX + 1)"
    spans = [
        Span("SDL_sysgpu.MAX_ENUM_VALUES", SYSGPU,
             "#define SDL_GPU_TEXTUREFORMAT_MAX_ENUM_VALUE        (SDL_GPU_TEXTUREFORMAT_ASTC_12x12_FLOAT + 1)",
             end=max_end, section="tables"),
        *[Span(f"SDLToVK_{t}", VKC, f"static {tables[t]} SDLToVK_{t}[] = {{", end="brace+assert", section="tables")
          for t in names],
    ]
    for sp in SDL_SPANS:
        if sp.name == "SDL_sysgpu.MAX_ENUM_VALUES" or (sp.name.startswith("SDLToVK_") and sp.name[8:] in SDL_TABLES):
            continue
        if sp.name == "SDL_gpu.CHECK_TEXTUREFORMAT_ENUM_INVALID" and patched:
            spans += [Span(f"SDL_gpu.CHECK_{c}_ENUM_INVALID", SDLGPU, f"#define CHECK_{c}_ENUM_INVALID(enumval, retval)",
                           match="prefix", section="validate_helpers") for c in PATCHED_CHECKS]
        spans.append(sp)
        if patched and sp.name == "VULKAN_CreateGraphicsPipeline.locals":
            spans.append(Span("VULKAN_CreateGraphicsPipeline.clamp_local", VKC, PATCHED_CLAMP_LOCAL, end="self",
                              section="pipeline_locals",
                              cuts=["the backend's refusal of the clamp property on a device without it, which "
                                    "follows this line (the debug-mode validation span holds it)"]))
        if patched and sp.name == "VULKAN_CreateGraphicsPipeline.state":
            spans.append(Span("VULKAN_CreateSampler.border_local", VKC, PATCHED_BORDER_LOCAL, end="self",
                              section="sampler_state",
                              cuts=["the backend's refusal of a border colour out of range, which follows this line "
                                    "(the debug-mode validation span holds it)"]))
    assert [c for c in checks if c not in SDL_CHECKS] == (PATCHED_CHECKS if patched else [])
    return spans


SDL_SPANS: list[Span] = [
    Span("SDL_sysgpu.MAX_ENUM_VALUES", SYSGPU,
         "#define SDL_GPU_TEXTUREFORMAT_MAX_ENUM_VALUE        (SDL_GPU_TEXTUREFORMAT_ASTC_12x12_FLOAT + 1)",
         end="#define SDL_GPU_PRESENTMODE_MAX_ENUM_VALUE          (SDL_GPU_PRESENTMODE_MAILBOX + 1)", section="tables"),
    *[Span(f"SDLToVK_{t}", VKC, f"static {SDL_TABLE_TYPES[t]} SDLToVK_{t}[] = {{", end="brace+assert", section="tables")
      for t in SDL_TABLES],
    Span("SDLToVK_PolygonMode", VKC, "static inline VkPolygonMode SDLToVK_PolygonMode(", section="tables"),
    # VULKAN_CreateGraphicsPipeline: its locals (the function's head, verbatim) and the span from
    # "Vertex input" through the colour-blend state (just before "Pipeline Layout"), in a function
    # of test/ref_sdl.c that copies the filled structures out.
    Span("VULKAN_CreateGraphicsPipeline.locals", VKC, "static SDL_GPUGraphicsPipeline *VULKAN_CreateGraphicsPipeline(",
         end="VulkanRenderer *renderer = (VulkanRenderer *)driverData;", drop_first=4, section="pipeline_locals"),
    Span("VULKAN_CreateGraphicsPipeline.state", VKC, "// Vertex input", end="colorBlendStateCreateInfo.logicOp = 0;",
         section="pipeline_state"),
    Span("VULKAN_CreateSampler.state", VKC, "VkSamplerCreateInfo vkSamplerCreateInfo;",
         end="vkSamplerCreateInfo.unnormalizedCoordinates = VK_FALSE;", section="sampler_state"),
    # ---- SDL's debug-mode validation of a pipeline and a sampler create info (SDL_gpu.c), the
    # checks d9g's descriptions meet before SDL builds anything (the confirm's residual 3, folded
    # as the review's extraction finding 5 says: SDL's own checks, not a new one). Its helpers:
    # the CHECK_*_ENUM_INVALID macros, IsDepthFormat, FormatHasAlpha and the attribute limits.
    *[Span(f"SDL_gpu.CHECK_{c}_ENUM_INVALID", SDLGPU, f"#define CHECK_{c}_ENUM_INVALID(enumval, retval)", match="prefix",
           section="validate_helpers") for c in SDL_CHECKS],
    Span("SDL_sysgpu.MAX_VERTEX_BUFFERS", SYSGPU, "#define MAX_VERTEX_BUFFERS", match="prefix", end="self",
         section="validate_helpers"),
    Span("SDL_sysgpu.MAX_VERTEX_ATTRIBUTES", SYSGPU, "#define MAX_VERTEX_ATTRIBUTES", match="prefix", end="self",
         section="validate_helpers"),
    Span("SDL_sysgpu.IsDepthFormat", SYSGPU, "static inline bool IsDepthFormat(", section="validate_helpers"),
    Span("SDL_sysgpu.FormatHasAlpha", SYSGPU, "static inline bool FormatHasAlpha(", section="validate_helpers"),
    # the shaders are OPEN-1 (none exists), so the two null-shader checks are the only lines
    # replaced; SDL_GPUTextureSupportsFormat is the device's question, answered by ref_sdl.c from
    # the device's parameters
    Span("SDL_CreateGPUGraphicsPipeline.validation", SDLGPU, "if (device->debug_mode) {",
         after="SDL_GPUGraphicsPipeline *SDL_CreateGPUGraphicsPipeline(", occurs=14,
         subs=[("graphicsPipelineCreateInfo->vertex_shader == NULL", "0 /* d9g: no shader route (OPEN-1) */", 1),
               ("graphicsPipelineCreateInfo->fragment_shader == NULL", "0 /* d9g: no shader route (OPEN-1) */", 1),
               ("SDL_GPUTextureSupportsFormat(device, ", "d9g_sdl_texture_supports_format(device, ", 2)],
         section="pipeline_validate"),
    Span("SDL_CreateGPUSampler.validation", SDLGPU, "if (device->debug_mode) {", after="SDL_GPUSampler *SDL_CreateGPUSampler(",
         occurs=14, section="sampler_validate"),
]

# DXVK's render-state magic values (FOURCC D3D9Formats compared in SetRenderState): the audit
# lists them so the sweep includes each (M5).
MAGIC_RE = re.compile(r"D3D9Format::(\w+)\)|uint32_t\(D3D9Format::(\w+)\)|constexpr uint32_t (\w+)\s*=\s*(0x[0-9a-fA-F]+);")
# A transcription in ref_dxvk.cpp: D9G_TRANSCRIBED(name, "src/...:a-b[, src/...:c-d]")
TRANSCRIBED_RE = re.compile(r'D9G_TRANSCRIBED\((\w[\w.]*),\s*"([^"]+)"\)')
FEATURE_RE = re.compile(r"d9g_features\(\)\.([\w.]+)")
GETOPTION_RE = re.compile(r'getOption<[^>]*>\s*\("([\w.]+)"')


class Refusal(Exception):
    pass


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git_blob_sha1(b: bytes) -> str:
    """git hash-object of these bytes (a blob's object id), computed from the bytes themselves."""
    return hashlib.sha1(b"blob %d\0" % len(b) + b).hexdigest()


def git(dxvk: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(dxvk), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise Refusal(f"git {' '.join(args)} in {dxvk}: {r.stderr.strip()}")
    return r.stdout.strip()


class DxvkTree:
    """The pinned DXVK checkout: every file read is held, by the bytes read, to its blob at the
    pinned commit (a submodule's file to its blob at the commit the superproject records)."""

    def __init__(self, root: Path, commit: str, overrides: dict[str, Path]):
        self.root, self.commit, self.overrides = root.resolve(), commit, overrides
        head = git(self.root, "rev-parse", "HEAD")
        if head != commit:
            raise Refusal(f"DXVK checkout {root} is at {head}, not the pinned {commit} (tools/pins.json tools.dxvk.commit)")
        self.submodules: dict[str, str] = {}
        for line in git(self.root, "ls-tree", "-r", commit).splitlines():
            meta, path = line.split("\t", 1)
            _mode, kind, obj = meta.split()
            if kind == "commit":
                self.submodules[path] = obj
        self.read_files: dict[str, str] = {}

    def want_blob(self, rel: str) -> str:
        sub = next((s for s in self.submodules if rel.startswith(s + "/")), None)
        if sub:
            sroot = self.root / sub
            head = git(sroot, "rev-parse", "HEAD")
            if head != self.submodules[sub]:
                raise Refusal(f"submodule {sub} is at {head}, not {self.submodules[sub]} (recorded at {self.commit})")
            return git(sroot, "rev-parse", f"{self.submodules[sub]}:{rel[len(sub) + 1:]}")
        return git(self.root, "rev-parse", f"{self.commit}:{rel}")

    def read_bytes(self, rel: str) -> bytes:
        path = self.overrides.get(rel, self.root / rel)
        data = Path(path).read_bytes()
        want, got = self.want_blob(rel), git_blob_sha1(data)
        if got != want:
            raise Refusal(f"DXVK file {rel} ({path}) hashes to {got}, not its blob {want} at {self.commit}")
        self.read_files[rel] = want
        return data

    def read(self, rel: str) -> str:
        return self.read_bytes(rel).decode()


class SdlTarball:
    """The pinned SDL tarball, read from the very bytes that hash to the pin; with patches, the
    members they write read as patched."""

    def __init__(self, path: Path, want_sha: str):
        data = path.read_bytes()
        got = sha256(data)
        if got != want_sha:
            raise Refusal(f"SDL tarball {path} hashes to {got}, not the pinned {want_sha} (tools/pins.json tools.sdl3.sha256)")
        self.path, self.sha = path, got
        self.tar = tarfile.open(fileobj=io.BytesIO(data), mode="r:gz")
        self.members = {m.name: m for m in self.tar.getmembers()}
        self.patched: dict[str, bytes] = {}
        self.patches: list[dict] = []

    def apply_patches(self, repo: Path, pin: dict) -> None:
        """The pinned patches, checked and applied as tools/s5/build_host_deps.sh applies them
        (tools/s5/sdl3_patches.py), to a scratch copy of the members they name."""
        sys.path.insert(0, str(repo / "tools" / "s5"))
        try:
            import sdl3_patches
        finally:
            sys.path.pop(0)
        import tempfile
        try:
            patches, vendor = sdl3_patches.checked_patches(repo, pin)
            program = sdl3_patches.patch_program(repo)
            with tempfile.TemporaryDirectory(prefix="sa2-sdl-patch-") as scratch:
                tree = Path(scratch) / SDL_ROOT
                names = sorted({n for path, _ in patches for n in sdl3_patches.named_files(path.read_text())})
                for n in names:
                    if (SDL_ROOT + n) in self.members:
                        (tree / n).parent.mkdir(parents=True, exist_ok=True)
                        (tree / n).write_bytes(self.read_bytes(SDL_ROOT + n))
                for path, digest in patches:
                    sdl3_patches.apply_one(tree, path, program)
                    self.patches.append({"path": str(path.relative_to(repo)), "sha256": digest})
                self.patched = {SDL_ROOT + n: (tree / n).read_bytes() for n in names}
        except sdl3_patches.PatchError as error:
            raise Refusal(f"the SDL patches: {error}") from None
        self.vendor = vendor

    def read_bytes(self, name: str) -> bytes:
        if name in self.patched:
            return self.patched[name]
        m = self.members.get(name)
        if m is None or not m.isfile():
            raise Refusal(f"SDL tarball has no file {name}")
        return self.tar.extractfile(m).read()

    def read(self, name: str) -> str:
        return self.read_bytes(name).decode()


# ----------------------------------------------------------------------------- span cutting
def _matching_brace(lines: list[str], i0: int, j0: int = 0) -> tuple[int, int]:
    """(line, column) of the brace that closes the first '{' at or after line i0, column j0,
    skipping comments, strings and character literals."""
    depth = 0
    seen = False
    in_block = False
    for i in range(i0, len(lines)):
        s = lines[i]
        j = j0 if i == i0 else 0
        while j < len(s):
            c = s[j]
            if in_block:
                if s.startswith("*/", j):
                    in_block = False
                    j += 2
                    continue
                j += 1
                continue
            if s.startswith("//", j):
                break
            if s.startswith("/*", j):
                in_block = True
                j += 2
                continue
            if c in "\"'":
                q = c
                j += 1
                while j < len(s) and s[j] != q:
                    j += 2 if s[j] == "\\" else 1
                j += 1
                continue
            if c == "{":
                depth += 1
                seen = True
            elif c == "}":
                depth -= 1
                if seen and depth == 0:
                    return i, j
            j += 1
    raise Refusal(f"no matching brace after line {i0 + 1}")


def _start_matches(span: Span, line: str) -> bool:
    s = line.strip()
    return s.startswith(span.start.strip()) if span.match == "prefix" else s == span.start.strip()


def cut(span: Span, text: str) -> tuple[int, int, list[str]]:
    """(first line number, last line number, the span's lines), 1-based inclusive."""
    lines = text.split("\n")
    hits = [i for i, l in enumerate(lines) if _start_matches(span, l)]
    if len(hits) != span.occurs:
        raise Refusal(f"span {span.name}: start line occurs {len(hits)} times in {span.source}, not {span.occurs}")
    if span.after is not None:
        anchors = [i for i, l in enumerate(lines) if l.strip() == span.after.strip()]
        if len(anchors) != 1:
            raise Refusal(f"span {span.name}: its anchor line occurs {len(anchors)} times in {span.source}, not once")
        later = [i for i in hits if i > anchors[0]]
        if not later:
            raise Refusal(f"span {span.name}: no start line after its anchor")
        i0 = later[0]
    else:
        i0 = hits[span.occurrence - 1]
    if span.end in ("brace", "brace+assert", "brace-else"):
        i1, j1 = _matching_brace(lines, i0)
        if span.end == "brace-else":
            while re.match(r"\s*else\b", lines[i1][j1 + 1:]):
                i1, j1 = _matching_brace(lines, i1, j1 + 1)
            if lines[i1][j1 + 1:].strip():
                raise Refusal(f"span {span.name}: text after its closing brace on line {i1 + 1}")
        if span.end == "brace+assert" and i1 + 1 < len(lines) and \
                lines[i1 + 1].startswith(f"SDL_COMPILE_TIME_ASSERT({span.name},"):
            i1 += 1
    elif span.end == "line":
        i1 = _statement_end(lines, i0, span.name)
        _braced(lines, i0, span.name)
    elif span.end == "self":
        i1 = i0
    else:
        ends = [i for i in range(i0, len(lines)) if lines[i].strip() == span.end.strip()]
        if not ends:
            raise Refusal(f"span {span.name}: no end line after line {i0 + 1}")
        i1 = ends[0]
    i0 += span.drop_first
    return i0 + 1, i1 + 1, lines[i0:i1 + 1]


def _statement_end(lines: list[str], i0: int, name: str) -> int:
    """The line that ends the statement starting at line i0: the first ending with ";". A line
    before it holding a ";" (a statement followed by a comment, or a second statement) refuses:
    the cut would swallow what follows (the extraction re-review's NIT 7)."""
    i = i0
    while i < len(lines) and not lines[i].rstrip().endswith(";"):
        if ";" in lines[i]:
            raise Refusal(f"span {name}: line {i + 1} holds a ';' but does not end with one (a trailing comment or a "
                          f"second statement): the statement's end is not its line's")
        i += 1
    if i == len(lines):
        raise Refusal(f"span {name}: no line ending with ';' after line {i0 + 1}")
    return i


def _braced(lines: list[str], i0: int, name: str) -> None:
    """A kept statement must not be the body of an unbraced if/else/for/while: its condition would
    be lost (the extraction re-review's NIT 7). The previous line that is not blank or a comment
    must end with "{", "}" or ";", or be a #line or preprocessor line."""
    k = i0 - 1
    while k >= 0 and (not lines[k].strip() or lines[k].strip().startswith(("//", "/*", "*"))):
        k -= 1
    if k < 0:
        return
    prev = lines[k].split("//")[0].rstrip()
    if prev.endswith(("{", "}", ";")) or prev.lstrip().startswith("#"):
        return
    raise Refusal(f"span {name}: the statement at line {i0 + 1} follows {prev.strip()!r}, which may be an unbraced "
                  f"condition: its condition would not be kept")


def statements(lines: list[str], first: int, pattern: str, name: str = "") -> list[tuple[int, list[str]]]:
    rx = re.compile(pattern)
    out = []
    i = 0
    while i < len(lines):
        if rx.match(lines[i]):
            j = _statement_end(lines, i, name)
            _braced(lines, i, name)
            out.append((first + i, lines[i:j + 1]))
            i = j + 1
        else:
            i += 1
    if not out:
        raise Refusal(f"no statement matches {pattern}")
    return out


def apply_subs(span: Span, body: str) -> tuple[str, list[dict]]:
    done = []
    for old, new, count in span.subs:
        n = body.count(old)
        if n != count:
            raise Refusal(f"span {span.name}: substitution {old!r} occurs {n} times, not {count}")
        body = body.replace(old, new)
        done.append({"old": old, "new": new, "count": n})
    return body, done


def alterations(rec: dict) -> list[str]:
    """Every alteration made to a span, in words (zlib clause 2)."""
    out = []
    if rec.get("lines_dropped_from_head"):
        out.append(f"its first {rec['lines_dropped_from_head']} line(s) dropped")
    if rec.get("statements_matching"):
        out.append(f"only the statements matching {rec['statements_matching']!r} kept, at lines {rec['statements_at']}")
    for s in rec["substitutions"]:
        out.append(f"{s['old']!r} -> {s['new']!r} ({s['count']}x)")
    if rec.get("wrapped_in"):
        out.append(f"wrapped in {rec['wrapped_in']!r} ... '}}'" + (f" with {rec['wrapper_tail']!r}" if rec.get("wrapper_tail") else ""))
    if rec.get("section"):
        out.append(f"under #if defined(D9G_SECTION_{rec['section'].upper()})")
    for c in rec.get("cuts") or []:
        out.append(f"leaves out {c}")
    return out


def render(spans: list[Span], read, pin: dict, source_label) -> tuple[str, list[dict]]:
    out, manifest = [], []
    for sp in spans:
        text = read(sp.source)
        first, last, lines = cut(sp, text)
        src_sha = sha256("\n".join(lines).encode())
        label = source_label(sp.source)
        if sp.statements:
            parts = statements(lines, first, sp.statements, sp.name)
            body = "".join(f'#line {n} "{label}"\n' + "\n".join(ls) + "\n" for n, ls in parts)
            stmt_lines = [n for n, _ in parts]
        else:
            body = f'#line {first} "{label}"\n' + "\n".join(lines) + "\n"
            stmt_lines = None
        body, subs = apply_subs(sp, body)
        if sp.wrap:
            body = sp.wrap + "\n" + body + (f"  {sp.tail}  // generated\n" if sp.tail else "") + "}\n"
        if sp.section:
            body = f"#if defined(D9G_SECTION_{sp.section.upper()})\n" + body + "#endif\n"
        rec = {"name": sp.name, "source": sp.source, "lines": [first, last], "source_sha256": src_sha,
               "substitutions": subs, "wrapped_in": sp.wrap, "wrapper_tail": sp.tail or None,
               "standins": sp.standins, "section": sp.section, "lines_dropped_from_head": sp.drop_first,
               "cuts": sp.cuts, "pin": pin}
        if stmt_lines is not None:
            rec["statements_matching"] = sp.statements
            rec["statements_at"] = stmt_lines
        rec["alterations"] = alterations(rec)
        out.append(f"// ---- {sp.name}: {sp.source}:{first}-{last}; altered: {'; '.join(rec['alterations']) or 'none'}\n"
                   + body)
        manifest.append(rec)
    return "".join(out), manifest


def check_sections(spans: list[Span], including: Path, generated: set[str]) -> list[str]:
    """Every span's section is one the including file defines (D9G_SECTION_<NAME>), and every
    section it defines is a span's or a generated one: a span no file includes is extracted for
    nothing, and a section no span has includes nothing (the extraction re-review's NIT 7)."""
    text = including.read_text()
    defined = set(re.findall(r"^\s*#define D9G_SECTION_(\w+)\s*$", text, re.M))
    used = {s.section.upper() for s in spans if s.section}
    missing = sorted(used - defined)
    if missing:
        raise Refusal(f"{including.name} includes no span of section(s) {missing}")
    extra = sorted(defined - used - generated)
    if extra:
        raise Refusal(f"{including.name} defines section(s) {extra}, which no span has")
    return sorted(used | (defined & generated))


def check_notice(licence: str, including: Path) -> int:
    """The DXVK notice a transcribing file carries is its LICENSE's, byte for byte after each
    line's "// " (zlib clause 3: the notice may not be altered; the extraction re-review's NIT 5).
    Returns the line it starts at."""
    want = ["//" + (" " + l if l else "") for l in (x.rstrip() for x in licence.rstrip().split("\n"))]
    lines = including.read_text().split("\n")
    for i in range(len(lines) - len(want) + 1):
        if lines[i:i + len(want)] == want:
            return i + 1
    raise Refusal(f"{including.name} does not carry DXVK's LICENSE verbatim (zlib clause 3)")


def header(licence: str, what: str, manifest: list[dict], context: str = "") -> str:
    body = "\n".join(" * " + l if l else " *" for l in licence.rstrip().split("\n"))
    alt = "\n".join(f" *   {m['name']} ({m['source']}:{m['lines'][0]}-{m['lines'][1]}): "
                    + ("; ".join(m["alterations"]) or "none").replace("*/", "* /").replace("/*", "/ *") for m in manifest)
    ctx = "".join(f" * {l}\n" if l else " *\n" for l in context.split("\n")) if context else ""
    return (f"/* Generated by host/d3d9gpu/test/extract_refs.py: {what}.\n *\n{body}\n *\n"
            f" * Altered from the original: spans copied by line range, each under a #line naming its\n"
            f" * source line, and each altered as listed here (and above each span):\n{alt}\n"
            + (f" *\n{ctx}" if ctx else "") + " */\n")


# ----------------------------------------------------------------------------- audits
def audit(dxvk: DxvkTree) -> dict:
    text = dxvk.read(D3D9)
    _, _, srs = cut(AUDIT_SPANS[0], text)
    _, _, pd = cut(AUDIT_SPANS[1], text)
    cases = sorted(set(re.findall(r"case (D3DRS_\w+):", "\n".join(srs))))
    reads = sorted(set(re.findall(r"\b(D3DRS_\w+)\b", "\n".join(srs + pd))))
    magic = set()
    for m in MAGIC_RE.finditer("\n".join(srs)):
        for g in m.groups():
            if g and not g.startswith("0x"):
                magic.add(g)
    hexes = {name: val for name, val in re.findall(r"constexpr uint32_t (\w+)\s*=\s*(0x[0-9a-fA-F]+);", "\n".join(srs))}
    return {"set_render_state_cases": cases, "tokens_read": reads,
            "magic_formats": sorted(m for m in magic if m.isupper() and m not in hexes),
            "magic_constants": hexes}


def features_read(*texts: str) -> list[str]:
    return sorted({f for t in texts for f in FEATURE_RE.findall(t)})


def options_read(text: str) -> list[str]:
    return sorted(set(GETOPTION_RE.findall(text)))


# The two options that would move the adapter's vendor ID (DxvkParams.vendor_id, from the device's query).
VENDOR_OPTIONS = {"d3d9.customVendorId", "d3d9.customDeviceId"}


def modelled_options() -> list[str]:
    """The DXVK options the state check's shims answer with getOption's default: the getOption
    names of the span table's start lines and the two vendor options. extract() holds this to
    the options the rendered spans read, so the two cannot drift; check_params.py reads it where
    no manifest is given."""
    return sorted({o for sp in DXVK_SPANS for o in GETOPTION_RE.findall(sp.start)} | VENDOR_OPTIONS)


def transcriptions(dxvk: DxvkTree, ref_text: str) -> list[dict]:
    """The tagged transcriptions of ref_dxvk.cpp, each citation held to a pinned file's lines."""
    out, names = [], set()
    for n, line in enumerate(ref_text.split("\n"), 1):
        for m in TRANSCRIBED_RE.finditer(line):
            name = m.group(1)
            if name in names:
                raise Refusal(f"ref_dxvk.cpp:{n}: transcription {name} tagged twice")
            names.add(name)
            cites = []
            for c in m.group(2).split(","):
                cm = re.fullmatch(r"\s*(src/[\w/.]+):(\d+)(?:-(\d+))?\s*", c)
                if not cm:
                    raise Refusal(f"ref_dxvk.cpp:{n}: transcription {name} cites {c!r}, not FILE:LINE[-LINE]")
                src, a, b = cm.group(1), int(cm.group(2)), int(cm.group(3) or cm.group(2))
                count = dxvk.read(src).count("\n") + 1
                if not 1 <= a <= b <= count:
                    raise Refusal(f"ref_dxvk.cpp:{n}: transcription {name} cites {src}:{a}-{b}, outside its {count} lines")
                cites.append({"source": src, "lines": [a, b]})
            out.append({"name": name, "at": f"host/d3d9gpu/test/ref_dxvk.cpp:{n}", "cites": cites})
    if not out:
        raise Refusal("ref_dxvk.cpp tags no transcription (D9G_TRANSCRIBED)")
    return out


ENUM_BLOCK_RE = re.compile(r"typedef enum (Vk\w+) \{(.*?)\} \1;", re.S)
ENUM_LINE_RE = re.compile(r"^\s*(VK_\w+)\s*=\s*([^,\n]+?),?\s*$", re.M)
FLAG64_RE = re.compile(r"^static const Vk\w+ (VK_\w+) = (0x[0-9A-Fa-f]+|\d+)ULL;", re.M)


def vulkan_enumerants(text: str) -> dict[str, int]:
    """Every enumerant of a vulkan_core.h (typedef enum blocks and the 64-bit flag constants),
    aliases resolved."""
    raw: dict[str, str] = {}
    for _name, body in ENUM_BLOCK_RE.findall(text):
        for k, v in ENUM_LINE_RE.findall(body):
            raw[k] = v.strip()
    for k, v in FLAG64_RE.findall(text):
        raw[k] = v
    out: dict[str, int] = {}

    def value(k: str, depth: int = 0) -> int:
        if k in out:
            return out[k]
        v = raw[k]
        if depth > 20:
            raise Refusal(f"vulkan_core.h: {k} aliases in a loop")
        if re.fullmatch(r"VK_\w+", v):
            r = value(v, depth + 1)
        else:
            r = int(v.rstrip("UuLl"), 0)
        out[k] = r
        return r

    for k in raw:
        value(k)
    return out


def compare_vulkan_headers(dxvk: DxvkTree, tarball: SdlTarball) -> dict:
    """The facts cross between the two sides as Vulkan enumerants' numbers: every enumerant the
    two bundled vulkan_core.h share must have one value (the review's extraction finding 6)."""
    dx_rel = "include/vulkan/include/vulkan/vulkan_core.h"
    sdl_rel = SDL_ROOT + "src/video/khronos/vulkan/vulkan_core.h"
    a = vulkan_enumerants(dxvk.read(dx_rel))
    b = vulkan_enumerants(tarball.read(sdl_rel))
    shared = sorted(set(a) & set(b))
    differ = [k for k in shared if a[k] != b[k]]
    if differ:
        raise Refusal(f"the two vulkan_core.h give different values for {differ[:10]}")
    if not shared:
        raise Refusal("the two vulkan_core.h share no enumerant (the parser read neither)")
    ver = lambda t: (re.search(r"#define VK_HEADER_VERSION (\d+)", t) or [None, None])[1]
    return {"dxvk": dx_rel, "sdl": sdl_rel, "dxvk_header_version": ver(dxvk.read(dx_rel)),
            "sdl_header_version": ver(tarball.read(sdl_rel)), "dxvk_enumerants": len(a), "sdl_enumerants": len(b),
            "shared": len(shared), "differ": differ}


def check_installed_dxvk_headers(dxvk: DxvkTree, inst: Path) -> int:
    """The DXVK Native headers the device and the check compile against ($I/include/dxvk, D3D9
    enums cross the boundary as numbers) are the checkout's include/native files, byte for byte
    (the review's extraction finding 7)."""
    by_name: dict[str, list[str]] = {}
    native = dxvk.root / "include" / "native"
    for p in sorted(native.rglob("*")):
        if p.is_file():
            by_name.setdefault(p.name, []).append(str(p.relative_to(dxvk.root)))
    compared = 0
    for h in sorted(inst.rglob("*")):
        if not h.is_file():
            continue
        cands = by_name.get(h.name, [])
        if len(cands) != 1:
            raise Refusal(f"installed {h}: {len(cands)} files of that name in the checkout's include/native")
        if h.read_bytes() != dxvk.read_bytes(cands[0]):
            raise Refusal(f"installed {h} differs from the checkout's {cands[0]}")
        compared += 1
    if not compared:
        raise Refusal(f"no installed DXVK Native header under {inst}")
    return compared


def check_dxvk_conf(conf: Path, modelled: list[str]) -> list[str]:
    """Options the extracted code reads (generated: the getOption names of the extracted spans)
    that host/player/dxvk.conf must not set (the shim answers getOption with its default), and
    the two that would move the adapter's vendor ID (DxvkParams.vendor_id, from the device's
    query)."""
    text = conf.read_text() if conf.exists() else ""
    set_ = [l.split("=")[0].strip() for l in text.splitlines() if "=" in l and not l.strip().startswith("#")]
    bad = [o for o in modelled if o in set_]
    if bad:
        raise Refusal(f"{conf} sets {bad}, which the state check's DXVK shim answers with getOption's default")
    return set_


def sdl_configuration(include: Path, tarball: SdlTarball, sd: dict, repo: Path) -> tuple[str, list[Span], str | None]:
    """The install's SDL configuration, read from its revision header (`include`/SDL_revision.h):
    "patched" when it defines SDL_VENDOR_INFO as tools.sdl3.vendor_info (`sd`), the tarball then
    reading as patched by `repo`'s pinned patches; "stock" when it defines none; anything else
    refuses. Returns the configuration, the SDL spans cut for it and the vendor string."""
    try:
        revision_h = (include / "SDL_revision.h").read_text()
    except OSError as e:
        raise Refusal(f"no installed SDL_revision.h under {include}: {e}") from None
    vendors = re.findall(r'^#define SDL_VENDOR_INFO "([^"\n]*)"\s*$', revision_h, re.M)
    if not vendors:
        configuration = "stock"
    elif vendors == [sd.get("vendor_info")]:
        configuration = "patched"
        tarball.apply_patches(repo, sd)
    else:
        raise Refusal(f"the installed SDL_revision.h defines SDL_VENDOR_INFO {vendors}, not tools.sdl3.vendor_info "
                      f"{sd.get('vendor_info')!r} (patched) nor none (stock)")
    return configuration, sdl_spans(configuration == "patched"), vendors[0] if vendors else None


# ----------------------------------------------------------------------------- main
def extract(args) -> dict:
    pins = json.loads(Path(args.pins).read_text())["tools"]
    dx = pins["dxvk"]
    sd = pins["sdl3"]
    overrides = {}
    for o in args.override or []:
        rel, path = o.split("=", 1)
        overrides[rel] = Path(path)
    dxvk = DxvkTree(Path(args.dxvk or PRIVATE / dx["path"]), dx["commit"], overrides)
    tarball = SdlTarball(Path(args.sdl_tarball or PRIVATE / sd["path"]), sd["sha256"])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    # the install's configuration: patched (its revision header's SDL_VENDOR_INFO, the pin's) or stock
    inst = Path(args.install) / "include" / "SDL3"
    configuration, spans_sdl, vendor = sdl_configuration(inst, tarball, sd, REPO)

    # the installed SDL3 headers are the tarball's
    tar_inc = {n[len(SDL_ROOT + "include/SDL3/"):]: n for n in tarball.members
               if n.startswith(SDL_ROOT + "include/SDL3/") and tarball.members[n].isfile()}
    configured = {"SDL_revision.h"}   # written by SDL's build from a template (cmake/SDL_revision.h.cmake)
    compared = 0
    for h in sorted(inst.glob("*.h")):
        if h.name in configured:
            continue
        if h.name not in tar_inc:
            raise Refusal(f"installed {h} is not in the tarball's include/SDL3")
        if h.read_bytes() != tarball.read_bytes(tar_inc[h.name]):
            raise Refusal(f"installed {h} differs from the tarball's {tar_inc[h.name]}")
        compared += 1
    if compared < 50:
        raise Refusal(f"only {compared} installed SDL3 headers compared")
    dxvk_installed = check_installed_dxvk_headers(dxvk, Path(args.install) / "include" / "dxvk")
    for name, member in tar_inc.items():
        dst = out / "sdl" / "include" / "SDL3" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(tarball.read_bytes(member))
    for n, m in tarball.members.items():
        if n.startswith(SDL_ROOT + "src/video/khronos/") and m.isfile():
            dst = out / "sdl" / "khronos" / n[len(SDL_ROOT + "src/video/khronos/"):]
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(tarball.read_bytes(n))

    dx_pin = {"repository": "dxvk", "commit": dx["commit"]}
    sd_pin = {"tarball": sd["path"], "sha256": tarball.sha}
    dx_body, dx_man = render(DXVK_SPANS, dxvk.read, dx_pin, lambda s: f"dxvk-3.1.1/{s}")
    sd_pin["configuration"] = configuration
    sdl_body, sdl_man = render(spans_sdl, tarball.read, sd_pin, lambda s: s)
    ref_text = REF_DXVK_CPP.read_text()
    # the device features: every member the spans or ref_dxvk.cpp's own code read, as an X-list
    # ref_dxvk.cpp fills from DxvkParams and refuses without (the review's extraction finding 3)
    feats = features_read(dx_body, ref_text)
    if not feats:
        raise Refusal("no DxvkDeviceFeatures member read: the feature scan read nothing")
    feat_section = ("// ---- the DxvkDeviceFeatures members the spans and test/ref_dxvk.cpp read (generated)\n"
                    "#if defined(D9G_SECTION_FEATURES)\n" +
                    "".join(f'D9G_FEATURE({f}, "{f}")\n' for f in feats) + "#endif\n")
    trans = transcriptions(dxvk, ref_text)
    dx_lic = dxvk.read("LICENSE")
    sdl_lic = tarball.read(SDL_ROOT + "LICENSE.txt")
    notice_at = check_notice(dx_lic, REF_DXVK_CPP)
    sections = {"dxvk": check_sections(DXVK_SPANS, REF_DXVK_CPP, {"FEATURES"}),
                "sdl": check_sections(spans_sdl, REF_SDL_C, set())}
    (out / "dxvk_extract.inc").write_text(header(dx_lic, f"DXVK 3.1.1 at {dx['commit']}", dx_man) + dx_body + feat_section)
    what = f"SDL 3.4.16 from {sd['path']} (sha256 {tarball.sha})" + (
        f", with the project's patches {[x['path'] for x in tarball.patches]} applied (not from the SDL project)"
        if tarball.patches else "")
    (out / "sdl_extract.inc").write_text(header(sdl_lic, what, sdl_man, SDL_CONTEXT) + sdl_body)
    aud = audit(dxvk)
    modelled = sorted(set(options_read(dx_body)) | VENDOR_OPTIONS)
    if modelled != modelled_options():
        raise Refusal(f"the spans read options {modelled}, the span table's start lines name {modelled_options()}")
    conf_set = check_dxvk_conf(Path(args.dxvk_conf), modelled)
    vk = compare_vulkan_headers(dxvk, tarball)
    standins = sorted({s for m in dx_man for s in m["standins"]})
    manifest = {
        "schema": "sa2-d3d9gpu-extract/v1",
        "tool": "host/d3d9gpu/test/extract_refs.py",
        "dxvk": {"path": str(dxvk.root), "commit": dx["commit"], "files": dxvk.read_files,
                 "installed_native_headers_equal": dxvk_installed},
        "sdl": {"tarball": sd["path"], "sha256": tarball.sha, "installed_headers_equal": compared,
                "configured_headers_not_compared": sorted(configured), "configuration": configuration,
                "patches": tarball.patches, "vendor_info": vendor},
        "spans": dx_man + sdl_man,
        "standins_open1": standins,
        "device_features_read": feats,
        "transcriptions": trans,
        "vulkan_enumerants": vk,
        "dxvk_conf": {"path": str(args.dxvk_conf), "sets": conf_set, "modelled_options": modelled},
        "dxvk_notice": {"file": "host/d3d9gpu/test/ref_dxvk.cpp", "line": notice_at, "equals": "LICENSE"},
        "sections": sections,
        # every file written here, at its hash: verify-deps holds each file a compile read to it
        "outputs": {n: sha256((out / n).read_bytes()) for n in ("dxvk_extract.inc", "sdl_extract.inc")} |
                   {str(f.relative_to(out)): sha256(f.read_bytes()) for f in sorted((out / "sdl").rglob("*")) if f.is_file()},
    }
    (out / "render_state_audit.json").write_text(json.dumps(aud, indent=1) + "\n")
    manifest["outputs"]["render_state_audit.json"] = sha256((out / "render_state_audit.json").read_bytes())
    (out / "extract_manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    return manifest


def parse_depfile(path: Path) -> tuple[str, list[str]]:
    """(target, dependencies) of a make dependency file (-MD), the first dependency the source."""
    text = path.read_text().replace("\\\n", " ")
    rules = [l for l in text.splitlines() if l.strip()]
    if not rules:
        raise Refusal(f"{path}: an empty dependency file")
    target, _, deps = rules[0].partition(": ")
    toks = deps.split()
    # a later "header:" line is a phony target (-MP); none is expected
    if not toks:
        raise Refusal(f"{path}: names no dependency")
    return target.strip(), toks


# The host/d3d9gpu files each side's compiles may read (relative to host/d3d9gpu), and the
# system paths none may: an installed Vulkan, SDL, DXVK or SPIR-V header would put another
# header's numbers where the two sides' facts meet (the extraction re-review's finding 1).
DXVK_HOST_FILES = {"test/ref_dxvk.cpp", "test/ref_dxvk.h", "test/ref_link_shims.cpp", "test/ref_facts.h", "d9g_state.h"}
SDL_HOST_FILES = {"test/ref_sdl.c", "test/ref_facts.h"}
SYSTEM_ALLOWED = ("/usr/include/", "/usr/lib/gcc/", "/usr/lib/linux/")
SYSTEM_DENIED = ("/usr/local/", "/usr/include/vulkan/", "/usr/include/vk_video/", "/usr/include/SDL", "/usr/include/dxvk",
                 "/usr/include/spirv", "/usr/include/dxbc", "/usr/include/d3d")


def verify_deps(args) -> int:
    """Every file a compile read (its -MD dependency file) accounted for, by the side the
    compile's source makes (the review's extraction finding 1; the re-review's findings 1 and 2):

      * a DXVK compile (its source a DXVK file, test/ref_dxvk.cpp or test/ref_link_shims.cpp):
        DXVK files at their pinned blobs (submodule headers at their gitlink commits), the
        generated dxvk_extract.inc at the manifest's hash, DXVK_HOST_FILES; it names a DXVK file;
      * the SDL compile (test/ref_sdl.c): the extraction's SDL files (sdl_extract.inc and the
        tarball's headers under sdl/) at the manifest's hashes, SDL_HOST_FILES, and no DXVK file;
      * either: the system toolchain's headers under SYSTEM_ALLOWED, none under SYSTEM_DENIED.

    Anything else refuses, and so does no DXVK file checked at all, and so does anything but
    exactly one SDL-side dependency file: a build whose SDL compile writes none, or that checks
    before that compile, would otherwise pass with the SDL side unseen (the r2b-final review's
    finding 1)."""
    pins = json.loads(Path(args.pins).read_text())["tools"]
    dx = pins["dxvk"]
    dxvk = DxvkTree(Path(args.dxvk or PRIVATE / dx["path"]), dx["commit"], {})
    root = dxvk.root
    refs = Path(args.refs).resolve() if args.refs else Path(args.deps[0]).resolve().parent.parent
    man_path = refs / "extract_manifest.json"
    if not man_path.is_file():
        raise Refusal(f"no {man_path}: the generated files cannot be held to the extraction's hashes")
    man = json.loads(man_path.read_text())
    if Path(man["dxvk"]["path"]).resolve() != root:
        raise Refusal(f"the extraction read DXVK at {man['dxvk']['path']}, the compiles {root}")
    tracked = (REPO / "host" / "d3d9gpu").resolve()
    dxvk_sources = {(tracked / "test" / "ref_dxvk.cpp").resolve(), (tracked / "test" / "ref_link_shims.cpp").resolve()}
    sdl_source = (tracked / "test" / "ref_sdl.c").resolve()
    dxvk_files, generated, own = set(), set(), set()
    sides = {"dxvk": 0, "sdl": 0}
    for d in args.deps:
        dpath = Path(d)
        _target, toks = parse_depfile(dpath)
        src = Path(toks[0])
        if not src.is_absolute():
            raise Refusal(f"{dpath}: a relative source {src} (it cannot be placed)")
        src = src.resolve()
        if src == sdl_source:
            side, host_ok = "sdl", SDL_HOST_FILES
        elif src.is_relative_to(root) or src in dxvk_sources:
            side, host_ok = "dxvk", DXVK_HOST_FILES
        else:
            raise Refusal(f"{dpath}: its source {toks[0]} is neither a DXVK file, test/ref_dxvk.cpp, "
                          f"test/ref_link_shims.cpp nor test/ref_sdl.c")
        sides[side] += 1
        named_dxvk = False
        for tok in toks:
            p = Path(tok)
            if not p.is_absolute():
                raise Refusal(f"{dpath}: a relative dependency {tok} (it cannot be placed)")
            r = p.resolve()
            if r.is_relative_to(root):
                if side == "sdl":
                    raise Refusal(f"{dpath}: the SDL side's compile read the DXVK file {r}")
                dxvk_files.add(str(r.relative_to(root)))
                named_dxvk = True
            elif r.is_relative_to(refs):
                rel = str(r.relative_to(refs))
                if side == "dxvk" and rel != "dxvk_extract.inc":
                    raise Refusal(f"{dpath}: the DXVK side's compile read the generated {rel}")
                if side == "sdl" and not (rel == "sdl_extract.inc" or rel.startswith("sdl/")):
                    raise Refusal(f"{dpath}: the SDL side's compile read the generated {rel}")
                generated.add(rel)
            elif r.is_relative_to(tracked):
                rel = str(r.relative_to(tracked))
                if rel not in host_ok:
                    raise Refusal(f"{dpath}: the {side} side's compile read host/d3d9gpu/{rel}, not one of its files "
                                  f"({sorted(host_ok)})")
                own.add(rel)
            elif str(r).startswith(SYSTEM_DENIED) or str(p).startswith(SYSTEM_DENIED):
                raise Refusal(f"{dpath}: {tok} is an installed header of a library the check takes from its pinned "
                              f"sources (or under /usr/local), not the system toolchain's")
            elif str(r).startswith(SYSTEM_ALLOWED):
                pass
            else:
                raise Refusal(f"{dpath}: {tok} is neither the pinned DXVK checkout's, the extraction's, this "
                              f"checkout's host/d3d9gpu, nor the system toolchain's")
        if side == "dxvk" and not named_dxvk:
            raise Refusal(f"{dpath}: names no DXVK file")
    checked = 0
    for rel in sorted(dxvk_files):
        if "dxbc-spirv" in rel or "dxbc_spv" in rel:
            raise Refusal(f"a compile read {rel}: no dxbc-spirv file may be reachable (OPEN-1)")
        dxvk.read_bytes(rel)
        checked += 1
    for rel in sorted(generated):
        want = man["outputs"].get(rel)
        if want is None:
            raise Refusal(f"a compile read the generated {rel}, which the extraction did not write")
        if sha256((refs / rel).read_bytes()) != want:
            raise Refusal(f"the generated {rel} differs from what the extraction wrote")
    if checked == 0:
        raise Refusal("no DXVK file checked")
    if sides["sdl"] != 1:
        raise Refusal(f"{sides['sdl']} SDL-side dependency files, not 1: the SDL compile (test/ref_sdl.c) is "
                      f"checked by its own dependency file, written by its compile, once")
    print(f"DEPS_OK {checked} DXVK files at their pinned blobs; {len(generated)} generated files at the extraction's "
          f"hashes; {len(own)} host/d3d9gpu sources; {len(args.deps)} dependency files ({sides['dxvk']} DXVK side, "
          f"{sides['sdl']} SDL side)")
    return 0


def dxvk_flags(args) -> int:
    """The flags DXVK's own build compiles `--source` with (its compile_commands.json), minus
    what places outputs and every include path (the build directory's and dxbc-spirv's
    included: the state check adds the checkout's own, without dxbc-spirv); one per line, and
    the dropped ones, DXVK's compiler and the file's sha256 in --record (the critic's S7)."""
    cc_path = Path(args.compile_commands)
    cc_bytes = cc_path.read_bytes()
    cc = json.loads(cc_bytes)
    entry = next((e for e in cc if e["file"].endswith("/" + args.source)), None)
    if entry is None:
        raise Refusal(f"{args.compile_commands} has no {args.source}")
    # the build these commands are from is the one that made the pinned library: its directory's
    # libdxvk_d3d9.so hashes to tools/pins.json's library_sha256 (the extraction re-review's NIT 9)
    pin = json.loads(Path(args.pins).read_text())["tools"]["dxvk"]["library_sha256"]
    built = Path(entry.get("directory") or cc_path.parent) / "src" / "d3d9" / "libdxvk_d3d9.so"
    if not built.is_file() or sha256(built.read_bytes()) != pin:
        raise Refusal(f"{args.compile_commands}: its build's {built} is not the pinned library ({pin})")
    toks = shlex.split(entry["command"])
    compiler, toks = toks[0], toks[1:]
    keep, dropped = [], []
    i = 0
    while i < len(toks):
        t = toks[i]
        if t in ("-o", "-MQ", "-MF", "-c"):
            dropped += toks[i:i + (1 if t == "-c" else 2)]
            i += 1 if t == "-c" else 2
            continue
        if t.startswith("-I") or t == "-MD" or t.endswith(".cpp") or t.startswith("-fdiagnostics-color"):
            dropped.append(t)
            i += 1
            continue
        keep.append(t)
        i += 1
    if any("dxbc" in t for t in keep):
        raise Refusal(f"a kept flag names dxbc-spirv: {keep}")
    if args.record:
        Path(args.record).write_text(json.dumps({"compile_commands": args.compile_commands,
                                                 "compile_commands_sha256": sha256(cc_bytes),
                                                 "built_library": str(built), "built_library_sha256": pin,
                                                 "source": args.source,
                                                 "dxvk_compiler": compiler, "kept": keep, "dropped": dropped},
                                                indent=1) + "\n")
    print("\n".join(keep))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")
    ap.add_argument("--out")
    ap.add_argument("--pins", default=str(REPO / "tools" / "pins.json"))
    ap.add_argument("--dxvk")
    ap.add_argument("--sdl-tarball")
    ap.add_argument("--install", default=str(PRIVATE / "private" / "s5-tools" / "install"))
    ap.add_argument("--dxvk-conf", default=str(REPO / "host" / "player" / "dxvk.conf"))
    ap.add_argument("--override", action="append",
                    help="REL=PATH: read PATH in place of the checkout's REL (a test of the provenance refusal)")
    fl = sub.add_parser("dxvk-flags")
    fl.add_argument("--compile-commands", default=str(PRIVATE / "private" / "s5-tools" / "build" / "dxvk" / "compile_commands.json"))
    fl.add_argument("--source", default="src/d3d9/d3d9_util.cpp")
    fl.add_argument("--record")
    vd = sub.add_parser("verify-deps")
    vd.add_argument("--deps", nargs="+", required=True)
    vd.add_argument("--pins", default=str(REPO / "tools" / "pins.json"))
    vd.add_argument("--dxvk")
    vd.add_argument("--refs", help="the extraction's output directory (default: the first .d's directory's parent)")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "verify-deps":
            return verify_deps(args)
        if args.cmd == "dxvk-flags":
            return dxvk_flags(args)
        if not args.out:
            ap.error("--out is required")
        m = extract(args)
        print(f"EXTRACT_OK {len(m['spans'])} spans, DXVK {m['dxvk']['commit'][:12]}, SDL {m['sdl']['sha256'][:12]}")
        return 0
    except Refusal as e:
        print(f"EXTRACT_REFUSED: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
