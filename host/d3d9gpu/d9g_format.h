// d9g_format.h: Direct3D 9 formats, colours and vertex declarations as SDL_gpu's,
// following DXVK 3.1.1's mapping (src/d3d9/d3d9_format.cpp, d3d9_util.cpp) where
// SDL_gpu has the same format, and naming what it does not have.
//
// Textures: A8R8G8B8 (21) and X8R8G8B8 (22) are B8G8R8A8_UNORM; X8's alpha is
// forced to 0xFF on upload, which is what DXVK's A=1 view swizzle gives when the
// texture is sampled (R2a samples nothing; the fill is for R3). DXT1, DXT3 and DXT5
// are BC1, BC2 and BC3. D24S8 (75) is D24_UNORM_S8_UINT, or D32_FLOAT_S8_UINT where
// the device has no D24S8 (DXVK's fallback, d3d9_format.cpp). R5G6B5 (23) and A8 (28)
// keep their own 16- and 8-bit layout in the CPU store and are B8G8R8A8_UNORM on the
// GPU, expanded on upload to what DXVK's view samples: R5G6B5_UNORM_PACK16 as
// (r, g, b, 1), and A8's R8_UNORM with the (0, 0, 0, R) swizzle as (0, 0, 0, a). The
// expansion is a choice, not a need: Vulkan requires R5G6B5_UNORM_PACK16 and R8_UNORM to
// be sampleable, and SDL_gpu names both (B5G6R5_UNORM, and A8_UNORM, which its Vulkan
// backend makes as R8 with that swizzle); here the two share the 4-byte formats' BGRA8
// path. Every other format is not implemented, by name.
//
// The pitch and row counts are the packed ones of the recording device and
// host/player/texcodec (one row of blocks for DXT), which a replay's lock data follows.
// For the 4-byte and BC formats they are also DXVK's lock pitch (d3d9_device.cpp
// LockImage: align(elementSize * blocks, 4)). For R5G6B5 and A8 they are not: DXVK pads
// each row to 4 bytes (16 and 8 bytes for 7 texels, where the packed rows are 14 and 7).
//
// X8 render targets (DXVK's A=1 swizzle and blend fix-ups) are not handled: no
// stream renders to one, and R3 has to add them before one does. R5G6B5 and A8 render
// targets are not implemented, by name: their GPU image is BGRA8, expanded only on upload.
#pragma once

#include <windows.h>
#include <d3d9.h>

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace d9g {

enum class GFmt : uint8_t { none, bgra8, bc1, bc2, bc3, d24s8, d32s8 };
// A store layout the GPU texture does not share: expanded to BGRA8 on upload.
enum class Expand : uint8_t { none, r5g6b5, a8 };

struct FormatInfo {
    uint32_t d3d;          // the D3DFORMAT
    GFmt gpu;              // SDL_gpu's format (d24s8 may become d32s8 on a device without it)
    uint32_t block_bytes;  // bytes per 4x4 block for BC formats, else 0
    uint32_t pixel_bytes;  // bytes per pixel for the others
    bool depth, stencil;   // aspects
    bool x8;               // alpha forced to 0xFF on upload
    // DXVK's view of a texture of this format has the identity component mapping: its format
    // mapping (src/d3d9/d3d9_format.cpp ConvertFormatUnfixed) carries no Swizzle. False where it
    // does (X8R8G8B8's A=ONE, :19-24; A8's (0, 0, 0, R), :50-55): DXVK's BORDER on such a view
    // applies the swizzle's inverse to the colour and the view's swizzle to the border, which
    // this device's swizzle-free BGRA8 texture cannot build (d9g_sampler.cpp maps BORDER only
    // where this holds).
    bool dxvk_view_identity;
    const char* name;
    Expand expand = Expand::none;   // the store's pixels expanded to BGRA8 on upload
};

// nullptr: a format this device does not implement.
const FormatInfo* format_info(uint32_t d3dfmt);
// Every format this device implements, *count of them (the state check's sweep runs each).
const FormatInfo* all_formats(size_t* count);
const char* gfmt_name(GFmt g);

inline uint32_t level_dim(uint32_t d, uint32_t level) {
    const uint32_t v = level >= 32 ? 0u : d >> level;
    return v ? v : 1u;
}
uint32_t level_pitch(uint32_t w, const FormatInfo& f);
uint32_t level_rows(uint32_t h, const FormatInfo& f);
inline size_t level_bytes(uint32_t w, uint32_t h, const FormatInfo& f) {
    return size_t(level_pitch(w, f)) * level_rows(h, f);
}
// util::computeMipLevelCount: levels down to 1x1.
uint32_t full_chain(uint32_t w, uint32_t h);

// Byte 3 of every BGRA8 pixel set to 0xFF (X8R8G8B8 uploads).
void x8_alpha_fill(uint8_t* p, size_t bytes);

// `pixels` packed store pixels of an Expand format as BGRA8 into `dst` (pixels * 4 bytes):
// R5G6B5's channels as the nearest 8-bit value to UNORM's c / 31 and c / 63 (round(c * 255 / 31),
// round(c * 255 / 63); bit replication is one off for 14 of the codes), alpha 255; A8 as black
// with its alpha.
void expand_to_bgra8(Expand e, const uint8_t* src, size_t pixels, uint8_t* dst);

// DXVK's DecodeD3DCOLOR (src/util/util_misc.h): ARGB bytes / 255.0f, as RGBA floats.
void decode_d3dcolor(uint32_t argb, float rgba[4]);

// Vertex declarations: each element's SDL_GPUVertexElementFormat (as an int, so
// this header needs no SDL), 0 where SDL_gpu has no format DXVK's (d3d9_util.cpp
// DecodeDecltype) can be matched by. D3DCOLOR is DXVK's B8G8R8A8_UNORM, which SDL_gpu
// does not have: it is UBYTE4_NORM with the element's bit in the BGRA input mask, for
// the shader lowering to swap .zyxw (R3, the open shader-route question; not here).
struct VertexElement {
    uint16_t stream, offset;
    uint8_t type, method, usage, usage_index;
    int sdl_format;        // SDL_GPUVertexElementFormat, 0 (INVALID) when none
};
struct DeclInfo {
    std::vector<VertexElement> elements;
    uint32_t bgra_mask = 0;          // bit i: element i is D3DCOLOR
    uint32_t without_sdl_format = 0; // elements with no SDL_gpu format
};
DeclInfo decl_info(const D3DVERTEXELEMENT9* elements, size_t count);
int decltype_sdl_format(uint8_t d3d_decltype);

// Triangle-fan vertices 0..n-1 as a triangle list in Vulkan's fan order
// (i + 1, i + 2, 0), a rotation of D3D9's (0, i + 1, i + 2) that keeps the winding;
// the order DXVK hands Vulkan (RENDERER.md section 2).
std::vector<uint32_t> fan_indices(uint32_t vertices);

// DXVK's GetVertexCount (d3d9_util.cpp): vertices for `count` primitives.
uint32_t vertex_count(uint32_t primitive_type, uint32_t count);
std::string primitive_name(uint32_t primitive_type);  // "TRIANGLESTRIP", ... or "TYPE_<n>"

}  // namespace d9g
