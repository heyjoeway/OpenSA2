// d9g_gpu_cpu.cpp: a CPU model of d9g_gpu.h, for the unit test only (never linked
// into libsa2_d3d9gpu.a or sa2_player_gpu). Each texture subresource (a cube's face's level,
// layer * levels + level; a 2D or volume texture's level) is a byte array: BGRA8
// as BGRA8, BC as its blocks, a depth-stencil format as one uint32 per pixel (unorm24
// depth in bits 0-23, stencil in bits 24-31); a volume's level is its slices in order.
// Clears, copies and downloads are of 2D textures only, as the device makes them. A clear writes what Vulkan's clear-value
// conversion gives (unorm8 = round(c * 255), unorm24 = round(z * 0xFFFFFF)); the aspects
// not cleared keep their bits, as LOAD does.
#include "d9g.h"

#include <algorithm>
#include <cmath>
#include <cstring>
#include <unordered_map>

namespace d9g {

namespace {

size_t elem_bytes(GFmt f) { return f == GFmt::bc1 ? 8 : (f == GFmt::bc2 || f == GFmt::bc3) ? 16 : 4; }
bool block(GFmt f) { return f == GFmt::bc1 || f == GFmt::bc2 || f == GFmt::bc3; }
bool depth(GFmt f) { return f == GFmt::d24s8 || f == GFmt::d32s8; }

class CpuGpu final : public Gpu {
public:
    draw::Outcome draw(const draw::DrawRequest& request) override {
        draw_requests_.push_back(request.values());
        uint64_t bytes = 0;
        const auto& geometry = request.values().geometry;
        if (const auto* up = std::get_if<draw::OwnedIndexedUp>(&geometry)) bytes = up->bytes.size();
        auto owned = [&](const draw::VertexSource& source) {
            if (const auto* up = std::get_if<draw::OwnedVertices>(&source)) bytes += up->padded_bytes.size();
        };
        if (const auto* dp = std::get_if<draw::NonIndexed>(&geometry)) owned(dp->vertices);
        if (const auto* dip = std::get_if<draw::BufferIndexed>(&geometry)) owned(dip->vertices);
        return draw::Encoded{bytes};
    }
    std::vector<draw::Values> draw_requests_;
    CpuGpu(bool clamp_to_border, bool clip_and_clamp, bool clip_distance, bool bc, bool clip_only, bool vk11) : bc_(bc) {
        if (!bc) info_.bc_unsupported = "BC1_RGBA_UNORM, BC2_RGBA_UNORM, BC3_RGBA_UNORM";
        info_.driver = "cpu";
        info_.device_name = "d9g CPU model";
        // The SDL patch's capabilities: a test parameter (make_cpu_gpu), reported as detected
        info_.sampler_clamp_to_border_detected = clamp_to_border;
        info_.depth_clip_and_clamp_detected = clip_and_clamp;
        apply_caps_off(info_, CapsOff{});
        info_.depth_clamp_clip_only = clip_only;   // a test parameter: a live session's, when true
        info_.depth_format = gfmt_name(GFmt::d24s8);
        // The CPU model has no Vulkan device: a test value, named as one
        info_.vk_query_ok = true;
        info_.vk_query = "cpu model: test values, not a device";
        info_.vk_point_size_max = 64.0f;
        // R1: the translator's device facts, test values as the device name is (a lavapipe-like
        // set: no driver rule of DXVK's that lowers anything beyond the core set applies)
        info_.shader_facts_ok = true;
        info_.shader_facts_query = "cpu model: test values, not a device";
        shader::DeviceFacts& f = info_.shader_facts;
        f.driver_id = 13;   // VK_DRIVER_ID_MESA_LLVMPIPE
        f.driver_version_raw = (25u << 22) | (0u << 12) | 7u;
        f.min_storage_buffer_offset_alignment = 16;
        f.max_uniform_buffer_range = 65536;
        f.max_per_stage_descriptor_uniform_buffers = 1000000;
        f.storage_push_constant16 = f.storage_push_constant8 = true;
        f.r32_storage_read_without_format = true;
        for (int i = 0; i < 3; i++) f.sz_inf_nan_preserve[i] = f.rte[i] = f.denorm_preserve[i] = true;
        f.rounding_mode_independence = f.denorm_behavior_independence = 1;   // ..._ALL
        f.resource_indexing = true;
        // shaderClipDistance: a test parameter (make_cpu_gpu), as on a Mali GPU when false
        f.clip_distance = info_.clip_distance = clip_distance;
        // The Vulkan 1.1 profile: a test parameter (make_cpu_gpu), as a device made at 1.1 has it
        if (vk11) {
            f.vk11 = info_.vk11 = true;
            f.sub_dword_access = info_.sub_dword_access = false;
            info_.vk11_reason = "cpu model: a test value";
        }
    }
    uint32_t create_texture(const TexDesc& d) override {
        if ((d.type == TexType::cube && d.w != d.h) || (d.type == TexType::volume && d.target) ||
            (d.type != TexType::flat && depth(d.fmt)) || !d.depth ||
            (d.type != TexType::volume && d.depth != 1)) {
            err_ = "bad texture type";
            return 0;
        }
        Tex t;
        t.d = d;
        for (uint32_t layer = 0; layer < layers(d); layer++)
            for (uint32_t l = 0; l < d.levels; l++) t.lv.emplace_back(bytes(d, l), 0);
        if (block(d.fmt) && !bc_) { err_ = "texture format/usage is unsupported"; return 0; }
        tex_[next_] = std::move(t);
        return next_++;
    }
    void release_texture(uint32_t id) override { tex_.erase(id); }
    uint32_t create_buffer(uint32_t bytes, bool) override {
        buf_[next_].assign(bytes, 0);
        return next_++;
    }
    void release_buffer(uint32_t id) override { buf_.erase(id); }
    bool upload_subresource(uint32_t id, uint32_t layer, uint32_t level, const uint8_t* data, size_t n) override {
        auto it = tex_.find(id);
        if (it == tex_.end() || level >= it->second.d.levels || layer >= layers(it->second.d) ||
            n != it->second.lv[size_t(layer) * it->second.d.levels + level].size()) {
            err_ = "bad upload";
            return false;
        }
        std::memcpy(it->second.lv[size_t(layer) * it->second.d.levels + level].data(), data, n);
        c_.uploads++;
        c_.upload_bytes += n;
        return true;
    }
    bool upload_buffer(uint32_t id, uint32_t offset, const uint8_t* data, uint32_t n) override {
        auto it = buf_.find(id);
        if (it == buf_.end() || uint64_t(offset) + n > it->second.size()) { err_ = "bad buffer upload"; return false; }
        std::memcpy(it->second.data() + offset, data, n);
        c_.uploads++;
        return true;
    }
    bool clear_layer(uint32_t id, uint32_t layer, uint32_t level, const ClearValue& v) override {
        auto it = tex_.find(id);
        if (it == tex_.end() || block(it->second.d.fmt) || it->second.d.type == TexType::volume || level >= it->second.d.levels ||
            layer >= layers(it->second.d)) {
            err_ = "bad clear";
            return false;
        }
        std::vector<uint8_t>& px = it->second.lv[size_t(layer) * it->second.d.levels + level];
        if (depth(it->second.d.fmt)) {
            const uint32_t d24 = uint32_t(std::lrint(double(std::clamp(v.z, 0.0f, 1.0f)) * 0xFFFFFF));
            for (size_t i = 0; i < px.size(); i += 4) {
                uint32_t w;
                std::memcpy(&w, &px[i], 4);
                if (v.depth) w = (w & 0xFF000000u) | d24;
                if (v.stencil) w = (w & 0x00FFFFFFu) | (uint32_t(uint8_t(v.s)) << 24);
                std::memcpy(&px[i], &w, 4);
            }
        } else if (v.colour) {
            auto u8 = [](float f) { return uint8_t(std::lrint(double(std::clamp(f, 0.0f, 1.0f)) * 255.0)); };
            const uint8_t bgra[4] = {u8(v.rgba[2]), u8(v.rgba[1]), u8(v.rgba[0]), u8(v.rgba[3])};
            for (size_t i = 0; i < px.size(); i += 4) std::memcpy(&px[i], bgra, 4);
        }
        c_.render_passes++;
        return true;
    }
    bool copy_layers(uint32_t src, uint32_t sa, uint32_t sl, uint32_t sx, uint32_t sy, uint32_t dst, uint32_t da, uint32_t dl,
                     uint32_t dx, uint32_t dy, uint32_t w, uint32_t h) override {
        auto a = tex_.find(src), b = tex_.find(dst);
        if (a == tex_.end() || b == tex_.end() || a->second.d.fmt != b->second.d.fmt || block(a->second.d.fmt) ||
            a->second.d.type == TexType::volume || b->second.d.type == TexType::volume || sa >= layers(a->second.d) ||
            da >= layers(b->second.d) || sl >= a->second.d.levels || dl >= b->second.d.levels) {
            err_ = "bad copy";
            return false;
        }
        const uint32_t aw = level_dim(a->second.d.w, sl), bw = level_dim(b->second.d.w, dl);
        const uint32_t ah = level_dim(a->second.d.h, sl), bh = level_dim(b->second.d.h, dl);
        if (sx + w > aw || sy + h > ah || dx + w > bw || dy + h > bh) { err_ = "copy out of bounds"; return false; }
        std::vector<uint8_t>& to = b->second.lv[size_t(da) * b->second.d.levels + dl];
        const std::vector<uint8_t>& from = a->second.lv[size_t(sa) * a->second.d.levels + sl];
        for (uint32_t y = 0; y < h; y++)
            std::memmove(&to[(size_t(dy + y) * bw + dx) * 4], &from[(size_t(sy + y) * aw + sx) * 4], size_t(w) * 4);
        c_.copies++;
        return true;
    }
    bool download(uint32_t id, uint32_t level, uint8_t* out, size_t n) override {
        auto it = tex_.find(id);
        if (it == tex_.end() || it->second.d.fmt != GFmt::bgra8 || it->second.d.type != TexType::flat ||
            n != it->second.lv[level].size()) {
            err_ = "bad download";
            return false;
        }
        std::memcpy(out, it->second.lv[level].data(), n);
        c_.downloads++;
        return true;
    }
    bool present(uint32_t, PresentInfo* out) override {
        *out = PresentInfo{};
        c_.submits++;
        return true;
    }
    bool finish() override { return true; }
    bool attach_window(SDL_Window*, bool, std::string* mode) override { *mode = "none"; return true; }
    const GpuInfo& info() const override { return info_; }
    const GpuCounters& counters() const override { return c_; }
    const std::string& error() const override { return err_; }
    GFmt depth_format() const override { return GFmt::d24s8; }
    bool samples(GFmt f, TexType) const override { return bc_ || !block(f); }

    bool bytes_of(uint32_t id, uint32_t level, uint32_t layer, std::string* out) const {
        auto it = tex_.find(id);
        if (it == tex_.end() || level >= it->second.d.levels || layer >= layers(it->second.d)) return false;
        const std::vector<uint8_t>& b = it->second.lv[size_t(layer) * it->second.d.levels + level];
        out->assign(reinterpret_cast<const char*>(b.data()), b.size());
        return true;
    }
    bool desc_of(uint32_t id, TexDesc* out) const {
        auto it = tex_.find(id);
        if (it == tex_.end()) return false;
        *out = it->second.d;
        return true;
    }

private:
    struct Tex { TexDesc d; std::vector<std::vector<uint8_t>> lv; };   // lv by layer * levels + level
    static uint32_t layers(const TexDesc& d) { return d.type == TexType::cube ? 6u : 1u; }
    static size_t bytes(const TexDesc& d, uint32_t l) {
        const uint32_t w = level_dim(d.w, l), h = level_dim(d.h, l);
        const size_t slice = block(d.fmt) ? size_t((w + 3) / 4) * ((h + 3) / 4) * elem_bytes(d.fmt) : size_t(w) * h * 4;
        return slice * (d.type == TexType::volume ? level_dim(d.depth, l) : 1u);
    }
    std::unordered_map<uint32_t, Tex> tex_;
    std::unordered_map<uint32_t, std::vector<uint8_t>> buf_;
    uint32_t next_ = 1;
    bool bc_ = true;
    GpuInfo info_;
    GpuCounters c_;
    std::string err_;
};

}  // namespace

std::unique_ptr<Gpu> make_cpu_gpu(bool clamp_to_border, bool clip_and_clamp, bool clip_distance, bool bc, bool clip_only,
                                  bool vk11) {
    return std::make_unique<CpuGpu>(clamp_to_border, clip_and_clamp, clip_distance, bc, clip_only, vk11);
}

bool cpu_gpu_bytes(Gpu* g, uint32_t tex, uint32_t level, std::string* out, uint32_t layer) {
    auto* c = dynamic_cast<CpuGpu*>(g);
    return c && c->bytes_of(tex, level, layer, out);
}

bool cpu_gpu_desc(Gpu* g, uint32_t tex, TexDesc* out) {
    auto* c = dynamic_cast<CpuGpu*>(g);
    return c && c->desc_of(tex, out);
}

const std::vector<draw::Values>* cpu_gpu_draws(Gpu* g) {
    const auto* cpu = dynamic_cast<CpuGpu*>(g);
    return cpu ? &cpu->draw_requests_ : nullptr;
}
}  // namespace d9g
