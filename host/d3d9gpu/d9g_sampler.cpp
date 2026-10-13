// d9g_sampler.cpp: see d9g_sampler.h. Every rule is DXVK 3.1.1's, cited by line;
// test/state_check.cpp holds the result to DXVK's own code, field by field.
#include "d9g_sampler.h"

#include "d9g_format.h"
#include "d9g_pipeline.h"

#include <algorithm>
#include <cstring>

namespace d9g {

// encodeFixed and decodeFixed (util_bit.h:657-698), for the sampler key's fields.
uint32_t encode_fixed_u48(float n) {
    if (n != n) return 0u;
    n *= float(1u << 8);
    n = std::max(n, 0.0f);
    n = std::min(n, float(1u << 12) - 1.0f);
    n += 0.5f;
    return uint32_t(n) & 0xFFFu;           // the key's 12-bit fields (dxvk_sampler.h:44-45)
}
int32_t encode_fixed_s68(float n) {
    if (n != n) return 0;
    n *= float(1u << 8);
    n = std::max(n, -float(1u << 13));
    n = std::min(n, float(1u << 13) - 1.0f);
    n += n < 0.0f ? -0.5f : 0.5f;
    int32_t r = int32_t(n);
    r &= (int32_t(1u) << 14) - 1;
    return r;
}
float decode_fixed_u48(uint32_t n) { return float(n) / float(1u << 8); }
float decode_fixed_s68(uint32_t field14) {
    int32_t n = int32_t(field14 & 0x3FFFu);
    n -= (n & (int32_t(1u) << 13)) << 1u;
    return float(n) / float(1u << 8);
}

namespace {
// DecodeFilter, DecodeMipFilter (d3d9_util.h:168-174): above POINT is LINEAR.
SDL_GPUFilter filter(uint32_t f) { return f > D3DTEXF_POINT ? SDL_GPU_FILTER_LINEAR : SDL_GPU_FILTER_NEAREST; }
SDL_GPUSamplerMipmapMode mip(uint32_t f) {
    return f > D3DTEXF_POINT ? SDL_GPU_SAMPLERMIPMAPMODE_LINEAR : SDL_GPU_SAMPLERMIPMAPMODE_NEAREST;
}
// DecodeAddressMode (d3d9_util.h:176-187), its look-up table and shift: an out-of-range mode
// gives REPEAT. The Vulkan value, then SDL's. A BORDER axis comes back as fallback_border with
// the placeholder REPEAT; map_sampler decides, for the sampler as a whole, whether the patch's
// CLAMP_TO_BORDER takes it.
AddrDecision address(uint32_t mode, uint32_t faults, SDL_GPUSamplerAddressMode* out, std::string* why) {
    constexpr uint32_t kRepeat = 0, kMirroredRepeat = 1, kClampToEdge = 2, kClampToBorder = 3, kMirrorClampToEdge = 4;
    constexpr uint32_t Lut = (kMirroredRepeat << (3 * D3DTADDRESS_MIRROR)) | (kClampToEdge << (3 * D3DTADDRESS_CLAMP)) |
                             (kClampToBorder << (3 * D3DTADDRESS_BORDER)) |
                             (kMirrorClampToEdge << (3 * D3DTADDRESS_MIRRORONCE));
    const uint32_t shift = std::min(uint32_t(mode) * 3u, 31u);
    uint32_t vk = (Lut >> shift) & 0x7u;
    if (faults & MF_ADDRESS_SWAP) vk = vk == kRepeat ? kMirroredRepeat : vk == kMirroredRepeat ? kRepeat : vk;
    switch (vk) {
    case kRepeat: *out = SDL_GPU_SAMPLERADDRESSMODE_REPEAT; return AddrDecision::mapped;
    case kMirroredRepeat: *out = SDL_GPU_SAMPLERADDRESSMODE_MIRRORED_REPEAT; return AddrDecision::mapped;
    case kClampToEdge: *out = SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_EDGE; return AddrDecision::mapped;
    case kClampToBorder:
        if (faults & MF_BORDER_CLAMP) { *out = SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_EDGE; return AddrDecision::mapped; }
        *out = SDL_GPU_SAMPLERADDRESSMODE_REPEAT;   // a placeholder in SamplerFields only
        return AddrDecision::fallback_border;
    default:
        *why = "D3DTADDRESS_MIRRORONCE (VK_SAMPLER_ADDRESS_MODE_MIRROR_CLAMP_TO_EDGE; SDL_gpu has none)";
        *out = SDL_GPU_SAMPLERADDRESSMODE_REPEAT;
        return AddrDecision::unmapped;
    }
}
float bits_f(uint32_t v) {
    float f;
    std::memcpy(&f, &v, 4);
    return f;
}
}  // namespace

int32_t dxvk_standard_border_colour(uint32_t d3dcolor, bool depth_compare) {
    float c[4];
    decode_d3dcolor(d3dcolor, c);
    // determineBorderColorType's table, in its order (dxvk_sampler.cpp:85-89)
    static const float kStandard[3][4] = {{0.0f, 0.0f, 0.0f, 0.0f}, {0.0f, 0.0f, 0.0f, 1.0f}, {1.0f, 1.0f, 1.0f, 1.0f}};
    static const int32_t kSdl[3] = {kBorderTransparentBlack, kBorderOpaqueBlack, kBorderOpaqueWhite};
    const uint32_t components = depth_compare ? 1u : 4u;   // :92
    for (int e = 0; e < 3; e++) {
        bool equal = true;
        for (uint32_t i = 0; i < components; i++) equal &= c[i] == kStandard[e][i];
        if (equal) return kSdl[e];
    }
    return kBorderNone;
}

bool SamplerDesc::fallback() const {
    return std::any_of(axes.begin(), axes.end(), [](AddrDecision a) { return a == AddrDecision::fallback_border; });
}

SamplerDesc map_sampler(const SamplerInput& in, const SamplerOptions& opt, const DeviceParams& dp, uint32_t faults) {
    SamplerDesc d;
    SamplerFields& f = d.fields;
    // D3D9SamplerInfo (d3d9_state.h:524-534)
    const uint32_t addressU = in.ss[D3DSAMP_ADDRESSU], addressV = in.ss[D3DSAMP_ADDRESSV], addressW = in.ss[D3DSAMP_ADDRESSW];
    const uint32_t magFilter = in.ss[D3DSAMP_MAGFILTER], minFilter = in.ss[D3DSAMP_MINFILTER];
    const uint32_t mipFilter = in.ss[D3DSAMP_MIPFILTER];
    const float mipLodBias = bits_f(in.ss[D3DSAMP_MIPMAPLODBIAS]);
    const uint32_t maxMipLevel = in.ss[D3DSAMP_MAXMIPLEVEL], maxAnisotropy = in.ss[D3DSAMP_MAXANISOTROPY];
    d.border_d3dcolor = in.ss[D3DSAMP_BORDERCOLOR];
    // the sRGB view is the texture's, not the sampler's (BindSampler :7202, GetSampleView)
    if (in.ss[D3DSAMP_SRGBTEXTURE] & 1u) d.unmapped.push_back("D3DSAMP_SRGBTEXTURE (an sRGB texture view)");
    // key.setFilter (:7220-7223)
    f.min_filter = filter(minFilter);
    f.mag_filter = filter(magFilter);
    f.mipmap_mode = mip(mipFilter);
    // address modes (:7225-7238)
    if (in.tex && in.cube) {
        // CLAMP_TO_EDGE, and legacy (non-seamless) cube filtering unless d3d9.seamlessCubes: a
        // sampler flag SDL_gpu has no way to set
        for (auto& a : f.address) a = SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_EDGE;
        d.axes = {AddrDecision::mapped, AddrDecision::mapped, AddrDecision::mapped};
        if (!opt.seamless_cubes) d.unmapped.push_back("a cube texture (DXVK's non-seamless cube filtering)");
    } else {
        const uint32_t modes[3] = {addressU, addressV, addressW};
        for (int i = 0; i < 3; i++) {
            std::string why;
            d.axes[i] = address(modes[i], faults, &f.address[i], &why);
            if (d.axes[i] == AddrDecision::unmapped) d.unmapped.push_back(why);
        }
    }
    // key.setDepthCompare (:7240): LESS_OR_EQUAL where the stage's depth bit is set (the
    // device's m_textureSlotTracking.depth: a depth-format texture bound); off, compareOp 0 (NEVER)
    f.enable_compare = in.depth;
    f.compare_op = f.enable_compare ? SDL_GPU_COMPAREOP_LESS_OR_EQUAL : SDL_GPU_COMPAREOP_INVALID;
    // LOD range, bias and anisotropy (:7242-7264), through the key's fixed point
    uint32_t aniso = 0, minLod = 0, maxLod = 0;
    int32_t bias = 0;
    const bool multiMip = in.tex && in.levels > 1u;
    if (!multiMip) {
        minLod = encode_fixed_u48(0.0f);
        maxLod = encode_fixed_u48(std::max(1.0f, 0.0f));
        bias = encode_fixed_s68(0.0f);
    } else if (mipFilter) {
        aniso = minFilter == D3DTEXF_ANISOTROPIC ? maxAnisotropy : 0u;
        if (opt.sampler_anisotropy != -1 && minFilter > D3DTEXF_POINT) aniso = uint32_t(opt.sampler_anisotropy);
        aniso = std::min(aniso, 16u);                            // setAniso (dxvk_sampler.h:71-73)
        float lodBias = mipLodBias;
        lodBias += opt.sampler_lod_bias;
        if (opt.clamp_negative_lod_bias) lodBias = std::max(lodBias, 0.0f);
        const float lo = float(maxMipLevel), hi = 16.0f;         // setLodRange (dxvk_sampler.h:101-105)
        minLod = encode_fixed_u48(lo);
        maxLod = encode_fixed_u48(std::max(hi, lo));
        bias = encode_fixed_s68(lodBias);
    }
    // DxvkSampler (dxvk_sampler.cpp:36-49): the fields decoded back
    f.mip_lod_bias = decode_fixed_s68(uint32_t(bias));
    f.enable_anisotropy = aniso > 0u;
    f.max_anisotropy = float(aniso);
    f.min_lod = decode_fixed_u48(minLod);
    f.max_lod = (faults & MF_LOD_UNQUANTISED) && multiMip && mipFilter ? 16.0f : decode_fixed_u48(maxLod);

    // BORDER (ruling 3): the patch's CLAMP_TO_BORDER where the device reports it, DXVK's view of the
    // texture has the identity component mapping and DXVK's colour is one of SDL's three; the
    // shader route's fallback (fallback_border, never a stand-in address mode) anywhere else
    if (d.fallback()) {
        const FormatInfo* fi = in.tex ? format_info(in.tex_fmt) : nullptr;
        const int32_t colour = dxvk_standard_border_colour(d.border_d3dcolor, f.enable_compare);
        if (!dp.sampler_clamp_to_border || (faults & MF_BORDER_FALLBACK))
            d.fallback_why = "the device reports no CLAMP_TO_BORDER (SDL_PROP_GPU_DEVICE_SA2RECOMP_SAMPLER_CLAMP_TO_BORDER_BOOLEAN)";
        else if (in.tex && !fi)
            d.fallback_why = "a texture format this device does not make, whose view DXVK's table swizzles as it may";
        else if (fi && !fi->dxvk_view_identity && !(faults & MF_BORDER_SWIZZLED_VIEW))
            d.fallback_why = std::string("a ") + fi->name + " texture: DXVK's view of it swizzles (d9g_format.h dxvk_view_identity), "
                             "and so does DXVK's border";
        else if (colour == kBorderNone)
            d.fallback_why = "D3DSAMP_BORDERCOLOR is none of the three standard colours DXVK matches exactly";
        else {
            for (int i = 0; i < 3; i++)
                if (d.axes[i] == AddrDecision::fallback_border) {
                    d.axes[i] = AddrDecision::mapped;
                    f.address[i] = kAddressClampToBorder;
                }
            // MF_BORDER_COLOUR: the next of the three, a wrong colour
            d.border_colour = (faults & MF_BORDER_COLOUR) ? (colour + 1) % 3 : colour;
        }
    }
    if (!d.fallback() && d.unmapped.empty()) {
        SDL_GPUSamplerCreateInfo ci{};
        ci.min_filter = f.min_filter;
        ci.mag_filter = f.mag_filter;
        ci.mipmap_mode = f.mipmap_mode;
        ci.address_mode_u = f.address[0];
        ci.address_mode_v = f.address[1];
        ci.address_mode_w = f.address[2];
        ci.mip_lod_bias = f.mip_lod_bias;
        ci.max_anisotropy = f.max_anisotropy;
        ci.compare_op = f.compare_op;
        ci.min_lod = f.min_lod;
        ci.max_lod = f.max_lod;
        ci.enable_anisotropy = f.enable_anisotropy;
        ci.enable_compare = f.enable_compare;
        ci.props = 0;   // the border colour is create_info's (CreateProps, d9g_props.h)
        d.info = ci;
    }
    return d;
}

SDL_GPUSamplerCreateInfo SamplerDesc::create_info(SDL_PropertiesID props) const {
    SDL_GPUSamplerCreateInfo ci = *info;
    ci.props = props;
    return ci;
}

void sampler_input_bytes(const SamplerInput& in, uint8_t (&out)[kSamplerInputBytes]) {
    size_t n = 0;
    auto u32 = [&](uint32_t v) { for (int i = 0; i < 4; i++) out[n++] = uint8_t(v >> (8 * i)); };
    for (uint32_t v : in.ss) u32(v);
    u32(in.tex); u32(in.levels); u32(in.cube); u32(in.depth); u32(in.tex_fmt);
}

}  // namespace d9g
