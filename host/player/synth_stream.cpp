// synth_stream.cpp: writes a small, synthetic Direct3D 9 call stream in the
// recorder's format (host/sa2_stream.h), and the pixels the player must show
// for it, so the player can be tested at every stage before a recorded stream
// exists, and afterwards without running the game.
//
//   sa2_synth_stream OUT.sa2d3d9 OUT.expect.tsv [--variant NAME]
//
// The stream builds what the boot's first frames use: a device from recorded
// presentation parameters, one vs_3_0/ps_3_0 pair (hand-assembled tokens), a
// vertex declaration, DrawPrimitiveUP triangle fans with a 24-byte stride, a
// first frame drawn outside BeginScene/EndScene, and five textures:
//   A  DDS DXT1 256x256, one level in the file, D3DX_DEFAULT levels (so the
//      player must build the other eight: a one-texel red/green checkerboard
//      whose first mip is olive)
//   B  DDS DXT5 64x64 with its seven levels in the file (level 0 yellow)
//   C  D3DXCreateTexture 64x64 A8R8G8B8, filled through GetSurfaceLevel,
//      LockRect and UnlockRect, then D3DXFilterTexture
//   D  PNG 128x64 RGB, horizontal stripes
//   E  DDS DXT1 64x64, four solid quadrants, one level in the file
// Frames 7 and 8 add Metal Harbor's paths (render targets, a vertex buffer,
// DrawPrimitive, a D3DX copy of the back buffer). Frame 9 covers what the boot
// does that frames 1-8 do not, and what no replay does yet:
//   F  D3DXCreateTexture 64x64 DXT1 filled by a lock, then D3DXFilterTexture
//      (the boot's 89 DXT1 targets), drawn minified 4x
//   G  A8R8G8B8 filled whole, then through a sub-rectangle lock
//   H  PNG RGBA drawn with alpha blending
//   I  DDS A8R8G8B8 (RGB masks), then a READONLY lock whose bytes are zeros
//      (the recording device's empty store): the player must not upload them
//   J  DDS DXT3, K  DDS DXT5, both with colour and alpha gradients: each drawn
//      1:1 at texel centres and held, on a grid of 256 and 484 texels, to the
//      CPU decoder's own decode of the same blocks (the GPU against the CPU)
// Frame 5's Present has a NOTE between its CALL and its FRAME, which is the
// order the recorder's delivered version wrote a call's notes in; frame 3's
// FRAME has one after it, the order the folded recorder writes. So the stream
// has NOTE records, and a player plays it with --allow-notes.
//
// Like every stream the folded recorder writes, the header carries the D3DX
// "entries" table and the stream starts with Direct3DCreate9 (entry 12) at seq
// 0, before CreateDevice; an IDirect3D9 query follows it. The pixel shader's
// constant table is asked for (entry 5). After frame 9 come the two D3DX Save
// calls (entries 10 and 11, no effect on the picture), and a second
// Direct3DCreate9 whose IDirect3D9 is queried and released.
//
// Frame 7 asks for the targets as Metal Harbor does before each
// render-to-texture pass: GetRenderTarget 0 to 3 (1 to 3 refused: nothing is
// bound there) and GetDepthStencilSurface, then again once the texture's level
// and its depth surface are bound; the player holds each answer to the
// bindings it followed.
//
// The expect file lists, per frame and stage range, a pixel and its BGRA value
// with a tolerance; `sa2_player --expect` checks them.
//
// --variant writes a broken copy, for the player's negative controls:
//   cut       stops after the fourth FRAME record (no END)
//   v2        header version 2          schema    a schema other than v1's
//   seqgap    skips one seq number      argc      a SetRenderState with 3 arguments
//   afterend  a NOTE after END          noframe   frame 3's FRAME left out
//   badrect   texture C's lock names the rectangle (40,0)-(8,64), inverted
//   cube      a D3DXCreateCubeTexture (entry 8), which the player does not handle
//   create9   no Direct3DCreate9 record, though the header's table has entry 12
//   entryname the header's entries table names entry 3 D3DXFilterTextureEx
//   entryargc Direct3DCreate9 recorded with one argument
//   d3dxfirst thirty D3DXCreateTexture calls before CreateDevice
// and one each for a check a replay makes that needs no device, which a
// stats read (--stats-only) must make too:
//   badtex    a SetTexture naming a texture no call made
//   noblob    a SetViewport without its IN blob
//   noendtok  a vertex shader whose tokens do not end with END
//   clearnull a Clear of one rectangle with pRects NULL (not handled)
//   nolock    a surface UnlockRect with no LockRect and no LOCK blob
//   badlevel  a texture LockRect of level 3 of a one-level texture
//   badsurf   a GetBackBuffer answering a surface no call made
//   ownupload the depth surface filled through a lock: an upload into a
//             surface that is not a texture level (not handled)
//   badtarget frame 7's GetRenderTarget(0), with the texture level bound,
//             answers the back buffer
//   bbrtt     frame 7 asks GetBackBuffer with the texture level bound, and gets
//             that level, as the recording device answers (its render target 0)
//   refused   frame 7's first GetRenderTarget(0) refused, with the back buffer bound
#include "names.h"
#include "sa2_stream.h"
#include "texcodec.h"

#include <png.h>

#include <cmath>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>

namespace {

struct BlobW {
    uint16_t role, arg;
    std::vector<uint8_t> data;
};

template <typename T>
std::vector<uint8_t> bytes_of(const T* p, size_t n) {
    std::vector<uint8_t> v(sizeof(T) * n);
    std::memcpy(v.data(), p, v.size());
    return v;
}

class Writer {
public:
    explicit Writer(FILE* f) : f_(f) {}
    uint32_t version = SA2S_VERSION;
    uint32_t cut_after_frames = 0, gap_at = 0, drop_frame = 0;
    // header_bytes covers the 16 fixed bytes and the JSON, padded with spaces
    // to a multiple of 4 (host/sa2_stream.h).
    void header(std::string json) {
        while ((16 + json.size()) & 3) json += ' ';
        std::fwrite(SA2S_MAGIC, 1, 8, f_);
        put32(version);
        put32(16 + uint32_t(json.size()));
        std::fwrite(json.data(), 1, json.size(), f_);
    }
    void call(uint16_t type, uint16_t slot, uint32_t self, const std::vector<uint32_t>& args,
              uint32_t eax = 0, const std::vector<BlobW>& blobs = {}) {
        std::vector<uint8_t> p;
        app16(p, type); app16(p, slot); app32(p, self); app32(p, 0 /* guest thread */);
        args_blobs(p, args, eax, blobs);
        record(SA2S_CALL, p);
    }
    void d3dx(uint32_t entry, const std::vector<uint32_t>& args, uint32_t eax,
              const std::vector<BlobW>& blobs) {
        std::vector<uint8_t> p;
        app32(p, entry);
        args_blobs(p, args, eax, blobs);
        record(SA2S_D3DX, p);
    }
    void frame(uint64_t virtual_us, uint32_t frames, uint32_t polls) {
        if (drop_frame && frames == drop_frame) return;
        std::vector<uint8_t> p;
        app32(p, uint32_t(virtual_us)); app32(p, uint32_t(virtual_us >> 32));
        app32(p, frames); app32(p, polls);
        record(SA2S_FRAME, p);
        if (cut_after_frames && ++frames_written_ == cut_after_frames) closed_ = true;
    }
    void note(const std::string& s) {
        std::vector<uint8_t> p;
        app32(p, uint32_t(s.size()));
        p.insert(p.end(), s.begin(), s.end());
        while (p.size() & 3) p.push_back(0);
        record(SA2S_NOTE, p);
    }
    void end() { record(SA2S_END, {}); }
    uint32_t records() const { return seq_; }

private:
    static void app16(std::vector<uint8_t>& v, uint16_t x) { v.push_back(uint8_t(x)); v.push_back(uint8_t(x >> 8)); }
    static void app32(std::vector<uint8_t>& v, uint32_t x) { for (int i = 0; i < 4; i++) v.push_back(uint8_t(x >> (8 * i))); }
    void args_blobs(std::vector<uint8_t>& p, const std::vector<uint32_t>& args, uint32_t eax,
                    const std::vector<BlobW>& blobs) {
        app32(p, uint32_t(args.size()));
        for (uint32_t a : args) app32(p, a);
        app32(p, eax);
        app32(p, uint32_t(blobs.size()));
        for (const BlobW& b : blobs) {
            app16(p, b.role); app16(p, b.arg); app32(p, uint32_t(b.data.size()));
            p.insert(p.end(), b.data.begin(), b.data.end());
            while (p.size() & 3) p.push_back(0);
        }
    }
    void record(uint32_t kind, const std::vector<uint8_t>& payload) {
        if (closed_) return;
        if (gap_at && seq_ == gap_at) seq_++;
        put32(kind);
        put32(12 + uint32_t(payload.size()));
        put32(seq_++);
        std::fwrite(payload.data(), 1, payload.size(), f_);
    }
    void put32(uint32_t x) { std::fwrite(&x, 4, 1, f_); }
    FILE* f_;
    uint32_t seq_ = 0, frames_written_ = 0;
    bool closed_ = false;
};

// Guest-side numbering, as runtime/sa2_d3d9.c lays it out.
constexpr uint32_t OBJ_BASE = 0x03200000u, OBJ_STRIDE = 16u;
constexpr uint32_t D3DX_DEFAULT = 0xFFFFFFFFu;
uint32_t g_next_obj = 4;                 // 0 IDirect3D9, 1 device, 2 back buffer, 3 depth
uint32_t new_obj() { return OBJ_BASE + OBJ_STRIDE * g_next_obj++; }
uint32_t g_heap = 0x04100000u;           // fake guest pointers for the arguments
uint32_t gptr(size_t n) { const uint32_t p = g_heap; g_heap += uint32_t((n + 15) & ~size_t(15)); return p; }
uint32_t fbits(float f) { uint32_t u; std::memcpy(&u, &f, 4); return u; }

// ------------------------------------------------------------ shader tokens

// vs_3_0: dcl_position v0; dcl_texcoord v1; dcl_position o0; dcl_texcoord o1;
//         mov o0, v0; mov o1, v1
const uint32_t kVS[] = {
    0xFFFE0300u,
    0x0200001Fu, 0x80000000u, 0x900F0000u,
    0x0200001Fu, 0x80000005u, 0x900F0001u,
    0x0200001Fu, 0x80000000u, 0xE00F0000u,
    0x0200001Fu, 0x80000005u, 0xE00F0001u,
    0x02000001u, 0xE00F0000u, 0x90E40000u,
    0x02000001u, 0xE00F0001u, 0x90E40001u,
    0x0000FFFFu,
};
// ps_3_0: dcl_texcoord v0; dcl_2d s0; texld r0, v0, s0; mul oC0, r0, c0
const uint32_t kPS[] = {
    0xFFFF0300u,
    0x0200001Fu, 0x80000005u, 0x900F0000u,
    0x0200001Fu, 0x90000000u, 0xA00F0800u,
    0x03000042u, 0x800F0000u, 0x90E40000u, 0xA0E40800u,
    0x03000005u, 0x800F0800u, 0x80E40000u, 0xA0E40000u,
    0x0000FFFFu,
};

// D3DVERTEXELEMENT9: {Stream, Offset, Type, Method, Usage, UsageIndex}
std::vector<uint8_t> decl_bytes() {
    struct E { uint16_t stream, offset; uint8_t type, method, usage, index; };
    const E e[] = {{0, 0, 3 /* FLOAT4 */, 0, 0 /* POSITION */, 0},
                   {0, 16, 1 /* FLOAT2 */, 0, 5 /* TEXCOORD */, 0},
                   {0xFF, 0, 17 /* UNUSED */, 0, 0, 0}};
    return bytes_of(e, 3);
}

// ---------------------------------------------------------------- textures

std::vector<uint8_t> dds(uint32_t w, uint32_t h, uint32_t fmt, const std::vector<std::vector<uint8_t>>& levels) {
    std::vector<uint8_t> f(128, 0);
    auto put = [&](int dw, uint32_t v) { std::memcpy(&f[4 * dw], &v, 4); };
    std::memcpy(f.data(), "DDS ", 4);
    put(1, 124);
    put(2, 0x1007 | 0x80000 | (levels.size() > 1 ? 0x20000 : 0));
    put(3, h); put(4, w); put(7, uint32_t(levels.size()));
    put(19, 32); put(20, 0x4); put(21, fmt);
    put(27, 0x1000 | (levels.size() > 1 ? 0x400008 : 0));
    for (const auto& l : levels) f.insert(f.end(), l.begin(), l.end());
    return f;
}

// A one-level 32-bit DDS with RGB masks and alpha: A8R8G8B8.
std::vector<uint8_t> dds_argb(uint32_t w, uint32_t h, const std::vector<uint8_t>& bgra) {
    std::vector<uint8_t> f(128, 0);
    auto put = [&](int dw, uint32_t v) { std::memcpy(&f[4 * dw], &v, 4); };
    std::memcpy(f.data(), "DDS ", 4);
    put(1, 124);
    put(2, 0x1007 | 0x8);                     // CAPS HEIGHT WIDTH PIXELFORMAT PITCH
    put(3, h); put(4, w); put(5, w * 4);
    put(19, 32); put(20, 0x41 /* RGB | ALPHAPIXELS */); put(22, 32);
    put(23, 0x00FF0000u); put(24, 0x0000FF00u); put(25, 0x000000FFu); put(26, 0xFF000000u);
    put(27, 0x1000);
    f.insert(f.end(), bgra.begin(), bgra.end());
    return f;
}

std::vector<uint8_t> solid(uint32_t w, uint32_t h, uint8_t b, uint8_t g, uint8_t r, uint8_t a = 255) {
    std::vector<uint8_t> v(size_t(w) * h * 4);
    for (size_t i = 0; i < v.size(); i += 4) { v[i] = b; v[i + 1] = g; v[i + 2] = r; v[i + 3] = a; }
    return v;
}

std::vector<uint8_t> png_of(uint32_t w, uint32_t h, const std::vector<uint8_t>& px, bool alpha) {
    png_image img;
    std::memset(&img, 0, sizeof img);
    img.version = PNG_IMAGE_VERSION;
    img.width = w;
    img.height = h;
    img.format = alpha ? PNG_FORMAT_RGBA : PNG_FORMAT_RGB;
    const int stride = int(w * (alpha ? 4 : 3));
    png_alloc_size_t n = 0;
    png_image_write_to_memory(&img, nullptr, &n, 0, px.data(), stride, nullptr);
    std::vector<uint8_t> out(n);
    png_image_write_to_memory(&img, out.data(), &n, 0, px.data(), stride, nullptr);
    out.resize(n);
    return out;
}

// ------------------------------------------------------------------ drawing

// A screen-space rectangle as a triangle fan of 4 vertices: clip-space float4
// position and float2 texcoord, 24 bytes each, for a 1280x720 target.
std::vector<uint8_t> quad_ndc(float x0, float y0, float x1, float y1) {
    const float v[4][6] = {{x0, y0, 0.5f, 1.0f, 0.0f, 0.0f},
                           {x1, y0, 0.5f, 1.0f, 1.0f, 0.0f},
                           {x1, y1, 0.5f, 1.0f, 1.0f, 1.0f},
                           {x0, y1, 0.5f, 1.0f, 0.0f, 1.0f}};
    return bytes_of(&v[0][0], 24);
}
std::vector<uint8_t> quad(float x0, float y0, float x1, float y1) {
    return quad_ndc(x0 / 640.0f - 1.0f, 1.0f - y0 / 360.0f, x1 / 640.0f - 1.0f, 1.0f - y1 / 360.0f);
}

struct Expect {
    uint32_t frame, x, y, stage_lo, stage_hi;
    uint8_t b, g, r;
    uint32_t tol;
    std::string what;
};

}  // namespace

int main(int argc, char** argv) {
    std::string variant;
    if (argc == 5 && std::string(argv[3]) == "--variant") variant = argv[4];
    else if (argc != 3) {
        std::fprintf(stderr, "usage: %s OUT.sa2d3d9 OUT.expect.tsv [--variant cut|v2|schema|seqgap|argc|afterend|"
                     "noframe|badrect|cube|create9|entryname|entryargc|d3dxfirst|badtex|noblob|noendtok|clearnull|"
                     "nolock|badlevel|badsurf|ownupload|badtarget|bbrtt|refused|checkpoint]\n", argv[0]);
        return 2;
    }
    static const char* const kVariants[] = {"", "cut", "v2", "schema", "seqgap", "argc", "afterend", "noframe", "badrect",
                                            "cube", "create9", "entryname", "entryargc", "d3dxfirst", "badtex",
                                            "noblob", "noendtok", "clearnull", "nolock", "badlevel", "badsurf",
                                            "ownupload", "badtarget", "bbrtt", "refused", "checkpoint"};
    bool known = false;
    for (const char* v : kVariants) known |= variant == v;
    if (!known) { std::fprintf(stderr, "unknown variant %s\n", variant.c_str()); return 2; }
    FILE* f = std::fopen(argv[1], "wb");
    if (!f) { std::perror(argv[1]); return 2; }
    Writer w(f);
    if (variant == "v2") w.version = 2;
    if (variant == "cut") w.cut_after_frames = 4;
    if (variant == "seqgap") w.gap_at = 40;
    if (variant == "noframe") w.drop_frame = 3;
    // The header the recorder writes, with the method table limited to the
    // calls this stream makes (tools/s5/stream_dump.py and the player check
    // each call's argc against it).
    struct M { int type, slot; const char* name; int argc; };
    static const M methods[] = {
        {0, 2, "Release", 0}, {0, 4, "GetAdapterCount", 0},
        {0, 16, "CreateDevice", 6}, {1, 2, "Release", 0}, {1, 17, "Present", 4}, {1, 41, "BeginScene", 0},
        {1, 42, "EndScene", 0}, {1, 43, "Clear", 6}, {1, 47, "SetViewport", 1}, {1, 57, "SetRenderState", 2},
        {1, 65, "SetTexture", 2}, {1, 69, "SetSamplerState", 3}, {1, 75, "SetScissorRect", 1},
        {1, 83, "DrawPrimitiveUP", 4}, {1, 86, "CreateVertexDeclaration", 2}, {1, 87, "SetVertexDeclaration", 1},
        {1, 91, "CreateVertexShader", 2}, {1, 92, "SetVertexShader", 1}, {1, 94, "SetVertexShaderConstantF", 3},
        {1, 100, "SetStreamSource", 4}, {1, 106, "CreatePixelShader", 2}, {1, 107, "SetPixelShader", 1},
        {1, 109, "SetPixelShaderConstantF", 3}, {2, 18, "GetSurfaceLevel", 2}, {5, 13, "LockRect", 3},
        {5, 14, "UnlockRect", 0}, {1, 18, "GetBackBuffer", 4}, {1, 23, "CreateTexture", 8},
        {1, 26, "CreateVertexBuffer", 6}, {1, 29, "CreateDepthStencilSurface", 8}, {1, 37, "SetRenderTarget", 2},
        {1, 39, "SetDepthStencilSurface", 1}, {1, 81, "DrawPrimitive", 3}, {7, 11, "Lock", 4}, {7, 12, "Unlock", 0},
        {1, 38, "GetRenderTarget", 2}, {1, 40, "GetDepthStencilSurface", 1}};
    const std::string schema = variant == "schema" ? "sa2-d3d9-stream/v9" : SA2S_SCHEMA;
    std::string json = "{\"schema\": \"" + schema + "\", \"writer\": \"host/player/synth_stream.cpp\", "
                       "\"meta\": {\"build\": \"synthetic\", \"note\": \"a test stream; no game data\", \"variant\": \"" +
                       variant + "\"}, \"object_base\": 52428800, \"object_stride\": 16, \"interfaces\": [";
    for (uint32_t t = 0; t < 16; t++) json += std::string(t ? ", " : "") + "\"" + names::type_name(t) + "\"";
    json += "], \"methods\": [";
    for (size_t i = 0; i < sizeof methods / sizeof methods[0]; i++)
        json += std::string(i ? ", " : "") + "[" + std::to_string(methods[i].type) + ", " +
                std::to_string(methods[i].slot) + ", \"" + methods[i].name + "\", " +
                std::to_string(methods[i].argc) + "]";
    if (variant == "checkpoint")
        json += ", [1, 82, \"DrawIndexedPrimitive\", 6], [1, 84, \"DrawIndexedPrimitiveUP\", 8]";
    if (variant == "badlevel") json += ", [2, 19, \"LockRect\", 4]";
    // The D3DX entries' table, row for row as host/sa2_stream.h's `entry` lines
    // give it and the recorder writes it: [entry, name, argc, ops].
    struct E { int entry; const char* name; int argc; const char* ops; };
    static const E entries[] = {
        {1, "D3DXCreateTextureFromFileInMemoryEx", 15, "file 1 2; out 14 4; out 12 28; out 13 1024; file_desc 14"},
        {2, "D3DXCreateTexture", 8, "out 7 4; new_tex 7 1 2 5"},
        {3, "D3DXFilterTexture", 4, "in 1 1024"},
        {4, "D3DXLoadSurfaceFromSurface", 8, "in 1 1024; in 2 16; in 4 1024; in 5 16"},
        {5, "D3DXGetShaderConstantTable", 2, "shader 0; out 1 4"},
        {6, "D3DXCreateCubeTextureFromFileInMemoryEx", 14, "file 1 2; out 13 4; out 11 28; out 12 1024; file_desc 13"},
        {7, "D3DXCreateVolumeTextureFromFileInMemoryEx", 16, "file 1 2; out 15 4; out 13 28; out 14 1024; file_desc 15"},
        {8, "D3DXCreateCubeTexture", 7, "out 6 4; new_cube 6 1 4"},
        {9, "D3DXCreateVolumeTexture", 9, "out 8 4; new_vol 8 1 2 6"},
        {10, "D3DXSaveSurfaceToFileA", 5, "-"},
        {11, "D3DXSaveTextureToFileA", 4, "-"},
        {12, "Direct3DCreate9", 0, "object_eax"}};
    json += "], \"entries\": [";
    for (size_t i = 0; i < sizeof entries / sizeof entries[0]; i++) {
        const std::string name = variant == "entryname" && entries[i].entry == 3 ? "D3DXFilterTextureEx" : entries[i].name;
        json += std::string(i ? ", " : "") + "[" + std::to_string(entries[i].entry) + ", \"" + name + "\", " +
                std::to_string(entries[i].argc) + ", \"" + entries[i].ops + "\"]";
    }
    json += "]}";
    w.header(json);
    using namespace tex;
    const uint32_t D3D9 = OBJ_BASE, DEV = OBJ_BASE + OBJ_STRIDE;
    constexpr uint16_t T_D3D9 = 0, T_DEVICE = 1, T_TEXTURE = 2, T_SURFACE = 5;
    const uint16_t OUT = SA2S_BLOB_OUT, IN = SA2S_BLOB_IN;
    auto addr_blob = [](uint16_t arg, uint32_t a) { return BlobW{SA2S_BLOB_OUT, arg, bytes_of(&a, 1)}; };

    // Direct3DCreate9 at seq 0, as the folded recorder writes it (d3d9's entry,
    // argc 0, the IDirect3D9 in eax), then a query on what it answered.
    if (variant == "entryargc") w.d3dx(SA2S_D3DX_DIRECT3D_CREATE9, {32 /* D3D_SDK_VERSION */}, D3D9, {});
    else if (variant != "create9") w.d3dx(SA2S_D3DX_DIRECT3D_CREATE9, {}, D3D9, {});
    w.call(T_D3D9, 4, D3D9, {}, 1);                                  // GetAdapterCount
    if (variant == "d3dxfirst")
        for (int i = 0; i < 30; i++)
            w.d3dx(SA2S_D3DX_CREATE_TEXTURE, {DEV, 64, 64, 1, 0, FMT_A8R8G8B8, 1, gptr(4)}, 0, {addr_blob(7, new_obj())});

    // IDirect3D9::CreateDevice(0, HAL, hwnd token, HWVP, pp, ppDevice)
    const uint32_t pp[14] = {1280, 720, 21 /* A8R8G8B8 */, 1, 0, 0, 1 /* DISCARD */, 0, 1 /* windowed */,
                             1, 75 /* D24S8 */, 0, 0, 0};
    const uint32_t pp_ptr = gptr(56), dev_out = gptr(4);
    const sa2s_desc_device ddev = {DEV, DEV + OBJ_STRIDE, DEV + 2 * OBJ_STRIDE};
    w.call(T_D3D9, 16, D3D9, {0, 1, 0x7e000000u, 0x40, pp_ptr, dev_out}, 0,
           {BlobW{IN, 4, bytes_of(pp, 14)}, addr_blob(5, DEV), BlobW{SA2S_BLOB_DESC, SA2S_NO_ARG, bytes_of(&ddev, 1)}});
    w.note("synthetic: device created");
    if (variant == "argc") w.call(T_DEVICE, 57, DEV, {7, 0, 0});    // SetRenderState with a third argument

    // Shaders and the declaration.
    const uint32_t vs = new_obj(), ps = new_obj(), decl = new_obj();
    w.call(T_DEVICE, 91, DEV, {gptr(sizeof kVS), gptr(4)}, 0,
           {BlobW{IN, 0, bytes_of(kVS, sizeof kVS / 4)}, addr_blob(1, vs)});
    w.call(T_DEVICE, 106, DEV, {gptr(sizeof kPS), gptr(4)}, 0,
           {BlobW{IN, 0, bytes_of(kPS, sizeof kPS / 4)}, addr_blob(1, ps)});
    const auto db = decl_bytes();
    w.call(T_DEVICE, 86, DEV, {gptr(db.size()), gptr(4)}, 0, {BlobW{IN, 0, db}, addr_blob(1, decl)});
    // The variants' broken objects take addresses outside the stream's own
    // numbering (0x0BAD....), so the rest of the stream is unchanged.
    if (variant == "noendtok")                                      // the vertex shader without its END token
        w.call(T_DEVICE, 91, DEV, {gptr(sizeof kVS - 4), gptr(4)}, 0,
               {BlobW{IN, 0, bytes_of(kVS, sizeof kVS / 4 - 1)}, addr_blob(1, 0x0BAD0020u)});
    // D3DXGetShaderConstantTable(pFunction, ppConstantTable), as the boot calls it
    // for its shaders: an ID3DXConstantTable, no effect on the picture.
    const uint32_t ctab = new_obj();
    w.d3dx(SA2S_D3DX_GET_CONSTANT_TABLE, {gptr(sizeof kPS), gptr(4)}, 0,
           {BlobW{IN, 0, bytes_of(kPS, sizeof kPS / 4)}, addr_blob(1, ctab)});

    // Texture A: a one-texel red/green checkerboard, one DXT1 level in the file.
    std::vector<uint8_t> cb(256 * 256 * 4);
    for (uint32_t y = 0; y < 256; y++)
        for (uint32_t x = 0; x < 256; x++) {
            uint8_t* p = &cb[(size_t(y) * 256 + x) * 4];
            const bool red = (x ^ y) & 1;
            p[0] = 0; p[1] = red ? 0 : 255; p[2] = red ? 255 : 0; p[3] = 255;
        }
    std::vector<std::vector<uint8_t>> la(1);
    encode_level(cb.data(), 256, 256, FMT_DXT1, &la[0]);
    const auto ddsA = dds(256, 256, FMT_DXT1, la);
    // Texture B: DXT5 64x64 with seven levels in the file, each a solid colour.
    std::vector<std::vector<uint8_t>> lb;
    const uint8_t cols[7][3] = {{0, 255, 255}, {255, 255, 0}, {255, 0, 255}, {0, 0, 255},
                                {0, 255, 0}, {255, 0, 0}, {128, 128, 128}};   // BGR
    for (uint32_t lv = 0; lv < 7; lv++) {
        const uint32_t d = level_dim(64, lv);
        lb.emplace_back();
        const auto s = solid(d, d, cols[lv][0], cols[lv][1], cols[lv][2]);
        encode_level(s.data(), d, d, FMT_DXT5, &lb.back());
    }
    const auto ddsB = dds(64, 64, FMT_DXT5, lb);
    // Texture E: DXT1 64x64 quadrants red / green / blue / white, one level.
    std::vector<uint8_t> quads(64 * 64 * 4);
    for (uint32_t y = 0; y < 64; y++)
        for (uint32_t x = 0; x < 64; x++) {
            uint8_t* p = &quads[(size_t(y) * 64 + x) * 4];
            const int q = (y >= 32) * 2 + (x >= 32);
            const uint8_t c[4][3] = {{0, 0, 255}, {0, 255, 0}, {255, 0, 0}, {255, 255, 255}};
            p[0] = c[q][0]; p[1] = c[q][1]; p[2] = c[q][2]; p[3] = 255;
        }
    std::vector<std::vector<uint8_t>> le(1);
    encode_level(quads.data(), 64, 64, FMT_DXT1, &le[0]);
    const auto ddsE = dds(64, 64, FMT_DXT1, le);
    // Texture D: PNG 128x64 RGB, 8-row stripes cycling orange / teal / grey.
    std::vector<uint8_t> rgb(128 * 64 * 3);
    for (uint32_t y = 0; y < 64; y++)
        for (uint32_t x = 0; x < 128; x++) {
            const uint8_t c[3][3] = {{255, 128, 0}, {0, 128, 128}, {90, 90, 90}};   // RGB
            const int s = (y / 8) % 3;
            std::memcpy(&rgb[(size_t(y) * 128 + x) * 3], c[s], 3);
        }
    const auto pngD = png_of(128, 64, rgb, false);

    // The recording device's row for a load, as the recorder copies it into the
    // DESC blob (sa2s_desc_file_texture).
    auto file_desc = [](uint32_t tex_addr, const std::vector<uint8_t>& file, uint32_t mip_levels) {
        sa2s_desc_file_texture d{};
        d.object = tex_addr;
        d.kind = 0;
        d.depth = d.file_depth = 1;
        d.dims_from = 1;                                   // SA2_TEXP_STATED
        DdsInfo dds_info;
        PngInfo png_info;
        if (parse_dds(file.data(), file.size(), &dds_info)) {
            d.container = 1;
            d.file_width = dds_info.w; d.file_height = dds_info.h; d.file_levels = dds_info.levels;
            d.file_format = dds_info.fmt;
            d.format_from = 1;
        } else if (png_header(file.data(), file.size(), &png_info)) {
            d.container = 2;
            d.file_width = png_info.w; d.file_height = png_info.h; d.file_levels = 1; d.file_format = png_info.fmt;
            d.format_from = 2;                             // SA2_TEXP_DERIVED
            d.png_bit_depth = png_info.bit_depth; d.png_colour_type = png_info.colour_type;
            d.png_trns = png_info.trns;
        }
        d.width = d.file_width; d.height = d.file_height; d.format = d.file_format;
        if (mip_levels == D3DX_DEFAULT || !mip_levels) {
            d.levels = d.file_levels > 1 ? d.file_levels : full_chain(d.width, d.height);
            d.levels_from = d.file_levels > 1 ? 1 : 4;     // STATED, or API
        } else {
            d.levels = mip_levels;
            d.levels_from = 3;                             // CALLER
        }
        d.level0_bytes = level_bytes(d.width, d.height, d.format);
        for (uint32_t lv = 0; lv < d.levels; lv++)
            d.chain_bytes += level_bytes(level_dim(d.width, lv), level_dim(d.height, lv), d.format);
        return d;
    };
    auto from_memory = [&](const std::vector<uint8_t>& file, uint32_t mip_levels, uint32_t tex_addr) {
        const uint32_t src = gptr(file.size()), out = gptr(4);
        const sa2s_desc_file_texture d = file_desc(tex_addr, file, mip_levels);
        w.d3dx(SA2S_D3DX_TEXTURE_FROM_MEMORY,
               {DEV, src, uint32_t(file.size()), D3DX_DEFAULT, D3DX_DEFAULT, mip_levels, 0, 0, 1 /* MANAGED */,
                D3DX_DEFAULT, D3DX_DEFAULT, 0, 0, 0, out},
               0, {BlobW{SA2S_BLOB_FILE, 1, file}, addr_blob(14, tex_addr),
                   BlobW{SA2S_BLOB_DESC, SA2S_NO_ARG, bytes_of(&d, 1)}});
    };
    const uint32_t texA = new_obj(), texB = new_obj(), texE = new_obj(), texD = new_obj();
    from_memory(ddsA, D3DX_DEFAULT, texA);
    from_memory(ddsB, D3DX_DEFAULT, texB);
    from_memory(ddsE, 1, texE);
    from_memory(pngD, D3DX_DEFAULT, texD);

    // A lock through a level surface, as the boot's D3DXCreateTexture targets
    // are filled: GetSurfaceLevel, Surface::LockRect, Surface::UnlockRect with the
    // recorder's LOCK and LOCKED blobs. `rect` is null for a whole-level lock;
    // `locked` is the rows the recorder copies from pBits at the level's pitch.
    auto surface_lock = [&](uint32_t tex_addr, uint32_t surf, uint32_t fmt, uint32_t lw, uint32_t lh,
                            const int32_t* rect, uint32_t flags, const std::vector<uint8_t>& locked,
                            bool get_level) {
        if (get_level) w.call(T_TEXTURE, 18, tex_addr, {0, gptr(4)}, 0, {addr_blob(1, surf)});
        const uint32_t pitch = level_pitch(lw, fmt), store = gptr(level_bytes(lw, lh, fmt)), out = gptr(8);
        const uint32_t locked_rect[2] = {pitch, store};
        std::vector<BlobW> lock_blobs = {BlobW{OUT, 0, bytes_of(locked_rect, 2)}};
        const uint32_t prect = rect ? gptr(16) : 0;
        if (rect) lock_blobs.push_back(BlobW{IN, 1, bytes_of(rect, 4)});
        w.call(T_SURFACE, 13, surf, {out, prect, flags}, 0, lock_blobs);
        sa2s_lock_info lock{};
        lock.object = surf; lock.container = tex_addr; lock.level = 0; lock.bits = store; lock.pitch = pitch;
        lock.rows = uint32_t(locked.size() / pitch); lock.bytes = uint32_t(locked.size()); lock.format = fmt;
        lock.width = lw; lock.height = lh; lock.flags = flags;
        if (rect) { lock.has_rect = 1; std::memcpy(lock.rect, rect, 16); }
        else { lock.rect[2] = int32_t(lw); lock.rect[3] = int32_t(lh); }
        w.call(T_SURFACE, 14, surf, {}, 0,
               {BlobW{SA2S_BLOB_LOCK, SA2S_NO_ARG, bytes_of(&lock, 1)}, BlobW{SA2S_BLOB_LOCKED, SA2S_NO_ARG, locked}});
    };

    // Texture C: D3DXCreateTexture 64x64 A8R8G8B8 with a full chain, level 0
    // filled through a lock (rows of constant colour, blue rising with y), then
    // D3DXFilterTexture.
    const uint32_t texC = new_obj(), surfC = new_obj();
    w.d3dx(SA2S_D3DX_CREATE_TEXTURE, {DEV, 64, 64, D3DX_DEFAULT, 0, FMT_A8R8G8B8, 1, gptr(4)}, 0,
           {addr_blob(7, texC)});
    std::vector<uint8_t> fillC(64 * 64 * 4);
    for (uint32_t y = 0; y < 64; y++)
        for (uint32_t x = 0; x < 64; x++) {
            uint8_t* p = &fillC[(size_t(y) * 64 + x) * 4];
            p[0] = uint8_t(y * 4); p[1] = 64; p[2] = 32; p[3] = 255;
        }
    if (variant == "badrect") {
        const int32_t inverted[4] = {40, 0, 8, 64};           // left > right
        surface_lock(texC, surfC, FMT_A8R8G8B8, 64, 64, inverted, 0x2000, fillC, true);
    } else {
        surface_lock(texC, surfC, FMT_A8R8G8B8, 64, 64, nullptr, 0x2000, fillC, true);
    }
    w.d3dx(SA2S_D3DX_FILTER_TEXTURE, {texC, 0, 0, D3DX_DEFAULT}, 0, {});
    if (variant == "nolock") w.call(T_SURFACE, 14, surfC, {});      // UnlockRect: no LockRect, no LOCK blob
    // D3DXCreateCubeTexture(pDevice, Size, MipLevels, Usage, Format, Pool, ppCubeTexture)
    if (variant == "cube")
        w.d3dx(SA2S_D3DX_CREATE_CUBE_TEXTURE, {DEV, 64, 1, 0, FMT_A8R8G8B8, 1, gptr(4)}, 0, {addr_blob(6, new_obj())});

    // Frames. The first draws without BeginScene, as the boot's loading loop
    // does; later frames are inside a scene. Frame 5 comes three periods after
    // frame 4 in virtual time, so the video tool must hold frame 4 on screen.
    std::vector<Expect> ex;
    const uint32_t CLEAR_TARGET_Z = 0x1 | 0x2;
    uint64_t vt = 0;
    auto set_state = [&]() {
        const float vp[6] = {0, 0, 0, 0, 0, 1};
        uint32_t vpu[6] = {0, 0, 1280, 720, fbits(vp[4]), fbits(vp[5])};
        w.call(T_DEVICE, 47, DEV, {gptr(24)}, 0, {BlobW{IN, 0, bytes_of(vpu, 6)}});
        w.call(T_DEVICE, 87, DEV, {decl});
        w.call(T_DEVICE, 92, DEV, {vs});
        w.call(T_DEVICE, 107, DEV, {ps});
        const float one[4] = {1, 1, 1, 1};
        w.call(T_DEVICE, 109, DEV, {0, gptr(16), 1}, 0, {BlobW{IN, 1, bytes_of(one, 4)}});
        const float vsc[8] = {0.5f, 0.25f, 0, 1, 1, 2, 3, 4};
        w.call(T_DEVICE, 94, DEV, {0, gptr(32), 2}, 0, {BlobW{IN, 1, bytes_of(vsc, 8)}});
        w.call(T_DEVICE, 57, DEV, {22 /* CULLMODE */, 1 /* NONE */});
        w.call(T_DEVICE, 57, DEV, {7 /* ZENABLE */, 0});
        w.call(T_DEVICE, 57, DEV, {27 /* ALPHABLENDENABLE */, 0});
        w.call(T_DEVICE, 69, DEV, {0, 5 /* MAGFILTER */, 1 /* POINT */});
        w.call(T_DEVICE, 69, DEV, {0, 6 /* MINFILTER */, 2 /* LINEAR */});
        w.call(T_DEVICE, 69, DEV, {0, 7 /* MIPFILTER */, 2 /* LINEAR */});
        const int32_t sc[4] = {0, 0, 1280, 720};
        w.call(T_DEVICE, 75, DEV, {gptr(16)}, 0, {BlobW{IN, 0, bytes_of(sc, 4)}});
    };
    auto draw = [&](uint32_t tex_addr, float x0, float y0, float x1, float y1) {
        w.call(T_DEVICE, 65, DEV, {0, tex_addr});
        const auto q = quad(x0, y0, x1, y1);
        w.call(T_DEVICE, 83, DEV, {6 /* TRIANGLEFAN */, 2, gptr(q.size()), 24}, 0, {BlobW{IN, 2, q}});
        w.call(T_DEVICE, 100, DEV, {0, 0, 0, 0});
    };
    auto clear = [&](uint32_t argb) {
        w.call(T_DEVICE, 43, DEV, {0, 0, CLEAR_TARGET_Z, argb, fbits(1.0f), 0});
    };
    auto present = [&](uint32_t n, uint64_t at) {
        w.call(T_DEVICE, 17, DEV, {0, 0, 0, 0});
        // The recorder writes a call's notes right after the call, before the
        // Present's FRAME: frame 5 carries one there.
        if (n == 5) w.note("record: a note between a Present and its FRAME");
        w.frame(at, n, n * 3);
        if (n == 3) w.note("record: a note after a FRAME");
    };
    auto expect_clear_only = [&](uint32_t frame, uint32_t x, uint32_t y, uint8_t b, uint8_t g, uint8_t r) {
        ex.push_back({frame, x, y, 0, 3, b, g, r, 0, "clear colour, no draw here"});
    };

    // Frame 1: opaque black clear, texture E drawn with no BeginScene.
    set_state();
    clear(0xFF000000u);
    if (variant == "checkpoint") {
        // Empty UP calls return before draws_frame_ increments. They still
        // occupy their original draw ordinal; the final call is refused.
        w.call(T_DEVICE, 83, DEV, {4, 0, 0, 24}, 0, {BlobW{IN, 2, {}}});
        w.call(T_DEVICE, 84, DEV, {4, 0, 0, 0, 0, 101, 0, 24}, 0, {BlobW{IN, 4, {}}, BlobW{IN, 6, {}}});
        w.call(T_DEVICE, 81, DEV, {4, 0, 0});
        w.call(T_DEVICE, 82, DEV, {4, 0, 0, 0, 0, 0});
        w.call(T_DEVICE, 83, DEV, {6, 2, 0x0BAD0000u, 24});
    }
    // Each of these is refused before any host call, and changes no pixel.
    if (variant == "badtex") w.call(T_DEVICE, 65, DEV, {1, 0x0BAD0000u});           // SetTexture(1, no texture)
    if (variant == "noblob") w.call(T_DEVICE, 47, DEV, {gptr(24)});                 // SetViewport, no IN blob
    if (variant == "clearnull") w.call(T_DEVICE, 43, DEV, {1, 0, CLEAR_TARGET_Z, 0xFF000000u, fbits(1.0f), 0});
    if (variant == "badsurf") w.call(T_DEVICE, 18, DEV, {0, 0, 0, gptr(4)}, 0, {addr_blob(3, 0x0BAD0010u)});
    draw(texE, 64, 64, 192, 192);                 // 64x64 texels over 128x128 pixels
    present(1, vt);
    ex.push_back({1, 96, 96, 0, 0, 0, 0, 0, 0, "stage 0: only the clear (black)"});
    ex.push_back({1, 96, 96, 1, 1, 255, 255, 255, 0, "stage 1: white placeholder texture"});
    ex.push_back({1, 96, 96, 2, 3, 0, 0, 255, 8, "texture E red quadrant, DXT1 from DDS"});
    ex.push_back({1, 160, 160, 2, 3, 255, 255, 255, 8, "texture E white quadrant"});
    ex.push_back({1, 160, 96, 2, 3, 0, 255, 0, 8, "texture E green quadrant"});
    expect_clear_only(1, 400, 400, 0, 0, 0);

    for (uint32_t n = 2; n <= 6; n++) {
        vt += (n == 5 ? 3 : 1) * 16666u;
        w.call(T_DEVICE, 41, DEV, {});        // BeginScene
        set_state();
        clear(0xFF203040u);
        draw(texA, 0, 0, 256, 256);            // 1:1, level 0
        draw(texA, 300, 0, 364, 64);           // 256 texels over 64 pixels: level 2 sampled
        draw(texB, 400, 0, 464, 64);           // 1:1, level 0 yellow
        draw(texB, 500, 0, 516, 16);           // 64 texels over 16 pixels: level 2 magenta
        draw(texC, 600, 0, 664, 64);           // lock-filled, 1:1
        draw(texD, 700, 0, 828, 64);           // PNG, 1:1
        draw(texE, 900, 0, 964 + 8 * n, 64);   // moves by 8 pixels a frame
        w.call(T_DEVICE, 42, DEV, {});        // EndScene
        present(n, vt);
    }
    for (uint32_t n = 2; n <= 6; n++) {
        ex.push_back({n, 1000, 300, 0, 3, 0x40, 0x30, 0x20, 0, "clear colour 0xFF203040"});
        ex.push_back({n, 128, 128, 0, 0, 0x40, 0x30, 0x20, 0, "stage 0: clear only"});
        ex.push_back({n, 128, 128, 1, 1, 255, 255, 255, 0, "stage 1: white placeholder"});
        ex.push_back({n, 332, 32, 1, 1, 255, 255, 255, 0, "stage 1: white placeholder"});
    }
    // Stage 2 and 3: file textures. Texture A at 1:1 is the checkerboard: its
    // pixels are pure red or green, so check one pixel for either with the
    // green channel being 0 or 255 (tolerance covers one DXT1 step).
    ex.push_back({2, 432, 32, 2, 3, 0, 255, 255, 8, "texture B level 0 yellow (DXT5, file mips)"});
    ex.push_back({2, 508, 8, 2, 3, 255, 0, 255, 8, "texture B level 2 magenta, from the file's own mips"});
    // Texture A minified 4x: with generated mips (stage 3) the sample is the
    // olive mean of red and green; with the file's one level (stage 2) it is
    // whatever texel the sampler hits.
    ex.push_back({2, 332, 32, 3, 3, 0, 128, 128, 12, "texture A level 2, generated by the box filter"});
    ex.push_back({3, 332, 32, 3, 3, 0, 128, 128, 12, "texture A level 2, generated by the box filter"});
    ex.push_back({2, 632, 32, 3, 3, 128, 64, 32, 4, "texture C, lock upload (row 32: blue 128)"});
    ex.push_back({2, 632, 32, 2, 2, 255, 255, 255, 0, "stage 2: texture C is still a placeholder"});
    ex.push_back({2, 760, 4, 3, 3, 0, 128, 255, 2, "texture D PNG stripe 0 orange"});
    ex.push_back({2, 760, 12, 3, 3, 128, 128, 0, 2, "texture D PNG stripe 1 teal"});
    ex.push_back({2, 760, 20, 3, 3, 90, 90, 90, 2, "texture D PNG stripe 2 grey"});
    ex.push_back({2, 760, 20, 2, 2, 255, 255, 255, 0, "stage 2: PNG is still a placeholder"});
    // Texture E's quad grows 8 pixels a frame: its right edge is at 980 in frame
    // 2 and 1012 in frame 6, so pixel (1005, 16) is the clear colour in the one
    // and the green quadrant in the other.
    ex.push_back({2, 1005, 16, 0, 3, 0x40, 0x30, 0x20, 0, "frame 2: right of texture E's quad"});
    ex.push_back({6, 1005, 16, 2, 3, 0, 255, 0, 8, "frame 6: texture E's quad reaches here (green)"});
    ex.push_back({6, 1005, 16, 1, 1, 255, 255, 255, 0, "frame 6, stage 1: the placeholder reaches here"});
    ex.push_back({6, 920, 16, 2, 3, 0, 0, 255, 8, "frame 6: texture E red quadrant"});

    // Frame 7: what Metal Harbor adds to the boot. First a render-to-texture
    // pass: a 128x128 render-target texture and depth surface, cleared red, with
    // texture E drawn over its right half, then the back buffer and automatic
    // depth put back (answered by GetBackBuffer's object, as the game does).
    // Then a vertex buffer, filled through Lock/Unlock, drawn with
    // DrawPrimitive, the render-target texture drawn 1:1, and a D3DX copy of
    // the back buffer into a 1280x720 X8R8G8B8 texture, which frame 8 shows at
    // a quarter of its size.
    constexpr uint16_t T_VB = 7;
    const uint32_t BACKBUFFER = DEV + OBJ_STRIDE, AUTODEPTH = DEV + 2 * OBJ_STRIDE;
    vt += 16666u;
    w.call(T_DEVICE, 41, DEV, {});
    const uint32_t rtt = new_obj(), rts = new_obj(), rtds = new_obj();
    w.call(T_DEVICE, 23, DEV, {128, 128, 1, 1 /* RENDERTARGET */, FMT_A8R8G8B8, 0 /* DEFAULT */, gptr(4), 0}, 0,
           {addr_blob(6, rtt)});
    w.call(T_TEXTURE, 18, rtt, {0, gptr(4)}, 0, {addr_blob(1, rts)});
    w.call(T_DEVICE, 29, DEV, {128, 128, 75 /* D24S8 */, 0, 0, 0, gptr(4), 0}, 0, {addr_blob(6, rtds)});
    if (variant == "ownupload") {                  // the depth surface filled through a lock, as a level would be
        const uint32_t pitch = 128 * 4, bits = gptr(pitch * 128);
        const uint32_t locked_rect[2] = {pitch, bits};
        w.call(T_SURFACE, 13, rtds, {gptr(8), 0, 0}, 0, {BlobW{OUT, 0, bytes_of(locked_rect, 2)}});
        sa2s_lock_info lock{};
        lock.object = rtds; lock.bits = bits; lock.pitch = pitch; lock.rows = 128; lock.bytes = pitch * 128;
        lock.format = 75; lock.width = 128; lock.height = 128; lock.rect[2] = 128; lock.rect[3] = 128;
        w.call(T_SURFACE, 14, rtds, {}, 0,
               {BlobW{SA2S_BLOB_LOCK, SA2S_NO_ARG, bytes_of(&lock, 1)},
                BlobW{SA2S_BLOB_LOCKED, SA2S_NO_ARG, std::vector<uint8_t>(size_t(pitch) * 128, 0)}});
    }
    w.call(T_DEVICE, 18, DEV, {0, 0, 0, gptr(4)}, 0, {addr_blob(3, BACKBUFFER)});   // GetBackBuffer
    // The targets asked for as Metal Harbor asks before each pass: render target
    // 0 is the back buffer, 1 to 3 are refused (D3DERR_NOTAVAILABLE, the guest's
    // variable left 0), and the depth surface is the automatic one.
    constexpr uint32_t kNotAvailable = 0x8876086Au;
    if (variant == "refused") w.call(T_DEVICE, 38, DEV, {0, gptr(4)}, kNotAvailable, {addr_blob(1, 0)});
    else w.call(T_DEVICE, 38, DEV, {0, gptr(4)}, 0, {addr_blob(1, BACKBUFFER)});    // GetRenderTarget
    for (uint32_t i = 1; i < 4; i++) w.call(T_DEVICE, 38, DEV, {i, gptr(4)}, kNotAvailable, {addr_blob(1, 0)});
    w.call(T_DEVICE, 40, DEV, {gptr(4)}, 0, {addr_blob(0, AUTODEPTH)});             // GetDepthStencilSurface
    w.call(T_DEVICE, 37, DEV, {0, rts});
    w.call(T_DEVICE, 39, DEV, {rtds});
    // Asked again with the texture's level and its depth bound.
    w.call(T_DEVICE, 38, DEV, {0, gptr(4)}, 0, {addr_blob(1, variant == "badtarget" ? BACKBUFFER : rts)});
    w.call(T_DEVICE, 40, DEV, {gptr(4)}, 0, {addr_blob(0, rtds)});
    if (variant == "bbrtt") w.call(T_DEVICE, 18, DEV, {0, 0, 0, gptr(4)}, 0, {addr_blob(3, rts)});
    set_state();
    const float rt_vp[6] = {0, 0, 128, 128, 0, 1};
    const uint32_t rt_vpu[6] = {0, 0, 128, 128, fbits(rt_vp[4]), fbits(rt_vp[5])};
    w.call(T_DEVICE, 47, DEV, {gptr(24)}, 0, {BlobW{IN, 0, bytes_of(rt_vpu, 6)}});
    const int32_t rt_sc[4] = {0, 0, 128, 128};
    w.call(T_DEVICE, 75, DEV, {gptr(16)}, 0, {BlobW{IN, 0, bytes_of(rt_sc, 4)}});
    clear(0xFFFF0000u);
    w.call(T_DEVICE, 65, DEV, {0, texE});
    const auto half = quad_ndc(0.0f, 1.0f, 1.0f, -1.0f);            // the right half of the target
    w.call(T_DEVICE, 83, DEV, {6, 2, gptr(half.size()), 24}, 0, {BlobW{IN, 2, half}});
    w.call(T_DEVICE, 37, DEV, {0, BACKBUFFER});
    w.call(T_DEVICE, 39, DEV, {AUTODEPTH});
    set_state();
    clear(0xFF203040u);
    const uint32_t vb = new_obj(), vbp = gptr(96);
    w.call(T_DEVICE, 26, DEV, {96, 8 /* WRITEONLY */, 0, 1 /* MANAGED */, gptr(4), 0}, 0, {addr_blob(4, vb)});
    w.call(T_VB, 11, vb, {0, 96, gptr(4), 0}, 0, {BlobW{OUT, 2, bytes_of(&vbp, 1)}});
    const auto vq = quad(100, 300, 228, 428);                        // texture E magnified 2x
    sa2s_lock_info vl{};
    vl.object = vb; vl.bits = vbp; vl.rows = 1; vl.bytes = uint32_t(vq.size());
    w.call(T_VB, 12, vb, {}, 0,
           {BlobW{SA2S_BLOB_LOCK, SA2S_NO_ARG, bytes_of(&vl, 1)}, BlobW{SA2S_BLOB_LOCKED, SA2S_NO_ARG, vq}});
    w.call(T_DEVICE, 65, DEV, {0, texE});
    w.call(T_DEVICE, 100, DEV, {0, vb, 0, 24});
    w.call(T_DEVICE, 81, DEV, {6 /* TRIANGLEFAN */, 0, 2});
    w.call(T_DEVICE, 100, DEV, {0, 0, 0, 0});
    draw(rtt, 300, 300, 428, 428);                                    // the render target, 1:1
    const uint32_t cap = new_obj(), caps = new_obj();
    w.d3dx(SA2S_D3DX_CREATE_TEXTURE, {DEV, 1280, 720, 1, 0, FMT_X8R8G8B8, 1, gptr(4)}, 0, {addr_blob(7, cap)});
    w.call(T_TEXTURE, 18, cap, {0, gptr(4)}, 0, {addr_blob(1, caps)});
    w.d3dx(SA2S_D3DX_LOAD_SURFACE, {caps, 0, 0, BACKBUFFER, 0, 0, D3DX_DEFAULT, 0}, 0, {});
    w.call(T_DEVICE, 42, DEV, {});
    present(7, vt);
    // Frame 8: the copy, a quarter size, at (800, 400).
    vt += 16666u;
    w.call(T_DEVICE, 41, DEV, {});
    set_state();
    clear(0xFF203040u);
    draw(cap, 800, 400, 1120, 580);
    w.call(T_DEVICE, 42, DEV, {});
    present(8, vt);
    ex.push_back({7, 132, 332, 0, 0, 0x40, 0x30, 0x20, 0, "frame 7, stage 0: clear only"});
    ex.push_back({7, 132, 332, 1, 1, 255, 255, 255, 0, "frame 7, stage 1: the vertex-buffer quad, placeholder"});
    ex.push_back({7, 132, 332, 2, 3, 0, 0, 255, 8, "frame 7: vertex buffer + DrawPrimitive, texture E red"});
    ex.push_back({7, 330, 364, 1, 2, 255, 255, 255, 0, "frame 7, stages 1-2: the render target is a placeholder"});
    ex.push_back({7, 330, 364, 3, 3, 0, 0, 255, 0, "frame 7: render target's left half, cleared red"});
    ex.push_back({7, 412, 330, 3, 3, 0, 255, 0, 8, "frame 7: render target's right half, texture E green"});
    ex.push_back({7, 412, 400, 3, 3, 255, 255, 255, 8, "frame 7: render target's right half, texture E white"});
    ex.push_back({8, 833, 483, 3, 3, 0, 0, 255, 12, "frame 8: the back-buffer copy shows frame 7's red"});
    ex.push_back({8, 1000, 300, 0, 3, 0x40, 0x30, 0x20, 0, "frame 8: clear colour"});
    ex.push_back({8, 833, 483, 1, 2, 255, 255, 255, 0, "frame 8, stages 1-2: the copy is a placeholder"});

    // Frame 9: coverage (textures F to K, see the file header).
    // F: DXT1 64x64 full chain, level 0 a one-texel red/blue checkerboard written
    // through a lock as DXT1 blocks, then D3DXFilterTexture. Level 1 is the box
    // mean of red and blue, (128, 0, 128), which DXT1 stores as 565 (16, 0, 16)
    // and decodes to (132, 0, 132); level 2 keeps it. Drawn 64 texels over 16
    // pixels, the sampler reads level 2.
    std::vector<uint8_t> checkF(64 * 64 * 4);
    for (uint32_t y = 0; y < 64; y++)
        for (uint32_t x = 0; x < 64; x++) {
            uint8_t* p = &checkF[(size_t(y) * 64 + x) * 4];
            const bool red = (x ^ y) & 1;
            p[0] = red ? 0 : 255; p[1] = 0; p[2] = red ? 255 : 0; p[3] = 255;
        }
    std::vector<uint8_t> dxtF;
    encode_level(checkF.data(), 64, 64, FMT_DXT1, &dxtF);
    const uint32_t texF = new_obj(), surfF = new_obj();
    w.d3dx(SA2S_D3DX_CREATE_TEXTURE, {DEV, 64, 64, 0, 0, FMT_DXT1, 1, gptr(4)}, 0, {addr_blob(7, texF)});
    surface_lock(texF, surfF, FMT_DXT1, 64, 64, nullptr, 0x2000, dxtF, true);
    w.d3dx(SA2S_D3DX_FILTER_TEXTURE, {texF, 0, 0, D3DX_DEFAULT}, 0, {});
    // G: A8R8G8B8 64x64 one level, filled grey, then a lock of (16,16)-(48,48):
    // the guest writes the rectangle's rows from pBits at the level pitch, so
    // each LOCKED row starts with the rectangle's 32 orange pixels and the rest
    // is what the store held there (grey).
    const uint32_t texG = new_obj(), surfG = new_obj();
    w.d3dx(SA2S_D3DX_CREATE_TEXTURE, {DEV, 64, 64, 1, 0, FMT_A8R8G8B8, 1, gptr(4)}, 0, {addr_blob(7, texG)});
    const auto greyG = solid(64, 64, 80, 80, 80);
    surface_lock(texG, surfG, FMT_A8R8G8B8, 64, 64, nullptr, 0, greyG, true);
    std::vector<uint8_t> rectG(32 * 64 * 4);
    for (uint32_t y = 0; y < 32; y++)
        for (uint32_t x = 0; x < 64; x++) {
            uint8_t* p = &rectG[(size_t(y) * 64 + x) * 4];
            if (x < 32) { p[0] = 0; p[1] = 128; p[2] = 255; } else { p[0] = p[1] = p[2] = 80; }
            p[3] = 255;
        }
    const int32_t rG[4] = {16, 16, 48, 48};
    surface_lock(texG, surfG, FMT_A8R8G8B8, 64, 64, rG, 0, rectG, false);
    if (variant == "badlevel") {                                    // Texture::LockRect(3, pLockedRect, NULL, 0)
        const uint32_t locked_rect[2] = {64 * 4, gptr(64 * 64 * 4)};
        w.call(T_TEXTURE, 19, texG, {3, gptr(8), 0, 0}, 0, {BlobW{OUT, 1, bytes_of(locked_rect, 2)}});
    }
    // H: PNG RGBA 32x32, green at alpha 128, blended over the clear colour.
    std::vector<uint8_t> rgbaH(32 * 32 * 4);
    for (size_t i = 0; i < rgbaH.size(); i += 4) { rgbaH[i] = 0; rgbaH[i + 1] = 255; rgbaH[i + 2] = 0; rgbaH[i + 3] = 128; }
    const auto pngH = png_of(32, 32, rgbaH, true);
    // I: DDS A8R8G8B8 32x32, left half (200,100,50) right half (10,220,90) BGR.
    std::vector<uint8_t> bgraI(32 * 32 * 4);
    for (uint32_t y = 0; y < 32; y++)
        for (uint32_t x = 0; x < 32; x++) {
            uint8_t* p = &bgraI[(size_t(y) * 32 + x) * 4];
            if (x < 16) { p[0] = 200; p[1] = 100; p[2] = 50; } else { p[0] = 10; p[1] = 220; p[2] = 90; }
            p[3] = 255;
        }
    const auto ddsI = dds_argb(32, 32, bgraI);
    // J: DXT3 32x32, colour gradients along both axes, alpha rising along x.
    std::vector<uint8_t> srcJ(32 * 32 * 4);
    for (uint32_t y = 0; y < 32; y++)
        for (uint32_t x = 0; x < 32; x++) {
            uint8_t* p = &srcJ[(size_t(y) * 32 + x) * 4];
            p[0] = uint8_t(30 + y * 6); p[1] = uint8_t(60 + x * 5); p[2] = uint8_t(220 - y * 5); p[3] = uint8_t(x * 8);
        }
    std::vector<std::vector<uint8_t>> lj(1);
    encode_level(srcJ.data(), 32, 32, FMT_DXT3, &lj[0]);
    const auto ddsJ = dds(32, 32, FMT_DXT3, lj);
    // K: DXT5 64x64, colour and alpha gradients, one level.
    std::vector<uint8_t> srcK(64 * 64 * 4);
    for (uint32_t y = 0; y < 64; y++)
        for (uint32_t x = 0; x < 64; x++) {
            uint8_t* p = &srcK[(size_t(y) * 64 + x) * 4];
            p[0] = uint8_t(x * 4); p[1] = uint8_t(255 - y * 4); p[2] = uint8_t((x + y) * 2); p[3] = uint8_t(y * 4);
        }
    std::vector<std::vector<uint8_t>> lk(1);
    encode_level(srcK.data(), 64, 64, FMT_DXT5, &lk[0]);
    const auto ddsK = dds(64, 64, FMT_DXT5, lk);
    const uint32_t texH = new_obj(), texI = new_obj(), texJ = new_obj(), texK = new_obj(), surfI = new_obj();
    from_memory(pngH, D3DX_DEFAULT, texH);
    from_memory(ddsI, 1, texI);
    from_memory(ddsJ, 1, texJ);
    from_memory(ddsK, 1, texK);
    // I's store in the recording device is empty (a file texture's pixels are
    // never in guest memory), so a READONLY lock reads zeros; nothing may be uploaded.
    surface_lock(texI, surfI, FMT_A8R8G8B8, 32, 32, nullptr, 0x10 /* READONLY */,
                 std::vector<uint8_t>(32 * 32 * 4, 0), true);

    vt += 16666u;
    w.call(T_DEVICE, 41, DEV, {});
    set_state();
    clear(0xFF203040u);
    draw(texF, 0, 300, 16, 316);                  // 64 texels over 16 pixels: level 2
    draw(texG, 100, 300, 164, 364);               // 1:1
    w.call(T_DEVICE, 57, DEV, {27 /* ALPHABLENDENABLE */, 1});
    w.call(T_DEVICE, 57, DEV, {19 /* SRCBLEND */, 5 /* SRCALPHA */});
    w.call(T_DEVICE, 57, DEV, {20 /* DESTBLEND */, 6 /* INVSRCALPHA */});
    draw(texH, 200, 300, 232, 332);               // 1:1, blended
    w.call(T_DEVICE, 57, DEV, {27 /* ALPHABLENDENABLE */, 0});
    draw(texI, 300, 300, 332, 332);               // 1:1
    // J and K vary texel to texel, so each pixel must sample one texel's centre:
    // DXVK maps Direct3D 9's integer pixel centres onto Vulkan's (a viewport
    // offset of 0.5, d3d9_device.cpp:6844), which puts an unshifted 1:1 quad's
    // samples on texel edges; the usual half-pixel shift puts them on centres.
    draw(texJ, 399.5f, 299.5f, 431.5f, 331.5f);   // 1:1, texel centres
    draw(texK, 499.5f, 299.5f, 563.5f, 363.5f);   // 1:1, texel centres
    w.call(T_DEVICE, 42, DEV, {});
    present(9, vt);
    ex.push_back({9, 132, 332, 0, 0, 0x40, 0x30, 0x20, 0, "frame 9, stage 0: clear only"});
    for (const auto& [x, y] : std::vector<std::pair<uint32_t, uint32_t>>{{8, 308}, {132, 332}, {316, 316}, {416, 316}, {532, 332}})
        ex.push_back({9, x, y, 1, 1, 255, 255, 255, 0, "frame 9, stage 1: placeholder"});
    ex.push_back({9, 216, 316, 1, 2, 255, 255, 255, 0, "frame 9, stages 1-2: texture H is a white placeholder, blended opaque"});
    ex.push_back({9, 8, 308, 2, 2, 255, 255, 255, 0, "frame 9, stage 2: texture F is a placeholder"});
    ex.push_back({9, 132, 332, 2, 2, 255, 255, 255, 0, "frame 9, stage 2: texture G is a placeholder"});
    ex.push_back({9, 8, 308, 3, 3, 132, 0, 132, 4, "texture F: DXT1 lock, D3DXFilterTexture, level 2 decodes (132,0,132)"});
    ex.push_back({9, 132, 332, 3, 3, 0, 128, 255, 1, "texture G: the sub-rectangle lock's orange at texel (32,32)"});
    ex.push_back({9, 120, 320, 3, 3, 0, 128, 255, 1, "texture G: orange at texel (20,20), inside the rectangle"});
    ex.push_back({9, 150, 320, 3, 3, 80, 80, 80, 1, "texture G: grey at texel (50,20), right of the rectangle"});
    ex.push_back({9, 104, 304, 3, 3, 80, 80, 80, 1, "texture G: grey at texel (4,4), above the rectangle"});
    ex.push_back({9, 216, 316, 3, 3, 32, 152, 16, 2, "texture H: PNG alpha 128 green blended over the clear colour"});
    ex.push_back({9, 308, 316, 2, 3, 200, 100, 50, 0, "texture I: DDS A8R8G8B8 left half; the READONLY lock uploaded nothing"});
    ex.push_back({9, 324, 316, 2, 3, 10, 220, 90, 0, "texture I: DDS A8R8G8B8 right half"});
    // J and K against the CPU decoder's own decode of the same blocks, on a
    // grid that reaches every 4x4 block and every texel position within one:
    // every second texel of J, every third of K (a texel's colour depends on its
    // index, so a sparse sample misses a wrong palette entry).
    std::vector<uint8_t> decJ, decK;
    decode_level(lj[0].data(), 32, 32, FMT_DXT3, 0, &decJ);
    decode_level(lk[0].data(), 64, 64, FMT_DXT5, 0, &decK);
    for (uint32_t ty = 0; ty < 32; ty += 2)
        for (uint32_t tx = 0; tx < 32; tx += 2) {
            const uint8_t* q = &decJ[(size_t(ty) * 32 + tx) * 4];
            ex.push_back({9, 400 + tx, 300 + ty, 2, 3, q[0], q[1], q[2], 2,
                          "texture J: DXT3 decoded by DXVK equals the CPU decode, texel (" + std::to_string(tx) +
                              "," + std::to_string(ty) + ")"});
        }
    for (uint32_t ty = 0; ty < 64; ty += 3)
        for (uint32_t tx = 0; tx < 64; tx += 3) {
            const uint8_t* q = &decK[(size_t(ty) * 64 + tx) * 4];
            ex.push_back({9, 500 + tx, 300 + ty, 2, 3, q[0], q[1], q[2], 2,
                          "texture K: DXT5 decoded by DXVK equals the CPU decode, texel (" + std::to_string(tx) +
                              "," + std::to_string(ty) + ")"});
        }
    // The two Save calls: D3DXSaveSurfaceToFileA(pDestFile, DestFormat,
    // pSrcSurface, pSrcPalette, pSrcRect) and D3DXSaveTextureToFileA(pDestFile,
    // DestFormat, pSrcTexture, pSrcPalette); 3 is D3DXIFF_PNG.
    w.d3dx(SA2S_D3DX_SAVE_SURFACE_TO_FILE, {gptr(32), 3, surfC, 0, 0}, 0, {});
    w.d3dx(SA2S_D3DX_SAVE_TEXTURE_TO_FILE, {gptr(32), 3, texA, 0}, 0, {});
    // A second Direct3DCreate9: another IDirect3D9, queried and released.
    const uint32_t d3d9b = new_obj();
    w.d3dx(SA2S_D3DX_DIRECT3D_CREATE9, {}, d3d9b, {});
    w.call(T_D3D9, 4, d3d9b, {}, 1);                                 // GetAdapterCount
    w.call(T_D3D9, 2, d3d9b, {}, 0);                                 // Release
    w.call(T_DEVICE, 2, DEV, {}, 0);             // Device::Release
    w.end();
    if (variant == "afterend") w.note("after END");
    std::fclose(f);

    FILE* e = std::fopen(argv[2], "w");
    if (!e) { std::perror(argv[2]); return 2; }
    std::fprintf(e, "frame\tx\ty\tstage_lo\tstage_hi\tb\tg\tr\ttol\twhat\n");
    for (const Expect& x : ex)
        std::fprintf(e, "%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%u\t%s\n", x.frame, x.x, x.y, x.stage_lo, x.stage_hi,
                     x.b, x.g, x.r, x.tol, x.what.c_str());
    std::fclose(e);
    std::printf("wrote %u records to %s and %zu expectations to %s%s%s\n", w.records(), argv[1], ex.size(), argv[2],
                variant.empty() ? "" : "; variant ", variant.c_str());
    return 0;
}
