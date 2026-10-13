// census.cpp: sa2_frame_census, the per-frame census of a recorded Direct3D 9 stream
// that tools/s5/compare_hosts.py holds both hosts' runs to.
//
//   sa2_frame_census STREAM --stage N --out CENSUS.json [--tsv FRAMES.tsv]
//
// It reads the stream with host/player's own reader (stream.cpp: framing, zstd, the
// file's sha256) and follows host/player/player.cpp's decisions for the calls that
// matter here, at the given --stage: which records reach the device at all (a call
// before CreateDevice, a blob missing or short, a stage that skips the call), and with
// what. Then it applies the device's rules, which DXVK 3.1.1 and d3d9gpu share
// (host/d3d9gpu/d9g_clear.h for Clear, d9g_format.h for vertex counts):
//
//   draws    each host draw call classified as DXVK answers it: refused (stride 0 or no
//            vertex declaration), empty (no primitives, or for the indexed forms no
//            vertices) or a real draw (counted "skipped" for want of a shader route on
//            the second host); by method and primitive type, per frame, and the UP
//            arena's bytes (vertices x stride, and the indices of the indexed form)
//   frames   frame k is the k-th Present, as the player counts frame_; the draws after
//            the last Present are counted apart ("after_last_present")
//   lineage  whether any draw call reached the back buffer (render target 0..3 bound to
//            it) since its last full colour clear (DXVK's rule, d9g_clear.h), or since
//            the device was made (new images are zero): a frame with none is made by
//            clears alone, and a draw-free frame that is not is named, not compared; and
//            whether the back buffer was ever cleared whole in colour up to the frame (a
//            frame never cleared holds the new image's zeros)
//
// It knows only the targets the player can bind: the back buffer, the automatic
// depth-stencil, levels of textures made at stage 3 (CreateTexture, D3DXCreateTexture)
// and CreateRenderTarget/CreateDepthStencilSurface surfaces. A target it does not know
// is never taken for the back buffer.
#include "d9g_clear.h"
#include "d9g_format.h"

#include "stream.h"

#include <array>
#include <cinttypes>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <string>
#include <unordered_map>
#include <vector>

namespace {

constexpr uint16_t T_D3D9 = 0, T_DEVICE = 1, T_TEXTURE = 2, T_SWAPCHAIN = 13;
constexpr uint32_t kUsageRT = 1, kUsageDS = 2;

uint32_t rd32(const uint8_t* p) { uint32_t v; std::memcpy(&v, p, 4); return v; }

// host/player/player.cpp prim_vertices: 0 for a type it does not know.
uint32_t prim_vertices(uint32_t type, uint32_t n) {
    switch (type) {
    case 1: return n;
    case 2: return 2 * n;
    case 3: return n + 1;
    case 4: return 3 * n;
    case 5: case 6: return n + 2;
    default: return 0;
    }
}

struct Target { uint32_t w = 0, h = 0, usage = 0; bool made = false, back_buffer = false; };
struct Tex { uint32_t w = 0, h = 0, levels = 1, usage = 0; bool made = false; };

struct FrameRow {
    uint32_t frame = 0, present_seq = 0;
    uint64_t draws = 0, draws_bb = 0, clears = 0, full_bb_colour = 0;
    bool dirty = false, ever_cleared = false;
};

std::string jstr(const std::string& s) {
    std::string o = "\"";
    for (unsigned char c : s) {
        if (c == '"' || c == '\\') { o += '\\'; o += char(c); }
        else if (c < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
        else o += char(c);
    }
    return o + "\"";
}

using Kinds = std::map<std::string, std::map<std::string, uint64_t>>;
std::string kinds_json(const Kinds& m) {
    std::string s = "{";
    bool first = true;
    for (const auto& [method, by] : m) {
        s += (first ? "" : ", ") + jstr(method) + ": {";
        bool f2 = true;
        for (const auto& [prim, n] : by) {
            s += (f2 ? "" : ", ") + jstr(prim) + ": " + std::to_string(n);
            f2 = false;
        }
        s += "}";
        first = false;
    }
    return s + "}";
}

}  // namespace

int main(int argc, char** argv) {
    std::string stream, out, tsv;
    int stage = -1;
    for (int i = 1; i < argc; i++) {
        const std::string a = argv[i];
        auto val = [&]() -> std::string {
            if (i + 1 >= argc) { std::fprintf(stderr, "%s needs a value\n", a.c_str()); std::exit(2); }
            return argv[++i];
        };
        if (a == "--stage") stage = std::atoi(val().c_str());
        else if (a == "--out") out = val();
        else if (a == "--tsv") tsv = val();
        else if (!a.empty() && a[0] == '-') { std::fprintf(stderr, "unknown option %s\n", a.c_str()); return 2; }
        else stream = a;
    }
    if (stream.empty() || out.empty() || stage < 0 || stage > 3) {
        std::fprintf(stderr, "usage: sa2_frame_census STREAM --stage 0..3 --out CENSUS.json [--tsv FRAMES.tsv]\n");
        return 2;
    }
    Sa2StreamReader rd;
    std::string err;
    if (rd.open(stream, &err) != Sa2StreamReader::OPEN_OK) { std::fprintf(stderr, "census: %s\n", err.c_str()); return 3; }

    bool device = false, auto_ds = false;
    uint32_t bb_w = 0, bb_h = 0, bb_fmt = 0, rt_addr = 0, ds_addr = 0;
    std::unordered_map<uint32_t, Target> targets;
    std::unordered_map<uint32_t, Tex> textures;
    uint32_t rt[4] = {}, ds = 0;
    uint32_t vp[4] = {}, rs_scissor = 0, rs_srgb = 0;
    int32_t sc[4] = {};
    std::set<uint32_t> decls;
    bool decl_bound = false;
    Kinds skipped, refused, empty;
    uint64_t total = 0, up_vertex = 0, up_index = 0, records = 0, clear_calls = 0, clear_host = 0;
    uint64_t bb_full_colour = 0, bb_partial_colour = 0, srgb_clears = 0, past_edge = 0;
    bool dirty = false, ever_cleared = false;
    std::vector<FrameRow> frames;
    FrameRow cur;
    uint64_t end_records = 0;

    auto target_size = [&](uint32_t addr, uint32_t* w, uint32_t* h) {
        auto it = targets.find(addr);
        if (it == targets.end()) return false;
        *w = it->second.w;
        *h = it->second.h;
        return true;
    };
    auto bb_bound = [&]() {
        for (uint32_t a : rt)
            if (a && a == rt_addr) return true;
        return false;
    };
    auto draw = [&](const char* method, uint32_t type, int cls) {
        Kinds& k = cls == 0 ? skipped : cls == 1 ? refused : empty;
        k[method][d9g::primitive_name(type)]++;
        total++;
        cur.draws++;
        if (bb_bound()) { cur.draws_bb++; dirty = true; }
    };
    auto close = [&](uint32_t seq) {
        cur.frame = uint32_t(frames.size() + 1);
        cur.present_seq = seq;
        cur.dirty = dirty;
        cur.ever_cleared = ever_cleared;
        frames.push_back(cur);
        cur = FrameRow{};
    };

    Sa2Record r;
    for (;;) {
        const int got = rd.next(&r, &err);
        if (got < 0) { std::fprintf(stderr, "census: malformed record: %s\n", err.c_str()); return 3; }
        if (got == 0) break;
        records++;
        if (r.kind == SA2S_END) end_records++;
        if (r.kind == SA2S_D3DX) {
            if (!device) continue;
            // D3DXCreateTexture (pDevice, Width, Height, MipLevels, Usage, Format, Pool, ppTexture)
            if (r.entry == SA2S_D3DX_CREATE_TEXTURE) {
                const Sa2Blob* o = r.blob(SA2S_BLOB_OUT, 7);
                if (o && o->len >= 4) {
                    Tex t;
                    t.w = r.arg(1); t.h = r.arg(2);
                    const uint32_t lv = r.arg(3);
                    t.levels = (lv == 0xFFFFFFFFu || !lv) ? d9g::full_chain(t.w, t.h) : lv;
                    t.usage = r.arg(4);
                    t.made = stage >= 3;
                    textures[rd32(o->data)] = t;
                }
            }
            continue;
        }
        if (r.kind != SA2S_CALL) continue;
        if (r.slot <= 2) continue;                          // QueryInterface, AddRef, Release
        if (r.type == T_D3D9) {
            if (r.slot != 16 || device) continue;
            // CreateDevice: the presentation parameters and the implicit pair.
            device = true;
            uint32_t pp[14] = {};
            if (const Sa2Blob* b = r.blob(SA2S_BLOB_IN, 4); b && b->len >= 56) std::memcpy(pp, b->data, 56);
            bb_w = pp[0] ? pp[0] : 1280;
            bb_h = pp[1] ? pp[1] : 720;
            bb_fmt = pp[2] ? pp[2] : 22;
            auto_ds = pp[9] != 0;
            const Sa2Blob* o = r.blob(SA2S_BLOB_OUT, 5);
            const uint32_t dev = o && o->len >= 4 ? rd32(o->data) : 0;
            if (const Sa2Blob* d = r.blob(SA2S_BLOB_DESC, -1); d && d->len >= 12) {
                rt_addr = rd32(d->data + 4);
                ds_addr = rd32(d->data + 8);
            } else {
                rt_addr = dev + 16;
                ds_addr = auto_ds ? dev + 32 : 0;
            }
            targets[rt_addr] = Target{bb_w, bb_h, kUsageRT, true, true};
            if (ds_addr) targets[ds_addr] = Target{bb_w, bb_h, kUsageDS, auto_ds, false};
            rt[0] = rt_addr;
            ds = auto_ds ? ds_addr : 0;
            vp[0] = 0; vp[1] = 0; vp[2] = bb_w; vp[3] = bb_h;
            sc[0] = 0; sc[1] = 0; sc[2] = int32_t(bb_w); sc[3] = int32_t(bb_h);
            continue;
        }
        if (!device) continue;
        if (r.type == T_SWAPCHAIN && r.slot == 3) { close(r.seq); continue; }
        if (r.type == T_TEXTURE && r.slot == 18) {          // GetSurfaceLevel(Level, ppSurfaceLevel)
            const Sa2Blob* o = r.blob(SA2S_BLOB_OUT, 1);
            if (!o || o->len < 4) continue;
            const uint32_t addr = rd32(o->data);
            if (targets.count(addr)) continue;
            auto it = textures.find(r.self);
            if (it == textures.end()) continue;
            const uint32_t level = r.arg(0);
            Target t;
            t.w = d9g::level_dim(it->second.w, level);
            t.h = d9g::level_dim(it->second.h, level);
            t.usage = it->second.usage;
            t.made = it->second.made && level < it->second.levels;
            targets[addr] = t;
            continue;
        }
        if (r.type != T_DEVICE) continue;
        switch (r.slot) {
        case 17:                                            // Present
            close(r.seq);
            break;
        case 23: {                                          // CreateTexture
            const Sa2Blob* o = r.blob(SA2S_BLOB_OUT, 6);
            if (!o || o->len < 4) break;
            Tex t;
            t.w = r.arg(0); t.h = r.arg(1); t.levels = r.arg(2) ? r.arg(2) : 1u; t.usage = r.arg(3);
            t.made = stage >= 3;
            textures[rd32(o->data)] = t;
            break;
        }
        case 28: case 29: case 36: {                        // CreateRenderTarget, CreateDepthStencilSurface, CreateOffscreenPlainSurface
            const Sa2Blob* o = r.blob(SA2S_BLOB_OUT, r.slot == 36 ? 4 : 6);
            if (!o || o->len < 4) break;
            targets[rd32(o->data)] = Target{r.arg(0), r.arg(1), r.slot == 28 ? kUsageRT : r.slot == 29 ? kUsageDS : 0u,
                                            stage >= 3, false};
            break;
        }
        case 37: {                                          // SetRenderTarget, stage 3
            if (stage < 3) break;
            const uint32_t index = r.arg(0), addr = r.arg(1);
            auto it = targets.find(addr);
            const bool made = addr && it != targets.end() && it->second.made;
            if (addr && !made) break;                       // the player's error: no host call
            // SetRenderTarget (DXVK :1638-1700, d9g_device.cpp): NULL at 0, an index past 3
            // or a surface without RENDERTARGET usage refused; target 0 resets the viewport
            // and the scissor to its size.
            if ((!addr && index == 0) || index >= 4 || (addr && !(it->second.usage & kUsageRT))) break;
            if (index == 0) {
                vp[0] = 0; vp[1] = 0; vp[2] = it->second.w; vp[3] = it->second.h;
                sc[0] = 0; sc[1] = 0; sc[2] = int32_t(it->second.w); sc[3] = int32_t(it->second.h);
            }
            rt[index] = addr;
            break;
        }
        case 39: {                                          // SetDepthStencilSurface, stage 3
            if (stage < 3) break;
            const uint32_t addr = r.arg(0);
            auto it = targets.find(addr);
            const bool made = addr && it != targets.end() && it->second.made;
            if (addr && !made) break;
            if (addr && !(it->second.usage & kUsageDS)) break;
            ds = addr;
            break;
        }
        case 47: {                                          // SetViewport, stage 1
            if (stage < 1) break;
            const Sa2Blob* b = r.blob(SA2S_BLOB_IN, 0);
            if (!b || b->len < 24) break;
            for (int i = 0; i < 4; i++) vp[i] = rd32(b->data + 4 * i);
            break;
        }
        case 75: {                                          // SetScissorRect, stage 1
            if (stage < 1) break;
            const Sa2Blob* b = r.blob(SA2S_BLOB_IN, 0);
            if (!b || b->len < 16) break;
            for (int i = 0; i < 4; i++) sc[i] = int32_t(rd32(b->data + 4 * i));
            break;
        }
        case 57:                                            // SetRenderState, stage 1
            if (stage < 1) break;
            if (r.arg(0) == 174) rs_scissor = r.arg(1);
            if (r.arg(0) == 194) rs_srgb = r.arg(1);
            break;
        case 86: {                                          // CreateVertexDeclaration, stage 1
            if (stage < 1) break;
            const Sa2Blob* o = r.blob(SA2S_BLOB_OUT, 1);
            const Sa2Blob* b = r.blob(SA2S_BLOB_IN, 0);
            if (!o || o->len < 4 || !b || b->len < 8) break;
            bool ended = false;
            for (uint32_t off = 0; off + 8 <= b->len; off += 8)
                if (b->data[off] == 0xFF && b->data[off + 1] == 0) { ended = true; break; }
            if (ended) decls.insert(rd32(o->data));
            break;
        }
        case 87:                                            // SetVertexDeclaration, stage 1
            if (stage < 1) break;
            decl_bound = r.arg(0) && decls.count(r.arg(0));
            break;
        case 89:                                            // SetFVF, stage 1: 0 changes nothing
            if (stage < 1) break;
            if (r.arg(0)) decl_bound = true;
            break;
        case 43: {                                          // Clear, every stage
            clear_calls++;
            cur.clears++;
            const uint32_t n = r.arg(0), flags = r.arg(2);
            if (n && !r.arg(1)) break;                      // not handled by the player: no host call
            std::vector<int32_t> rects;
            if (n) {
                const Sa2Blob* b = r.blob(SA2S_BLOB_IN, 1);
                if (!b || b->len < size_t(n) * 16) break;   // the player's error: no host call
                rects.resize(size_t(n) * 4);
                std::memcpy(rects.data(), b->data, size_t(n) * 16);
            }
            clear_host++;
            if (!n && r.arg(1)) break;                      // Count 0 with rectangles: nothing (DXVK :1902)
            if (!ds && (flags & 6)) break;                  // ZBUFFER or STENCIL with no depth-stencil: refused
            if ((flags & 1) && rs_srgb) { srgb_clears++; break; }
            d9g::ClearInput in;
            in.vp_x = vp[0]; in.vp_y = vp[1]; in.vp_w = vp[2]; in.vp_h = vp[3];
            in.sc_left = sc[0]; in.sc_top = sc[1]; in.sc_right = sc[2]; in.sc_bottom = sc[3];
            in.scissor = rs_scissor != 0;
            in.count = n;
            in.rects = n ? rects.data() : nullptr;
            if (!(flags & 1)) break;                        // no colour: the back buffer's pixels stay
            for (const d9g::ClearBox& box : d9g::clear_regions(in)) {
                for (uint32_t a : rt) {
                    uint32_t w = 0, h = 0;
                    if (!a || !target_size(a, &w, &h)) continue;
                    const d9g::ImageClear ic = d9g::clear_image(box, w, h);
                    if (ic.past_edge) past_edge++;
                    if (a != rt_addr) continue;
                    if (ic.kind == d9g::ClearKind::full) {
                        bb_full_colour++;
                        cur.full_bb_colour++;
                        dirty = false;
                        ever_cleared = true;
                    }
                    else if (ic.kind == d9g::ClearKind::partial) bb_partial_colour++;
                }
            }
            break;
        }
        case 81:                                            // DrawPrimitive (Type, StartVertex, PrimitiveCount)
            if (stage < 1) break;
            draw("DrawPrimitive", r.arg(0), !decl_bound ? 1 : !r.arg(2) ? 2 : 0);
            break;
        case 82:                                            // DrawIndexedPrimitive (.., NumVertices 3, .., primCount 5)
            if (stage < 1) break;
            draw("DrawIndexedPrimitive", r.arg(0), !decl_bound ? 1 : (!r.arg(5) || !r.arg(3)) ? 2 : 0);
            break;
        case 83: {                                          // DrawPrimitiveUP (Type, PrimitiveCount, pData, Stride)
            if (stage < 1) break;
            const uint32_t n = prim_vertices(r.arg(0), r.arg(1)), stride = r.arg(3);
            const Sa2Blob* b = r.blob(SA2S_BLOB_IN, 2);
            if (!b || b->len < size_t(n) * stride || !n) break;   // the player makes no host call
            const int cls = (!stride || !decl_bound) ? 1 : !r.arg(1) ? 2 : 0;
            if (cls == 0) up_vertex += uint64_t(d9g::vertex_count(r.arg(0), r.arg(1))) * stride;
            draw("DrawPrimitiveUP", r.arg(0), cls);
            break;
        }
        case 84: {                                          // DrawIndexedPrimitiveUP
            if (stage < 1) break;
            const uint32_t idx = prim_vertices(r.arg(0), r.arg(3));
            const uint32_t isz = r.arg(5) == 101 ? 2 : 4;
            const Sa2Blob* ib = r.blob(SA2S_BLOB_IN, 4);
            const Sa2Blob* vb = r.blob(SA2S_BLOB_IN, 6);
            if (!ib || ib->len < size_t(idx) * isz) break;
            if (!vb || vb->len < size_t(r.arg(1) + r.arg(2)) * r.arg(7)) break;
            const uint32_t stride = r.arg(7);
            const int cls = (!stride || !decl_bound) ? 1 : (!r.arg(3) || !r.arg(2)) ? 2 : 0;
            if (cls == 0) {
                up_vertex += uint64_t(r.arg(1) + r.arg(2)) * stride;
                up_index += uint64_t(d9g::vertex_count(r.arg(0), r.arg(3))) * isz;
            }
            draw("DrawIndexedPrimitiveUP", r.arg(0), cls);
            break;
        }
        default:
            break;
        }
    }
    const std::string sha = rd.file_sha256();

    // Run-length per-frame draws, as the device's report keeps them.
    std::vector<std::array<uint64_t, 3>> runs;
    std::vector<uint32_t> draw_free, draw_free_dirty, draw_free_never_cleared;
    uint64_t clean = 0;
    for (const FrameRow& f : frames) {
        if (!runs.empty() && runs.back()[2] == f.draws && runs.back()[1] + 1 == f.frame) runs.back()[1] = f.frame;
        else runs.push_back({f.frame, f.frame, f.draws});
        if (!f.draws) {
            draw_free.push_back(f.frame);
            if (f.dirty) draw_free_dirty.push_back(f.frame);
            if (!f.ever_cleared) draw_free_never_cleared.push_back(f.frame);
        }
        if (!f.dirty) clean++;
    }
    if (!tsv.empty()) {
        FILE* t = std::fopen(tsv.c_str(), "w");
        if (!t) { std::fprintf(stderr, "census: cannot write %s\n", tsv.c_str()); return 2; }
        std::fprintf(t, "frame\tpresent_seq\tdraws\tdraws_bb\tclears\tfull_bb_colour_clears\tdirty\tever_cleared\n");
        for (const FrameRow& f : frames)
            std::fprintf(t, "%u\t%u\t%" PRIu64 "\t%" PRIu64 "\t%" PRIu64 "\t%" PRIu64 "\t%d\t%d\n", f.frame, f.present_seq,
                         f.draws, f.draws_bb, f.clears, f.full_bb_colour, f.dirty ? 1 : 0, f.ever_cleared ? 1 : 0);
        std::fclose(t);
    }
    auto list = [](const std::vector<uint32_t>& v) {
        std::string s = "[";
        for (size_t i = 0; i < v.size(); i++) s += (i ? ", " : "") + std::to_string(v[i]);
        return s + "]";
    };
    std::string runs_s = "[";
    for (size_t i = 0; i < runs.size(); i++)
        runs_s += (i ? ", " : "") + std::string("[") + std::to_string(runs[i][0]) + ", " + std::to_string(runs[i][1]) +
                  ", " + std::to_string(runs[i][2]) + "]";
    runs_s += "]";
    std::string j = "{\n  \"tool\": \"host/d3d9gpu sa2_frame_census\",\n";
    j += "  \"stream\": " + jstr(stream) + ",\n  \"stream_sha256\": " + jstr(sha) + ",\n";
    j += "  \"stage\": " + std::to_string(stage) + ",\n  \"records\": " + std::to_string(records) +
         ",\n  \"end_records\": " + std::to_string(end_records) + ",\n";
    j += "  \"device\": {\"made\": " + std::string(device ? "true" : "false") + ", \"back_buffer\": [" +
         std::to_string(bb_w) + ", " + std::to_string(bb_h) + ", " + std::to_string(bb_fmt) + "], \"auto_depth\": " +
         (auto_ds ? "true" : "false") + "},\n";
    j += "  \"frames\": " + std::to_string(frames.size()) + ",\n";
    j += "  \"draws\": {\"total\": " + std::to_string(total) + ", \"skipped_no_shader_route\": " + kinds_json(skipped) +
         ", \"refused\": " + kinds_json(refused) + ", \"empty\": " + kinds_json(empty) + ", \"per_frame\": " + runs_s +
         ", \"after_last_present\": " + std::to_string(cur.draws) + "},\n";
    j += "  \"up_vertex_bytes\": " + std::to_string(up_vertex) + ",\n  \"up_index_bytes\": " + std::to_string(up_index) + ",\n";
    j += "  \"clears\": {\"records\": " + std::to_string(clear_calls) + ", \"host_calls\": " + std::to_string(clear_host) +
         ", \"back_buffer_full_colour\": " + std::to_string(bb_full_colour) + ", \"back_buffer_partial_colour\": " +
         std::to_string(bb_partial_colour) + ", \"srgb_write\": " + std::to_string(srgb_clears) + ", \"past_edge\": " +
         std::to_string(past_edge) + "},\n";
    j += "  \"draw_free_frames\": " + list(draw_free) + ",\n";
    j += "  \"draw_free_frames_back_buffer_drawn_earlier\": " + list(draw_free_dirty) + ",\n";
    j += "  \"draw_free_frames_never_colour_cleared\": " + list(draw_free_never_cleared) + ",\n";
    j += "  \"frames_made_by_clears_alone\": " + std::to_string(clean) + "\n}\n";
    FILE* f = std::fopen(out.c_str(), "w");
    if (!f || std::fwrite(j.data(), 1, j.size(), f) != j.size()) { std::fprintf(stderr, "census: cannot write %s\n", out.c_str()); return 2; }
    std::fclose(f);
    std::printf("census %s stage %d: %zu frames, %" PRIu64 " draws, %zu draw-free (%zu drawn earlier), sha256 %s\n",
                stream.c_str(), stage, frames.size(), total, draw_free.size(), draw_free_dirty.size(),
                sha.substr(0, 16).c_str());
    return 0;
}
