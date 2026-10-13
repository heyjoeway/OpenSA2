// d9g_draw.h: the draw-path helpers that need no shader (R2b-prep,
// private/generated/s5/r2b/PLAN.md section 6): DXVK 3.1.1's user-pointer (UP) buffer layout
// and the arguments an SDL_gpu draw takes, mirroring DXVK's draw emission.
//
//   * The UP arena: DrawPrimitiveUP (d3d9_device.cpp:3135-3182) and DrawIndexedPrimitiveUP
//     (:3185-3250) copy a draw's vertices into a buffer of GetUPBufferSize bytes
//     ((n - 1) * stride + max(declaration size, stride), d3d9_device.h:1308-1314), zero past the
//     data (FillUPVertexBuffer :1316-1330), indices right after the vertices; each allocation's
//     offset is aligned to CACHE_LINE_SIZE, 64 (AllocUPBuffer :4798, util_math.h): DXVK's
//     allocator's alignment rule (the critic's N3), not a rendering rule. The offsets are not
//     DXVK's: DXVK suballocates a 1 MiB ring that runs across frames, and this arena starts at 0
//     each frame. Each block's size, contents and alignment are DXVK's (the draw tests).
//   * draw_args: DrawPrimitive's non-indexed draw (:3023-3069), DrawIndexedPrimitive's indexed
//     one (:3072-3132) and the UP forms, as SDL's draw calls take them; a triangle fan is drawn
//     as a list indexed into the fan cache (d9g_format.h fan_indices, Vulkan's fan order), with
//     its first vertex as the vertex offset. An indexed fan needs its indices rewritten on the
//     CPU, which no stream needs: not mapped (the critic's S11).
//
// SDL binds a vertex buffer with no size, where DXVK binds the UP slice with its size: a
// difference no non-indexed draw can observe (every vertex fetched is inside the block), and
// an indexed UP draw reads only the indices' vertices, which GetUPBufferSize covers; recorded,
// not a rule (the critic's S10).
//
// Rules mirrored from DXVK 3.1.1 (zlib licence; Copyright Philip Rebohle, Joshua Ashton et al.;
// private/s5-tools/src/dxvk-3.1.1/LICENSE), cited per rule.
#pragma once

#include "d9g_state.h"

#include <cstdint>
#include <string>
#include <vector>

namespace d9g {

// D3D9VertexDecl::Classify's stream-0 size (d3d9_vertex_declaration.cpp:357-358): the largest
// Offset + GetDecltypeSize(Type) (d3d9_util.cpp:311-332) over stream 0's elements that are not
// UNUSED; GetUPBufferSize reads it.
uint32_t decl_stream0_size(const VertexElem* elems, size_t n);

struct UpBlock {
    uint64_t vb_offset = 0;      // the vertex block's offset in the arena (64-aligned)
    uint32_t data_bytes = 0;     // GetUPDataSize: vertices * stride (what the caller gave)
    uint32_t buffer_bytes = 0;   // GetUPBufferSize: what the binding covers (zero-padded)
    uint64_t ib_offset = 0;      // the indices, right after the vertex block
    uint32_t index_bytes = 0;
};

// One frame's UP blocks, each laid out as DXVK lays out its slice (not at DXVK's offsets: above).
class UpArena {
public:
    // One draw's block. `decl_size0` is the declaration's stream-0 size (D3D9VertexDecl::GetSize(0)).
    UpBlock add(const void* vertices, uint32_t vertex_count, uint32_t stride, uint32_t decl_size0,
                const void* indices = nullptr, uint32_t index_bytes = 0);
    const std::vector<uint8_t>& bytes() const { return bytes_; }
    uint64_t pad_bytes() const { return pad_; }
    void reset() { bytes_.clear(); pad_ = 0; }

private:
    std::vector<uint8_t> bytes_;
    uint64_t pad_ = 0;           // alignment and zero padding: the arena's bytes beyond the data
};

// GetUPBufferSize (d3d9_device.h:1312-1314), in DXVK's uint32_t arithmetic.
uint32_t up_buffer_size(uint32_t vertex_count, uint32_t stride, uint32_t decl_size0);

enum DrawMethodArg : uint32_t { DM_DP = 0, DM_DIP = 1, DM_DPUP = 2, DM_DIPUP = 3 };

struct DrawArgs {
    bool indexed = false;
    bool fan_cache = false;      // indexed into the fan cache
    uint32_t count = 0;          // vertices (non-indexed) or indices
    uint32_t first_index = 0, first_vertex = 0;
    int32_t vertex_offset = 0;
    uint32_t index_size = 0;     // 2 or 4 for the draw's own indices; 4 for the fan cache
    uint32_t instances = 1;
    std::string unmapped;        // "" when SDL can draw it
};

// `start` is StartVertex (DP), StartIndex (DIP) or 0 (UP); `base` is DIP's BaseVertexIndex.
DrawArgs draw_args(uint32_t method, uint32_t prim, uint32_t start, uint32_t prims, int32_t base, uint32_t index_fmt);

// The fan cache's indices for `vertices` (d9g_format.h fan_indices), or under the map-fan-order
// fault D3D9's own order (0, i + 1, i + 2), for the state check's control.
std::vector<uint32_t> fan_cache_indices(uint32_t vertices, uint32_t map_faults);

}  // namespace d9g
