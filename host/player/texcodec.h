// texcodec.h: the pixel work D3DX did natively and the recording device never
// does (runtime/sa2_d3d9.c decodes no pixel, by design): D3DFORMAT sizes with
// the recording device's packed pitch, DDS headers, DXT1/3/5 and the small
// uncompressed formats to and from BGRA8, D3DX's box filter for mip chains,
// and PNG through libpng. Pure CPU code: no Direct3D, no SDL, so it is tested
// on its own (test_texcodec.cpp).
#pragma once

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace tex {

constexpr uint32_t fourcc(char a, char b, char c, char d) {
    return uint32_t(uint8_t(a)) | uint32_t(uint8_t(b)) << 8 | uint32_t(uint8_t(c)) << 16 |
           uint32_t(uint8_t(d)) << 24;
}

enum : uint32_t {
    FMT_UNKNOWN = 0,
    FMT_R8G8B8 = 20,
    FMT_A8R8G8B8 = 21,
    FMT_X8R8G8B8 = 22,
    FMT_R5G6B5 = 23,
    FMT_X1R5G5B5 = 24,
    FMT_A1R5G5B5 = 25,
    FMT_A4R4G4B4 = 26,
    FMT_A8 = 28,
    FMT_X4R4G4B4 = 30,
    FMT_A8B8G8R8 = 32,
    FMT_X8B8G8R8 = 33,
    FMT_L8 = 50,
    FMT_A8L8 = 51,
    FMT_D24S8 = 75,
    FMT_DXT1 = fourcc('D', 'X', 'T', '1'),
    FMT_DXT2 = fourcc('D', 'X', 'T', '2'),
    FMT_DXT3 = fourcc('D', 'X', 'T', '3'),
    FMT_DXT4 = fourcc('D', 'X', 'T', '4'),
    FMT_DXT5 = fourcc('D', 'X', 'T', '5'),
};

// Bytes per 4x4 block for DXT1..5, else 0 (runtime/sa2_d3d9.c fmt_block).
uint32_t block_bytes(uint32_t fmt);
// Bytes per pixel for the formats the recording device sizes, else 0
// (runtime/sa2_d3d9.c fmt_bpp).
uint32_t pixel_bytes(uint32_t fmt);
const char* format_name(uint32_t fmt);  // "DXT1", "A8R8G8B8", ... or "?"

inline uint32_t level_dim(uint32_t d, uint32_t level) {
    const uint32_t v = level >= 32 ? 0u : d >> level;
    return v ? v : 1u;
}
// The recording device's lock pitch and the DDS payload's row pitch: packed,
// w*bpp, or one row of blocks for DXT (runtime/sa2_d3d9.c lock_rect).
uint32_t level_pitch(uint32_t w, uint32_t fmt);
uint32_t level_rows(uint32_t h, uint32_t fmt);  // pitch rows: h, or (h+3)/4 for DXT
uint32_t level_bytes(uint32_t w, uint32_t h, uint32_t fmt);
uint32_t full_chain(uint32_t w, uint32_t h);  // levels down to 1x1

// ------------------------------------------------------------------- DDS
struct DdsInfo {
    uint32_t w = 0, h = 0, depth = 1, levels = 1, fmt = 0, caps2 = 0;
    uint32_t data_offset = 128;
    std::string error;
};
// The header as runtime/sa2_d3d9.c reads it (dds_format's mask rules);
// false with `error` set for anything it would refuse.
bool parse_dds(const uint8_t* p, size_t n, DdsInfo* out);

// DDSCAPS2: a cube map (the bit runtime/sa2_d3d9.c checks against the cube
// creator), the six face bits (+X 0x400 .. -Z 0x8000, D3DCUBEMAP_FACES order),
// and a volume (the bit it checks against the volume creator).
constexpr uint32_t kDdsCaps2Cubemap = 0x00000200u;
constexpr uint32_t kDdsCaps2AllFaces = 0x0000FC00u;
constexpr uint32_t kDdsCaps2Volume = 0x00200000u;
// A DDS cube map's payload: the six faces one after another in D3DCUBEMAP_FACES
// order (+X, -X, +Y, -Y, +Z, -Z), each a chain of `info.levels` levels from
// info.w x info.h. faces[f] gets the first `keep` levels of face f (packed
// pitch, in the file's format); the levels past `keep` are skipped, not read.
// false with `err` set when the payload is shorter than six whole chains. The
// face bits are the caller's to check: this reads all six.
bool dds_cube_levels(const uint8_t* p, size_t n, const DdsInfo& info, uint32_t keep,
                     std::vector<std::vector<uint8_t>> faces[6], std::string* err);
// A DDS volume's payload: level i is level_dim(info.depth, i) slices of
// level_bytes(level_dim(info.w, i), level_dim(info.h, i)) bytes, one after
// another (D3DLOCKED_BOX order at the packed row and slice pitches). levels gets
// the first `keep` (at most info.levels); false with `err` set when the payload
// runs short.
bool dds_volume_levels(const uint8_t* p, size_t n, const DdsInfo& info, uint32_t keep,
                       std::vector<std::vector<uint8_t>>* levels, std::string* err);

// ------------------------------------------------------------------- PNG
struct PngInfo {
    uint32_t w = 0, h = 0, bit_depth = 0, colour_type = 0;
    bool trns = false;
    uint32_t fmt = 0;  // A8R8G8B8 or X8R8G8B8, as runtime/sa2_d3d9.c decides it
    std::string error;
};
bool png_header(const uint8_t* p, size_t n, PngInfo* out);
// Decode to BGRA8 rows (w*4 pitch). Alpha is 255 where the image has none.
bool decode_png(const uint8_t* p, size_t n, std::vector<uint8_t>* bgra, uint32_t* w,
                uint32_t* h, std::string* err);
// Write BGRA8 rows as an 8-bit RGB PNG (alpha dropped: a back buffer's alpha is
// whatever the game last wrote there, not transparency).
bool write_png_rgb(const char* path, const uint8_t* bgra, uint32_t w, uint32_t h,
                   uint32_t pitch, std::string* err);

// --------------------------------------------------------------- pixels
// Whether decode_level reads `fmt` (DXT1..5 and the uncompressed formats in
// decode_pixel). The player refuses a back buffer it could not read back.
bool decodable(uint32_t fmt);
// Decode one level (packed pitch unless `pitch` is given) to BGRA8, w*h*4.
bool decode_level(const uint8_t* src, uint32_t w, uint32_t h, uint32_t fmt, uint32_t pitch,
                  std::vector<uint8_t>* bgra);
// decode_level as the reference GPU samples a DXT level (BC1..3 by NVIDIA's arithmetic, which the
// host comparison's frames were made with: texcodec.cpp decode_block_sampled), the other formats as
// decode_level. host/d3d9gpu's decode for a GPU without BC formats (a Mali's) is this, so its frames
// equal the reference's; decode_level keeps D3DX's arithmetic, which the player's D3DX work uses.
bool decode_level_sampled(const uint8_t* src, uint32_t w, uint32_t h, uint32_t fmt, uint32_t pitch,
                          std::vector<uint8_t>* bgra);
// Encode BGRA8 (w*4 pitch) into `fmt` with the packed pitch.
bool encode_level(const uint8_t* bgra, uint32_t w, uint32_t h, uint32_t fmt,
                  std::vector<uint8_t>* out);
// D3DX_FILTER_BOX down one level: each output pixel the rounded mean of the
// 2x2 (2x1 or 1x2 at a unit dimension) block above it.
void box_down(const uint8_t* bgra, uint32_t w, uint32_t h, std::vector<uint8_t>* out,
              uint32_t* ow, uint32_t* oh);
// Fill levels[from+1 .. count) of a chain in `fmt` from levels[from], by
// decoding, box-filtering level by level in BGRA8 and re-encoding, which is
// what D3DX does for a filtered chain. `levels` is resized to `count`.
bool generate_mips(std::vector<std::vector<uint8_t>>* levels, uint32_t w, uint32_t h,
                   uint32_t fmt, uint32_t from, uint32_t count);
// A stack of `slices` levels of w x h in `from` (packed, one after another, as
// a volume level holds them) converted slice by slice to `to`
// (decode_level, encode_level).
bool convert_slices(const uint8_t* src, uint32_t w, uint32_t h, uint32_t slices, uint32_t from,
                    uint32_t to, std::vector<uint8_t>* out);
// box_down in three dimensions, for a volume: each output texel the rounded
// mean of the 2x2x2 block above it, a unit dimension repeating its one texel
// (so a volume of depth 1 filters exactly as box_down). `bgra` is d slices of
// w x h, slice after slice.
void box_down_3d(const uint8_t* bgra, uint32_t w, uint32_t h, uint32_t d, std::vector<uint8_t>* out,
                 uint32_t* ow, uint32_t* oh, uint32_t* od);
// generate_mips for a volume: levels[i] holds level_dim(d, i) slices (as
// dds_volume_levels gives them), filled from levels[from] by box_down_3d.
bool generate_volume_mips(std::vector<std::vector<uint8_t>>* levels, uint32_t w, uint32_t h, uint32_t d,
                          uint32_t fmt, uint32_t from, uint32_t count);

// ------------------------------------------------------------------ video
// One BGRA8 frame (alpha ignored) to planar I420 in `out` (w*h luma bytes, then
// the (w+1)/2 x (h+1)/2 Cb and Cr planes): BT.709 coefficients, limited range
// (Y 16..235, Cb/Cr 16..240), in 16.16 fixed point; chroma is the rounded mean
// of each 2x2 block (edge pixels repeated). The player's y4m frames are this.
void bgra_to_i420(const uint8_t* bgra, uint32_t pitch, uint32_t w, uint32_t h, uint8_t* out);

}  // namespace tex
