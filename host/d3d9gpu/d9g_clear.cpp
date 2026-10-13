// d9g_clear.cpp: DXVK 3.1.1's Clear rule (see d9g_clear.h), with DXVK's integer
// types kept: the wraps below are DXVK's, not this file's choices.
#include "d9g_clear.h"

#include <algorithm>

namespace d9g {

std::vector<ClearBox> clear_regions(const ClearInput& in) {
    // VkOffset3D offset = { int32_t(vp.X), int32_t(vp.Y), 0 }; extent = { vp.Width, vp.Height }
    int32_t ox = int32_t(in.vp_x), oy = int32_t(in.vp_y);
    uint32_t ew = in.vp_w, eh = in.vp_h;
    if (in.scissor) {
        ox = std::max<int32_t>(ox, in.sc_left);
        oy = std::max<int32_t>(oy, in.sc_top);
        // std::min<uint32_t>(extent.width, sc.right - offset.x): the difference wraps.
        ew = std::min<uint32_t>(ew, uint32_t(in.sc_right) - uint32_t(ox));
        eh = std::min<uint32_t>(eh, uint32_t(in.sc_bottom) - uint32_t(oy));
    }
    uint32_t count = in.count;
    if (count) {
        if (!in.rects) {
            count = 0;
        } else {
            const int32_t* r = in.rects;
            const int32_t right = int32_t(uint32_t(ox) + ew), bottom = int32_t(uint32_t(oy) + eh);
            if (r[0] <= ox && r[1] <= oy && r[2] >= right && r[3] >= bottom) count = 0;
        }
    }
    if (ew == 0 || eh == 0) return {};
    if (!count) return {ClearBox{ox, oy, ew, eh}};
    std::vector<ClearBox> out;
    // offset.x + extent.width is unsigned in DXVK; compared as int32_t, it is that sum's bits.
    const int32_t ex = int32_t(uint32_t(ox) + ew), ey = int32_t(uint32_t(oy) + eh);
    for (uint32_t i = 0; i < count; i++) {
        const int32_t* r = in.rects + 4 * size_t(i);
        const int32_t rx = std::max<int32_t>(r[0], ox), ry = std::max<int32_t>(r[1], oy);
        if (std::min<int32_t>(r[2], ex) <= rx || std::min<int32_t>(r[3], ey) <= ry) continue;
        const uint32_t w = std::min<uint32_t>(uint32_t(r[2]), uint32_t(ox) + ew) - uint32_t(rx);
        const uint32_t h = std::min<uint32_t>(uint32_t(r[3]), uint32_t(oy) + eh) - uint32_t(ry);
        out.push_back(ClearBox{rx, ry, w, h});
    }
    return out;
}

ImageClear clear_image(const ClearBox& r, uint32_t iw, uint32_t ih) {
    ImageClear c;
    const uint32_t w = std::min(iw, r.w), h = std::min(ih, r.h);
    if (uint32_t(r.x) >= iw || uint32_t(r.y) >= ih) return c;
    if (w == iw && h == ih && r.x == 0 && r.y == 0) {
        c.kind = ClearKind::full;
        c.box = ClearBox{0, 0, iw, ih};
        return c;
    }
    c.kind = ClearKind::partial;
    c.box = ClearBox{r.x, r.y, w, h};
    c.past_edge = uint64_t(uint32_t(r.x)) + w > iw || uint64_t(uint32_t(r.y)) + h > ih;
    return c;
}

}  // namespace d9g
