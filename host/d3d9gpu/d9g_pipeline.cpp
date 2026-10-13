// d9g_pipeline.cpp: see d9g_pipeline.h. Every rule is DXVK 3.1.1's
// (private/s5-tools/src/dxvk-3.1.1/src/d3d9/d3d9_device.cpp unless a file is named), cited by
// line; test/state_check.cpp holds the result to DXVK's own code, field by field.
#include "d9g_pipeline.h"

#include "d9g_format.h"

#include <cmath>
#include <cstring>
#include <iterator>
#include <stdexcept>

namespace d9g {

namespace {

constexpr uint32_t RS(uint32_t state) { return uint32_t(pipeline_rs_index(state)); }
uint32_t rsv(const PipelineInput& in, uint32_t state) { return in.rs[RS(state)]; }
float rsf(const PipelineInput& in, uint32_t state) {
    float f;
    const uint32_t v = rsv(in, state);
    std::memcpy(&f, &v, 4);
    return f;
}

// DXVK's FOURCC render-state values (d3d9_format.h D3D9Format; SetRenderState :2511-2670): the
// driver hacks whose effects depend on the order of past calls. Never mapped.
constexpr uint32_t fourcc(char a, char b, char c, char d) {
    return uint32_t(uint8_t(a)) | uint32_t(uint8_t(b)) << 8 | uint32_t(uint8_t(c)) << 16 | uint32_t(uint8_t(d)) << 24;
}
constexpr uint32_t kA2M1 = fourcc('A', '2', 'M', '1'), kA2M0 = fourcc('A', '2', 'M', '0');
constexpr uint32_t kINST = fourcc('I', 'N', 'S', 'T'), kCENT = fourcc('C', 'E', 'N', 'T');
constexpr uint32_t kRESZ = 0x7fa05000u;
constexpr uint32_t kATOC = fourcc('A', 'T', 'O', 'C'), kCOPM = fourcc('C', 'O', 'P', 'M'), kSSAA = fourcc('S', 'S', 'A', 'A');
constexpr uint32_t kNVDB = fourcc('N', 'V', 'D', 'B');

void unmapped(PipelineDesc& d, const std::string& what) { d.unmapped.push_back(what); }

// The colour write mask DXVK keeps per target (BindBlendState :7004-7005, a 4-bit field in
// DxvkBlendMode): COLORWRITEENABLE(i) & 0xF.
uint32_t write_mask(const PipelineInput& in, uint32_t i, uint32_t faults) {
    static constexpr uint32_t kIdx[4] = {168, 190, 191, 192};   // ColorWriteIndex (d3d9_util.h:241-243)
    uint32_t m = rsv(in, kIdx[i]) & 0xFu;
    if (faults & MF_WRITEMASK) m = (m & 0xAu) | ((m & 1u) << 2) | ((m & 4u) >> 2);
    return m;
}

struct Blend {   // DxvkBlendMode, in SDL's enums
    bool enable = false;
    SDL_GPUBlendFactor cs = SDL_GPU_BLENDFACTOR_ZERO, cd = SDL_GPU_BLENDFACTOR_ZERO;
    SDL_GPUBlendOp co = SDL_GPU_BLENDOP_ADD;
    SDL_GPUBlendFactor as = SDL_GPU_BLENDFACTOR_ZERO, ad = SDL_GPU_BLENDFACTOR_ZERO;
    SDL_GPUBlendOp ao = SDL_GPU_BLENDOP_ADD;
    uint32_t mask = 0;
};

// DxvkBlendMode::normalize (dxvk_constant_state.cpp:69-118).
void normalize_blend(Blend& b) {
    constexpr uint32_t colorMask = 7u, alphaMask = 8u;
    uint32_t newWriteMask = b.mask;
    if (!newWriteMask) b.enable = false;
    if (b.enable) {
        if (b.co == SDL_GPU_BLENDOP_ADD && b.cs == SDL_GPU_BLENDFACTOR_ZERO && b.cd == SDL_GPU_BLENDFACTOR_ONE)
            newWriteMask &= ~colorMask;
        if (b.ao == SDL_GPU_BLENDOP_ADD && b.as == SDL_GPU_BLENDFACTOR_ZERO && b.ad == SDL_GPU_BLENDFACTOR_ONE)
            newWriteMask &= ~alphaMask;
        bool needsBlending = false;
        if (newWriteMask & colorMask)
            needsBlending |= b.cs != SDL_GPU_BLENDFACTOR_ONE || b.cd != SDL_GPU_BLENDFACTOR_ZERO || b.co != SDL_GPU_BLENDOP_ADD;
        if (newWriteMask & alphaMask)
            needsBlending |= b.as != SDL_GPU_BLENDFACTOR_ONE || b.ad != SDL_GPU_BLENDFACTOR_ZERO || b.ao != SDL_GPU_BLENDOP_ADD;
        if (!needsBlending) b.enable = false;
    }
    if (!b.enable || !(newWriteMask & colorMask)) {
        b.cs = b.cd = SDL_GPU_BLENDFACTOR_ZERO;
        b.co = SDL_GPU_BLENDOP_ADD;
    }
    if (!b.enable || !(newWriteMask & alphaMask)) {
        b.as = b.ad = SDL_GPU_BLENDFACTOR_ZERO;
        b.ao = SDL_GPU_BLENDOP_ADD;
    }
    b.mask = newWriteMask;
}

// DxvkStencilOp in SDL's enums, with DXVK's uint8 masks (dxvk_constant_state.h:298-327).
struct Stencil {
    SDL_GPUStencilOp fail = SDL_GPU_STENCILOP_KEEP, pass = SDL_GPU_STENCILOP_KEEP, dfail = SDL_GPU_STENCILOP_KEEP;
    SDL_GPUCompareOp cmp = SDL_GPU_COMPAREOP_NEVER;   // DxvkStencilOp() is all zero: VK_COMPARE_OP_NEVER
    uint8_t cmask = 0, wmask = 0;
};

// DxvkStencilOp::normalize (dxvk_constant_state.cpp:5-31).
bool normalize_stencil(Stencil& s, SDL_GPUCompareOp depthOp) {
    if (s.wmask) {
        if (depthOp == SDL_GPU_COMPAREOP_ALWAYS) s.dfail = SDL_GPU_STENCILOP_KEEP;
        if (s.cmp == SDL_GPU_COMPAREOP_ALWAYS) s.fail = SDL_GPU_STENCILOP_KEEP;
        else if (s.cmp == SDL_GPU_COMPAREOP_NEVER) s.pass = SDL_GPU_STENCILOP_KEEP;
        if (s.pass == SDL_GPU_STENCILOP_KEEP && s.fail == SDL_GPU_STENCILOP_KEEP && s.dfail == SDL_GPU_STENCILOP_KEEP)
            s.wmask = 0;
    } else {
        s.pass = s.fail = s.dfail = SDL_GPU_STENCILOP_KEEP;
    }
    return s.wmask || s.cmp != SDL_GPU_COMPAREOP_ALWAYS;
}

// The render-target formats this device makes (d9g_format.h) as SDL's, with DXVK's view format
// rules (D3D9_VK_FORMAT_MAPPING: A8R8G8B8 is B8G8R8A8_UNORM; X8R8G8B8 is the same with an A=1
// swizzle, which DXVK's blend state adjusts for, :6978-6998: not mapped here).
bool rt_format(uint32_t d3dfmt, SDL_GPUTextureFormat* out, std::string* why) {
    if (d3dfmt == D3DFMT_A8R8G8B8) { *out = SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM; return true; }
    if (d3dfmt == D3DFMT_X8R8G8B8) { *why = "X8R8G8B8 render target (DXVK's alpha swizzle, hasAlphaSwizzle)"; return false; }
    *why = "render target format " + std::to_string(d3dfmt);
    return false;
}

}  // namespace

// ------------------------------------------------------------------ the decoders
// DecodeBlendFactor (d3d9_util.cpp:163-184).
bool sdl_blend_factor(uint32_t b, bool is_alpha, SDL_GPUBlendFactor* out, std::string* why) {
    switch (b) {
    default:
    case D3DBLEND_ZERO: *out = SDL_GPU_BLENDFACTOR_ZERO; return true;
    case D3DBLEND_ONE: *out = SDL_GPU_BLENDFACTOR_ONE; return true;
    case D3DBLEND_SRCCOLOR: *out = SDL_GPU_BLENDFACTOR_SRC_COLOR; return true;
    case D3DBLEND_INVSRCCOLOR: *out = SDL_GPU_BLENDFACTOR_ONE_MINUS_SRC_COLOR; return true;
    case D3DBLEND_SRCALPHA: *out = SDL_GPU_BLENDFACTOR_SRC_ALPHA; return true;
    case D3DBLEND_INVSRCALPHA: *out = SDL_GPU_BLENDFACTOR_ONE_MINUS_SRC_ALPHA; return true;
    case D3DBLEND_DESTALPHA: *out = SDL_GPU_BLENDFACTOR_DST_ALPHA; return true;
    case D3DBLEND_INVDESTALPHA: *out = SDL_GPU_BLENDFACTOR_ONE_MINUS_DST_ALPHA; return true;
    case D3DBLEND_DESTCOLOR: *out = SDL_GPU_BLENDFACTOR_DST_COLOR; return true;
    case D3DBLEND_INVDESTCOLOR: *out = SDL_GPU_BLENDFACTOR_ONE_MINUS_DST_COLOR; return true;
    case D3DBLEND_SRCALPHASAT: *out = SDL_GPU_BLENDFACTOR_SRC_ALPHA_SATURATE; return true;
    case D3DBLEND_BOTHSRCALPHA: *out = SDL_GPU_BLENDFACTOR_SRC_ALPHA; return true;
    case D3DBLEND_BOTHINVSRCALPHA: *out = SDL_GPU_BLENDFACTOR_ONE_MINUS_SRC_ALPHA; return true;
    case D3DBLEND_BLENDFACTOR:
        if (is_alpha) { *why = "BLENDFACTOR as an alpha factor (VK_BLEND_FACTOR_CONSTANT_ALPHA)"; return false; }
        *out = SDL_GPU_BLENDFACTOR_CONSTANT_COLOR;
        return true;
    case D3DBLEND_INVBLENDFACTOR:
        if (is_alpha) { *why = "INVBLENDFACTOR as an alpha factor (VK_BLEND_FACTOR_ONE_MINUS_CONSTANT_ALPHA)"; return false; }
        *out = SDL_GPU_BLENDFACTOR_ONE_MINUS_CONSTANT_COLOR;
        return true;
    case D3DBLEND_SRCCOLOR2: *why = "SRCCOLOR2 (VK_BLEND_FACTOR_SRC1_COLOR)"; return false;
    case D3DBLEND_INVSRCCOLOR2: *why = "INVSRCCOLOR2 (VK_BLEND_FACTOR_ONE_MINUS_SRC1_COLOR)"; return false;
    }
}

// DecodeBlendOp (d3d9_util.cpp:187-196).
SDL_GPUBlendOp sdl_blend_op(uint32_t op) {
    switch (op) {
    default:
    case D3DBLENDOP_ADD: return SDL_GPU_BLENDOP_ADD;
    case D3DBLENDOP_SUBTRACT: return SDL_GPU_BLENDOP_SUBTRACT;
    case D3DBLENDOP_REVSUBTRACT: return SDL_GPU_BLENDOP_REVERSE_SUBTRACT;
    case D3DBLENDOP_MIN: return SDL_GPU_BLENDOP_MIN;
    case D3DBLENDOP_MAX: return SDL_GPU_BLENDOP_MAX;
    }
}

// DecodeCompareOp (d3d9_util.cpp:199-211).
SDL_GPUCompareOp sdl_compare_op(uint32_t f) {
    switch (f) {
    default:
    case D3DCMP_NEVER: return SDL_GPU_COMPAREOP_NEVER;
    case D3DCMP_LESS: return SDL_GPU_COMPAREOP_LESS;
    case D3DCMP_EQUAL: return SDL_GPU_COMPAREOP_EQUAL;
    case D3DCMP_LESSEQUAL: return SDL_GPU_COMPAREOP_LESS_OR_EQUAL;
    case D3DCMP_GREATER: return SDL_GPU_COMPAREOP_GREATER;
    case D3DCMP_NOTEQUAL: return SDL_GPU_COMPAREOP_NOT_EQUAL;
    case D3DCMP_GREATEREQUAL: return SDL_GPU_COMPAREOP_GREATER_OR_EQUAL;
    case D3DCMP_ALWAYS: return SDL_GPU_COMPAREOP_ALWAYS;
    }
}

// DecodeStencilOp (d3d9_util.cpp:214-226).
SDL_GPUStencilOp sdl_stencil_op(uint32_t op) {
    switch (op) {
    default:
    case D3DSTENCILOP_KEEP: return SDL_GPU_STENCILOP_KEEP;
    case D3DSTENCILOP_ZERO: return SDL_GPU_STENCILOP_ZERO;
    case D3DSTENCILOP_REPLACE: return SDL_GPU_STENCILOP_REPLACE;
    case D3DSTENCILOP_INCRSAT: return SDL_GPU_STENCILOP_INCREMENT_AND_CLAMP;
    case D3DSTENCILOP_DECRSAT: return SDL_GPU_STENCILOP_DECREMENT_AND_CLAMP;
    case D3DSTENCILOP_INVERT: return SDL_GPU_STENCILOP_INVERT;
    case D3DSTENCILOP_INCR: return SDL_GPU_STENCILOP_INCREMENT_AND_WRAP;
    case D3DSTENCILOP_DECR: return SDL_GPU_STENCILOP_DECREMENT_AND_WRAP;
    }
}

// ------------------------------------------------------------------ faults
namespace {
struct FaultName { uint32_t bit; const char* name; };
constexpr FaultName kMapFaults[] = {
    {MF_BLEND_SWAP, "map-blend-swap"}, {MF_ZFUNC_SWAP, "map-zfunc-swap"}, {MF_NORMALIZE_OFF, "map-normalize-off"},
    {MF_WRITEMASK, "map-writemask"}, {MF_ADDRESS_SWAP, "map-address-swap"}, {MF_BORDER_CLAMP, "map-border-clamp"},
    {MF_LOD_UNQUANTISED, "map-lod-unquantised"}, {MF_BGRA_MASK, "map-bgra-mask"}, {MF_FAN_ORDER, "map-fan-order"},
    {MF_DEPTH_CLIP_OFF, "map-depth-clip-off"}, {MF_ALPHA_OP, "map-alpha-op"}, {MF_DS_BIND, "map-ds-bind"},
    {MF_CULL_SWAP, "map-cull-swap"}, {MF_VI_OFFSET, "map-vi-offset"}, {MF_BGRA_EXTRA, "map-bgra-extra"},
    {MF_FAN_ON_LIST, "map-fan-on-list"}, {MF_BORDER_COLOUR, "map-border-colour"},
    {MF_DEPTH_CLAMP_OFF, "map-depth-clamp-off"}, {MF_DEPTH_BIAS_PUSH, "map-depth-bias-push"},
    {MF_DEPTH_CLAMP_EXTRA, "map-depth-clamp-extra"}, {MF_BORDER_SWIZZLED_VIEW, "map-border-swizzled-view"},
    {MF_BORDER_FALLBACK, "map-border-fallback"}, {MF_VI_DECLORDER, "map-vi-declorder"}, {MF_VI_NO_NULL, "map-vi-no-null"},
    {MF_NULL_VERTEX_RATE, "map-null-vertex-rate"}, {MF_VI_UNREAD_ATTR, "map-vi-unread-attr"},
    {MF_BGRA_DECL_INDEX, "map-bgra-decl-index"}, {MF_PS_SAMPLER_SUPERSET, "map-ps-sampler-superset"},
};
}  // namespace

const char* map_fault_name(uint32_t bit) {
    for (const FaultName& f : kMapFaults)
        if (f.bit == bit) return f.name;
    return nullptr;
}
uint32_t map_fault_bit(const std::string& name) {
    for (const FaultName& f : kMapFaults)
        if (name == f.name) return f.bit;
    return 0;
}

// ------------------------------------------------------------------ the pipeline
PipelineDesc map_pipeline(const PipelineInput& in, const DeviceParams& dp, uint32_t faults) {
    PipelineDesc d;

    // ---- shaders: fixed function has no route in this device. DXVK draws
    // with its fixed-function vertex shader also when a vertex shader is bound but the
    // declaration is missing or has a POSITIONT element (UseProgrammableVS :8490-8494).
    if (!in.vs_id) unmapped(d, "fixed-function vertex processing (no vertex shader)");
    if (!in.ps_id) unmapped(d, "fixed-function pixel processing (no pixel shader)");
    if (in.vs_id && !in.decl_id && !in.fvf_decl)
        unmapped(d, "fixed-function vertex processing (a vertex shader with no declaration)");
    for (uint32_t e = 0; e < in.n_elems && e < 16; e++)
        if (in.vs_id && in.elems[e].usage == D3DDECLUSAGE_POSITIONT) {
            unmapped(d, "fixed-function vertex processing (a vertex shader with a POSITIONT element)");
            break;
        }

    // ---- DXVK's render-state driver hacks (SetRenderState :2511-2670): never mapped
    const uint32_t ps = rsv(in, D3DRS_POINTSIZE), ty = rsv(in, D3DRS_ADAPTIVETESS_Y);
    if (ps == kA2M1 || ps == kA2M0 || ps == kRESZ || ps == kINST || ps == kCENT)
        unmapped(d, "D3DRS_POINTSIZE driver hack value " + std::to_string(ps));
    if (ty == kATOC || ty == kCOPM || ty == kSSAA) unmapped(d, "D3DRS_ADAPTIVETESS_Y driver hack value " + std::to_string(ty));
    if (rsv(in, D3DRS_ADAPTIVETESS_X) == kNVDB) unmapped(d, "NVDB depth-bounds test (D3DRS_ADAPTIVETESS_X)");
    // EncodePointSize converts a NaN to uint16_t, which C++ leaves undefined
    for (uint32_t st : {uint32_t(D3DRS_POINTSIZE), uint32_t(D3DRS_POINTSIZE_MIN), uint32_t(D3DRS_POINTSIZE_MAX)})
        if (std::isnan(rsf(in, st))) unmapped(d, "a NaN point size (render state " + std::to_string(st) + ")");

    // ---- vertex input: BindInputLayout (:7666-7750), by the vertex shader's input signature
    // (PipelineInput::vs_inputs: R1's reflection of DXVK's own analysis, which the state check holds
    // to the bytecode). One attribute per signature entry, at location = its index (:7695-7696);
    // its format, offset and stream from the declaration element of the same semantic
    // (D3D9InputSignature::find, d3d9_shader_analysis.h:139-160, on the packed semantic: usage and
    // usage index four bits each, POSITIONT read as POSITION, :7677-7678; the first entry holding
    // it, and the last element of it, :7680-7684); an entry no element holds reads DXVK's null
    // stream (:7704-7707), the device's kNullStreamSlot; an element the shader does not read gets no
    // attribute. Bindings: the streams the attributes read, ascending, then the null stream (DXVK's
    // bindMask order, :7722-7745), whose rate is per instance (:7738-7742). A stream is SDL's slot of
    // its number; stream 15 is SDL's slot for the null stream (kNullStreamSlot): not mapped.
    if (in.decl_overflow) unmapped(d, "a declaration with more than 16 elements");
    if (in.fvf_decl) unmapped(d, "an FVF declaration (DXVK converts it to elements; this device does not)");
    if (in.vs_inputs_overflow) unmapped(d, "a vertex shader with more than 16 inputs");
    auto packed = [](uint32_t usage, uint32_t index) {
        return uint8_t((usage & 0xFu) | ((index & 0xFu) << 4));
    };
    int elem_of[16];          // the declaration element attribute i reads, or -1 (the null stream)
    uint16_t semantic[16]{};
    uint32_t n_in = 0;
    const uint32_t n_elems = in.n_elems < 16 ? in.n_elems : 16u;
    if (faults & MF_VI_DECLORDER) {
        // R2b's declaration-order stand-in: every declared element, in declaration order
        for (uint32_t e = 0; e < n_elems; e++) {
            if (in.elems[e].type == D3DDECLTYPE_UNUSED) continue;
            elem_of[n_in] = int(e);
            semantic[n_in++] = uint16_t(in.elems[e].usage << 8 | in.elems[e].usage_index);
        }
    } else {
        n_in = in.vs_n_inputs < 16 ? in.vs_n_inputs : 16u;
        for (uint32_t i = 0; i < n_in; i++) {
            elem_of[i] = -1;
            semantic[i] = uint16_t(in.vs_inputs[i].usage << 8 | in.vs_inputs[i].usage_index);
        }
        for (uint32_t e = 0; e < n_elems; e++) {
            const uint32_t usage = in.elems[e].usage == D3DDECLUSAGE_POSITIONT
                                 ? uint32_t(D3DDECLUSAGE_POSITION) : uint32_t(in.elems[e].usage);
            const uint8_t pe = packed(usage, in.elems[e].usage_index);
            for (uint32_t i = 0; i < n_in; i++)
                if (packed(in.vs_inputs[i].usage, in.vs_inputs[i].usage_index) == pe) {
                    elem_of[i] = int(e);
                    break;
                }
        }
        if (faults & MF_VI_UNREAD_ATTR)
            for (uint32_t e = 0; e < n_elems && n_in < 16; e++) {
                bool read = false;
                for (uint32_t i = 0; i < n_in; i++) read |= elem_of[i] == int(e);
                if (read || in.elems[e].type == D3DDECLTYPE_UNUSED) continue;
                elem_of[n_in] = int(e);
                semantic[n_in++] = uint16_t(in.elems[e].usage << 8 | in.elems[e].usage_index);
            }
    }
    uint32_t streams_read = 0;   // bit s: an attribute reads stream s
    for (uint32_t i = 0; i < n_in; i++) {
        SDL_GPUVertexAttribute& a = d.attrs[d.n_attrs];
        a.location = i;
        d.attr_semantic[d.n_attrs] = semantic[i];
        if (elem_of[i] < 0) {
            if (faults & MF_VI_NO_NULL) continue;
            a.buffer_slot = kNullStreamSlot;
            a.format = SDL_GPU_VERTEXELEMENTFORMAT_FLOAT4;   // R32G32B32A32_SFLOAT
            a.offset = 0;
            d.null_stream = true;
            d.n_attrs++;
            continue;
        }
        const VertexElem& el = in.elems[elem_of[i]];
        if (el.stream >= kNullStreamSlot) {
            unmapped(d, "a vertex element on stream " + std::to_string(el.stream) + " (SDL's slot for the null stream)");
            continue;
        }
        if (el.type == D3DDECLTYPE_UNUSED) { unmapped(d, "a D3DDECLTYPE_UNUSED element the vertex shader reads"); continue; }
        const int f = decltype_sdl_format(el.type);
        if (f == SDL_GPU_VERTEXELEMENTFORMAT_INVALID) {
            unmapped(d, "vertex element type " + std::to_string(el.type) + " (DXVK's DecodeDecltype has no SDL_gpu format)");
            continue;
        }
        a.buffer_slot = el.stream;
        a.format = SDL_GPUVertexElementFormat(f);
        a.offset = el.offset + ((faults & MF_VI_OFFSET) ? 4u : 0u);
        // D3DCOLOR: DXVK's B8G8R8A8_UNORM; SDL's UBYTE4_NORM is R8G8B8A8 (the BGRA rule), by location
        if (el.type == D3DDECLTYPE_D3DCOLOR && !(faults & MF_BGRA_MASK))
            d.bgra_mask |= 1u << ((faults & MF_BGRA_DECL_INDEX) ? uint32_t(elem_of[i]) : d.n_attrs);
        d.n_attrs++;
        streams_read |= 1u << el.stream;
    }
    if ((faults & MF_BGRA_EXTRA) && d.n_attrs) d.bgra_mask |= 1u;
    if (streams_read & 1u) {
        SDL_GPUVertexBufferDescription& b = d.vbs[d.n_vbs++];
        b.slot = 0;
        b.pitch = in.stride0;                            // DXVK binds the stride at the draw (dynamic)
        b.input_rate = SDL_GPU_VERTEXINPUTRATE_VERTEX;
        b.instance_step_rate = 0;
        // :7730-7733: INSTANCEDATA makes the binding per instance with a divisor; SDL's step
        // rate must be 0 (SDL_gpu.c): not mapped.
        if (in.freq0 & D3DSTREAMSOURCE_INSTANCEDATA) unmapped(d, "instanced stream 0 (SetStreamSourceFreq INSTANCEDATA)");
    }
    // Streams 1-14 (:7726-7737): per vertex, or with INSTANCEDATA per instance with divisor
    // Setting & 0x7fffff. SDL's instance rate steps by one instance (its step rate must be 0,
    // SDL_gpu.c:1120; Vulkan's default divisor), so divisor 1 is DXVK's binding and any other is not
    // mapped. The stride is the stream's SetStreamSource stride, bound at the draw as DXVK's is.
    for (uint32_t s = 1; s < kNullStreamSlot; s++) {
        if (!((streams_read >> s) & 1u)) continue;
        if (d.n_vbs >= std::size(d.vbs)) { unmapped(d, "more than four vertex bindings"); break; }
        SDL_GPUVertexBufferDescription& b = d.vbs[d.n_vbs++];
        b.slot = s;
        b.pitch = in.stream_stride[s];
        b.instance_step_rate = 0;
        const uint32_t freq = in.stream_freq[s];
        if (freq & D3DSTREAMSOURCE_INSTANCEDATA) {
            b.input_rate = SDL_GPU_VERTEXINPUTRATE_INSTANCE;
            const uint32_t divisor = freq & 0x7fffffu;
            if (divisor != 1u)
                unmapped(d, "an instanced stream with divisor " + std::to_string(divisor) +
                                " (SDL_gpu steps per instance by one)");
        } else {
            b.input_rate = SDL_GPU_VERTEXINPUTRATE_VERTEX;
        }
    }
    if (d.null_stream && d.n_vbs >= std::size(d.vbs)) {
        unmapped(d, "more than four vertex bindings");
    } else if (d.null_stream) {
        // DXVK's dummy binding: per instance, divisor 0, a null buffer of stride 0 (:7738-7742,
        // dxvk_context.cpp:7865-7870); here pitch 0 and SDL's instance rate, which steps by one
        // (every instance reads offset 0 either way)
        SDL_GPUVertexBufferDescription& b = d.vbs[d.n_vbs++];
        b.slot = kNullStreamSlot;
        b.pitch = 0;
        b.input_rate = (faults & MF_NULL_VERTEX_RATE) ? SDL_GPU_VERTEXINPUTRATE_VERTEX : SDL_GPU_VERTEXINPUTRATE_INSTANCE;
        b.instance_step_rate = 0;
    }

    // ---- input assembly: DecodeInputAssemblyState (d3d9_util.cpp:139-160). A type outside
    // D3DPRIMITIVETYPE: DXVK keeps the last draw's topology when the value equals it
    // (ApplyPrimitiveType :8502-8517) and turns D3DPT_FORCE_DWORD into a point list: not mapped.
    if (in.prim < D3DPT_POINTLIST || in.prim > D3DPT_TRIANGLEFAN)
        unmapped(d, "primitive type " + std::to_string(in.prim) + " (not a D3DPRIMITIVETYPE)");
    switch (in.prim) {
    case D3DPT_POINTLIST: d.prim = SDL_GPU_PRIMITIVETYPE_POINTLIST; break;
    case D3DPT_LINELIST: d.prim = SDL_GPU_PRIMITIVETYPE_LINELIST; break;
    case D3DPT_LINESTRIP: d.prim = SDL_GPU_PRIMITIVETYPE_LINESTRIP; break;
    case D3DPT_TRIANGLESTRIP: d.prim = SDL_GPU_PRIMITIVETYPE_TRIANGLESTRIP; break;
    case D3DPT_TRIANGLEFAN:
        // SDL has no fan: a list through the fan cache (d9g_draw), Vulkan's fan order (the FAN rule)
        d.prim = SDL_GPU_PRIMITIVETYPE_TRIANGLELIST;
        d.fan_as_list = true;
        if (in.method == M_DIP || in.method == M_DIPUP)
            unmapped(d, "an indexed triangle fan (needs a CPU index rewrite, not the fan cache)");
        break;
    default:
    case D3DPT_TRIANGLELIST: d.prim = SDL_GPU_PRIMITIVETYPE_TRIANGLELIST; break;
    }
    if ((faults & MF_FAN_ON_LIST) && in.prim == D3DPT_TRIANGLELIST) d.fan_as_list = true;

    // ---- rasterizer: BindRasterizerState (:7093-7113)
    switch (rsv(in, D3DRS_CULLMODE)) {   // DecodeCullMode (d3d9_util.cpp:229-236)
    case D3DCULL_CW: d.rs.cull_mode = (faults & MF_CULL_SWAP) ? SDL_GPU_CULLMODE_BACK : SDL_GPU_CULLMODE_FRONT; break;
    case D3DCULL_CCW: d.rs.cull_mode = (faults & MF_CULL_SWAP) ? SDL_GPU_CULLMODE_FRONT : SDL_GPU_CULLMODE_BACK; break;
    default: d.rs.cull_mode = SDL_GPU_CULLMODE_NONE; break;
    }
    d.rs.front_face = SDL_GPU_FRONTFACE_CLOCKWISE;     // :7101 (both hosts flip the viewport: N6)
    switch (rsv(in, D3DRS_FILLMODE)) {   // DecodeFillMode (d3d9_util.cpp:239-246)
    case D3DFILL_POINT: unmapped(d, "D3DFILL_POINT (VK_POLYGON_MODE_POINT; SDL_gpu has no point fill)"); break;
    case D3DFILL_WIREFRAME:
        // VK_POLYGON_MODE_LINE needs fillModeNonSolid, which SDL_gpu neither reports nor refuses
        // without (it fills instead, SDL_gpu_vulkan.c:1311-1328): not mapped
        unmapped(d, "D3DFILL_WIREFRAME (SDL fills where the device lacks fillModeNonSolid, which it does not report)");
        d.rs.fill_mode = SDL_GPU_FILLMODE_LINE;
        break;
    default: d.rs.fill_mode = SDL_GPU_FILLMODE_FILL; break;
    }
    // :7100 setDepthClip(true): clip on; DXVK also clamps (dxvk_graphics.cpp:585-592). Ruling 3:
    // the patch's clamp beside clip where the device has it, the shader route's R_DEPTH_CLAMP
    // where it does not
    d.rs.enable_depth_clip = !(faults & MF_DEPTH_CLIP_OFF);
    if (!dp.depth_clip_and_clamp) d.residues |= R_DEPTH_CLAMP;
    else if (!(faults & MF_DEPTH_CLAMP_OFF)) d.depth_clamp = true;
    if (faults & MF_DEPTH_CLAMP_EXTRA) d.residues |= R_DEPTH_CLAMP;
    // BindDepthBias (:7116-7134): DXVK's constant is DEPTHBIAS times a scale that depends on the
    // vendor and on VK_EXT_depth_bias_control's representation (:98-108, :1814-1823), neither of
    // which SDL exposes or sets: a non-zero bias is not mapped (the critic's S9). A zero one is
    // DXVK's "disabled" (dxvk_cmdlist.h:1103-1105).
    if (rsf(in, D3DRS_DEPTHBIAS) != 0.0f || rsf(in, D3DRS_SLOPESCALEDEPTHBIAS) != 0.0f)
        unmapped(d, "a non-zero depth bias (DXVK's representation and scale are device-specific)");
    d.rs.enable_depth_bias = false;
    d.rs.depth_bias_constant_factor = d.rs.depth_bias_clamp = d.rs.depth_bias_slope_factor = 0.0f;

    // ---- targets: BindFramebuffer (:6694-6833)
    // A multisample type other than NONE: DXVK's sample count is DecodeMultiSampleType's
    // (NONMASKABLE gives 1 << quality, d3d9_util.cpp:41-63), and this device makes none. A target
    // at a mip level other than 0: DXVK compares the texture's level-0 extent
    // (GetImage()->info().extent, :6721-6725, :6750-6753, :6781-6785), which TargetDesc does not
    // carry (the review's transcription findings 2 and 6).
    for (uint32_t i = 0; i < 4; i++) {
        if (in.rt[i].bound && in.rt[i].ms != 0) unmapped(d, "a multisampled render target (D3DMULTISAMPLE_TYPE " + std::to_string(in.rt[i].ms) + ")");
        if (in.rt[i].bound && in.rt[i].level != 0) unmapped(d, "a render target at a mip level other than 0");
    }
    if (in.ds.bound && in.ds.ms != 0) unmapped(d, "a multisampled depth-stencil surface (D3DMULTISAMPLE_TYPE " + std::to_string(in.ds.ms) + ")");
    if (in.ds.bound && in.ds.level != 0) unmapped(d, "a depth-stencil surface at a mip level other than 0");
    if (rsv(in, D3DRS_SRGBWRITEENABLE)) unmapped(d, "D3DRS_SRGBWRITEENABLE (an sRGB render-target view)");
    uint32_t aw = 0, ah = 0;
    const bool rt0 = in.rt[0].bound;
    if (rt0) { aw = in.rt[0].w; ah = in.rt[0].h; }
    if (in.ds.bound) {
        uint32_t bound = 0;
        for (const TargetDesc& t : in.rt) bound += t.bound;
        const bool rt0Written = (in.ps_rt_mask & 1u) && rsv(in, D3DRS_COLORWRITEENABLE) != 0;
        const bool ignoreRT0 = bound == 1 && aw == 1 && ah == 1 && !rt0Written;
        const bool mismatch = in.ds.w < aw || in.ds.h < ah;   // sample counts: all single (above)
        // A depth-stencil surface whose texture is bound at a stage: DXVK's UpdateActiveHazardsDS
        // (:6361-6413) runs on the surface set, bound or not, and BindFramebuffer adds the depth
        // aspect to the feedback loop whenever its hazard is unresolvable (:6815-6822), a read-only
        // layout or a feedback loop SDL cannot express: not mapped, wherever the surface is set
        // (the R2b review's transcription finding 2)
        if (in.ds_tex_sampled) unmapped(d, "a depth-stencil surface set while its texture is bound at a stage (DXVK's depth hazard)");
        if (!mismatch || !rt0 || ignoreRT0 || (faults & MF_DS_BIND)) {
            if (!rt0 || ignoreRT0) { aw = in.ds.w; ah = in.ds.h; }
            if (in.ds.fmt == D3DFMT_D24S8) {
                d.has_ds = true;
                d.ds_fmt = dp.d24s8_supported ? SDL_GPU_TEXTUREFORMAT_D24_UNORM_S8_UINT : SDL_GPU_TEXTUREFORMAT_D32_FLOAT_S8_UINT;
            } else {
                unmapped(d, "depth-stencil format " + std::to_string(in.ds.fmt));
            }
        }
    }
    // ---- render-target hazards: UpdateActiveHazardsRT (:6302-6352) on the draw's state. DXVK
    // re-runs it whenever an input it reads changes (SetRenderTargetInternal :1743, a texture
    // bind :6286-6291, a pixel-shader mask change :3953-3955, COLORWRITEENABLE(i) turning on or
    // off :2347-2365 through UpdateAnyColorWrites :6214-6217), so its result is the state's,
    // except where it keeps the last one (wasHazard, below). For a target that is level 0 of a
    // texture bound at some stage (canBeSampled :1729-1731, rtUsage, the level-0 test :6320-6325):
    //   * the pixel shader samples one of those stages: a hazard; written too, unresolvable (the
    //     feedback-loop layout, :6814-6820), which SDL cannot express: undecided (M4);
    //   * it samples none and writes the target: no hazard (:6331-6332);
    //   * it samples none and does not write it: the hazard the last state left (wasHazard),
    //     which the draw's state does not say: not mapped.
    // "Written" is :6311-6314 and :6806-6807: the PS writes it and its COLORWRITEENABLE is not 0
    // (the whole value, not the four bits the blend state keeps).
    static constexpr uint32_t kCw[4] = {168, 190, 191, 192};   // ColorWriteIndex (d3d9_util.h:241-243)
    auto rt_written = [&](uint32_t i) { return ((in.ps_rt_mask >> i) & 1u) && rsv(in, kCw[i]) != 0; };
    uint32_t hazardRT = 0;   // the targets BindFramebuffer skips unless written (:6789-6808)
    {
        const uint32_t m = in.rt_tex_sampled & 0xFu;
        if (m & (m - 1u)) unmapped(d, "more than one render target bound as a texture (the stages per target are not recorded)");
        // the pixel shader's sampler mask, its reflection (MF_PS_SAMPLER_SUPERSET: R2b's superset
        // stand-in, every pixel-shader slot)
        const uint32_t ps_samplers = (faults & MF_PS_SAMPLER_SUPERSET) ? 0xFFFFu : in.ps_sampler_mask;
        const bool sampled = (in.rt_tex_stages & ps_samplers) != 0;
        for (uint32_t i = 0; i < 4; i++) {
            if (!((m >> i) & 1u) || !in.rt[i].bound) continue;
            if (sampled) {
                hazardRT |= 1u << i;
                if (rt_written(i)) d.rt_feedback = true;   // the feedback-loop layout (M4)
            } else if (!rt_written(i)) {
                unmapped(d, "a render target's texture bound where the pixel shader neither samples it nor writes the "
                            "target (DXVK keeps the last state's hazard)");
            }
        }
    }
    uint32_t n = 0;
    for (uint32_t i = 0; i < 4; i++) {
        if (!in.rt[i].bound || in.rt[i].w != aw || in.rt[i].h != ah) continue;   // :6779-6784
        if (((hazardRT >> i) & 1u) && !rt_written(i)) continue;                 // :6807-6808
        if (i != n) { unmapped(d, "render-target slots with a gap (SDL's targets are contiguous)"); break; }
        std::string why;
        if (!rt_format(in.rt[i].fmt, &d.ct[i].format, &why)) { unmapped(d, why); }
        n = i + 1;
    }
    d.n_ct = n;

    // ---- blending: BindBlendState (:6958-7027), then the fragment-output state
    // (dxvk_graphics.cpp:286-331)
    Blend mode;
    mode.enable = rsv(in, D3DRS_ALPHABLENDENABLE) != 0;
    uint32_t cs = rsv(in, D3DRS_SRCBLEND), cd = rsv(in, D3DRS_DESTBLEND), co = rsv(in, D3DRS_BLENDOP);
    auto fixup = [](uint32_t& s, uint32_t& t) {   // FixupBlendState (d3d9_util.h:30-41)
        if (s == D3DBLEND_BOTHSRCALPHA) { s = D3DBLEND_SRCALPHA; t = D3DBLEND_INVSRCALPHA; }
        else if (s == D3DBLEND_BOTHINVSRCALPHA) { s = D3DBLEND_INVSRCALPHA; t = D3DBLEND_SRCALPHA; }
    };
    fixup(cs, cd);
    uint32_t as = cs, ad = cd, ao = co;
    if (rsv(in, D3DRS_SEPARATEALPHABLENDENABLE)) {
        as = rsv(in, D3DRS_SRCBLENDALPHA);
        ad = rsv(in, D3DRS_DESTBLENDALPHA);
        ao = rsv(in, D3DRS_BLENDOPALPHA);
        fixup(as, ad);
    }
    if (faults & MF_BLEND_SWAP) {
        auto swap = [](uint32_t& v) {
            if (v == D3DBLEND_SRCALPHA) v = D3DBLEND_INVSRCALPHA;
            else if (v == D3DBLEND_INVSRCALPHA) v = D3DBLEND_SRCALPHA;
        };
        swap(cs);
        swap(as);
    }
    std::string why;
    if (!sdl_blend_factor(cs, false, &mode.cs, &why)) unmapped(d, "SRCBLEND: " + why);
    if (!sdl_blend_factor(cd, false, &mode.cd, &why)) unmapped(d, "DESTBLEND: " + why);
    if (!sdl_blend_factor(as, true, &mode.as, &why)) unmapped(d, "SRCBLEND (alpha): " + why);
    if (!sdl_blend_factor(ad, true, &mode.ad, &why)) unmapped(d, "DESTBLEND (alpha): " + why);
    mode.co = sdl_blend_op(co);
    mode.ao = sdl_blend_op(ao);
    for (uint32_t i = 0; i < d.n_ct; i++) {
        Blend b = mode;
        b.mask = write_mask(in, i, faults);
        if (!(faults & MF_NORMALIZE_OFF)) normalize_blend(b);
        // dxvk_graphics.cpp:299-331: only for targets the pixel shader writes (its reflection);
        // the identity view swizzle of B8G8R8A8 leaves the mask, the format's components are
        // RGBA (all four bits become RGBA), and a zero mask leaves the attachment all zero
        SDL_GPUColorTargetBlendState& s = d.ct[i].blend_state;
        uint32_t m = ((in.ps_rt_mask >> i) & 1u) ? (b.mask & 0xFu) : 0u;
        s.enable_color_write_mask = true;
        if (m) {
            s.enable_blend = b.enable;
            s.src_color_blendfactor = b.cs; s.dst_color_blendfactor = b.cd; s.color_blend_op = b.co;
            s.src_alpha_blendfactor = b.as; s.dst_alpha_blendfactor = b.ad; s.alpha_blend_op = b.ao;
            s.color_write_mask = SDL_GPUColorComponentFlags(m);
        } else {
            s.enable_blend = false;
            s.src_color_blendfactor = s.dst_color_blendfactor = SDL_GPU_BLENDFACTOR_ZERO;
            s.src_alpha_blendfactor = s.dst_alpha_blendfactor = SDL_GPU_BLENDFACTOR_ZERO;
            s.color_blend_op = s.alpha_blend_op = SDL_GPU_BLENDOP_ADD;
            s.color_write_mask = 0;
        }
    }

    // ---- multisampling: BindMultiSampleState (:6941-6955) with single-sampled targets: one
    // sample; DXVK's mask is 0xFFFF (m_validSampleMask false, :1761-1767) and ATOC is off unless
    // a driver hack (unmapped above) turns it on (:6904-6929)
    d.ms.sample_count = SDL_GPU_SAMPLECOUNT_1;
    d.ms.enable_mask = false;
    d.ms.sample_mask = 0;
    d.ms.enable_alpha_to_coverage = false;

    // ---- depth-stencil: BindDepthStencilState (:7044-7090), DxvkDepthStencilState::normalize
    // (dxvk_constant_state.cpp:35-67), then the dynamic emission (dxvk_context.cpp:8065-8127)
    bool depthTest = rsv(in, D3DRS_ZENABLE) != 0;
    bool depthWrite = rsv(in, D3DRS_ZWRITEENABLE) != 0;
    uint32_t zfunc = rsv(in, D3DRS_ZFUNC);
    if (faults & MF_ZFUNC_SWAP)
        zfunc = zfunc == D3DCMP_LESS ? uint32_t(D3DCMP_LESSEQUAL) : zfunc == D3DCMP_LESSEQUAL ? uint32_t(D3DCMP_LESS) : zfunc;
    SDL_GPUCompareOp depthOp = sdl_compare_op(zfunc);
    const bool stencilOn = rsv(in, D3DRS_STENCILENABLE) != 0;
    const bool twoSided = stencilOn && rsv(in, D3DRS_TWOSIDEDSTENCILMODE);
    Stencil front, back;
    if (stencilOn) {
        front.fail = sdl_stencil_op(rsv(in, D3DRS_STENCILFAIL));
        front.pass = sdl_stencil_op(rsv(in, D3DRS_STENCILPASS));
        front.dfail = sdl_stencil_op(rsv(in, D3DRS_STENCILZFAIL));
        front.cmp = sdl_compare_op(rsv(in, D3DRS_STENCILFUNC));
        front.cmask = uint8_t(rsv(in, D3DRS_STENCILMASK));
        front.wmask = uint8_t(rsv(in, D3DRS_STENCILWRITEMASK));
    }
    back = front;
    if (twoSided) {
        back.fail = sdl_stencil_op(rsv(in, D3DRS_CCW_STENCILFAIL));
        back.pass = sdl_stencil_op(rsv(in, D3DRS_CCW_STENCILPASS));
        back.dfail = sdl_stencil_op(rsv(in, D3DRS_CCW_STENCILZFAIL));
        back.cmp = sdl_compare_op(rsv(in, D3DRS_CCW_STENCILFUNC));
        back.cmask = uint8_t(rsv(in, D3DRS_STENCILMASK));
        back.wmask = uint8_t(rsv(in, D3DRS_STENCILWRITEMASK));
    }
    bool stencilTest = stencilOn;
    if (depthTest) {
        if (depthOp == SDL_GPU_COMPAREOP_EQUAL || depthOp == SDL_GPU_COMPAREOP_NEVER) depthWrite = false;
        if (depthOp == SDL_GPU_COMPAREOP_ALWAYS && !depthWrite) depthTest = false;
    } else {
        depthWrite = false;
        depthOp = SDL_GPU_COMPAREOP_ALWAYS;
    }
    if (stencilTest) {
        const bool frontNoOp = !normalize_stencil(front, depthOp);
        const bool backNoOp = !normalize_stencil(back, depthOp);
        if (frontNoOp && backNoOp) stencilTest = false;
    }
    if (!stencilTest) front = back = Stencil();
    // the emission: a disabled test writes nothing and compares ALWAYS
    d.ds.enable_depth_test = depthTest;
    d.ds.enable_depth_write = depthTest && depthWrite;
    d.ds.compare_op = depthTest ? depthOp : SDL_GPU_COMPAREOP_ALWAYS;
    d.ds.enable_stencil_test = stencilTest;
    if (stencilTest) {
        auto set = [](SDL_GPUStencilOpState& o, const Stencil& s) {
            o.fail_op = s.fail; o.pass_op = s.pass; o.depth_fail_op = s.dfail; o.compare_op = s.cmp;
        };
        set(d.ds.front_stencil_state, front);
        set(d.ds.back_stencil_state, back);
        // SDL has one mask pair for both faces. DXVK's faces share STENCILMASK; their write masks
        // differ only where normalisation zeroed a face whose ops are all KEEP (an inert mask)
        if (front.cmask != back.cmask) unmapped(d, "different stencil compare masks per face");
        d.ds.compare_mask = front.cmask;
        d.ds.write_mask = front.wmask ? front.wmask : back.wmask;
    } else {
        for (SDL_GPUStencilOpState* o : {&d.ds.front_stencil_state, &d.ds.back_stencil_state}) {
            o->fail_op = o->pass_op = o->depth_fail_op = SDL_GPU_STENCILOP_KEEP;
            o->compare_op = SDL_GPU_COMPAREOP_ALWAYS;
        }
        d.ds.compare_mask = d.ds.write_mask = 0;
    }
    // MF_DEPTH_BIAS_PUSH, the depth-clamp measurement's positive control: on a draw that tests
    // depth against a bound depth-stencil target, a constant bias of 2^24 units, unclamped (a
    // whole depth range on D24; less on the D32 float fallback: MapFault)
    if ((faults & MF_DEPTH_BIAS_PUSH) && d.has_ds && d.ds.enable_depth_test) {
        d.rs.enable_depth_bias = true;
        d.rs.depth_bias_constant_factor = 16777216.0f;
        d.rs.depth_bias_clamp = d.rs.depth_bias_slope_factor = 0.0f;
    }
    return d;
}

SDL_GPUGraphicsPipelineCreateInfo PipelineDesc::create_info(SDL_PropertiesID props) const {
    SDL_GPUGraphicsPipelineCreateInfo ci{};
    ci.vertex_shader = nullptr;
    ci.fragment_shader = nullptr;
    ci.vertex_input_state.vertex_buffer_descriptions = vbs;
    ci.vertex_input_state.num_vertex_buffers = n_vbs;
    ci.vertex_input_state.vertex_attributes = attrs;
    ci.vertex_input_state.num_vertex_attributes = n_attrs;
    ci.primitive_type = prim;
    ci.rasterizer_state = rs;
    ci.multisample_state = ms;
    ci.depth_stencil_state = ds;
    ci.target_info.color_target_descriptions = ct;
    ci.target_info.num_color_targets = n_ct;
    ci.target_info.has_depth_stencil_target = has_ds;
    ci.target_info.depth_stencil_format = ds_fmt;
    ci.props = props;
    return ci;
}

// ------------------------------------------------------------------ shader-side values
LegacyData map_legacy(const PipelineInput& in, uint32_t faults) {
    LegacyData l;
    // m_alphaTestEnabled = ALPHATESTENABLE && !m_atocEnabled (:6932); ATOC is off without the
    // driver hacks map_pipeline refuses
    const bool alphaTest = rsv(in, D3DRS_ALPHATESTENABLE) != 0;
    const SDL_GPUCompareOp op = sdl_compare_op(rsv(in, D3DRS_ALPHAFUNC));
    // BindAlphaTestState (:7173-7186): VK_COMPARE_OP_ALWAYS, or DecodeCompareOp(ALPHAFUNC);
    // SDL_GPUCompareOp is VkCompareOp + 1
    l.alpha_compare_vk = (alphaTest || (faults & MF_ALPHA_OP)) ? uint32_t(op) - 1u : uint32_t(SDL_GPU_COMPAREOP_ALWAYS) - 1u;
    // GetAlphaTestPrecision (:7137-7170) of RT0: this device's targets are 8-bit (0)
    l.alpha_precision = 0;
    l.alpha_precision_bits = 8;
    l.alpha_ref = uint8_t(rsv(in, D3DRS_ALPHAREF));
    // EncodePointSize (d3d9_device.h:1526-1529): 13.3 fixed point, clamped to [0, 65535]
    auto enc = [&](uint32_t state) {
        const float f = rsf(in, state) * 8.0f;
        const float c = f < 0.0f ? 0.0f : f > 65535.0f ? 65535.0f : f;   // std::clamp: NaN stays NaN
        return uint16_t(std::isnan(f) ? f : c);
    };
    l.point_size = enc(D3DRS_POINTSIZE);
    l.point_min = enc(D3DRS_POINTSIZE_MIN);
    l.point_max = enc(D3DRS_POINTSIZE_MAX);
    l.point_size_f = rsf(in, D3DRS_POINTSIZE);
    // UpdatePointMode (:6574-6607): sprite and scale only for point lists; scale only without a
    // programmable vertex shader (UseProgrammableVS, :8490-8494)
    if (in.prim == D3DPT_POINTLIST) {
        bool posT = false;
        for (uint32_t e = 0; e < in.n_elems && e < 16; e++) posT |= in.elems[e].usage == D3DDECLUSAGE_POSITIONT;
        const bool programmable = in.vs_id != 0 && !posT;
        l.point_scale = rsv(in, D3DRS_POINTSCALEENABLE) && !programmable;
        l.point_sprite = rsv(in, D3DRS_POINTSPRITEENABLE) != 0;
    }
    l.flat_shading = rsv(in, D3DRS_SHADEMODE) == D3DSHADE_FLAT;   // :7103
    return l;
}

// ------------------------------------------------------------------ keys
namespace {
// Little-endian fields into a caller's buffer (a key) or a vector (legacy bytes).
struct Ser {
    std::vector<uint8_t> b;
    void u8(uint32_t v) { b.push_back(uint8_t(v)); }
    void u16(uint32_t v) { u8(v); u8(v >> 8); }
    void u32(uint32_t v) { u16(v); u16(v >> 16); }
    void f32(float f) { uint32_t v; std::memcpy(&v, &f, 4); u32(v); }
};
struct FixedSer {
    uint8_t* b;
    size_t n = 0;
    void u8(uint32_t v) { b[n++] = uint8_t(v); }
    void u16(uint32_t v) { b[n] = uint8_t(v); b[n + 1] = uint8_t(v >> 8); n += 2; }
    void u32(uint32_t v) { b[n] = uint8_t(v); b[n + 1] = uint8_t(v >> 8); b[n + 2] = uint8_t(v >> 16); b[n + 3] = uint8_t(v >> 24); n += 4; }
    void f32(float f) { uint32_t v; std::memcpy(&v, &f, 4); u32(v); }
};
// The largest key: every array of the description full.
constexpr size_t kKeyMax = 1 + 4 + 4 * 16 + 4 + 16 * 18 + 1 + 4 + 1 + 12 + 12 + 3 + 4 + 4 + 2 + 4 + 2 * 16 + 2 + 3 + 4 +
                           4 * 34 + 1 + 4 + 4 + 4 + 1 + 8;
static_assert(kKeyMax <= kPipelineKeyMaxBytes, "kPipelineKeyMaxBytes holds every key");
}  // namespace

PipelineKey key_of(const PipelineDesc& d, const PipelineInput& in, bool with_shaders) {
    PipelineKey k;
    uint8_t buf[kPipelineKeyMaxBytes];
    const size_t n = key_bytes(d, in, with_shaders, buf);
    k.bytes.assign(buf, buf + n);
    k.hash = fnv1a64(k.bytes.data(), k.bytes.size());
    return k;
}

size_t key_bytes(const PipelineDesc& d, const PipelineInput& in, bool with_shaders, uint8_t (&out)[kPipelineKeyMaxBytes]) {
    if (d.n_vbs > std::size(d.vbs) || d.n_attrs > std::size(d.attrs) || d.n_ct > std::size(d.ct))
        throw std::length_error("pipeline description exceeds its fixed arrays");
    FixedSer s{out};
    s.u8(3);   // key version (3: the vertex input by the shader's signature; 2: the depth-clamp property)
    s.u32(d.n_vbs);
    for (uint32_t i = 0; i < d.n_vbs; i++) {
        s.u32(d.vbs[i].slot); s.u32(d.vbs[i].pitch); s.u32(d.vbs[i].input_rate); s.u32(d.vbs[i].instance_step_rate);
    }
    s.u32(d.n_attrs);
    for (uint32_t i = 0; i < d.n_attrs; i++) {
        s.u32(d.attrs[i].location); s.u32(d.attrs[i].buffer_slot); s.u32(d.attrs[i].format); s.u32(d.attrs[i].offset);
        s.u16(d.attr_semantic[i]);
    }
    s.u8(d.null_stream);
    s.u32(d.prim); s.u8(d.fan_as_list);
    s.u32(d.rs.fill_mode); s.u32(d.rs.cull_mode); s.u32(d.rs.front_face);
    s.f32(d.rs.depth_bias_constant_factor); s.f32(d.rs.depth_bias_clamp); s.f32(d.rs.depth_bias_slope_factor);
    s.u8(d.rs.enable_depth_bias); s.u8(d.rs.enable_depth_clip); s.u8(d.depth_clamp);
    s.u32(d.ms.sample_count); s.u32(d.ms.sample_mask); s.u8(d.ms.enable_mask); s.u8(d.ms.enable_alpha_to_coverage);
    s.u32(d.ds.compare_op);
    for (const SDL_GPUStencilOpState* o : {&d.ds.back_stencil_state, &d.ds.front_stencil_state}) {
        s.u32(o->fail_op); s.u32(o->pass_op); s.u32(o->depth_fail_op); s.u32(o->compare_op);
    }
    s.u8(d.ds.compare_mask); s.u8(d.ds.write_mask);
    s.u8(d.ds.enable_depth_test); s.u8(d.ds.enable_depth_write); s.u8(d.ds.enable_stencil_test);
    s.u32(d.n_ct);
    for (uint32_t i = 0; i < d.n_ct; i++) {
        const SDL_GPUColorTargetBlendState& b = d.ct[i].blend_state;
        s.u32(d.ct[i].format);
        s.u32(b.src_color_blendfactor); s.u32(b.dst_color_blendfactor); s.u32(b.color_blend_op);
        s.u32(b.src_alpha_blendfactor); s.u32(b.dst_alpha_blendfactor); s.u32(b.alpha_blend_op);
        s.u32(b.color_write_mask); s.u8(b.enable_blend); s.u8(b.enable_color_write_mask);
    }
    s.u8(d.has_ds); s.u32(d.ds_fmt);
    s.u32(d.bgra_mask); s.u32(d.residues); s.u8(d.rt_feedback);
    if (with_shaders) { s.u32(in.vs_id); s.u32(in.ps_id); }
    return s.n;
}

std::vector<uint8_t> legacy_bytes(const LegacyData& l) {
    Ser s;
    s.u32(l.alpha_compare_vk); s.u32(l.alpha_precision); s.u8(l.alpha_ref);
    s.u16(l.point_size); s.u16(l.point_min); s.u16(l.point_max);
    s.u8(l.point_sprite); s.u8(l.point_scale); s.u8(l.flat_shading);
    return s.b;
}

}  // namespace d9g

// ------------------------------------------------------------------ defaults
namespace d9g {

// ResetState (d3d9_device.cpp:8633-8786, render states; :8838-8851, sampler states), every
// assignment in DXVK's order; test/state_check.cpp holds this to the extracted lines.
void state_defaults(uint32_t rs[256], uint32_t ss[14], const DeviceParams& dp, bool auto_depth) {
    auto f = [](float v) { uint32_t u; std::memcpy(&u, &v, 4); return u; };
    rs[D3DRS_SEPARATEALPHABLENDENABLE] = FALSE;
    rs[D3DRS_ALPHABLENDENABLE] = FALSE;
    rs[D3DRS_BLENDOP] = D3DBLENDOP_ADD;
    rs[D3DRS_BLENDOPALPHA] = D3DBLENDOP_ADD;
    rs[D3DRS_DESTBLEND] = D3DBLEND_ZERO;
    rs[D3DRS_DESTBLENDALPHA] = D3DBLEND_ZERO;
    rs[D3DRS_COLORWRITEENABLE] = 0x0000000f;
    rs[D3DRS_COLORWRITEENABLE1] = 0x0000000f;
    rs[D3DRS_COLORWRITEENABLE2] = 0x0000000f;
    rs[D3DRS_COLORWRITEENABLE3] = 0x0000000f;
    rs[D3DRS_SRCBLEND] = D3DBLEND_ONE;
    rs[D3DRS_SRCBLENDALPHA] = D3DBLEND_ONE;
    rs[D3DRS_BLENDFACTOR] = 0xffffffff;
    rs[D3DRS_ZENABLE] = auto_depth ? D3DZB_TRUE : D3DZB_FALSE;
    rs[D3DRS_ZFUNC] = D3DCMP_LESSEQUAL;
    rs[D3DRS_TWOSIDEDSTENCILMODE] = FALSE;
    rs[D3DRS_ZWRITEENABLE] = TRUE;
    rs[D3DRS_STENCILENABLE] = FALSE;
    rs[D3DRS_STENCILFAIL] = D3DSTENCILOP_KEEP;
    rs[D3DRS_STENCILZFAIL] = D3DSTENCILOP_KEEP;
    rs[D3DRS_STENCILPASS] = D3DSTENCILOP_KEEP;
    rs[D3DRS_STENCILFUNC] = D3DCMP_ALWAYS;
    rs[D3DRS_CCW_STENCILFAIL] = D3DSTENCILOP_KEEP;
    rs[D3DRS_CCW_STENCILZFAIL] = D3DSTENCILOP_KEEP;
    rs[D3DRS_CCW_STENCILPASS] = D3DSTENCILOP_KEEP;
    rs[D3DRS_CCW_STENCILFUNC] = D3DCMP_ALWAYS;
    rs[D3DRS_STENCILMASK] = 0xFFFFFFFF;
    rs[D3DRS_STENCILWRITEMASK] = 0xFFFFFFFF;
    rs[D3DRS_STENCILREF] = 0;
    rs[D3DRS_FILLMODE] = D3DFILL_SOLID;
    rs[D3DRS_CULLMODE] = D3DCULL_CCW;
    rs[D3DRS_DEPTHBIAS] = f(0.0f);
    rs[D3DRS_SLOPESCALEDEPTHBIAS] = f(0.0f);
    rs[D3DRS_SCISSORTESTENABLE] = FALSE;
    rs[D3DRS_ALPHATESTENABLE] = FALSE;
    rs[D3DRS_ALPHAFUNC] = D3DCMP_ALWAYS;
    rs[D3DRS_ALPHAREF] = 0;
    rs[D3DRS_MULTISAMPLEMASK] = 0xffffffff;
    rs[D3DRS_TEXTUREFACTOR] = 0xffffffff;
    rs[D3DRS_DIFFUSEMATERIALSOURCE] = D3DMCS_COLOR1;
    rs[D3DRS_SPECULARMATERIALSOURCE] = D3DMCS_COLOR2;
    rs[D3DRS_AMBIENTMATERIALSOURCE] = D3DMCS_MATERIAL;
    rs[D3DRS_EMISSIVEMATERIALSOURCE] = D3DMCS_MATERIAL;
    rs[D3DRS_LIGHTING] = TRUE;
    rs[D3DRS_COLORVERTEX] = TRUE;
    rs[D3DRS_LOCALVIEWER] = TRUE;
    rs[D3DRS_RANGEFOGENABLE] = FALSE;
    rs[D3DRS_NORMALIZENORMALS] = FALSE;
    rs[D3DRS_SPECULARENABLE] = FALSE;
    rs[D3DRS_AMBIENT] = 0;
    rs[D3DRS_FOGENABLE] = FALSE;
    rs[D3DRS_FOGCOLOR] = 0;
    rs[D3DRS_FOGTABLEMODE] = D3DFOG_NONE;
    rs[D3DRS_FOGSTART] = f(0.0f);
    rs[D3DRS_FOGEND] = f(1.0f);
    rs[D3DRS_FOGDENSITY] = f(1.0f);
    rs[D3DRS_FOGVERTEXMODE] = D3DFOG_NONE;
    rs[D3DRS_CLIPPLANEENABLE] = 0;
    rs[D3DRS_POINTSPRITEENABLE] = FALSE;
    rs[D3DRS_POINTSCALEENABLE] = FALSE;
    rs[D3DRS_POINTSCALE_A] = f(1.0f);
    rs[D3DRS_POINTSCALE_B] = f(0.0f);
    rs[D3DRS_POINTSCALE_C] = f(0.0f);
    rs[D3DRS_POINTSIZE] = f(1.0f);
    rs[D3DRS_POINTSIZE_MIN] = f(1.0f);                     // a D3D9 device (D3D8's is 0)
    rs[D3DRS_POINTSIZE_MAX] = f(dp.point_size_max);        // limits.pointSizeRange[1]
    rs[D3DRS_SRGBWRITEENABLE] = 0;
    rs[D3DRS_SHADEMODE] = D3DSHADE_GOURAUD;
    rs[D3DRS_VERTEXBLEND] = D3DVBF_DISABLE;
    rs[D3DRS_INDEXEDVERTEXBLENDENABLE] = FALSE;
    rs[D3DRS_TWEENFACTOR] = f(0.0f);
    rs[D3DRS_LASTPIXEL] = TRUE;
    rs[D3DRS_DITHERENABLE] = FALSE;
    for (uint32_t w = D3DRS_WRAP0; w <= D3DRS_WRAP7; w++) rs[w] = 0;
    rs[D3DRS_CLIPPING] = TRUE;
    rs[D3DRS_MULTISAMPLEANTIALIAS] = TRUE;
    rs[D3DRS_PATCHEDGESTYLE] = D3DPATCHEDGE_DISCRETE;
    rs[D3DRS_DEBUGMONITORTOKEN] = D3DDMT_ENABLE;
    rs[D3DRS_POSITIONDEGREE] = D3DDEGREE_CUBIC;
    rs[D3DRS_NORMALDEGREE] = D3DDEGREE_LINEAR;
    rs[D3DRS_ANTIALIASEDLINEENABLE] = FALSE;
    rs[D3DRS_MINTESSELLATIONLEVEL] = f(1.0f);
    rs[D3DRS_MAXTESSELLATIONLEVEL] = f(1.0f);
    rs[D3DRS_ADAPTIVETESS_X] = f(0.0f);
    rs[D3DRS_ADAPTIVETESS_Y] = f(0.0f);
    rs[D3DRS_ADAPTIVETESS_Z] = f(1.0f);
    rs[D3DRS_ADAPTIVETESS_W] = f(0.0f);
    rs[D3DRS_ENABLEADAPTIVETESSELLATION] = FALSE;
    for (uint32_t w = D3DRS_WRAP8; w <= D3DRS_WRAP15; w++) rs[w] = 0;
    ss[D3DSAMP_ADDRESSU] = D3DTADDRESS_WRAP;
    ss[D3DSAMP_ADDRESSV] = D3DTADDRESS_WRAP;
    ss[D3DSAMP_ADDRESSW] = D3DTADDRESS_WRAP;
    ss[D3DSAMP_BORDERCOLOR] = 0x00000000;
    ss[D3DSAMP_MAGFILTER] = D3DTEXF_POINT;
    ss[D3DSAMP_MINFILTER] = D3DTEXF_POINT;
    ss[D3DSAMP_MIPFILTER] = D3DTEXF_NONE;
    ss[D3DSAMP_MIPMAPLODBIAS] = f(0.0f);
    ss[D3DSAMP_MAXMIPLEVEL] = 0;
    ss[D3DSAMP_MAXANISOTROPY] = 1;
    ss[D3DSAMP_SRGBTEXTURE] = 0;
    ss[D3DSAMP_ELEMENTINDEX] = 0;
    ss[D3DSAMP_DMAPOFFSET] = 0;
}

}  // namespace d9g
