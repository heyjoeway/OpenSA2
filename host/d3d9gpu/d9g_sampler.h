// d9g_sampler.h: a sampler stage's D3D9 state as an SDL_gpu sampler description (R2b-prep,
// private/generated/s5/r2b/PLAN.md section 5), as DXVK 3.1.1 decides it: BindSampler
// (d3d9_device.cpp:7199-7277), D3D9SamplerInfo (d3d9_state.h:523-546), DxvkSamplerKey
// (dxvk_sampler.h:65-121) and DxvkSampler's constructor (dxvk_sampler.cpp:9-63). No sampler
// object is made: nothing would bind it without a shader route (OPEN-1).
//
// D3DTADDRESS_BORDER (ruling 3, Documentation/decisions/2026-10-04-S5-user-rulings.md: the SDL patch
// where the device supports it, the shader emulation as the fallback where it does not) is
// CLAMP_TO_BORDER with one of the patch's three border colours (patches/sdl3) where three things
// hold: the device reports the capability (DeviceParams::sampler_clamp_to_border); the texture's
// view in DXVK has the identity component mapping (no texture, or a format whose DXVK mapping
// carries no swizzle: d9g_format.h dxvk_view_identity); and the colour DXVK builds is one of the
// three. Anywhere else BORDER
// is the shader route's obligation (AddrDecision::fallback_border): such a description carries no
// SDL_GPUSamplerCreateInfo at all, so no address mode can stand in for the border silently, and
// test/state_check.cpp counts it with every other field compared.
//
// Rules mirrored from DXVK 3.1.1 (zlib licence; Copyright Philip Rebohle, Joshua Ashton et al.;
// private/s5-tools/src/dxvk-3.1.1/LICENSE), cited per rule.
#pragma once

#include "d9g_state.h"

#include <SDL3/SDL_gpu.h>

#include <array>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace d9g {

// patches/sdl3's sampler additions by value and name, so a stock SDL3's headers build the same
// code (the device detects the patch at run time: d9g_gpu_sdl.cpp): address mode 3, the border
// colours, and the create property that carries one. Where the headers are the patched ones,
// they are held to them.
inline constexpr SDL_GPUSamplerAddressMode kAddressClampToBorder = SDL_GPUSamplerAddressMode(3);
enum BorderColour : int32_t { kBorderNone = -1, kBorderTransparentBlack = 0, kBorderOpaqueBlack = 1, kBorderOpaqueWhite = 2 };
inline constexpr char kPropSamplerBorderColour[] = "SDL.gpu.sampler.create.sa2recomp.border_color";
#ifdef SDL_PROP_GPU_SAMPLER_CREATE_SA2RECOMP_BORDER_COLOR_NUMBER
static_assert(SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_BORDER == kAddressClampToBorder);
static_assert(int(SDL_GPU_SAMPLERBORDERCOLOR_TRANSPARENT_BLACK) == kBorderTransparentBlack &&
              int(SDL_GPU_SAMPLERBORDERCOLOR_OPAQUE_BLACK) == kBorderOpaqueBlack &&
              int(SDL_GPU_SAMPLERBORDERCOLOR_OPAQUE_WHITE) == kBorderOpaqueWhite);
static_assert(std::string_view(SDL_PROP_GPU_SAMPLER_CREATE_SA2RECOMP_BORDER_COLOR_NUMBER) == kPropSamplerBorderColour);
#endif

// mapped: an SDL address mode; fallback_border: D3DTADDRESS_BORDER the shader route emulates
// (ruling 3's fallback, R1's lowering); unmapped: SDL_gpu has no value for it.
enum class AddrDecision : uint8_t { mapped, fallback_border, unmapped };

// Every field of the description, with a fallback axis left as SDL's REPEAT: a distinct type
// (not SDL_GPUSamplerCreateInfo) so it cannot reach SDL_CreateGPUSampler (the critic's N4). The
// state check reads it to compare a fallback BORDER stage's other fields.
struct SamplerFields {
    SDL_GPUFilter min_filter = SDL_GPU_FILTER_NEAREST, mag_filter = SDL_GPU_FILTER_NEAREST;
    SDL_GPUSamplerMipmapMode mipmap_mode = SDL_GPU_SAMPLERMIPMAPMODE_NEAREST;
    SDL_GPUSamplerAddressMode address[3] = {SDL_GPU_SAMPLERADDRESSMODE_REPEAT, SDL_GPU_SAMPLERADDRESSMODE_REPEAT,
                                            SDL_GPU_SAMPLERADDRESSMODE_REPEAT};
    float mip_lod_bias = 0.0f, max_anisotropy = 0.0f, min_lod = 0.0f, max_lod = 0.0f;
    SDL_GPUCompareOp compare_op = SDL_GPU_COMPAREOP_INVALID;
    bool enable_anisotropy = false, enable_compare = false;
};

struct SamplerDesc {
    // Empty when any axis is a fallback or anything unmapped. Its props are 0: SDL takes
    // create_info(CreateProps(desc).id()) (d9g_props.h), which carries the border colour, never
    // *info itself (a CLAMP_TO_BORDER sampler would take SDL's default colour).
    std::optional<SDL_GPUSamplerCreateInfo> info;
    std::array<AddrDecision, 3> axes{};
    SamplerFields fields;
    // kPropSamplerBorderColour's value, where an axis is mapped to CLAMP_TO_BORDER (kBorderNone
    // otherwise), which CreateProps (d9g_props.h) sets: R3's device when it creates the sampler,
    // and the state check, whose SDL side models SDL's property functions (test/ref_sdl.c).
    int32_t border_colour = kBorderNone;
    uint32_t border_d3dcolor = 0;                   // D3DSAMP_BORDERCOLOR
    std::string fallback_why;                       // why a BORDER axis is the shader route's, "" where none is
    std::vector<std::string> unmapped;
    bool fallback() const;
    // *info with `props`: CreateProps(*this).id() (d9g_props.h), the one maker of the border
    // colour property. Only where info is set.
    SDL_GPUSamplerCreateInfo create_info(SDL_PropertiesID props) const;
};

// `dp`: the device's capabilities (sampler_clamp_to_border).
SamplerDesc map_sampler(const SamplerInput& in, const SamplerOptions& opt, const DeviceParams& dp, uint32_t map_faults);

// The border colour DXVK builds for a BORDER sampler, as one of SDL's three, or kBorderNone
// where it is none of them (a custom colour, or DXVK's nearest-standard fallback on a device
// without custom colours: the shader route's). BindSampler (d3d9_device.cpp:7263-7268) decodes
// D3DSAMP_BORDERCOLOR (DecodeD3DCOLOR); DxvkSampler (dxvk_sampler.cpp:27, :55-56) applies the
// view's inverse swizzle, which is the identity for every view map_sampler maps BORDER on; and
// determineBorderColorType (:84-108) takes the first of transparent black, opaque black, opaque
// white that matches exactly in 4 components, or in 1 (red) with depth compare on.
int32_t dxvk_standard_border_colour(uint32_t d3dcolor, bool depth_compare);

// DXVK's fixed point (util_bit.h:657-698), which its sampler key stores the LOD range and bias
// in: minLod and maxLod are 4.8 unsigned, the bias 6.8 signed (a 14-bit field). This is why a
// multi-mip sampler's max_lod is 15.99609375, not 16.
uint32_t encode_fixed_u48(float n);
int32_t encode_fixed_s68(float n);
float decode_fixed_u48(uint32_t n);
float decode_fixed_s68(uint32_t field14);

// The bytes of a SamplerInput (the device's distinct-sampler map, the keys file): each sampler
// state, then tex, levels, cube, depth and tex_fmt, as little-endian u32s.
inline constexpr size_t kSamplerInputBytes = 4 * (std::tuple_size<decltype(SamplerInput::ss)>::value + 5);
void sampler_input_bytes(const SamplerInput& in, uint8_t (&out)[kSamplerInputBytes]);

}  // namespace d9g
