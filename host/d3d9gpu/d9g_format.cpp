// d9g_format.cpp: see d9g_format.h.
#include "d9g_format.h"

#include <SDL3/SDL_gpu.h>

namespace d9g {

namespace {
constexpr uint32_t fourcc(char a, char b, char c, char d) {
    return uint32_t(uint8_t(a)) | uint32_t(uint8_t(b)) << 8 | uint32_t(uint8_t(c)) << 16 | uint32_t(uint8_t(d)) << 24;
}
// DXVK's mappings (src/d3d9/d3d9_format.cpp ConvertFormatUnfixed): A8R8G8B8 and
// X8R8G8B8 to VK_FORMAT_B8G8R8A8_UNORM (X8 with an A=1 swizzle), DXT1/3/5 to
// BC1_RGBA/BC2/BC3, D24S8 to D24_UNORM_S8_UINT.
// dxvk_view_identity per DXVK's src/d3d9/d3d9_format.cpp: A8R8G8B8 :14-17, X8R8G8B8 :19-24 (A=ONE),
// DXT1 :211-214, DXT3 :221-224, DXT5 :231-234, D24S8 :245-248
const FormatInfo kFormats[] = {
    {21, GFmt::bgra8, 0, 4, false, false, false, true, "A8R8G8B8"},
    {22, GFmt::bgra8, 0, 4, false, false, true, false, "X8R8G8B8"},
    {fourcc('D', 'X', 'T', '1'), GFmt::bc1, 8, 0, false, false, false, true, "DXT1"},
    {fourcc('D', 'X', 'T', '3'), GFmt::bc2, 16, 0, false, false, false, true, "DXT3"},
    {fourcc('D', 'X', 'T', '5'), GFmt::bc3, 16, 0, false, false, false, true, "DXT5"},
    {75, GFmt::d24s8, 0, 4, true, true, false, true, "D24S8"},
};
}  // namespace

const FormatInfo* all_formats(size_t* count) {
    *count = sizeof kFormats / sizeof kFormats[0];
    return kFormats;
}

const FormatInfo* format_info(uint32_t d3dfmt) {
    for (const FormatInfo& f : kFormats)
        if (f.d3d == d3dfmt) return &f;
    return nullptr;
}

const char* gfmt_name(GFmt g) {
    switch (g) {
    case GFmt::bgra8: return "B8G8R8A8_UNORM";
    case GFmt::bc1: return "BC1_RGBA_UNORM";
    case GFmt::bc2: return "BC2_RGBA_UNORM";
    case GFmt::bc3: return "BC3_RGBA_UNORM";
    case GFmt::d24s8: return "D24_UNORM_S8_UINT";
    case GFmt::d32s8: return "D32_FLOAT_S8_UINT";
    default: return "none";
    }
}

uint32_t level_pitch(uint32_t w, const FormatInfo& f) {
    return f.block_bytes ? ((w + 3) / 4) * f.block_bytes : w * f.pixel_bytes;
}

uint32_t level_rows(uint32_t h, const FormatInfo& f) { return f.block_bytes ? (h + 3) / 4 : h; }

uint32_t full_chain(uint32_t w, uint32_t h) {
    uint32_t n = 1;
    for (uint32_t m = w > h ? w : h; m > 1; m >>= 1) n++;
    return n;
}

void x8_alpha_fill(uint8_t* p, size_t bytes) {
    for (size_t i = 3; i < bytes; i += 4) p[i] = 0xFF;
}

void decode_d3dcolor(uint32_t c, float rgba[4]) {
    rgba[3] = float((c & 0xff000000u) >> 24) / 255.0f;
    rgba[0] = float((c & 0x00ff0000u) >> 16) / 255.0f;
    rgba[1] = float((c & 0x0000ff00u) >> 8) / 255.0f;
    rgba[2] = float(c & 0x000000ffu) / 255.0f;
}

// DXVK's DecodeDecltype (d3d9_util.cpp:255-279) has a Vulkan format for each type;
// SDL_gpu has the same one for these, and none for UBYTE4 (USCALED), SHORT2/4
// (SSCALED), UDEC3 and DEC3N. D3DCOLOR (B8G8R8A8_UNORM) is UBYTE4_NORM plus a swizzle.
int decltype_sdl_format(uint8_t t) {
    switch (t) {
    case D3DDECLTYPE_FLOAT1: return SDL_GPU_VERTEXELEMENTFORMAT_FLOAT;
    case D3DDECLTYPE_FLOAT2: return SDL_GPU_VERTEXELEMENTFORMAT_FLOAT2;
    case D3DDECLTYPE_FLOAT3: return SDL_GPU_VERTEXELEMENTFORMAT_FLOAT3;
    case D3DDECLTYPE_FLOAT4: return SDL_GPU_VERTEXELEMENTFORMAT_FLOAT4;
    case D3DDECLTYPE_D3DCOLOR: return SDL_GPU_VERTEXELEMENTFORMAT_UBYTE4_NORM;
    case D3DDECLTYPE_UBYTE4N: return SDL_GPU_VERTEXELEMENTFORMAT_UBYTE4_NORM;
    case D3DDECLTYPE_SHORT2N: return SDL_GPU_VERTEXELEMENTFORMAT_SHORT2_NORM;
    case D3DDECLTYPE_SHORT4N: return SDL_GPU_VERTEXELEMENTFORMAT_SHORT4_NORM;
    case D3DDECLTYPE_USHORT2N: return SDL_GPU_VERTEXELEMENTFORMAT_USHORT2_NORM;
    case D3DDECLTYPE_USHORT4N: return SDL_GPU_VERTEXELEMENTFORMAT_USHORT4_NORM;
    case D3DDECLTYPE_FLOAT16_2: return SDL_GPU_VERTEXELEMENTFORMAT_HALF2;
    case D3DDECLTYPE_FLOAT16_4: return SDL_GPU_VERTEXELEMENTFORMAT_HALF4;
    default: return SDL_GPU_VERTEXELEMENTFORMAT_INVALID;
    }
}

DeclInfo decl_info(const D3DVERTEXELEMENT9* e, size_t n) {
    DeclInfo d;
    for (size_t i = 0; i < n; i++) {
        VertexElement v{e[i].Stream, e[i].Offset, e[i].Type, e[i].Method, e[i].Usage, e[i].UsageIndex,
                        decltype_sdl_format(e[i].Type)};
        if (v.sdl_format == SDL_GPU_VERTEXELEMENTFORMAT_INVALID) d.without_sdl_format++;
        if (v.type == D3DDECLTYPE_D3DCOLOR && i < 32) d.bgra_mask |= 1u << i;
        d.elements.push_back(v);
    }
    return d;
}

std::vector<uint32_t> fan_indices(uint32_t vertices) {
    std::vector<uint32_t> out;
    for (uint32_t i = 0; i + 2 < vertices; i++) {
        out.push_back(i + 1);
        out.push_back(i + 2);
        out.push_back(0);
    }
    return out;
}

uint32_t vertex_count(uint32_t type, uint32_t count) {
    switch (type) {
    default:
    case D3DPT_TRIANGLELIST: return count * 3;
    case D3DPT_POINTLIST: return count;
    case D3DPT_LINELIST: return count * 2;
    case D3DPT_LINESTRIP: return count + 1;
    case D3DPT_TRIANGLESTRIP: return count + 2;
    case D3DPT_TRIANGLEFAN: return count + 2;
    }
}

std::string primitive_name(uint32_t type) {
    switch (type) {
    case D3DPT_POINTLIST: return "POINTLIST";
    case D3DPT_LINELIST: return "LINELIST";
    case D3DPT_LINESTRIP: return "LINESTRIP";
    case D3DPT_TRIANGLELIST: return "TRIANGLELIST";
    case D3DPT_TRIANGLESTRIP: return "TRIANGLESTRIP";
    case D3DPT_TRIANGLEFAN: return "TRIANGLEFAN";
    default: return "TYPE_" + std::to_string(type);
    }
}

}  // namespace d9g
