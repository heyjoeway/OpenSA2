// texcodec.cpp: see texcodec.h.
#include "texcodec.h"

#include <png.h>

#include <algorithm>
#include <cmath>
#include <csetjmp>
#include <cstring>

namespace tex {

uint32_t block_bytes(uint32_t fmt) {
    if (fmt == FMT_DXT1) return 8;
    if (fmt == FMT_DXT2 || fmt == FMT_DXT3 || fmt == FMT_DXT4 || fmt == FMT_DXT5) return 16;
    return 0;
}

uint32_t pixel_bytes(uint32_t f) {
    switch (f) {
    case 20: return 3;
    case 21: case 22: case 31: case 32: case 33: case 34: case 35:
    case 71: case 75: case 77: case 79: case 82: case 83:
    case 102: case 114: case 112: return 4;
    case 23: case 24: case 25: case 26: case 30: case 29:
    case 40: case 51: case 60: case 61: case 70: case 73: case 80:
    case 81: case 101: case 111: return 2;
    case 27: case 28: case 41: case 50: case 52: return 1;
    case 36: case 110: case 113: case 115: return 8;
    case 116: return 16;
    default: return 0;
    }
}

const char* format_name(uint32_t f) {
    switch (f) {
    case FMT_R8G8B8: return "R8G8B8";
    case FMT_A8R8G8B8: return "A8R8G8B8";
    case FMT_X8R8G8B8: return "X8R8G8B8";
    case FMT_R5G6B5: return "R5G6B5";
    case FMT_X1R5G5B5: return "X1R5G5B5";
    case FMT_A1R5G5B5: return "A1R5G5B5";
    case FMT_A4R4G4B4: return "A4R4G4B4";
    case FMT_A8: return "A8";
    case FMT_X4R4G4B4: return "X4R4G4B4";
    case FMT_A8B8G8R8: return "A8B8G8R8";
    case FMT_X8B8G8R8: return "X8B8G8R8";
    case FMT_L8: return "L8";
    case FMT_A8L8: return "A8L8";
    case FMT_D24S8: return "D24S8";
    case FMT_DXT1: return "DXT1";
    case FMT_DXT2: return "DXT2";
    case FMT_DXT3: return "DXT3";
    case FMT_DXT4: return "DXT4";
    case FMT_DXT5: return "DXT5";
    default: return "?";
    }
}

uint32_t level_pitch(uint32_t w, uint32_t fmt) {
    if (const uint32_t b = block_bytes(fmt)) return ((w + 3u) / 4u) * b;
    return w * pixel_bytes(fmt);
}
uint32_t level_rows(uint32_t h, uint32_t fmt) {
    return block_bytes(fmt) ? (h + 3u) / 4u : h;
}
uint32_t level_bytes(uint32_t w, uint32_t h, uint32_t fmt) {
    return level_pitch(w, fmt) * level_rows(h, fmt);
}
uint32_t full_chain(uint32_t w, uint32_t h) {
    uint32_t n = 1;
    while (w > 1u || h > 1u) {
        if (w > 1u) w >>= 1;
        if (h > 1u) h >>= 1;
        n++;
    }
    return n;
}

// ------------------------------------------------------------------- DDS

static uint32_t rd32(const uint8_t* p) {
    uint32_t v;
    std::memcpy(&v, p, 4);
    return v;
}

static uint32_t dds_format(uint32_t pf_flags, uint32_t fourcc_, uint32_t bits, uint32_t rm,
                           uint32_t gm, uint32_t bm, uint32_t am) {
    constexpr uint32_t DDPF_ALPHAPIXELS = 0x1, DDPF_FOURCC = 0x4, DDPF_RGB = 0x40;
    if (pf_flags & DDPF_FOURCC) return block_bytes(fourcc_) ? fourcc_ : 0u;
    if (!(pf_flags & DDPF_RGB)) return 0u;
    const bool alpha = pf_flags & DDPF_ALPHAPIXELS;
    if (bits == 32) {
        if (rm == 0x00FF0000u && gm == 0x0000FF00u && bm == 0x000000FFu)
            return alpha && am == 0xFF000000u ? 21u : 22u;
        if (rm == 0x000000FFu && gm == 0x0000FF00u && bm == 0x00FF0000u)
            return alpha && am == 0xFF000000u ? 32u : 33u;
        return 0u;
    }
    if (bits == 24 && rm == 0x00FF0000u) return 20u;
    if (bits == 16) {
        if (rm == 0xF800u && gm == 0x07E0u && bm == 0x001Fu) return 23u;
        if (rm == 0x7C00u && gm == 0x03E0u && bm == 0x001Fu)
            return alpha && am == 0x8000u ? 25u : 24u;
        if (rm == 0x0F00u && gm == 0x00F0u && bm == 0x000Fu)
            return alpha && am == 0xF000u ? 26u : 30u;
        return 0u;
    }
    return 0u;
}

bool parse_dds(const uint8_t* p, size_t n, DdsInfo* out) {
    *out = DdsInfo{};
    if (n < 128 || std::memcmp(p, "DDS ", 4) != 0) {
        out->error = "not a DDS (no 'DDS ' magic or shorter than 128 bytes)";
        return false;
    }
    uint32_t d[32];
    for (int i = 0; i < 32; i++) d[i] = rd32(p + 4 * i);
    if (d[1] != 124u) {
        out->error = "DDS header size is not 124";
        return false;
    }
    constexpr uint32_t DDSD_MIPMAPCOUNT = 0x20000;
    out->h = d[3];
    out->w = d[4];
    out->depth = d[6] ? d[6] : 1u;
    out->levels = (d[2] & DDSD_MIPMAPCOUNT) && d[7] ? d[7] : 1u;
    out->fmt = dds_format(d[20], d[21], d[22], d[23], d[24], d[25], d[26]);
    out->caps2 = d[28];
    out->data_offset = 128;
    if (!out->w || !out->h || !out->fmt) {
        out->error = "DDS with no size or a pixel format with no D3DFORMAT";
        return false;
    }
    return true;
}

bool dds_cube_levels(const uint8_t* p, size_t n, const DdsInfo& info, uint32_t keep,
                     std::vector<std::vector<uint8_t>> faces[6], std::string* err) {
    size_t chain = 0;
    for (uint32_t lv = 0; lv < info.levels; lv++)
        chain += level_bytes(level_dim(info.w, lv), level_dim(info.h, lv), info.fmt);
    const uint32_t have = std::min(keep, info.levels);
    if (info.data_offset > n || n - info.data_offset < 6 * chain) {
        *err = "the DDS cube map's six faces of " + std::to_string(info.levels) + " level(s) need " +
               std::to_string(6 * chain) + " bytes after the header; the file holds " +
               std::to_string(n > info.data_offset ? n - info.data_offset : 0);
        return false;
    }
    for (uint32_t f = 0; f < 6; f++) {
        size_t off = info.data_offset + f * chain;
        faces[f].clear();
        for (uint32_t lv = 0; lv < have; lv++) {
            const size_t k = level_bytes(level_dim(info.w, lv), level_dim(info.h, lv), info.fmt);
            faces[f].emplace_back(p + off, p + off + k);
            off += k;
        }
    }
    return true;
}

bool dds_volume_levels(const uint8_t* p, size_t n, const DdsInfo& info, uint32_t keep,
                       std::vector<std::vector<uint8_t>>* levels, std::string* err) {
    levels->clear();
    size_t off = info.data_offset;
    const uint32_t have = std::min(keep, info.levels);
    for (uint32_t lv = 0; lv < have; lv++) {
        const size_t k = size_t(level_bytes(level_dim(info.w, lv), level_dim(info.h, lv), info.fmt)) *
                         level_dim(info.depth, lv);
        if (off > n || n - off < k) {
            *err = "the DDS volume's level " + std::to_string(lv) + " (" + std::to_string(level_dim(info.depth, lv)) +
                   " slices, " + std::to_string(k) + " bytes) runs past the file";
            return false;
        }
        levels->emplace_back(p + off, p + off + k);
        off += k;
    }
    return true;
}

// ------------------------------------------------------------------- PNG

static uint32_t be32(const uint8_t* p) {
    return uint32_t(p[0]) << 24 | uint32_t(p[1]) << 16 | uint32_t(p[2]) << 8 | p[3];
}

bool png_header(const uint8_t* p, size_t n, PngInfo* out) {
    *out = PngInfo{};
    static const uint8_t sig[8] = {0x89, 'P', 'N', 'G', 0x0D, 0x0A, 0x1A, 0x0A};
    if (n < 33 || std::memcmp(p, sig, 8) != 0 || std::memcmp(p + 12, "IHDR", 4) != 0) {
        out->error = "not a PNG with IHDR first";
        return false;
    }
    out->w = be32(p + 16);
    out->h = be32(p + 20);
    out->bit_depth = p[24];
    out->colour_type = p[25];
    size_t off = 8;
    for (int guard = 0; guard < 64 && off + 12 <= n; guard++) {
        const uint32_t len = be32(p + off);
        if (!std::memcmp(p + off + 4, "tRNS", 4)) { out->trns = true; break; }
        if (!std::memcmp(p + off + 4, "IDAT", 4) || !std::memcmp(p + off + 4, "IEND", 4)) break;
        if (len > n - off - 12) { out->error = "PNG chunk index runs past the blob"; return false; }
        off += 12u + len;
    }
    switch (out->colour_type) {
    case 0: case 2: case 3: out->fmt = out->trns ? FMT_A8R8G8B8 : FMT_X8R8G8B8; break;
    case 4: case 6: out->fmt = FMT_A8R8G8B8; break;
    default: out->fmt = 0; break;
    }
    if (!out->w || !out->h || !out->fmt) {
        out->error = "PNG size or colour type has no D3DFORMAT";
        return false;
    }
    return true;
}

namespace {
struct PngMem {
    const uint8_t* p;
    size_t n, off;
};
struct PngErr {
    std::jmp_buf jb;
    char msg[256];
};
void png_mem_read(png_structp png, png_bytep dst, png_size_t len) {
    auto* m = static_cast<PngMem*>(png_get_io_ptr(png));
    if (len > m->n - m->off) png_error(png, "read past the end of the PNG blob");
    std::memcpy(dst, m->p + m->off, len);
    m->off += len;
}
void png_err_fn(png_structp png, png_const_charp msg) {
    auto* e = static_cast<PngErr*>(png_get_error_ptr(png));
    std::snprintf(e->msg, sizeof e->msg, "%s", msg);
    std::longjmp(e->jb, 1);
}
void png_warn_fn(png_structp, png_const_charp) {}
}  // namespace

// The classic API rather than png_image, because png_image applies gamma
// conversions (16-bit samples are treated as linear) and D3DX does not: it
// keeps the top eight bits of each sample and expands palettes and tRNS.
bool decode_png(const uint8_t* p, size_t n, std::vector<uint8_t>* bgra, uint32_t* w,
                uint32_t* h, std::string* err) {
    PngMem mem{p, n, 0};
    PngErr e{};
    png_structp png = png_create_read_struct(PNG_LIBPNG_VER_STRING, &e, png_err_fn, png_warn_fn);
    if (!png) { *err = "png_create_read_struct failed"; return false; }
    png_infop info = png_create_info_struct(png);
    std::vector<png_bytep> rows;
    if (setjmp(e.jb)) {
        png_destroy_read_struct(&png, &info, nullptr);
        *err = std::string("libpng: ") + e.msg;
        return false;
    }
    png_set_read_fn(png, &mem, png_mem_read);
    png_read_info(png, info);
    const png_uint_32 pw = png_get_image_width(png, info), ph = png_get_image_height(png, info);
    const int depth = png_get_bit_depth(png, info), colour = png_get_color_type(png, info);
    if (colour == PNG_COLOR_TYPE_PALETTE) png_set_palette_to_rgb(png);
    if (colour == PNG_COLOR_TYPE_GRAY && depth < 8) png_set_expand_gray_1_2_4_to_8(png);
    if (png_get_valid(png, info, PNG_INFO_tRNS)) png_set_tRNS_to_alpha(png);
    if (depth == 16) png_set_strip_16(png);
    if (colour == PNG_COLOR_TYPE_GRAY || colour == PNG_COLOR_TYPE_GRAY_ALPHA) png_set_gray_to_rgb(png);
    png_set_bgr(png);
    png_set_filler(png, 0xFF, PNG_FILLER_AFTER);
    png_set_interlace_handling(png);
    png_read_update_info(png, info);
    if (png_get_rowbytes(png, info) != size_t(pw) * 4) png_error(png, "unexpected row size after transforms");
    bgra->assign(size_t(pw) * ph * 4, 0);
    rows.resize(ph);
    for (png_uint_32 y = 0; y < ph; y++) rows[y] = bgra->data() + size_t(y) * pw * 4;
    png_read_image(png, rows.data());
    png_read_end(png, nullptr);
    png_destroy_read_struct(&png, &info, nullptr);
    *w = pw;
    *h = ph;
    return true;
}

bool write_png_rgb(const char* path, const uint8_t* bgra, uint32_t w, uint32_t h, uint32_t pitch,
                   std::string* err) {
    std::vector<uint8_t> bgr(size_t(w) * h * 3);
    for (uint32_t y = 0; y < h; y++) {
        const uint8_t* s = bgra + size_t(y) * pitch;
        uint8_t* d = bgr.data() + size_t(y) * w * 3;
        for (uint32_t x = 0; x < w; x++) {
            d[3 * x + 0] = s[4 * x + 0];
            d[3 * x + 1] = s[4 * x + 1];
            d[3 * x + 2] = s[4 * x + 2];
        }
    }
    png_image img;
    std::memset(&img, 0, sizeof img);
    img.version = PNG_IMAGE_VERSION;
    img.width = w;
    img.height = h;
    img.format = PNG_FORMAT_BGR;
    if (!png_image_write_to_file(&img, path, 0, bgr.data(), int(w * 3), nullptr)) {
        *err = std::string("libpng: ") + img.message;
        return false;
    }
    return true;
}

// ------------------------------------------------------------ pixel codecs

namespace {

struct Px { uint8_t b, g, r, a; };

inline uint16_t rd16(const uint8_t* p) { return uint16_t(p[0] | p[1] << 8); }
inline void wr16(uint8_t* p, uint16_t v) { p[0] = uint8_t(v); p[1] = uint8_t(v >> 8); }

inline Px from565(uint16_t c) {
    const uint32_t r = (c >> 11) & 31, g = (c >> 5) & 63, b = c & 31;
    return Px{uint8_t(b << 3 | b >> 2), uint8_t(g << 2 | g >> 4), uint8_t(r << 3 | r >> 2), 255};
}
inline uint16_t to565(int r, int g, int b) {
    r = std::clamp(r, 0, 255); g = std::clamp(g, 0, 255); b = std::clamp(b, 0, 255);
    return uint16_t(((r * 31 + 127) / 255) << 11 | ((g * 63 + 127) / 255) << 5 | ((b * 31 + 127) / 255));
}

void colour_palette(uint16_t c0, uint16_t c1, bool four, Px pal[4]) {
    pal[0] = from565(c0);
    pal[1] = from565(c1);
    if (four) {
        pal[2] = Px{uint8_t((2 * pal[0].b + pal[1].b) / 3), uint8_t((2 * pal[0].g + pal[1].g) / 3),
                    uint8_t((2 * pal[0].r + pal[1].r) / 3), 255};
        pal[3] = Px{uint8_t((pal[0].b + 2 * pal[1].b) / 3), uint8_t((pal[0].g + 2 * pal[1].g) / 3),
                    uint8_t((pal[0].r + 2 * pal[1].r) / 3), 255};
    } else {
        pal[2] = Px{uint8_t((pal[0].b + pal[1].b) / 2), uint8_t((pal[0].g + pal[1].g) / 2),
                    uint8_t((pal[0].r + pal[1].r) / 2), 255};
        pal[3] = Px{0, 0, 0, 0};
    }
}

void decode_colour_block(const uint8_t* blk, bool dxt1, Px out[16]) {
    const uint16_t c0 = rd16(blk), c1 = rd16(blk + 2);
    Px pal[4];
    colour_palette(c0, c1, !dxt1 || c0 > c1, pal);
    const uint32_t idx = rd32(blk + 4);
    for (int i = 0; i < 16; i++) out[i] = pal[(idx >> (2 * i)) & 3];
}

void alpha_palette(uint8_t a0, uint8_t a1, uint8_t pal[8]) {
    pal[0] = a0;
    pal[1] = a1;
    if (a0 > a1) {
        for (int i = 1; i < 7; i++) pal[i + 1] = uint8_t(((7 - i) * a0 + i * a1) / 7);
    } else {
        for (int i = 1; i < 5; i++) pal[i + 1] = uint8_t(((5 - i) * a0 + i * a1) / 5);
        pal[6] = 0;
        pal[7] = 255;
    }
}

void decode_block(const uint8_t* blk, uint32_t fmt, Px out[16]) {
    if (fmt == FMT_DXT1) { decode_colour_block(blk, true, out); return; }
    decode_colour_block(blk + 8, false, out);
    if (fmt == FMT_DXT2 || fmt == FMT_DXT3) {
        for (int i = 0; i < 16; i++) {
            const uint8_t nib = (blk[i / 2] >> (4 * (i & 1))) & 15;
            out[i].a = uint8_t(nib * 17);
        }
    } else {
        uint8_t pal[8];
        alpha_palette(blk[0], blk[1], pal);
        uint64_t bits = 0;
        for (int i = 0; i < 6; i++) bits |= uint64_t(blk[2 + i]) << (8 * i);
        for (int i = 0; i < 16; i++) out[i].a = pal[(bits >> (3 * i)) & 7];
    }
}

// A BC block as the reference GPU samples it (decode_level_sampled): NVIDIA's arithmetic, which the
// host comparison's frames were made with. Colour: the endpoints bit-replicated; the interpolated red
// and blue from the 5-bit values ((2*c0 + c1) * 22 / 8, and (c0 + c1) * 33 / 8 in three-colour mode),
// green from the 8-bit ones with NVIDIA's rounding (the bc7enc project's rgbcx.h, unpack_bc1_nv).
// Alpha (BC3): (a0 * (2048 - W) + a1 * W + 1024) >> 11 with W 289, 578, 892, 1156, 1470, 1759 for codes
// 2..7 when a0 > a1, else 385, 770, 1278, 1663 for codes 2..5 (6 is 0, 7 is 255). Both measured on this
// project's RTX 5090 by tools/s8/bc_probe.cpp: every alpha endpoint pair and code, and 65,536 colour
// blocks of each BC format, decode exactly so.
void sampled_colour_palette(uint16_t c0, uint16_t c1, bool four, Px pal[4]) {
    const int r0 = (c0 >> 11) & 31, b0 = c0 & 31, r1 = (c1 >> 11) & 31, b1 = c1 & 31;
    pal[0] = from565(c0);
    pal[1] = from565(c1);
    const int e0 = pal[0].g, e1 = pal[1].g, gd = e1 - e0;
    if (four) {
        pal[2] = Px{uint8_t((2 * b0 + b1) * 22 / 8), uint8_t((256 * e0 + gd / 4 + 128 + gd * 80) / 256),
                    uint8_t((2 * r0 + r1) * 22 / 8), 255};
        pal[3] = Px{uint8_t((2 * b1 + b0) * 22 / 8), uint8_t((256 * e1 - gd / 4 + 128 - gd * 80) / 256),
                    uint8_t((2 * r1 + r0) * 22 / 8), 255};
    } else {
        pal[2] = Px{uint8_t((b0 + b1) * 33 / 8), uint8_t((256 * e0 + gd / 4 + 128 + gd * 128) / 256),
                    uint8_t((r0 + r1) * 33 / 8), 255};
        pal[3] = Px{0, 0, 0, 0};
    }
}

void sampled_alpha_palette(uint8_t a0, uint8_t a1, uint8_t pal[8]) {
    static const int w8[6] = {289, 578, 892, 1156, 1470, 1759}, w6[4] = {385, 770, 1278, 1663};
    pal[0] = a0;
    pal[1] = a1;
    if (a0 > a1) {
        for (int i = 0; i < 6; i++) pal[i + 2] = uint8_t((a0 * (2048 - w8[i]) + a1 * w8[i] + 1024) >> 11);
    } else {
        for (int i = 0; i < 4; i++) pal[i + 2] = uint8_t((a0 * (2048 - w6[i]) + a1 * w6[i] + 1024) >> 11);
        pal[6] = 0;
        pal[7] = 255;
    }
}

void decode_block_sampled(const uint8_t* blk, uint32_t fmt, Px out[16]) {
    const uint8_t* cb = fmt == FMT_DXT1 ? blk : blk + 8;
    const uint16_t c0 = rd16(cb), c1 = rd16(cb + 2);
    Px pal[4];
    sampled_colour_palette(c0, c1, fmt != FMT_DXT1 || c0 > c1, pal);
    const uint32_t idx = rd32(cb + 4);
    for (int i = 0; i < 16; i++) out[i] = pal[(idx >> (2 * i)) & 3];
    if (fmt == FMT_DXT1) return;
    if (fmt == FMT_DXT2 || fmt == FMT_DXT3) {
        for (int i = 0; i < 16; i++) out[i].a = uint8_t(((blk[i / 2] >> (4 * (i & 1))) & 15) * 17);
        return;
    }
    uint8_t apal[8];
    sampled_alpha_palette(blk[0], blk[1], apal);
    uint64_t bits = 0;
    for (int i = 0; i < 6; i++) bits |= uint64_t(blk[2 + i]) << (8 * i);
    for (int i = 0; i < 16; i++) out[i].a = apal[(bits >> (3 * i)) & 7];
}

inline int dist2(const Px& a, const Px& b) {
    const int db = a.b - b.b, dg = a.g - b.g, dr = a.r - b.r;
    return dr * dr + dg * dg + db * db;
}

// One DXT colour block from 16 pixels. The endpoints are the extreme pixels
// along the colours' principal axis (a power iteration on the covariance),
// then every pixel takes the nearest of the palette the decoder will build.
void encode_colour_block(const Px px[16], bool dxt1, uint8_t* blk) {
    bool transparent = false;
    if (dxt1)
        for (int i = 0; i < 16; i++) transparent |= px[i].a < 128;
    float mean[3] = {0, 0, 0};
    int n = 0;
    for (int i = 0; i < 16; i++) {
        if (transparent && px[i].a < 128) continue;
        mean[0] += px[i].r; mean[1] += px[i].g; mean[2] += px[i].b;
        n++;
    }
    uint16_t c0 = 0, c1 = 0;
    if (n == 0) {                      // every pixel transparent: 3-colour mode, index 3
        wr16(blk, 0); wr16(blk + 2, 0);
        const uint32_t all3 = 0xFFFFFFFFu;
        std::memcpy(blk + 4, &all3, 4);
        return;
    }
    for (float& m : mean) m /= float(n);
    float cov[6] = {0, 0, 0, 0, 0, 0};
    for (int i = 0; i < 16; i++) {
        if (transparent && px[i].a < 128) continue;
        const float r = px[i].r - mean[0], g = px[i].g - mean[1], b = px[i].b - mean[2];
        cov[0] += r * r; cov[1] += r * g; cov[2] += r * b;
        cov[3] += g * g; cov[4] += g * b; cov[5] += b * b;
    }
    // Start the iteration from the covariance column with the largest variance:
    // a fixed start such as (1,1,1) is orthogonal to the axis of a block whose
    // red falls as its blue rises, and the iteration never leaves that plane.
    float axis[3];
    if (cov[0] >= cov[3] && cov[0] >= cov[5]) { axis[0] = cov[0]; axis[1] = cov[1]; axis[2] = cov[2]; }
    else if (cov[3] >= cov[5])                { axis[0] = cov[1]; axis[1] = cov[3]; axis[2] = cov[4]; }
    else                                      { axis[0] = cov[2]; axis[1] = cov[4]; axis[2] = cov[5]; }
    if (std::fabs(axis[0]) + std::fabs(axis[1]) + std::fabs(axis[2]) < 1e-6f) axis[0] = axis[1] = axis[2] = 1.0f;
    for (int it = 0; it < 8; it++) {
        const float x = cov[0] * axis[0] + cov[1] * axis[1] + cov[2] * axis[2];
        const float y = cov[1] * axis[0] + cov[3] * axis[1] + cov[4] * axis[2];
        const float z = cov[2] * axis[0] + cov[4] * axis[1] + cov[5] * axis[2];
        const float len = std::max({std::fabs(x), std::fabs(y), std::fabs(z)});
        if (len < 1e-6f) break;
        axis[0] = x / len; axis[1] = y / len; axis[2] = z / len;
    }
    // The endpoints lie on the principal line through the mean, at the pixels'
    // extreme projections, so colour variation off the axis is split evenly.
    const float norm = std::sqrt(axis[0] * axis[0] + axis[1] * axis[1] + axis[2] * axis[2]);
    for (float& v : axis) v /= norm;
    float lo = 1e30f, hi = -1e30f;
    for (int i = 0; i < 16; i++) {
        if (transparent && px[i].a < 128) continue;
        const float d = (px[i].r - mean[0]) * axis[0] + (px[i].g - mean[1]) * axis[1] +
                        (px[i].b - mean[2]) * axis[2];
        lo = std::min(lo, d);
        hi = std::max(hi, d);
    }
    auto endpoint = [&](float t) {
        return to565(int(std::lround(mean[0] + t * axis[0])), int(std::lround(mean[1] + t * axis[1])),
                     int(std::lround(mean[2] + t * axis[2])));
    };
    const uint16_t a = endpoint(hi);
    const uint16_t b = endpoint(lo);
    bool four;
    if (transparent) {                 // 3-colour mode needs c0 <= c1
        c0 = std::min(a, b); c1 = std::max(a, b); four = false;
    } else {
        c0 = std::max(a, b); c1 = std::min(a, b); four = c0 > c1;
    }
    Px pal[4];
    colour_palette(c0, c1, four, pal);
    uint32_t idx = 0;
    for (int i = 0; i < 16; i++) {
        uint32_t best = 0;
        if (transparent && px[i].a < 128) {
            best = 3;
        } else if (c0 != c1) {
            int bd = 1 << 30;
            const int choices = four ? 4 : 3;
            for (int k = 0; k < choices; k++) {
                const int d = dist2(px[i], pal[k]);
                if (d < bd) { bd = d; best = uint32_t(k); }
            }
        }
        idx |= best << (2 * i);
    }
    wr16(blk, c0);
    wr16(blk + 2, c1);
    std::memcpy(blk + 4, &idx, 4);
}

void encode_block(const Px px[16], uint32_t fmt, uint8_t* blk) {
    if (fmt == FMT_DXT1) { encode_colour_block(px, true, blk); return; }
    if (fmt == FMT_DXT2 || fmt == FMT_DXT3) {
        std::memset(blk, 0, 8);
        for (int i = 0; i < 16; i++) {
            const uint8_t nib = uint8_t((px[i].a * 15 + 127) / 255);
            blk[i / 2] |= uint8_t(nib << (4 * (i & 1)));
        }
    } else {
        uint8_t a0 = 0, a1 = 255;
        for (int i = 0; i < 16; i++) { a0 = std::max(a0, px[i].a); a1 = std::min(a1, px[i].a); }
        uint8_t pal[8];
        alpha_palette(a0, a1, pal);
        uint64_t bits = 0;
        for (int i = 0; i < 16; i++) {
            uint64_t best = 0;
            if (a0 != a1) {
                int bd = 1 << 30;
                for (int k = 0; k < 8; k++) {
                    const int d = std::abs(int(px[i].a) - int(pal[k]));
                    if (d < bd) { bd = d; best = uint64_t(k); }
                }
            }
            bits |= best << (3 * i);
        }
        blk[0] = a0;
        blk[1] = a1;
        for (int i = 0; i < 6; i++) blk[2 + i] = uint8_t(bits >> (8 * i));
    }
    encode_colour_block(px, false, blk + 8);
}

bool decode_pixel(const uint8_t* s, uint32_t fmt, Px* o) {
    switch (fmt) {
    case FMT_A8R8G8B8: *o = Px{s[0], s[1], s[2], s[3]}; return true;
    case FMT_X8R8G8B8: *o = Px{s[0], s[1], s[2], 255}; return true;
    case FMT_A8B8G8R8: *o = Px{s[2], s[1], s[0], s[3]}; return true;
    case FMT_X8B8G8R8: *o = Px{s[2], s[1], s[0], 255}; return true;
    case FMT_R8G8B8: *o = Px{s[0], s[1], s[2], 255}; return true;
    case FMT_R5G6B5: *o = from565(rd16(s)); return true;
    case FMT_X1R5G5B5: case FMT_A1R5G5B5: {
        const uint16_t v = rd16(s);
        const uint32_t r = (v >> 10) & 31, g = (v >> 5) & 31, b = v & 31;
        *o = Px{uint8_t(b << 3 | b >> 2), uint8_t(g << 3 | g >> 2), uint8_t(r << 3 | r >> 2),
                uint8_t(fmt == FMT_A1R5G5B5 ? ((v >> 15) ? 255 : 0) : 255)};
        return true;
    }
    case FMT_A4R4G4B4: case FMT_X4R4G4B4: {
        const uint16_t v = rd16(s);
        *o = Px{uint8_t((v & 15) * 17), uint8_t(((v >> 4) & 15) * 17), uint8_t(((v >> 8) & 15) * 17),
                uint8_t(fmt == FMT_A4R4G4B4 ? ((v >> 12) & 15) * 17 : 255)};
        return true;
    }
    case FMT_A8: *o = Px{0, 0, 0, s[0]}; return true;
    case FMT_L8: *o = Px{s[0], s[0], s[0], 255}; return true;
    case FMT_A8L8: *o = Px{s[0], s[0], s[0], s[1]}; return true;
    default: return false;
    }
}

bool encode_pixel(const Px& p, uint32_t fmt, uint8_t* d) {
    auto q = [](uint8_t v, int bits) { return uint32_t((v * ((1 << bits) - 1) + 127) / 255); };
    switch (fmt) {
    case FMT_A8R8G8B8: d[0] = p.b; d[1] = p.g; d[2] = p.r; d[3] = p.a; return true;
    case FMT_X8R8G8B8: d[0] = p.b; d[1] = p.g; d[2] = p.r; d[3] = 255; return true;
    case FMT_A8B8G8R8: d[0] = p.r; d[1] = p.g; d[2] = p.b; d[3] = p.a; return true;
    case FMT_X8B8G8R8: d[0] = p.r; d[1] = p.g; d[2] = p.b; d[3] = 255; return true;
    case FMT_R8G8B8: d[0] = p.b; d[1] = p.g; d[2] = p.r; return true;
    case FMT_R5G6B5: wr16(d, to565(p.r, p.g, p.b)); return true;
    case FMT_X1R5G5B5: case FMT_A1R5G5B5:
        wr16(d, uint16_t((fmt == FMT_A1R5G5B5 ? (p.a >= 128 ? 0x8000u : 0u) : 0x8000u) |
                         q(p.r, 5) << 10 | q(p.g, 5) << 5 | q(p.b, 5)));
        return true;
    case FMT_A4R4G4B4: case FMT_X4R4G4B4:
        wr16(d, uint16_t((fmt == FMT_A4R4G4B4 ? q(p.a, 4) : 15u) << 12 | q(p.r, 4) << 8 |
                         q(p.g, 4) << 4 | q(p.b, 4)));
        return true;
    case FMT_A8: d[0] = p.a; return true;
    case FMT_L8: d[0] = uint8_t((p.r * 77 + p.g * 150 + p.b * 29 + 128) >> 8); return true;
    case FMT_A8L8: d[0] = uint8_t((p.r * 77 + p.g * 150 + p.b * 29 + 128) >> 8); d[1] = p.a; return true;
    default: return false;
    }
}

}  // namespace

bool decodable(uint32_t fmt) {
    if (block_bytes(fmt)) return true;
    const uint8_t zero[8] = {};
    Px p;
    return pixel_bytes(fmt) && decode_pixel(zero, fmt, &p);
}

bool decode_level(const uint8_t* src, uint32_t w, uint32_t h, uint32_t fmt, uint32_t pitch,
                  std::vector<uint8_t>* bgra) {
    if (!pitch) pitch = level_pitch(w, fmt);
    bgra->assign(size_t(w) * h * 4, 0);
    Px* out = reinterpret_cast<Px*>(bgra->data());
    if (const uint32_t bb = block_bytes(fmt)) {
        Px px[16];
        for (uint32_t by = 0; by < (h + 3) / 4; by++)
            for (uint32_t bx = 0; bx < (w + 3) / 4; bx++) {
                decode_block(src + size_t(by) * pitch + size_t(bx) * bb, fmt, px);
                for (uint32_t y = 0; y < 4; y++)
                    for (uint32_t x = 0; x < 4; x++)
                        if (bx * 4 + x < w && by * 4 + y < h)
                            out[size_t(by * 4 + y) * w + bx * 4 + x] = px[y * 4 + x];
            }
        return true;
    }
    const uint32_t bpp = pixel_bytes(fmt);
    if (!bpp) return false;
    for (uint32_t y = 0; y < h; y++)
        for (uint32_t x = 0; x < w; x++)
            if (!decode_pixel(src + size_t(y) * pitch + size_t(x) * bpp, fmt, &out[size_t(y) * w + x]))
                return false;
    return true;
}

bool decode_level_sampled(const uint8_t* src, uint32_t w, uint32_t h, uint32_t fmt, uint32_t pitch,
                          std::vector<uint8_t>* bgra) {
    const uint32_t bb = block_bytes(fmt);
    if (!bb) return decode_level(src, w, h, fmt, pitch, bgra);
    if (!pitch) pitch = level_pitch(w, fmt);
    bgra->resize(size_t(w) * h * 4);
    Px* out = reinterpret_cast<Px*>(bgra->data());
    Px px[16];
    for (uint32_t by = 0; by < (h + 3) / 4; by++)
        for (uint32_t bx = 0; bx < (w + 3) / 4; bx++) {
            decode_block_sampled(src + size_t(by) * pitch + size_t(bx) * bb, fmt, px);
            const uint32_t xs = std::min(4u, w - bx * 4), ys = std::min(4u, h - by * 4);
            for (uint32_t y = 0; y < ys; y++)
                std::memcpy(&out[size_t(by * 4 + y) * w + bx * 4], &px[y * 4], xs * sizeof(Px));
        }
    return true;
}

bool encode_level(const uint8_t* bgra, uint32_t w, uint32_t h, uint32_t fmt,
                  std::vector<uint8_t>* out) {
    const uint32_t pitch = level_pitch(w, fmt);
    out->assign(size_t(pitch) * level_rows(h, fmt), 0);
    const Px* in = reinterpret_cast<const Px*>(bgra);
    if (const uint32_t bb = block_bytes(fmt)) {
        Px px[16];
        for (uint32_t by = 0; by < (h + 3) / 4; by++)
            for (uint32_t bx = 0; bx < (w + 3) / 4; bx++) {
                for (uint32_t y = 0; y < 4; y++)          // a level smaller than a block
                    for (uint32_t x = 0; x < 4; x++) {    // repeats its edge pixels
                        const uint32_t sx = std::min(bx * 4 + x, w - 1), sy = std::min(by * 4 + y, h - 1);
                        px[y * 4 + x] = in[size_t(sy) * w + sx];
                    }
                encode_block(px, fmt, out->data() + size_t(by) * pitch + size_t(bx) * bb);
            }
        return true;
    }
    const uint32_t bpp = pixel_bytes(fmt);
    if (!bpp) return false;
    for (uint32_t y = 0; y < h; y++)
        for (uint32_t x = 0; x < w; x++)
            if (!encode_pixel(in[size_t(y) * w + x], fmt, out->data() + size_t(y) * pitch + size_t(x) * bpp))
                return false;
    return true;
}

void box_down(const uint8_t* bgra, uint32_t w, uint32_t h, std::vector<uint8_t>* out, uint32_t* ow,
              uint32_t* oh) {
    const uint32_t dw = std::max(1u, w / 2), dh = std::max(1u, h / 2);
    out->assign(size_t(dw) * dh * 4, 0);
    for (uint32_t y = 0; y < dh; y++) {
        const uint32_t y0 = std::min(2 * y, h - 1), y1 = h > 1 ? std::min(2 * y + 1, h - 1) : y0;
        for (uint32_t x = 0; x < dw; x++) {
            const uint32_t x0 = std::min(2 * x, w - 1), x1 = w > 1 ? std::min(2 * x + 1, w - 1) : x0;
            for (int c = 0; c < 4; c++) {
                const uint32_t s = bgra[(size_t(y0) * w + x0) * 4 + c] + bgra[(size_t(y0) * w + x1) * 4 + c] +
                                   bgra[(size_t(y1) * w + x0) * 4 + c] + bgra[(size_t(y1) * w + x1) * 4 + c];
                (*out)[(size_t(y) * dw + x) * 4 + c] = uint8_t((s + 2) / 4);
            }
        }
    }
    *ow = dw;
    *oh = dh;
}

bool generate_mips(std::vector<std::vector<uint8_t>>* levels, uint32_t w, uint32_t h, uint32_t fmt,
                   uint32_t from, uint32_t count) {
    if (from >= levels->size() || from >= count) return false;
    uint32_t lw = level_dim(w, from), lh = level_dim(h, from);
    std::vector<uint8_t> cur, next;
    if (!decode_level((*levels)[from].data(), lw, lh, fmt, 0, &cur)) return false;
    levels->resize(count);
    for (uint32_t lv = from + 1; lv < count; lv++) {
        uint32_t nw, nh;
        box_down(cur.data(), lw, lh, &next, &nw, &nh);
        if (!encode_level(next.data(), nw, nh, fmt, &(*levels)[lv])) return false;
        cur.swap(next);
        lw = nw;
        lh = nh;
    }
    return true;
}

bool convert_slices(const uint8_t* src, uint32_t w, uint32_t h, uint32_t slices, uint32_t from, uint32_t to,
                    std::vector<uint8_t>* out) {
    const size_t in = level_bytes(w, h, from);
    out->clear();
    std::vector<uint8_t> bgra, enc;
    for (uint32_t z = 0; z < slices; z++) {
        if (!decode_level(src + size_t(z) * in, w, h, from, 0, &bgra) || !encode_level(bgra.data(), w, h, to, &enc))
            return false;
        out->insert(out->end(), enc.begin(), enc.end());
    }
    return true;
}

void box_down_3d(const uint8_t* bgra, uint32_t w, uint32_t h, uint32_t d, std::vector<uint8_t>* out, uint32_t* ow,
                 uint32_t* oh, uint32_t* od) {
    const uint32_t dw = std::max(1u, w / 2), dh = std::max(1u, h / 2), dd = std::max(1u, d / 2);
    out->assign(size_t(dw) * dh * dd * 4, 0);
    auto at = [&](uint32_t x, uint32_t y, uint32_t z, int c) { return bgra[((size_t(z) * h + y) * w + x) * 4 + c]; };
    for (uint32_t z = 0; z < dd; z++) {
        const uint32_t z0 = std::min(2 * z, d - 1), z1 = d > 1 ? std::min(2 * z + 1, d - 1) : z0;
        for (uint32_t y = 0; y < dh; y++) {
            const uint32_t y0 = std::min(2 * y, h - 1), y1 = h > 1 ? std::min(2 * y + 1, h - 1) : y0;
            for (uint32_t x = 0; x < dw; x++) {
                const uint32_t x0 = std::min(2 * x, w - 1), x1 = w > 1 ? std::min(2 * x + 1, w - 1) : x0;
                for (int c = 0; c < 4; c++) {
                    const uint32_t s = at(x0, y0, z0, c) + at(x1, y0, z0, c) + at(x0, y1, z0, c) + at(x1, y1, z0, c) +
                                       at(x0, y0, z1, c) + at(x1, y0, z1, c) + at(x0, y1, z1, c) + at(x1, y1, z1, c);
                    (*out)[((size_t(z) * dh + y) * dw + x) * 4 + c] = uint8_t((s + 4) / 8);
                }
            }
        }
    }
    *ow = dw;
    *oh = dh;
    *od = dd;
}

bool generate_volume_mips(std::vector<std::vector<uint8_t>>* levels, uint32_t w, uint32_t h, uint32_t d,
                          uint32_t fmt, uint32_t from, uint32_t count) {
    if (from >= levels->size() || from >= count) return false;
    uint32_t lw = level_dim(w, from), lh = level_dim(h, from), ld = level_dim(d, from);
    const size_t slice = level_bytes(lw, lh, fmt);
    if ((*levels)[from].size() < slice * ld) return false;
    std::vector<uint8_t> cur, next, one;
    for (uint32_t z = 0; z < ld; z++) {          // the source level's slices, decoded in order
        if (!decode_level((*levels)[from].data() + slice * z, lw, lh, fmt, 0, &one)) return false;
        cur.insert(cur.end(), one.begin(), one.end());
    }
    levels->resize(count);
    for (uint32_t lv = from + 1; lv < count; lv++) {
        uint32_t nw, nh, nd;
        box_down_3d(cur.data(), lw, lh, ld, &next, &nw, &nh, &nd);
        auto& dst = (*levels)[lv];
        dst.clear();
        for (uint32_t z = 0; z < nd; z++) {
            std::vector<uint8_t> enc;
            if (!encode_level(next.data() + size_t(nw) * nh * 4 * z, nw, nh, fmt, &enc)) return false;
            dst.insert(dst.end(), enc.begin(), enc.end());
        }
        cur.swap(next);
        lw = nw;
        lh = nh;
        ld = nd;
    }
    return true;
}

void bgra_to_i420(const uint8_t* bgra, uint32_t pitch, uint32_t w, uint32_t h, uint8_t* out) {
    const uint32_t cw = (w + 1) / 2, ch = (h + 1) / 2;
    uint8_t* Y = out;
    uint8_t* U = Y + size_t(w) * h;
    uint8_t* V = U + size_t(cw) * ch;
    // Kr 0.2126, Kb 0.0722; luma scaled by 219/255, chroma by 224/255, x 65536.
    for (uint32_t y = 0; y < h; y++) {
        const uint8_t* s = bgra + size_t(y) * pitch;
        for (uint32_t x = 0; x < w; x++) {
            const int b = s[4 * x], g = s[4 * x + 1], r = s[4 * x + 2];
            Y[size_t(y) * w + x] = uint8_t((11966 * r + 40254 * g + 4064 * b + (16 << 16) + 32768) >> 16);
        }
    }
    for (uint32_t y = 0; y < ch; y++)
        for (uint32_t x = 0; x < cw; x++) {
            int b = 0, g = 0, r = 0;
            for (uint32_t dy = 0; dy < 2; dy++)
                for (uint32_t dx = 0; dx < 2; dx++) {
                    const uint32_t sx = std::min(2 * x + dx, w - 1), sy = std::min(2 * y + dy, h - 1);
                    const uint8_t* p = bgra + size_t(sy) * pitch + 4 * size_t(sx);
                    b += p[0]; g += p[1]; r += p[2];
                }
            b = (b + 2) / 4; g = (g + 2) / 4; r = (r + 2) / 4;
            U[size_t(y) * cw + x] = uint8_t(std::clamp((-6596 * r - 22189 * g + 28785 * b + (128 << 16) + 32768) >> 16, 0, 255));
            V[size_t(y) * cw + x] = uint8_t(std::clamp((28784 * r - 26145 * g - 2639 * b + (128 << 16) + 32768) >> 16, 0, 255));
        }
}

}  // namespace tex
