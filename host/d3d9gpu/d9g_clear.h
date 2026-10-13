// d9g_clear.h: DXVK 3.1.1's Clear rule as pure functions (src/d3d9/d3d9_device.cpp
// D3D9DeviceEx::Clear, lines 1894-2075), shared by the device (d9g_device.cpp) and the
// frame census (census.cpp), so the two cannot disagree on what a Clear covers.
//
// The device keeps DXVK's order: (1) Count 0 with pRects non-NULL clears nothing and
// answers D3D_OK (:1902); (2) ZBUFFER or STENCIL with no depth-stencil bound answers
// D3DERR_INVALIDCALL (:1909-1912); then clear_regions() below; then, per image bound
// (the depth-stencil, then render targets 0..3), clear_image().
//
// clear_regions(): the viewport, intersected with the scissor rectangle when
// SCISSORTESTENABLE is set, with DXVK's integer types (an unsigned min against
// sc.right - offset.x, which wraps when negative, :1927-1928); Count with pRects NULL,
// or a first rectangle covering that region, means no rectangles (:1932-1940); an empty
// region clears nothing (:2048-2050); otherwise each rectangle is intersected with the
// region and dropped when empty (:2056-2070).
//
// clear_image(): DXVK's ClearImageView with alignment 1 (no lenientClear): only the
// extent is clamped to the image, never offset plus extent (:1965-1967); an offset at or
// past the image's edge skips the image (:1969-1970); full when the clamped extent is the
// image's and the offset 0 (:1972-1976), else partial. A partial region DXVK leaves past
// the image's edge (offset + extent > image) is marked past_edge: Vulkan does not define
// that clear, so the device counts it and does not implement it.
#pragma once

#include <cstdint>
#include <vector>

namespace d9g {

struct ClearBox {
    int32_t x = 0, y = 0;
    uint32_t w = 0, h = 0;
};

struct ClearInput {
    uint32_t vp_x = 0, vp_y = 0, vp_w = 0, vp_h = 0;      // D3DVIEWPORT9 X, Y, Width, Height
    int32_t sc_left = 0, sc_top = 0, sc_right = 0, sc_bottom = 0;
    bool scissor = false;                                // D3DRS_SCISSORTESTENABLE
    uint32_t count = 0;
    const int32_t* rects = nullptr;                      // Count D3DRECTs (x1, y1, x2, y2), or NULL
};

// The regions DXVK clears in every bound image, before each image's clamp. Empty when
// nothing is cleared. Call it after rules (1) and (2) above.
std::vector<ClearBox> clear_regions(const ClearInput& in);

enum class ClearKind { skip, full, partial };
struct ImageClear {
    ClearKind kind = ClearKind::skip;
    ClearBox box;                 // for partial: the region DXVK clears
    bool past_edge = false;       // partial, and box runs past the image's edge
};
ImageClear clear_image(const ClearBox& region, uint32_t image_w, uint32_t image_h);

}  // namespace d9g
