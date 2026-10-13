// d9g_draw.cpp: see d9g_draw.h.
#include "d9g_draw.h"

#include "d9g_format.h"
#include "d9g_pipeline.h"

#include <algorithm>
#include <cstring>

namespace d9g {

uint32_t decl_stream0_size(const VertexElem* elems, size_t n) {
    uint32_t size = 0;
    for (size_t i = 0; i < n; i++) {
        const VertexElem& e = elems[i];
        if (e.stream != 0 || e.type == D3DDECLTYPE_UNUSED) continue;
        uint32_t t = 0;   // GetDecltypeSize
        switch (e.type) {
        case D3DDECLTYPE_FLOAT1: t = 4; break;
        case D3DDECLTYPE_FLOAT2: t = 8; break;
        case D3DDECLTYPE_FLOAT3: t = 12; break;
        case D3DDECLTYPE_FLOAT4: t = 16; break;
        case D3DDECLTYPE_D3DCOLOR: case D3DDECLTYPE_UBYTE4: case D3DDECLTYPE_SHORT2: case D3DDECLTYPE_UBYTE4N:
        case D3DDECLTYPE_SHORT2N: case D3DDECLTYPE_USHORT2N: case D3DDECLTYPE_UDEC3: case D3DDECLTYPE_DEC3N:
        case D3DDECLTYPE_FLOAT16_2: t = 4; break;
        case D3DDECLTYPE_SHORT4: case D3DDECLTYPE_SHORT4N: case D3DDECLTYPE_USHORT4N: case D3DDECLTYPE_FLOAT16_4: t = 8; break;
        default: t = 0;
        }
        size = std::max<uint32_t>(size, uint32_t(e.offset) + t);
    }
    return size;
}

uint32_t up_buffer_size(uint32_t vertex_count, uint32_t stride, uint32_t decl_size0) {
    return (vertex_count - 1) * stride + std::max(decl_size0, stride);   // d3d9_device.h:1313
}

UpBlock UpArena::add(const void* vertices, uint32_t vertex_count, uint32_t stride, uint32_t decl_size0,
                     const void* indices, uint32_t index_bytes) {
    UpBlock b;
    b.data_bytes = vertex_count * stride;                                 // GetUPDataSize :1308-1310
    b.buffer_bytes = up_buffer_size(vertex_count, stride, decl_size0);
    b.index_bytes = index_bytes;
    // AllocUPBuffer (:4765-4815): each allocation 64-aligned (align(size, CACHE_LINE_SIZE))
    const uint64_t at = (bytes_.size() + 63u) & ~uint64_t(63u);
    pad_ += at - bytes_.size();
    b.vb_offset = at;
    b.ib_offset = at + b.buffer_bytes;                                    // DIPUP :3220-3222
    bytes_.resize(at + uint64_t(b.buffer_bytes) + index_bytes, 0);
    // FillUPVertexBuffer (:1319-1330): min(data, buffer) bytes copied, the rest zero
    const uint32_t copy = std::min(b.data_bytes, b.buffer_bytes);
    if (copy) std::memcpy(bytes_.data() + at, vertices, copy);
    if (b.buffer_bytes > copy) pad_ += b.buffer_bytes - copy;
    if (index_bytes) std::memcpy(bytes_.data() + b.ib_offset, indices, index_bytes);
    return b;
}

DrawArgs draw_args(uint32_t method, uint32_t prim, uint32_t start, uint32_t prims, int32_t base, uint32_t index_fmt) {
    DrawArgs a;
    const uint32_t n = vertex_count(prim, prims);                         // GetVertexCount
    const bool fan = prim == D3DPT_TRIANGLEFAN;
    switch (method) {
    case DM_DP:
    case DM_DPUP:
        // DrawPrimitive: VkDrawIndirectCommand{vertexCount, 1, firstVertex = StartVertex} (:3045-3048);
        // DrawPrimitiveUP: firstVertex 0 in the UP block (:3168-3172)
        if (fan) {
            a.indexed = a.fan_cache = true;
            a.count = n >= 2 ? 3u * (n - 2u) : 0u;
            a.first_index = 0;
            a.vertex_offset = int32_t(method == DM_DP ? start : 0u);
            a.index_size = 4;
        } else {
            a.count = n;
            a.first_vertex = method == DM_DP ? start : 0u;
        }
        break;
    case DM_DIP:
    case DM_DIPUP:
        // DrawIndexedPrimitive: indexCount, firstIndex = StartIndex, vertexOffset = BaseVertexIndex
        // (:3109-3113); the UP form: the indices from the block's start, no offset (:3236-3240)
        a.indexed = true;
        a.count = n;
        a.first_index = method == DM_DIP ? start : 0u;
        a.vertex_offset = method == DM_DIP ? base : 0;
        a.index_size = index_fmt == D3DFMT_INDEX16 ? 2u : 4u;
        if (fan) a.unmapped = "an indexed triangle fan (needs a CPU index rewrite, not the fan cache)";
        break;
    default:
        a.unmapped = "draw method " + std::to_string(method);
    }
    return a;
}

std::vector<uint32_t> fan_cache_indices(uint32_t vertices, uint32_t map_faults) {
    if (!(map_faults & MF_FAN_ORDER)) return fan_indices(vertices);
    std::vector<uint32_t> out;
    for (uint32_t i = 0; i + 2 < vertices; i++) {
        out.push_back(0);
        out.push_back(i + 1);
        out.push_back(i + 2);
    }
    return out;
}

}  // namespace d9g
