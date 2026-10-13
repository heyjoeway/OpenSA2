// test_texcodec.cpp: checks texcodec against values worked out by hand. Exit 0
// when every check holds; each failure prints its line.
#include "texcodec.h"

#include <png.h>

#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

static int g_fail = 0, g_pass = 0;
#define CHECK(cond)                                                               \
    do {                                                                          \
        if (cond) g_pass++;                                                       \
        else { g_fail++; std::printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } \
    } while (0)

static std::vector<uint8_t> gradient(uint32_t w, uint32_t h, bool alpha_ramp) {
    std::vector<uint8_t> v(size_t(w) * h * 4);
    for (uint32_t y = 0; y < h; y++)
        for (uint32_t x = 0; x < w; x++) {
            uint8_t* p = &v[(size_t(y) * w + x) * 4];
            p[0] = uint8_t(255 * x / (w - 1));            // B
            p[1] = uint8_t(255 * y / (h - 1));            // G
            p[2] = uint8_t(255 - 255 * x / (w - 1));      // R
            p[3] = alpha_ramp ? uint8_t(255 * (x + y) / (w + h - 2)) : 255;
        }
    return v;
}

static int max_err(const std::vector<uint8_t>& a, const std::vector<uint8_t>& b, int channel) {
    int m = 0;
    for (size_t i = channel; i < a.size(); i += 4) m = std::max(m, std::abs(int(a[i]) - int(b[i])));
    return m;
}

int main() {
    using namespace tex;
    // Sizes, as runtime/sa2_d3d9.c computes them.
    CHECK(level_pitch(64, FMT_DXT1) == 128);
    CHECK(level_bytes(64, 64, FMT_DXT1) == 2048);
    CHECK(level_bytes(512, 128, FMT_DXT5) == 65536);
    CHECK(level_bytes(2, 2, FMT_DXT1) == 8);
    CHECK(level_bytes(1, 1, FMT_DXT5) == 16);
    CHECK(level_pitch(320, FMT_A8R8G8B8) == 1280);
    CHECK(level_bytes(1280, 720, FMT_X8R8G8B8) == 3686400);
    CHECK(full_chain(512, 128) == 10);
    CHECK(full_chain(256, 256) == 9);
    CHECK(full_chain(1, 1) == 1);
    CHECK(level_dim(128, 9) == 1);
    CHECK(block_bytes(FMT_DXT3) == 16);
    CHECK(pixel_bytes(FMT_R8G8B8) == 3);

    // DXT1: a gradient along one line in colour space (blue rising while red
    // falls, the case a fixed power-iteration start misses) survives a round
    // trip within the 565 steps; a second, independent gradient along y is off
    // any single palette line, so its error is bounded by half its range in a
    // block (51/2) plus quantisation.
    {
        auto src = gradient(16, 16, false);
        for (size_t i = 0; i < src.size(); i += 4) src[i + 1] = 0;
        std::vector<uint8_t> enc, dec;
        CHECK(encode_level(src.data(), 16, 16, FMT_DXT1, &enc));
        CHECK(enc.size() == 128);
        CHECK(decode_level(enc.data(), 16, 16, FMT_DXT1, 0, &dec));
        CHECK(max_err(src, dec, 0) <= 8 && max_err(src, dec, 1) <= 8 && max_err(src, dec, 2) <= 8);
        CHECK(max_err(src, dec, 3) == 0);
        auto src2 = gradient(16, 16, false);
        CHECK(encode_level(src2.data(), 16, 16, FMT_DXT1, &enc));
        CHECK(decode_level(enc.data(), 16, 16, FMT_DXT1, 0, &dec));
        CHECK(max_err(src2, dec, 0) <= 8 && max_err(src2, dec, 2) <= 8 && max_err(src2, dec, 1) <= 30);
    }
    // A block of one colour decodes to that colour quantised to 565.
    {
        std::vector<uint8_t> src(16 * 4);
        for (int i = 0; i < 16; i++) { src[4 * i] = 0x40; src[4 * i + 1] = 0x80; src[4 * i + 2] = 0xC0; src[4 * i + 3] = 255; }
        std::vector<uint8_t> enc, dec;
        CHECK(encode_level(src.data(), 4, 4, FMT_DXT1, &enc));
        CHECK(decode_level(enc.data(), 4, 4, FMT_DXT1, 0, &dec));
        CHECK(std::abs(dec[0] - 0x40) <= 4 && std::abs(dec[1] - 0x80) <= 2 && std::abs(dec[2] - 0xC0) <= 4);
    }
    // DXT1 with transparency: alpha 0 pixels decode transparent, the rest opaque.
    {
        std::vector<uint8_t> src(16 * 4, 0);
        for (int i = 0; i < 16; i++) { src[4 * i + 2] = 200; src[4 * i + 3] = (i % 2) ? 255 : 0; }
        std::vector<uint8_t> enc, dec;
        CHECK(encode_level(src.data(), 4, 4, FMT_DXT1, &enc));
        CHECK(decode_level(enc.data(), 4, 4, FMT_DXT1, 0, &dec));
        bool ok = true;
        for (int i = 0; i < 16; i++) ok &= dec[4 * i + 3] == ((i % 2) ? 255 : 0);
        CHECK(ok);
        CHECK(std::abs(dec[4 * 1 + 2] - 200) <= 4);
    }
    // DXT5 and DXT3 carry an alpha ramp.
    {
        auto src = gradient(16, 16, true);
        std::vector<uint8_t> enc, dec;
        CHECK(encode_level(src.data(), 16, 16, FMT_DXT5, &enc));
        CHECK(enc.size() == 256);
        CHECK(decode_level(enc.data(), 16, 16, FMT_DXT5, 0, &dec));
        CHECK(max_err(src, dec, 3) <= 10);
        CHECK(max_err(src, dec, 0) <= 8 && max_err(src, dec, 1) <= 30);   // G is off-axis, as above
        CHECK(encode_level(src.data(), 16, 16, FMT_DXT3, &enc));
        CHECK(decode_level(enc.data(), 16, 16, FMT_DXT3, 0, &dec));
        CHECK(max_err(src, dec, 3) <= 9);
    }
    // A hand-built DXT1 block: c0 = pure red (0xF800), c1 = pure blue (0x001F),
    // indices 0,1,2,3 on the first row.
    {
        const uint8_t blk[8] = {0x00, 0xF8, 0x1F, 0x00, 0xE4, 0, 0, 0};
        std::vector<uint8_t> dec;
        CHECK(decode_level(blk, 4, 4, FMT_DXT1, 0, &dec));
        CHECK(dec[0] == 0 && dec[1] == 0 && dec[2] == 255);          // red (B,G,R)
        CHECK(dec[4] == 255 && dec[5] == 0 && dec[6] == 0);          // blue
        CHECK(dec[8] == 85 && dec[10] == 170);                       // 2/3 red + 1/3 blue
        CHECK(dec[12] == 170 && dec[14] == 85);
    }
    // Uncompressed formats round-trip exactly where they hold 8 bits.
    {
        auto src = gradient(8, 8, true);
        for (uint32_t f : {FMT_A8R8G8B8, FMT_A8B8G8R8}) {
            std::vector<uint8_t> enc, dec;
            CHECK(encode_level(src.data(), 8, 8, f, &enc));
            CHECK(decode_level(enc.data(), 8, 8, f, 0, &dec));
            CHECK(dec == src);
        }
        std::vector<uint8_t> enc, dec;
        CHECK(encode_level(src.data(), 8, 8, FMT_R5G6B5, &enc));
        CHECK(enc.size() == 128);
        CHECK(decode_level(enc.data(), 8, 8, FMT_R5G6B5, 0, &dec));
        CHECK(max_err(src, dec, 0) <= 4 && max_err(src, dec, 1) <= 2);
    }
    // The box filter: a 2x2 goes to its rounded mean; a 4x1 halves along x only.
    {
        const uint8_t px[16] = {0, 0, 0, 0, 10, 20, 30, 40, 20, 40, 60, 80, 30, 60, 90, 120};
        std::vector<uint8_t> out;
        uint32_t w, h;
        box_down(px, 2, 2, &out, &w, &h);
        CHECK(w == 1 && h == 1);
        CHECK(out[0] == 15 && out[1] == 30 && out[2] == 45 && out[3] == 60);
        box_down(px, 4, 1, &out, &w, &h);
        CHECK(w == 2 && h == 1);
        CHECK(out[0] == 5 && out[4] == 25);
    }
    // A mip chain from a solid DXT1 level 0 keeps the colour at every level.
    {
        std::vector<uint8_t> solid(64 * 64 * 4);
        for (size_t i = 0; i < solid.size(); i += 4) { solid[i] = 32; solid[i + 1] = 160; solid[i + 2] = 224; solid[i + 3] = 255; }
        std::vector<std::vector<uint8_t>> levels(1);
        CHECK(encode_level(solid.data(), 64, 64, FMT_DXT1, &levels[0]));
        CHECK(generate_mips(&levels, 64, 64, FMT_DXT1, 0, 7));
        CHECK(levels.size() == 7);
        CHECK(levels[6].size() == 8);
        std::vector<uint8_t> dec;
        CHECK(decode_level(levels[6].data(), 1, 1, FMT_DXT1, 0, &dec));
        CHECK(std::abs(dec[0] - 32) <= 4 && std::abs(dec[1] - 160) <= 2 && std::abs(dec[2] - 224) <= 4);
        CHECK(levels[3].size() == level_bytes(8, 8, FMT_DXT1));
    }
    // A checkerboard's first mip is its grey mean.
    {
        std::vector<uint8_t> cb(8 * 8 * 4);
        for (uint32_t y = 0; y < 8; y++)
            for (uint32_t x = 0; x < 8; x++) {
                const uint8_t v = ((x ^ y) & 1) ? 255 : 0;
                uint8_t* p = &cb[(y * 8 + x) * 4];
                p[0] = p[1] = p[2] = v;
                p[3] = 255;
            }
        std::vector<std::vector<uint8_t>> levels(1);
        CHECK(encode_level(cb.data(), 8, 8, FMT_A8R8G8B8, &levels[0]));
        CHECK(generate_mips(&levels, 8, 8, FMT_A8R8G8B8, 0, 4));
        CHECK(levels[1][0] == 128 && levels[1][1] == 128 && levels[1][2] == 128);
    }
    // DDS header parsing: a 512x128 DXT5 file with ten levels, and an ARGB one.
    {
        std::vector<uint8_t> dds(128 + 16, 0);
        auto put = [&](int dw, uint32_t v) { std::memcpy(&dds[4 * dw], &v, 4); };
        std::memcpy(dds.data(), "DDS ", 4);
        put(1, 124); put(2, 0x1007 | 0x20000); put(3, 128); put(4, 512); put(7, 10);
        put(19, 32); put(20, 0x4); put(21, FMT_DXT5);
        DdsInfo info;
        CHECK(parse_dds(dds.data(), dds.size(), &info));
        CHECK(info.w == 512 && info.h == 128 && info.levels == 10 && info.fmt == FMT_DXT5);
        put(2, 0x1007); put(20, 0x41); put(21, 0); put(22, 32);
        put(23, 0x00FF0000); put(24, 0x0000FF00); put(25, 0x000000FF); put(26, 0xFF000000);
        CHECK(parse_dds(dds.data(), dds.size(), &info));
        CHECK(info.levels == 1 && info.fmt == FMT_A8R8G8B8);
        put(20, 0x40);
        CHECK(parse_dds(dds.data(), dds.size(), &info) && info.fmt == FMT_X8R8G8B8);
        put(1, 100);
        CHECK(!parse_dds(dds.data(), dds.size(), &info));
    }
    // PNG: an RGB image written by libpng reads back exactly, alpha 255, and
    // the header says X8R8G8B8.
    {
        const uint32_t w = 5, h = 3;
        std::vector<uint8_t> rgb(w * h * 3);
        for (size_t i = 0; i < rgb.size(); i++) rgb[i] = uint8_t(i * 7);
        png_image img;
        std::memset(&img, 0, sizeof img);
        img.version = PNG_IMAGE_VERSION;
        img.width = w;
        img.height = h;
        img.format = PNG_FORMAT_RGB;
        png_alloc_size_t bytes = 0;
        CHECK(png_image_write_to_memory(&img, nullptr, &bytes, 0, rgb.data(), int(w * 3), nullptr));
        std::vector<uint8_t> file(bytes);
        CHECK(png_image_write_to_memory(&img, file.data(), &bytes, 0, rgb.data(), int(w * 3), nullptr));
        file.resize(bytes);
        PngInfo pi;
        CHECK(png_header(file.data(), file.size(), &pi));
        CHECK(pi.w == 5 && pi.h == 3 && pi.colour_type == 2 && pi.fmt == FMT_X8R8G8B8);
        std::vector<uint8_t> bgra;
        uint32_t ow = 0, oh = 0;
        std::string err;
        CHECK(decode_png(file.data(), file.size(), &bgra, &ow, &oh, &err));
        CHECK(ow == 5 && oh == 3);
        bool same = true;
        for (uint32_t i = 0; i < w * h; i++)
            same &= bgra[4 * i] == rgb[3 * i + 2] && bgra[4 * i + 1] == rgb[3 * i + 1] &&
                    bgra[4 * i + 2] == rgb[3 * i] && bgra[4 * i + 3] == 255;
        CHECK(same);
    }
    // Hand-worked decode vectors, independent of the encoder (the review's
    // dxt_ref.cpp): DXT5 alpha in both modes, DXT3's nibble order, DXT1's
    // three-colour mode with transparency, and the texel order inside a block.
    {
        // DXT5, a0 = 255 > a1 = 0: eight alphas 255, 0, then (7-i)*255/7 for i = 1..6
        // truncated: 218, 182, 145, 109, 72, 36. Texel i takes index i % 8.
        uint8_t blk[16] = {255, 0};
        uint64_t bits = 0;
        for (int i = 0; i < 16; i++) bits |= uint64_t(i % 8) << (3 * i);
        for (int i = 0; i < 6; i++) blk[2 + i] = uint8_t(bits >> (8 * i));
        blk[8] = 0xFF; blk[9] = 0xFF;                 // colour c0 = 0xFFFF white, c1 = 0, indices 0
        std::vector<uint8_t> d;
        CHECK(decode_level(blk, 4, 4, FMT_DXT5, 0, &d));
        const int want8[8] = {255, 0, 218, 182, 145, 109, 72, 36};
        bool ok = true;
        for (int i = 0; i < 16; i++) ok &= d[4 * i + 3] == want8[i % 8];
        CHECK(ok);
        CHECK(d[0] == 255 && d[1] == 255 && d[2] == 255);
        // a0 = 0 <= a1 = 255: six-alpha mode, 2..5 are 51, 102, 153, 204; 6 is 0, 7 is 255.
        blk[0] = 0; blk[1] = 255;
        CHECK(decode_level(blk, 4, 4, FMT_DXT5, 0, &d));
        const int want6[8] = {0, 255, 51, 102, 153, 204, 0, 255};
        ok = true;
        for (int i = 0; i < 16; i++) ok &= d[4 * i + 3] == want6[i % 8];
        CHECK(ok);
    }
    {
        // DXT3: texel i's alpha is nibble i, low nibble first, times 17.
        uint8_t blk[16] = {};
        for (int i = 0; i < 16; i++) blk[i / 2] |= uint8_t((i & 15) << (4 * (i & 1)));
        blk[8] = 0x1F; blk[9] = 0x00;                 // c0 = 0x001F pure blue, c1 = 0
        std::vector<uint8_t> d;
        CHECK(decode_level(blk, 4, 4, FMT_DXT3, 0, &d));
        bool ok = true;
        for (int i = 0; i < 16; i++) ok &= d[4 * i + 3] == i * 17;
        CHECK(ok);
        CHECK(d[0] == 255 && d[1] == 0 && d[2] == 0);  // blue, in BGRA
    }
    {
        // DXT1 with c0 = 0 <= c1 = 0x001F: index 2 is the midpoint (0 + 255) / 2 = 127,
        // index 3 transparent black. Texel i takes index i % 4, so texel (1, 2), i = 9, is blue.
        uint8_t blk[8] = {0x00, 0x00, 0x1F, 0x00, 0, 0, 0, 0};
        uint32_t idx = 0;
        for (int i = 0; i < 16; i++) idx |= uint32_t(i % 4) << (2 * i);
        std::memcpy(blk + 4, &idx, 4);
        std::vector<uint8_t> d;
        CHECK(decode_level(blk, 4, 4, FMT_DXT1, 0, &d));
        CHECK(d[0] == 0 && d[3] == 255);
        CHECK(d[4] == 255 && d[7] == 255);
        CHECK(d[8] == 127 && d[11] == 255);
        CHECK(d[12] == 0 && d[13] == 0 && d[14] == 0 && d[15] == 0);
        CHECK(d[4 * 9] == 255 && d[4 * 9 + 2] == 0);
    }
    // The y4m conversion, BT.709 limited range, against values worked from the
    // definition (Y = 16 + 219 Y', Cb = 128 + 224 (B' - Y') / 1.8556,
    // Cr = 128 + 224 (R' - Y') / 1.5748, rounded): black, white, red, green on
    // both rows of a 4x2 frame; chroma averages each 2x2 block first.
    {
        const uint8_t px[4][4] = {{0, 0, 0, 255}, {255, 255, 255, 255}, {0, 0, 255, 255}, {0, 255, 0, 255}};   // BGRA
        std::vector<uint8_t> img(4 * 2 * 4);
        for (int y = 0; y < 2; y++)
            for (int x = 0; x < 4; x++) std::memcpy(&img[(y * 4 + x) * 4], px[x], 4);
        std::vector<uint8_t> out(4 * 2 + 2 * 2 * 1);
        bgra_to_i420(img.data(), 16, 4, 2, out.data());
        const uint8_t wantY[8] = {16, 235, 63, 173, 16, 235, 63, 173};
        CHECK(std::memcmp(out.data(), wantY, 8) == 0);
        CHECK(out[8] == 128 && out[9] == 72);          // Cb: black+white grey; red+green
        CHECK(out[10] == 128 && out[11] == 133);       // Cr
        CHECK(decodable(FMT_DXT1) && decodable(FMT_A8R8G8B8) && decodable(FMT_R5G6B5) && !decodable(35) && !decodable(0));
    }
    // Cube and volume DDS payloads, read as runtime/sa2_d3d9.c lays them out: a cube map's six
    // faces one after another, each its whole chain; a volume's levels one after another, each
    // its slices. A8R8G8B8, with every texel of face f level l (or volume level l slice z) one
    // byte value, so a byte read from the wrong place shows.
    {
        auto header = [](uint32_t w, uint32_t h, uint32_t depth, uint32_t levels, uint32_t caps2) {
            uint32_t d[32] = {};
            d[0] = 0x20534444u; d[1] = 124; d[2] = 0x1007 | (levels > 1 ? 0x20000u : 0) | (depth ? 0x800000u : 0);
            d[3] = h; d[4] = w; d[6] = depth; d[7] = levels > 1 ? levels : 0;
            d[19] = 32; d[20] = 0x41; d[22] = 32; d[23] = 0xFF0000u; d[24] = 0xFF00u; d[25] = 0xFFu;
            d[26] = 0xFF000000u; d[27] = 0x1000; d[28] = caps2;
            std::vector<uint8_t> v(128);
            std::memcpy(v.data(), d, 128);
            return v;
        };
        // A 2x2 cube map with two levels per face: 16 + 4 bytes a face, value 10 f + l.
        std::vector<uint8_t> cube = header(2, 2, 0, 2, kDdsCaps2Cubemap | kDdsCaps2AllFaces);
        for (int f = 0; f < 6; f++) {
            cube.insert(cube.end(), 16, uint8_t(10 * f));
            cube.insert(cube.end(), 4, uint8_t(10 * f + 1));
        }
        DdsInfo info;
        CHECK(parse_dds(cube.data(), cube.size(), &info) && info.levels == 2 && info.fmt == FMT_A8R8G8B8);
        std::vector<std::vector<uint8_t>> faces[6];
        std::string err;
        CHECK(dds_cube_levels(cube.data(), cube.size(), info, 2, faces, &err));
        bool ok = true;
        for (int f = 0; f < 6; f++)
            ok &= faces[f].size() == 2 && faces[f][0] == std::vector<uint8_t>(16, uint8_t(10 * f)) &&
                  faces[f][1] == std::vector<uint8_t>(4, uint8_t(10 * f + 1));
        CHECK(ok);
        // Keeping one level skips each face's second, so face 5 is still read at 128 + 5 x 20.
        CHECK(dds_cube_levels(cube.data(), cube.size(), info, 1, faces, &err));
        CHECK(faces[5].size() == 1 && faces[5][0] == std::vector<uint8_t>(16, 50));
        CHECK(!dds_cube_levels(cube.data(), cube.size() - 1, info, 1, faces, &err) && !err.empty());
        // A 2x2x2 volume with two levels: level 0 is two slices of 16 bytes, level 1 one of 4.
        std::vector<uint8_t> vol = header(2, 2, 2, 2, kDdsCaps2Volume);
        vol.insert(vol.end(), 16, 1);
        vol.insert(vol.end(), 16, 2);
        vol.insert(vol.end(), 4, 3);
        CHECK(parse_dds(vol.data(), vol.size(), &info) && info.depth == 2 && info.levels == 2);
        std::vector<std::vector<uint8_t>> levels;
        CHECK(dds_volume_levels(vol.data(), vol.size(), info, 2, &levels, &err));
        CHECK(levels.size() == 2 && levels[0].size() == 32 && levels[0][15] == 1 && levels[0][16] == 2 &&
              levels[1] == std::vector<uint8_t>(4, 3));
        CHECK(dds_volume_levels(vol.data(), vol.size(), info, 1, &levels, &err) && levels.size() == 1);
        CHECK(!dds_volume_levels(vol.data(), vol.size() - 1, info, 2, &levels, &err) && !err.empty());
    }
    // The box filter in three dimensions: a 2x2x2 block's rounded mean of eight, and at
    // depth 1 exactly box_down's.
    {
        std::vector<uint8_t> v(2 * 2 * 2 * 4), out;
        for (int i = 0; i < 8; i++) { v[4 * i] = uint8_t(10 * i); v[4 * i + 1] = 255; v[4 * i + 2] = uint8_t(i); v[4 * i + 3] = 0; }
        uint32_t ow, oh, od;
        box_down_3d(v.data(), 2, 2, 2, &out, &ow, &oh, &od);
        CHECK(ow == 1 && oh == 1 && od == 1 && out.size() == 4);
        CHECK(out[0] == 35 && out[1] == 255 && out[2] == 4 && out[3] == 0);   // (280 + 4) / 8; (28 + 4) / 8
        const std::vector<uint8_t> img = gradient(8, 6, true);
        std::vector<uint8_t> two, three;
        uint32_t w2, h2, w3, h3, d3;
        box_down(img.data(), 8, 6, &two, &w2, &h2);
        box_down_3d(img.data(), 8, 6, 1, &three, &w3, &h3, &d3);
        CHECK(two == three && w2 == w3 && h2 == h3 && d3 == 1);
        // A volume chain: 4x4x4 whose slice z is blue 64 z, built down to 1x1x1: level 1's two
        // slices are the means of slices 0-1 and 2-3 (32 and 160), level 2 their mean (96).
        std::vector<std::vector<uint8_t>> chain(1);
        for (uint32_t z = 0; z < 4; z++)
            for (int i = 0; i < 16; i++) chain[0].insert(chain[0].end(), {uint8_t(64 * z), 0, 0, 255});
        CHECK(generate_volume_mips(&chain, 4, 4, 4, FMT_A8R8G8B8, 0, 3));
        CHECK(chain.size() == 3 && chain[1].size() == 2 * 2 * 2 * 4 && chain[2].size() == 4);
        CHECK(chain[1][0] == 32 && chain[1][16] == 160 && chain[2][0] == 96 && chain[2][3] == 255);
        CHECK(!generate_volume_mips(&chain, 4, 4, 4, FMT_A8R8G8B8, 3, 3));
        // The same in DXT1: each slice decoded, filtered and encoded again.
        std::vector<std::vector<uint8_t>> dxt(1);
        std::vector<uint8_t> enc, dec;
        for (uint32_t z = 0; z < 2; z++) {
            std::vector<uint8_t> px;
            for (int i = 0; i < 16; i++) px.insert(px.end(), {0, uint8_t(z ? 252 : 0), 0, 255});
            CHECK(encode_level(px.data(), 4, 4, FMT_DXT1, &enc));
            dxt[0].insert(dxt[0].end(), enc.begin(), enc.end());
        }
        CHECK(generate_volume_mips(&dxt, 4, 4, 2, FMT_DXT1, 0, 3));
        CHECK(dxt[1].size() == 8 && dxt[2].size() == 8);
        CHECK(decode_level(dxt[1].data(), 2, 2, FMT_DXT1, 0, &dec) && std::abs(int(dec[1]) - 126) <= 4);
    }
    // A stack of slices converted slice by slice.
    {
        const uint8_t two[8] = {1, 2, 3, 4, 5, 6, 7, 8};             // two 1x1 A8R8G8B8 slices
        std::vector<uint8_t> out;
        CHECK(convert_slices(two, 1, 1, 2, FMT_A8R8G8B8, FMT_X8R8G8B8, &out));
        CHECK(out.size() == 8 && out[0] == 1 && out[2] == 3 && out[4] == 5 && out[6] == 7);
        CHECK(!convert_slices(two, 1, 1, 2, FMT_A8R8G8B8, 35, &out));
    }
    // decode_level_sampled: DXT as the RTX 5090 samples it (tools/s8/bc_probe.cpp read these
    // back). Each block's rows hold the indices 0, 1, 2, 3; pixels are B, G, R, A.
    {
        auto px = [](const std::vector<uint8_t>& v, int i) {
            return std::vector<int>{v[4 * i], v[4 * i + 1], v[4 * i + 2], v[4 * i + 3]};
        };
        std::vector<uint8_t> dec;
        // DXT1 four colours: red over blue, green over black. Green's thirds are 175 and 80 where
        // decode_level's are 170 and 85.
        const uint8_t rb[8] = {0x00, 0xF8, 0x1F, 0x00, 0xE4, 0xE4, 0xE4, 0xE4};
        CHECK(decode_level_sampled(rb, 4, 4, FMT_DXT1, 0, &dec) && dec.size() == 64);
        CHECK(px(dec, 0) == (std::vector<int>{0, 0, 255, 255}) && px(dec, 1) == (std::vector<int>{255, 0, 0, 255}));
        CHECK(px(dec, 2) == (std::vector<int>{85, 0, 170, 255}) && px(dec, 3) == (std::vector<int>{170, 0, 85, 255}));
        const uint8_t g[8] = {0xE0, 0x07, 0x00, 0x00, 0xE4, 0xE4, 0xE4, 0xE4};
        CHECK(decode_level_sampled(g, 4, 4, FMT_DXT1, 0, &dec));
        CHECK(px(dec, 2) == (std::vector<int>{0, 175, 0, 255}) && px(dec, 3) == (std::vector<int>{0, 80, 0, 255}));
        CHECK(decode_level(g, 4, 4, FMT_DXT1, 0, &dec) && dec[4 * 2 + 1] == 170 && dec[4 * 3 + 1] == 85);
        // DXT1 three colours (c0 <= c1): black under green, their half, transparent black.
        const uint8_t three[8] = {0x00, 0x00, 0xE0, 0x07, 0xE4, 0xE4, 0xE4, 0xE4};
        CHECK(decode_level_sampled(three, 4, 4, FMT_DXT1, 0, &dec));
        CHECK(px(dec, 2) == (std::vector<int>{0, 128, 0, 255}) && px(dec, 3) == (std::vector<int>{0, 0, 0, 0}));
        // DXT5 alpha, codes 0..7 in each texel row pair: 8 values (a0 > a1) and 6 (a0 <= a1).
        auto alphas = [&](uint8_t a0, uint8_t a1) {
            uint8_t b[16] = {a0, a1};
            uint64_t bits = 0;
            for (int t = 0; t < 16; t++) bits |= uint64_t(t & 7) << (3 * t);
            for (int k = 0; k < 6; k++) b[2 + k] = uint8_t(bits >> (8 * k));
            std::vector<int> a;
            if (decode_level_sampled(b, 4, 4, FMT_DXT5, 0, &dec))
                for (int t = 0; t < 8; t++) a.push_back(dec[4 * t + 3]);
            return a;
        };
        CHECK(alphas(63, 0) == (std::vector<int>{63, 0, 54, 45, 36, 27, 18, 9}));
        CHECK(alphas(60, 10) == (std::vector<int>{60, 10, 53, 46, 38, 32, 24, 17}));
        CHECK(alphas(0, 255) == (std::vector<int>{0, 255, 48, 96, 159, 207, 0, 255}));
        CHECK(alphas(10, 200) == (std::vector<int>{10, 200, 46, 81, 129, 164, 0, 255}));
        // A level smaller than its block, and an uncompressed format, as decode_level.
        CHECK(decode_level_sampled(rb, 2, 1, FMT_DXT1, 0, &dec) && dec.size() == 8 && px(dec, 1)[0] == 255);
        const uint8_t argb[4] = {1, 2, 3, 4};
        CHECK(decode_level_sampled(argb, 1, 1, FMT_A8R8G8B8, 0, &dec) && dec == (std::vector<uint8_t>{1, 2, 3, 4}));
    }
    std::printf("texcodec: %d checks passed, %d failed\n", g_pass, g_fail);
    return g_fail ? 1 : 0;
}
