/* M7's synthetic streams: one Direct3D 9 call stream per target method, made through the
 * real recording device (runtime/sa2_d3d9.c, runtime/sa2_guest_call.c) and the real
 * recorder (host/sa2_present_rec.c, linked with the presentation build's -Wl,-wrap list,
 * tools/s5/objcheck.py WRAPS), with no game.
 *
 *   usage: rec_harness_m7 --target NAME [--omit] [--variant other-pp] --out DIR
 *          rec_harness_m7 --list
 *
 * The user's ruling of 2026-10-04 (Documentation/decisions/2026-10-04-S5-user-rulings-2.md):
 * "Methods no recorded run reaches are exercised by a synthetic stream per method, so the
 * check can fail." Each target belongs to a family, a scenario of a 64x64 device drawing
 * one frame that a person can predict pixel by pixel; the target is called in it, and
 * --omit leaves the target's call out (with the calls that need an object only the target
 * makes), so the omit stream is the falsifier: the same predicate must fail on it.
 *
 * The recorder's output goes where SA2_REC_TRACE and SA2_REC_COUNTS say
 * (tools/s5/m7_synthetic.py sets DIR/stream.sa2s and DIR/rec.counts.json). A counts file
 * named counts.json is refused: tools/s4/m7_inputs.py registers any directory a receipt
 * cites that holds a recorder counts.json as a recorded run, which would turn a synthetic
 * case into a recorded run of the game.
 *
 * --out writes DIR/answers.json (each call the scenario made, by name, with its EAX) and
 * DIR/expect.tsv: the pixels `sa2_player --expect` checks, the positive scenario's
 * picture in both modes, so an omitted call with an effect on the picture fails there.
 *
 * Families (the target names are M7's, Documentation/receipts/d3d9-methods.json):
 *   indexed   a vertex buffer of two quads and an index buffer selecting the left one,
 *             drawn with DrawIndexedPrimitive: left half green, right half the clear blue;
 *             and a witness, a yellow corner through DrawPrimitiveUP, so that every stream
 *             of the family draws whichever call it leaves out
 *   freq      instancing as the game draws it (sonic2app's four SetStreamSourceFreq sites,
 *             0x867c41..0x867d57): stream 0 INDEXEDDATA|2 holding one indexed quad,
 *             stream 1 INSTANCEDATA|1 holding a per-instance offset (TEXCOORD0), one
 *             DrawIndexedPrimitive, then both streams reset to 1. Two instances: the left
 *             half green and the same quad moved right; without the frequencies D3D9 reads
 *             stream 1 per vertex and draws one sheared quad, so the right half's pixels fail
 *   vsbool vsint psbool psint   a full-screen quad whose colour a shader takes from a
 *             bool constant (b0 set: green, else red) or an int loop (i0 = 4 iterations
 *             of a quarter of green; unset: black)
 *   query     the full-screen green quad, plus Device QueryInterface, TestCooperativeLevel
 *             and the two D3DX Save calls on a D3DX texture
 *   ctabquery the green quad, plus an authored constant table's GetConstantByName and
 *             GetConstantDesc
 *   ctabset   an authored constant table's five setters, each driving the one
 *             pixel-shader register the pixel shows
 *   reset     Device Reset with the creation parameters between two frames;
 *             --variant other-pp resets to 32x32
 *   cube      (F7) a cube texture made the way the target makes it -- the device's
 *             CreateCubeTexture as the game's render-target creator calls it (16, one level,
 *             RENDERTARGET, A8R8G8B8, DEFAULT), D3DXCreateCubeTexture (4, a complete chain by
 *             D3DX's rule for MipLevels 0) or D3DXCreateCubeTextureFromFileInMemoryEx (an
 *             authored DXT1 cube DDS, six 4x4 faces, with its D3DXIMAGE_INFO) -- and the
 *             created object's answers: its GetLevelCount and every level's GetLevelDesc; for
 *             GetCubeMapSurface, as the game uses a face (0x00868F56, then SetRenderTarget):
 *             each face's surface made the render target, cleared and released, +X asked for
 *             again (the same interface), and the back buffer made the render target again;
 *             then the full-screen green quad, which no target reaches
 *   volume    (F8) a volume texture made by D3DXCreateVolumeTexture (4x4x2, two levels) or
 *             D3DXCreateVolumeTextureFromFileInMemoryEx (an authored DXT1 volume DDS, 4x4x2,
 *             with its D3DXIMAGE_INFO), its GetLevelCount and every level's GetLevelDesc;
 *             then the green quad
 *
 * The cube and volume families have no effect on the picture (a host-2 draw that samples a
 * cube texture is unmapped by design, host/d3d9gpu/d9g_sampler.cpp), so their falsifier is
 * the recorder's count. What a count cannot show, each case checks in the harness: the
 * answers the real device gave (an HRESULT, the object written, a level count, every field
 * of a level's description, a face's own surface and the same one when asked again, the
 * D3DXIMAGE_INFO, and the device's own record of the blob it read, the upload's readback,
 * whose sha256 the test holds to the authored blob's). Each check is written to
 * DIR/answers.json ("checks": [what, at, want, got]); the target's own checks are held in
 * full (a cube level description's Type is the D3DRTYPE_SURFACE Direct3D 9 writes), the
 * other calls' to what the target made. Any check that fails makes the harness exit 3 after
 * every file is written, so tools/s4/m7_inputs.py reads the case's positive stream as not
 * held ("the fixture that recorded it exited 3"). The calls the cases make are all on M7's
 * list (Documentation/receipts/d3d9-methods.json `called`), so the device's closed world
 * answers each: no cube or volume texture is released (neither Release is on the list).
 *
 * The shaders are m7_shaders.h, assembled by tools/s5/m7_synthetic.py with
 * host/d3d9gpu/test/sm3_asm.py into the build directory. The stand-in runtime (guest
 * memory, function table, CPU state) is tools/s5/tests/rec_harness.c's, included here as
 * tools/s5/tests/ctab_recorder_harness.c includes it.
 */
#define main rec_harness_original_main
#include "rec_harness.c"
#undef main
#include "sa2_guest_call.h"
#include "m7_shaders.h"

/* ------------------------------------------------------------ addresses */

#define M7_SH      (SCRATCH + 0x8000u)      /* shader blobs, 0x200 bytes each */
#define M7_VERTS   (SCRATCH + 0xA000u)
#define M7_CONST   (SCRATCH + 0xA800u)
#define M7_IID     (SCRATCH + 0xA900u)
#define M7_PATH    (SCRATCH + 0xAA00u)
#define M7_OUTS    (SCRATCH + 0xB000u)      /* one dword per out-parameter */
#define M7_NAME    (SCRATCH + 0xB100u)
#define M7_DESC    (SCRATCH + 0xB200u)
#define M7_PP2     (SCRATCH + 0xB300u)
#define M7_MATRIX  (SCRATCH + 0xB400u)
#define M7_CTAB    (SCRATCH + 0xC000u)
#define M7_DDS     (SCRATCH + 0xD000u)      /* an authored DDS blob */
#define M7_INFO    (SCRATCH + 0xD800u)      /* D3DXIMAGE_INFO, seven dwords */

#define DXT1  0x31545844u
#define GREEN 0xFF00FF00u
#define RED   0xFFFF0000u
#define BLUE  0xFF0000FFu
#define YELLOW 0xFFFFFF00u
#define F1    0x3F800000u

/* ------------------------------------------------------------ the targets */

typedef struct { const char *name, *family; int prepared; } target;
static const target TARGETS[] = {
    {"IDirect3DDevice9::CreateIndexBuffer", "indexed", 0},
    {"IDirect3DIndexBuffer9::Lock", "indexed", 0},
    {"IDirect3DIndexBuffer9::Unlock", "indexed", 0},
    {"IDirect3DIndexBuffer9::Release", "indexed", 0},
    {"IDirect3DDevice9::DrawIndexedPrimitive", "indexed", 0},
    {"IDirect3DVertexBuffer9::Release", "indexed", 0},
    {"IDirect3DDevice9::SetStreamSourceFreq", "freq", 0},
    {"IDirect3DDevice9::SetVertexShaderConstantB", "vsbool", 0},
    {"IDirect3DDevice9::SetVertexShaderConstantI", "vsint", 0},
    {"IDirect3DDevice9::SetPixelShaderConstantB", "psbool", 0},
    {"IDirect3DDevice9::SetPixelShaderConstantI", "psint", 0},
    {"IDirect3DDevice9::QueryInterface", "query", 0},
    {"IDirect3DDevice9::TestCooperativeLevel", "query", 0},
    {"d3dx9_42!D3DXSaveSurfaceToFileA", "query", 0},
    {"d3dx9_42!D3DXSaveTextureToFileA", "query", 0},
    {"ID3DXConstantTable::GetConstantByName", "ctabquery", 0},
    {"ID3DXConstantTable::GetConstantDesc", "ctabquery", 0},
    {"ID3DXConstantTable::SetBool", "ctabset", 0},
    {"ID3DXConstantTable::SetInt", "ctabset", 0},
    {"ID3DXConstantTable::SetFloat", "ctabset", 0},
    {"ID3DXConstantTable::SetVector", "ctabset", 0},
    {"ID3DXConstantTable::SetMatrix", "ctabset", 0},
    {"IDirect3DDevice9::Reset", "reset", 0},
    {"IDirect3DDevice9::CreateCubeTexture", "cube", 0},
    {"d3dx9_42!D3DXCreateCubeTexture", "cube", 0},
    {"d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx", "cube", 0},
    {"IDirect3DCubeTexture9::GetCubeMapSurface", "cube", 0},
    {"IDirect3DCubeTexture9::GetLevelCount", "cube", 0},
    {"IDirect3DCubeTexture9::GetLevelDesc", "cube", 0},
    {"d3dx9_42!D3DXCreateVolumeTexture", "volume", 0},
    {"d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx", "volume", 0},
    {"IDirect3DVolumeTexture9::GetLevelCount", "volume", 0},
    {"IDirect3DVolumeTexture9::GetLevelDesc", "volume", 0},
};
#define NTARGETS (sizeof TARGETS / sizeof TARGETS[0])

static const target *g_target;
static int g_omit;
static const char *g_variant = "";

/* Whether the scenario makes the call `name`: every call but the target's under --omit. */
static int keep(const char *name) { return !(g_omit && !strcmp(g_target->name, name)); }

/* ------------------------------------------------------------ calls, by name */

#define M7_MAX 160
static const char *g_called[M7_MAX];
static uint32_t g_eax[M7_MAX];
static int g_ncalled;

static void called(const char *name, uint32_t eax) {
    if (g_ncalled < M7_MAX) { g_called[g_ncalled] = name; g_eax[g_ncalled] = eax; g_ncalled++; }
}
#define NCALL(name, self, slot, ...) \
    (called(name, CALL(self, slot, __VA_ARGS__)), g_eax[g_ncalled - 1])
#define NCALL0(name, self, slot) (called(name, CALL0(self, slot)), g_eax[g_ncalled - 1])

/* ------------------------------------------------------------ behavioural checks */

/* What the scenario checked of the device's answers: `what`, at `at` (a level or face, -1
 * for none), the value Direct3D 9 or D3DX gives and the value the device gave. A failed
 * check, or more checks than there is room to write, makes the harness exit 3 once every
 * file is written. */
#define M7_CHECKS 160
static struct { const char *what; int at; uint32_t want, got; } g_check[M7_CHECKS];
static int g_nchecks, g_failed;

static void check(const char *what, int at, uint32_t want, uint32_t got) {
    if (want != got) g_failed++;
    if (g_nchecks < M7_CHECKS) {
        g_check[g_nchecks].what = what; g_check[g_nchecks].at = at;
        g_check[g_nchecks].want = want; g_check[g_nchecks].got = got;
        g_nchecks++;
    } else {
        g_failed++;
    }
}
/* Whether the scenario's target is `name`: its own checks are held in full. */
static int target_is(const char *name) { return !strcmp(g_target->name, name); }

/* ------------------------------------------------------------ the device and its frame */

static const uint32_t PP64[14] = {64u, 64u, 21u, 1u, 0u, 0u, 1u, 0u, 1u, 1u, 75u, 0u, 0u, 0u};

static void device64(void) {
    put_words(PP, PP64, 14);
    const uint32_t d3d9 = sa2_d3d9_make_d3d9(CALLER);
    called("d3d9!Direct3DCreate9", d3d9);
    NCALL("IDirect3D9::CreateDevice", d3d9, 16u, 0u, 1u, 0x7e000000u, 0x40u, PP, OUTS + 0u);
    g_dev = peek32(OUTS + 0u);
}

static uint32_t shader(int pixel, const uint32_t *words, uint32_t n, unsigned k) {
    put_words(M7_SH + 0x200u * k, words, n);
    NCALL(pixel ? "IDirect3DDevice9::CreatePixelShader" : "IDirect3DDevice9::CreateVertexShader",
          g_dev, pixel ? 106u : 91u, M7_SH + 0x200u * k, M7_OUTS + 4u * k);
    return peek32(M7_OUTS + 4u * k);
}
#define M7SHADER(pixel, arr, k) shader(pixel, arr, (uint32_t)(sizeof arr / 4u), k)

/* POSITION float3 at 0, COLOR0 D3DCOLOR at 12, then D3DDECL_END: 16-byte vertices. */
static uint32_t declaration(void) {
    const uint32_t decl[6] = {0x00000000u, 0x00000002u, 0x000C0000u, 0x000A0004u,
                              0x000000FFu, 0x00000011u};
    put_words(DECL, decl, 6);
    NCALL("IDirect3DDevice9::CreateVertexDeclaration", g_dev, 86u, DECL, M7_OUTS + 0x40u);
    return peek32(M7_OUTS + 0x40u);
}

static void vertex(uint32_t at, float x, float y, uint32_t colour) {
    uint32_t w[4];
    const float z = 0.5f;
    memcpy(&w[0], &x, 4); memcpy(&w[1], &y, 4); memcpy(&w[2], &z, 4);
    w[3] = colour;
    put_words(at, w, 4);
}
/* A quad from (x0, y1) to (x1, y0) in clip space, as a strip: top-left, top-right,
 * bottom-left, bottom-right; quad() spans the whole height. */
static void quad_rect(uint32_t at, float x0, float x1, float y0, float y1, uint32_t colour) {
    vertex(at, x0, y1, colour);
    vertex(at + 16u, x1, y1, colour);
    vertex(at + 32u, x0, y0, colour);
    vertex(at + 48u, x1, y0, colour);
}
static void quad(uint32_t at, float x0, float x1, uint32_t colour) { quad_rect(at, x0, x1, -1.0f, 1.0f, colour); }

static void begin_frame(uint32_t decl, uint32_t vs, uint32_t ps) {
    NCALL("IDirect3DDevice9::SetRenderState", g_dev, 57u, 22u, 1u);    /* CULLMODE NONE */
    NCALL("IDirect3DDevice9::SetRenderState", g_dev, 57u, 7u, 0u);     /* ZENABLE FALSE */
    const uint32_t vp[6] = {0u, 0u, 64u, 64u, 0x00000000u, F1};
    put_words(VIEWPORT, vp, 6);
    NCALL("IDirect3DDevice9::SetViewport", g_dev, 47u, VIEWPORT);
    NCALL("IDirect3DDevice9::SetVertexDeclaration", g_dev, 87u, decl);
    NCALL("IDirect3DDevice9::SetVertexShader", g_dev, 92u, vs);
    NCALL("IDirect3DDevice9::SetPixelShader", g_dev, 107u, ps);
    NCALL0("IDirect3DDevice9::BeginScene", g_dev, 41u);
    NCALL("IDirect3DDevice9::Clear", g_dev, 43u, 0u, 0u, 3u, BLUE, F1, 0u);
}
static void end_frame(void) {
    NCALL0("IDirect3DDevice9::EndScene", g_dev, 42u);
    NCALL("IDirect3DDevice9::Present", g_dev, 17u, 0u, 0u, 0u, 0u);
}
/* The full-screen quad through DrawPrimitiveUP, in `colour`. */
static void draw_screen(uint32_t colour) {
    quad(M7_VERTS, -1.0f, 1.0f, colour);
    NCALL("IDirect3DDevice9::DrawPrimitiveUP", g_dev, 83u, 5u, 2u, M7_VERTS, 16u);
}

/* ------------------------------------------------------------ expectations */

static FILE *g_expect;
static void expect(unsigned frame, unsigned x, unsigned y, uint32_t colour, const char *what) {
    fprintf(g_expect, "%u\t%u\t%u\t3\t3\t%u\t%u\t%u\t2\t%s %s\n", frame, x, y, colour & 0xFFu,
            (colour >> 8) & 0xFFu, (colour >> 16) & 0xFFu, g_target->name, what);
}

/* ------------------------------------------------------------ the families */

static void family_indexed(void) {
    const uint32_t vs = M7SHADER(0, M7_VS_COLOR, 0), ps = M7SHADER(1, M7_PS_COLOR, 1);
    const uint32_t decl = declaration();
    /* Eight vertices: the left quad green (0..3), the right quad red (4..7). */
    NCALL("IDirect3DDevice9::CreateVertexBuffer", g_dev, 26u, 8u * 16u, 8u, 0u, 1u, M7_OUTS + 0x48u, 0u);
    const uint32_t vb = peek32(M7_OUTS + 0x48u);
    NCALL("IDirect3DVertexBuffer9::Lock", vb, 11u, 0u, 0u, M7_OUTS + 0x4cu, 0u);
    const uint32_t vbits = peek32(M7_OUTS + 0x4cu);
    quad(vbits, -1.0f, 0.0f, GREEN);
    quad(vbits + 64u, 0.0f, 1.0f, RED);
    NCALL0("IDirect3DVertexBuffer9::Unlock", vb, 12u);
    uint32_t ib = 0;
    if (keep("IDirect3DDevice9::CreateIndexBuffer")) {
        NCALL("IDirect3DDevice9::CreateIndexBuffer", g_dev, 27u, 6u * 2u, 8u, 101u, 1u, M7_OUTS + 0x50u, 0u);
        ib = peek32(M7_OUTS + 0x50u);
        if (keep("IDirect3DIndexBuffer9::Lock")) {
            NCALL("IDirect3DIndexBuffer9::Lock", ib, 11u, 0u, 0u, M7_OUTS + 0x54u, 0u);
            const uint32_t ibits = peek32(M7_OUTS + 0x54u);
            const uint16_t idx[6] = {0u, 1u, 2u, 2u, 1u, 3u};
            sa2_poke(ibits, sizeof idx, idx);
            if (keep("IDirect3DIndexBuffer9::Unlock")) NCALL0("IDirect3DIndexBuffer9::Unlock", ib, 12u);
        }
    }
    begin_frame(decl, vs, ps);
    NCALL("IDirect3DDevice9::SetStreamSource", g_dev, 100u, 0u, vb, 0u, 16u);
    if (ib) NCALL("IDirect3DDevice9::SetIndices", g_dev, 104u, ib);
    if (ib && keep("IDirect3DDevice9::DrawIndexedPrimitive"))
        NCALL("IDirect3DDevice9::DrawIndexedPrimitive", g_dev, 82u, 4u, 0u, 0u, 4u, 0u, 2u);
    /* A witness the target cannot reach: a yellow quad in the bottom-right corner through
     * DrawPrimitiveUP, so every stream of the family draws, whichever call it leaves out. */
    quad_rect(M7_VERTS, 0.5f, 1.0f, -1.0f, -0.5f, YELLOW);
    NCALL("IDirect3DDevice9::DrawPrimitiveUP", g_dev, 83u, 5u, 2u, M7_VERTS, 16u);
    end_frame();
    expect(1, 16, 32, GREEN, "the indexed left quad");
    expect(1, 48, 32, BLUE, "the right quad no index names, the clear colour");
    expect(1, 56, 56, YELLOW, "the witness quad");
    if (ib && keep("IDirect3DIndexBuffer9::Release")) NCALL0("IDirect3DIndexBuffer9::Release", ib, 2u);
    if (keep("IDirect3DVertexBuffer9::Release")) NCALL0("IDirect3DVertexBuffer9::Release", vb, 2u);
}

/* Instancing as the game calls it. Stream 0: the left quad (x -1..0), green, four vertices
 * drawn through six indices; stream 1: four TEXCOORD0 float2 offsets, (0,0) (1,0) (0,0) (0,0),
 * so instance 0 draws the quad where it is and instance 1 one half-screen right. Without the
 * frequencies (the omit stream) stream 1 is per-vertex data and four elements are defined: the
 * top-right vertex moves right and the quad shears, leaving the bottom of the right half the
 * clear blue. */
#define D3DSTREAMSOURCE_INDEXEDDATA  0x40000000u
#define D3DSTREAMSOURCE_INSTANCEDATA 0x80000000u
static void family_instanced(void) {
    const uint32_t vs = M7SHADER(0, M7_VS_INSTANCE, 0), ps = M7SHADER(1, M7_PS_COLOR, 1);
    /* POSITION float3 and COLOR0 D3DCOLOR on stream 0 (16 bytes), TEXCOORD0 float2 on stream 1. */
    const uint32_t decl[8] = {0x00000000u, 0x00000002u, 0x000C0000u, 0x000A0004u,
                              0x00000001u, 0x00050001u, 0x000000FFu, 0x00000011u};
    put_words(DECL, decl, 8);
    NCALL("IDirect3DDevice9::CreateVertexDeclaration", g_dev, 86u, DECL, M7_OUTS + 0x40u);
    const uint32_t vdecl = peek32(M7_OUTS + 0x40u);
    NCALL("IDirect3DDevice9::CreateVertexBuffer", g_dev, 26u, 4u * 16u, 8u, 0u, 1u, M7_OUTS + 0x48u, 0u);
    const uint32_t vb = peek32(M7_OUTS + 0x48u);
    NCALL("IDirect3DVertexBuffer9::Lock", vb, 11u, 0u, 0u, M7_OUTS + 0x4cu, 0u);
    quad(peek32(M7_OUTS + 0x4cu), -1.0f, 0.0f, GREEN);
    NCALL0("IDirect3DVertexBuffer9::Unlock", vb, 12u);
    NCALL("IDirect3DDevice9::CreateVertexBuffer", g_dev, 26u, 4u * 8u, 8u, 0u, 1u, M7_OUTS + 0x58u, 0u);
    const uint32_t ivb = peek32(M7_OUTS + 0x58u);
    NCALL("IDirect3DVertexBuffer9::Lock", ivb, 11u, 0u, 0u, M7_OUTS + 0x5cu, 0u);
    const uint32_t offsets[8] = {0u, 0u, F1, 0u, 0u, 0u, 0u, 0u};
    put_words(peek32(M7_OUTS + 0x5cu), offsets, 8);
    NCALL0("IDirect3DVertexBuffer9::Unlock", ivb, 12u);
    NCALL("IDirect3DDevice9::CreateIndexBuffer", g_dev, 27u, 6u * 2u, 8u, 101u, 1u, M7_OUTS + 0x50u, 0u);
    const uint32_t ib = peek32(M7_OUTS + 0x50u);
    NCALL("IDirect3DIndexBuffer9::Lock", ib, 11u, 0u, 0u, M7_OUTS + 0x54u, 0u);
    const uint16_t idx[6] = {0u, 1u, 2u, 2u, 1u, 3u};
    sa2_poke(peek32(M7_OUTS + 0x54u), sizeof idx, idx);
    NCALL0("IDirect3DIndexBuffer9::Unlock", ib, 12u);
    begin_frame(vdecl, vs, ps);
    const int freq = keep("IDirect3DDevice9::SetStreamSourceFreq");
    NCALL("IDirect3DDevice9::SetStreamSource", g_dev, 100u, 0u, vb, 0u, 16u);
    if (freq) NCALL("IDirect3DDevice9::SetStreamSourceFreq", g_dev, 102u, 0u, D3DSTREAMSOURCE_INDEXEDDATA | 2u);
    NCALL("IDirect3DDevice9::SetStreamSource", g_dev, 100u, 1u, ivb, 0u, 8u);
    if (freq) NCALL("IDirect3DDevice9::SetStreamSourceFreq", g_dev, 102u, 1u, D3DSTREAMSOURCE_INSTANCEDATA | 1u);
    NCALL("IDirect3DDevice9::SetIndices", g_dev, 104u, ib);
    NCALL("IDirect3DDevice9::DrawIndexedPrimitive", g_dev, 82u, 4u, 0u, 0u, 4u, 0u, 2u);
    if (freq) {
        NCALL("IDirect3DDevice9::SetStreamSourceFreq", g_dev, 102u, 0u, 1u);
        NCALL("IDirect3DDevice9::SetStreamSourceFreq", g_dev, 102u, 1u, 1u);
    }
    end_frame();
    expect(1, 16, 32, GREEN, "instance 0, the quad where stream 0 puts it");
    expect(1, 56, 40, GREEN, "instance 1, moved right by its stream 1 offset");
    expect(1, 40, 56, GREEN, "instance 1's lower half, which the sheared single draw leaves blue");
    NCALL0("IDirect3DIndexBuffer9::Release", ib, 2u);
    NCALL0("IDirect3DVertexBuffer9::Release", ivb, 2u);
    NCALL0("IDirect3DVertexBuffer9::Release", vb, 2u);
}

/* A full-screen quad whose colour one shader takes from a bool or int constant. */
static void family_constant(int pixel, int is_bool) {
    const uint32_t vs = pixel ? M7SHADER(0, M7_VS_COLOR, 0) : is_bool ? M7SHADER(0, M7_VS_BOOL, 0)
                                                                   : M7SHADER(0, M7_VS_INT, 0);
    const uint32_t ps = !pixel ? M7SHADER(1, M7_PS_COLOR, 1) : is_bool ? M7SHADER(1, M7_PS_BOOL, 1)
                                                                    : M7SHADER(1, M7_PS_INT, 1);
    const uint32_t decl = declaration();
    begin_frame(decl, vs, ps);
    const char *name = pixel ? (is_bool ? "IDirect3DDevice9::SetPixelShaderConstantB"
                                        : "IDirect3DDevice9::SetPixelShaderConstantI")
                             : (is_bool ? "IDirect3DDevice9::SetVertexShaderConstantB"
                                        : "IDirect3DDevice9::SetVertexShaderConstantI");
    if (is_bool) put_words(M7_CONST, (const uint32_t[]){1u}, 1);
    else put_words(M7_CONST, (const uint32_t[]){4u, 0u, 1u, 0u}, 4);
    const uint32_t slot = pixel ? (is_bool ? 113u : 111u) : (is_bool ? 98u : 96u);
    if (keep(name)) NCALL(name, g_dev, slot, 0u, M7_CONST, 1u);
    draw_screen(0xFF808080u);            /* the shader's constant decides the colour, not this */
    end_frame();
    expect(1, 32, 32, GREEN, is_bool ? "b0 set: the shader's green" : "i0 = 4 quarters of green");
    expect(1, 8, 56, GREEN, "the same quad, another corner");
}

static void family_query(void) {
    const uint32_t vs = M7SHADER(0, M7_VS_COLOR, 0), ps = M7SHADER(1, M7_PS_COLOR, 1);
    const uint32_t decl = declaration();
    /* IID_IDirect3DDevice9 {D0223B96-BF7A-43fd-92BD-A43B0D82B9EB}: the device answers
     * E_NOINTERFACE and writes 0, as it does for every interface. */
    put_words(M7_IID, (const uint32_t[]){0xD0223B96u, 0x43FDBF7Au, 0x3BA4BD92u, 0xEBB9820Du}, 4);
    if (keep("IDirect3DDevice9::QueryInterface"))
        NCALL("IDirect3DDevice9::QueryInterface", g_dev, 0u, M7_IID, M7_OUTS + 0x60u);
    if (keep("IDirect3DDevice9::TestCooperativeLevel"))
        NCALL0("IDirect3DDevice9::TestCooperativeLevel", g_dev, 3u);
    /* A 4x4 A8R8G8B8 D3DX texture, its level 0, and both Save calls. */
    const uint32_t r = sa2_d3d9_d3dx_create_texture(g_dev, 4u, 4u, 1u, 0u, 21u, 1u, M7_OUTS + 0x64u, CALLER);
    called("d3dx9_42!D3DXCreateTexture", r);
    note_dx(r);
    const uint32_t tex = peek32(M7_OUTS + 0x64u);
    NCALL("IDirect3DTexture9::GetSurfaceLevel", tex, 18u, 0u, M7_OUTS + 0x68u);
    const uint32_t surf = peek32(M7_OUTS + 0x68u);
    const char path[] = "C:\\SA2\\m7-save.dds";
    sa2_poke(M7_PATH, (uint32_t)sizeof path, path);
    if (keep("d3dx9_42!D3DXSaveTextureToFileA")) {
        const uint32_t e = sa2_d3d9_d3dx_save_texture_to_file((const uint32_t[]){M7_PATH, 4u, tex, 0u}, CALLER);
        called("d3dx9_42!D3DXSaveTextureToFileA", e);
        note_dx(e);
    }
    if (keep("d3dx9_42!D3DXSaveSurfaceToFileA")) {
        const uint32_t e = sa2_d3d9_d3dx_save_surface_to_file((const uint32_t[]){M7_PATH, 4u, surf, 0u, 0u},
                                                              CALLER);
        called("d3dx9_42!D3DXSaveSurfaceToFileA", e);
        note_dx(e);
    }
    NCALL0("IDirect3DSurface9::Release", surf, 2u);
    NCALL0("IDirect3DTexture9::Release", tex, 2u);
    begin_frame(decl, vs, ps);
    draw_screen(GREEN);
    end_frame();
    expect(1, 32, 32, GREEN, "the full-screen quad");
}

/* An authored constant table, as tools/s5/tests/ctab_recorder_harness.c makes it: six root
 * constants s (c7, float), v (c11, vector), i (i2), b (b3), mr (c20, 2x3 rows), mc (c24,
 * 2x3 columns), in a pixel shader's CTAB comment handed to D3DXGetShaderConstantTable;
 * the bytecode is overwritten once the table is made, as a guest frees it. */
static uint32_t ctab_table(void) {
    uint8_t b[1024] = {0};
    uint32_t used = 148;
    static const char *names[6] = {"s", "v", "i", "b", "mr", "mc"};
    static const uint16_t sets[6] = {2, 2, 1, 0, 2, 2}, indices[6] = {7, 11, 2, 3, 20, 24};
    static const uint16_t counts[6] = {1, 1, 1, 1, 2, 3}, classes[6] = {0, 1, 0, 0, 2, 3};
    static const uint16_t types[6] = {3, 3, 2, 1, 3, 3}, rows[6] = {1, 1, 1, 1, 2, 2};
    static const uint16_t cols[6] = {1, 3, 1, 1, 3, 3};
    for (unsigned i = 0; i < 6; i++) {
        uint8_t t[16] = {0};
        memcpy(t, &classes[i], 2); memcpy(t + 2, &types[i], 2); memcpy(t + 4, &rows[i], 2);
        memcpy(t + 6, &cols[i], 2);
        const uint16_t one = 1;
        memcpy(t + 8, &one, 2);
        const uint32_t ty = used;
        memcpy(b + used, t, 16); used += 16;
        const uint32_t nm = used;
        memcpy(b + used, names[i], strlen(names[i]) + 1u); used += (uint32_t)strlen(names[i]) + 1u;
        uint8_t *info = b + 28 + 20 * i;
        memcpy(info, &nm, 4); memcpy(info + 4, &sets[i], 2); memcpy(info + 6, &indices[i], 2);
        memcpy(info + 8, &counts[i], 2); memcpy(info + 12, &ty, 4);
    }
    const uint32_t creator = used;
    memcpy(b + used, "authored m7", 12); used += 12;
    const uint32_t target_at = used;
    memcpy(b + used, "ps_3_0", 7); used += 7;
    const uint32_t ver = 0xffff0300u;
    const uint32_t head[7] = {28, creator, ver, 6, 28, 0, target_at};
    memcpy(b, head, 28);
    const uint32_t words = (used + 3u) / 4u, code = M7_CTAB;
    pk32(code, ver);
    pk32(code + 4u, ((words + 1u) << 16) | 0xfffeu);
    pk32(code + 8u, 0x42415443u);
    sa2_poke(code + 12u, words * 4u, b);
    pk32(code + 12u + words * 4u, 0xffffu);
    uint32_t a[2] = {code, M7_OUTS + 0x70u};
    const uint32_t e = sa2_d3d9_d3dx_get_shader_constant_table(a, CALLER);
    called("d3dx9_42!D3DXGetShaderConstantTable", e);
    note_dx(e);
    const uint32_t table = peek32(M7_OUTS + 0x70u);
    sa2_guest_fill(code, 0xee, 16u + words * 4u);
    return table;
}
static uint32_t ctab_constant(uint32_t table, const char *name) {
    sa2_poke(M7_NAME, (uint32_t)strlen(name) + 1u, name);
    return NCALL("ID3DXConstantTable::GetConstantByName", table, 9u, 0u, M7_NAME);
}

static void family_ctabquery(void) {
    const uint32_t vs = M7SHADER(0, M7_VS_COLOR, 0), ps = M7SHADER(1, M7_PS_COLOR, 1);
    const uint32_t decl = declaration();
    const uint32_t table = ctab_table();
    if (keep("ID3DXConstantTable::GetConstantByName")) {
        const uint32_t h = ctab_constant(table, "s");
        if (keep("ID3DXConstantTable::GetConstantDesc"))
            NCALL("ID3DXConstantTable::GetConstantDesc", table, 6u, h, M7_DESC, M7_DESC + 0x40u);
    }
    NCALL0("ID3DXConstantTable::Release", table, 2u);
    begin_frame(decl, vs, ps);
    draw_screen(GREEN);
    end_frame();
    expect(1, 32, 32, GREEN, "the full-screen quad");
}

/* The outer setters nest through the guest device (sa2_d3d9.c table_set): the device's
 * Set*ShaderConstant records the recorder writes carry the value; the pixel shows the one
 * register the setter writes. */
static void family_ctabset(void) {
    const char *t = g_target->name;
    const uint32_t vs = M7SHADER(0, M7_VS_COLOR, 0);
    const int is_bool = !strcmp(t, "ID3DXConstantTable::SetBool"), is_int = !strcmp(t, "ID3DXConstantTable::SetInt");
    const int is_float = !strcmp(t, "ID3DXConstantTable::SetFloat"), is_vector = !strcmp(t, "ID3DXConstantTable::SetVector");
    const uint32_t ps = is_bool ? M7SHADER(1, M7_PS_CTAB_BOOL, 1) : is_int ? M7SHADER(1, M7_PS_CTAB_INT, 1)
                      : is_float ? M7SHADER(1, M7_PS_CTAB_FLOAT, 1) : is_vector ? M7SHADER(1, M7_PS_CTAB_VECTOR, 1)
                      : M7SHADER(1, M7_PS_CTAB_MATRIX, 1);
    const uint32_t decl = declaration();
    const uint32_t table = ctab_table();
    const uint32_t h = ctab_constant(table, is_bool ? "b" : is_int ? "i" : is_float ? "s" : is_vector ? "v" : "mr");
    begin_frame(decl, vs, ps);
    put_words(M7_CONST, (const uint32_t[]){0u, F1, 0u, F1}, 4);
    const uint32_t matrix[16] = {F1, F1, F1, F1, 0u, F1, 0u, F1, 0u, 0u, 0u, 0u, 0u, 0u, 0u, 0u};
    put_words(M7_MATRIX, matrix, 16);
    const uint32_t slot = is_bool ? 13u : is_int ? 15u : is_float ? 17u : is_vector ? 19u : 21u;
    const uint32_t input = is_bool ? 1u : is_int ? 4u : is_float ? F1 : is_vector ? M7_CONST : M7_MATRIX;
    if (keep(t)) NCALL(t, table, slot, g_dev, h, input);
    draw_screen(0xFF808080u);
    end_frame();
    expect(1, 32, 32, is_float ? 0xFFFFFFFFu : GREEN,
           is_float ? "c7.x = 1: white" : "the register the setter wrote: green");
    NCALL0("ID3DXConstantTable::Release", table, 2u);
}

static void family_reset(void) {
    const uint32_t vs = M7SHADER(0, M7_VS_COLOR, 0), ps = M7SHADER(1, M7_PS_COLOR, 1);
    const uint32_t decl = declaration();
    begin_frame(decl, vs, ps);
    draw_screen(GREEN);
    end_frame();
    uint32_t pp[14];
    memcpy(pp, PP64, sizeof pp);
    if (!strcmp(g_variant, "other-pp")) pp[0] = pp[1] = 32u;
    put_words(M7_PP2, pp, 14);
    if (keep("IDirect3DDevice9::Reset")) NCALL("IDirect3DDevice9::Reset", g_dev, 16u, M7_PP2);
    begin_frame(decl, vs, ps);
    draw_screen(GREEN);
    end_frame();
    expect(1, 32, 32, GREEN, "before the Reset");
    expect(2, 32, 32, GREEN, "after the Reset to the same parameters");
}

/* ------------------------------------------------------------ cube and volume (F7, F8) */

#define D3DRTYPE_SURFACE 1u
#define D3DRTYPE_VOLUME  2u
#define D3DRTYPE_VOLUMETEXTURE 4u
#define D3DRTYPE_CUBETEXTURE 5u
#define D3DXIFF_DDS 4u
#define POOL_DEFAULT 0u
#define POOL_MANAGED 1u
#define USAGE_RENDERTARGET 1u

static uint32_t mip(uint32_t n, uint32_t level) { return (n >> level) ? (n >> level) : 1u; }

/* The frame no cube or volume target reaches: the full-screen green quad. */
static void witness_frame(void) {
    const uint32_t vs = M7SHADER(0, M7_VS_COLOR, 0), ps = M7SHADER(1, M7_PS_COLOR, 1);
    const uint32_t decl = declaration();
    begin_frame(decl, vs, ps);
    draw_screen(GREEN);
    end_frame();
    expect(1, 32, 32, GREEN, "the full-screen quad, which no target reaches");
    expect(1, 8, 56, GREEN, "the same quad, another corner");
}

/* A DXT1 DDS of 4x4 faces or slices, one level in the file: a cube map with all six faces
 * (dwCaps2 CUBEMAP and every face bit), or a volume of `depth` slices (VOLUME, DEPTH), its
 * caps DDSCAPS_COMPLEX | DDSCAPS_TEXTURE, each 8-byte block a colour pair and indices of its
 * own. Returns the blob's length. */
static uint32_t put_dds_dxt1(uint32_t at, int cube, uint32_t depth) {
    uint32_t h[32] = {0};
    h[0] = 0x20534444u;                              /* "DDS " */
    h[1] = 124u;
    h[2] = 0x00081007u | (cube ? 0u : 0x00800000u);  /* CAPS HEIGHT WIDTH PIXELFORMAT LINEARSIZE [DEPTH] */
    h[3] = 4u; h[4] = 4u; h[5] = 8u;                 /* height, width, one block's bytes */
    h[6] = cube ? 0u : depth;
    h[19] = 32u; h[20] = 4u; h[21] = DXT1;           /* DDPF_FOURCC DXT1 */
    h[27] = 0x1008u;                                 /* DDSCAPS_COMPLEX | DDSCAPS_TEXTURE */
    h[28] = cube ? 0x0000FE00u : 0x00200000u;        /* CUBEMAP and all faces, or VOLUME */
    put_words(at, h, 32);
    const uint32_t blocks = cube ? 6u : depth;
    for (uint32_t i = 0; i < blocks; i++) {
        /* colour0 > colour1 (four-colour mode), a different pair and index word per block */
        const uint32_t pair = ((0xF800u - 0x0800u * i) & 0xFFFFu) | ((0x001Fu + i) << 16);
        put_words(at + 128u + 8u * i, (const uint32_t[]){pair, 0x1B1B1B1Bu ^ (i * 0x01010101u)}, 2);
    }
    return 128u + 8u * blocks;
}

/* D3DXIMAGE_INFO as the device wrote it: Width, Height, Depth, MipLevels, Format,
 * ResourceType, ImageFileFormat, each against the authored file. */
static void check_info(uint32_t depth, uint32_t rtype) {
    const uint32_t want[7] = {4u, 4u, depth, 1u, DXT1, rtype, D3DXIFF_DDS};
    static const char *what[7] = {"D3DXIMAGE_INFO.Width", "D3DXIMAGE_INFO.Height", "D3DXIMAGE_INFO.Depth",
                                  "D3DXIMAGE_INFO.MipLevels", "D3DXIMAGE_INFO.Format",
                                  "D3DXIMAGE_INFO.ResourceType", "D3DXIMAGE_INFO.ImageFileFormat"};
    for (unsigned i = 0; i < 7; i++) check(what[i], -1, want[i], peek32(M7_INFO + 4u * i));
}

/* The device's own record of the blob it read (sa2_d3d9_texsrc, the last row): it read the
 * whole blob as a DDS of this kind, parsed its header and refused nothing, and names the
 * object the call wrote. The row's sha256 goes to answers.json, where the test holds it to
 * the blob's. */
static char g_texsrc_sha[65];
static void check_texsrc(uint32_t kind, uint32_t obj, uint32_t len, uint32_t depth, uint32_t caps2) {
    const uint32_t n = sa2_d3d9_texsrc_count();
    const sa2_d3d9_texsrc *r = n ? sa2_d3d9_texsrc_at(n - 1u) : NULL;
    check("the device's texture-source rows", -1, 1u, r ? 1u : 0u);
    if (!r) return;
    check("texsrc.kind", -1, kind, r->kind);
    check("texsrc.container (DDS)", -1, SA2_TEXC_DDS, r->container);
    check("texsrc.parsed", -1, 1u, r->parsed);
    check("texsrc.refused", -1, 0u, r->refused);
    check("texsrc.obj", -1, obj, r->obj);
    check("texsrc.src_len", -1, len, r->src_len);
    check("texsrc.hashed_bytes", -1, len, r->hashed_bytes);
    check("texsrc.file_w", -1, 4u, r->file_w);
    check("texsrc.file_h", -1, 4u, r->file_h);
    check("texsrc.file_depth", -1, depth, r->file_depth);
    check("texsrc.file_fmt", -1, DXT1, r->file_fmt);
    check("texsrc.file_caps2", -1, caps2, r->file_caps2);
    for (unsigned i = 0; i < 32; i++) snprintf(g_texsrc_sha + 2 * i, 3, "%02x", r->sha256[i]);
}

/* A cube texture's answers: GetLevelCount, and GetLevelDesc of every level, each field of
 * the D3DSURFACE_DESC the device wrote over a buffer filled with 0xEE first. Under the
 * GetLevelDesc target every field is held, its Type to D3DRTYPE_SURFACE (Direct3D 9
 * describes a face's level as a surface); otherwise the fields the creator decided. */
static void cube_answers(uint32_t cube, uint32_t size, uint32_t levels, uint32_t fmt, uint32_t usage,
                         uint32_t pool) {
    if (keep("IDirect3DCubeTexture9::GetLevelCount"))
        check("IDirect3DCubeTexture9::GetLevelCount", -1, levels,
              NCALL0("IDirect3DCubeTexture9::GetLevelCount", cube, 13u));
    if (!keep("IDirect3DCubeTexture9::GetLevelDesc")) return;
    const int full = target_is("IDirect3DCubeTexture9::GetLevelDesc");
    for (uint32_t lv = 0; lv < levels; lv++) {
        sa2_guest_fill(M7_DESC, 0xee, 32u);
        check("IDirect3DCubeTexture9::GetLevelDesc HRESULT", (int)lv, 0u,
              NCALL("IDirect3DCubeTexture9::GetLevelDesc", cube, 17u, lv, M7_DESC));
        check("D3DSURFACE_DESC.Format", (int)lv, fmt, peek32(M7_DESC + 0u));
        check("D3DSURFACE_DESC.Usage", (int)lv, usage, peek32(M7_DESC + 8u));
        check("D3DSURFACE_DESC.Pool", (int)lv, pool, peek32(M7_DESC + 12u));
        check("D3DSURFACE_DESC.Width", (int)lv, mip(size, lv), peek32(M7_DESC + 24u));
        check("D3DSURFACE_DESC.Height", (int)lv, mip(size, lv), peek32(M7_DESC + 28u));
        if (full) {
            check("D3DSURFACE_DESC.Type", (int)lv, D3DRTYPE_SURFACE, peek32(M7_DESC + 4u));
            check("D3DSURFACE_DESC.MultiSampleType", (int)lv, 0u, peek32(M7_DESC + 16u));
            check("D3DSURFACE_DESC.MultiSampleQuality", (int)lv, 0u, peek32(M7_DESC + 20u));
        }
    }
}

/* GetCubeMapSurface's case, as the game's render-target creator uses a cube (0x00869759,
 * then 0x00868F56 and SetRenderTarget): each of the six faces' level 0 asked for, made the
 * render target and cleared to a colour of its own, and released; then +X asked for again,
 * which Direct3D 9 answers with the same interface, and the back buffer made the render
 * target again. Each GetCubeMapSurface takes a reference of the cube's, which the
 * surface's Release gives back. */
static void cube_faces(uint32_t cube) {
    if (!keep("IDirect3DCubeTexture9::GetCubeMapSurface")) return;
    check("IDirect3DDevice9::GetRenderTarget HRESULT", 0, 0u,
          NCALL("IDirect3DDevice9::GetRenderTarget", g_dev, 38u, 0u, M7_OUTS + 0xa0u));
    const uint32_t back = peek32(M7_OUTS + 0xa0u);
    uint32_t surf[6] = {0};
    for (uint32_t face = 0; face < 6u; face++) {
        pk32(M7_OUTS + 0xa4u + 4u * face, 0xEEEEEEEEu);
        check("IDirect3DCubeTexture9::GetCubeMapSurface HRESULT", (int)face, 0u,
              NCALL("IDirect3DCubeTexture9::GetCubeMapSurface", cube, 18u, face, 0u, M7_OUTS + 0xa4u + 4u * face));
        surf[face] = peek32(M7_OUTS + 0xa4u + 4u * face);
        uint32_t fresh = surf[face] && surf[face] != 0xEEEEEEEEu && surf[face] != cube;
        for (uint32_t k = 0; k < face; k++) fresh &= surf[face] != surf[k];
        check("GetCubeMapSurface writes a surface of the face's own", (int)face, 1u, fresh);
        check("IDirect3DDevice9::SetRenderTarget HRESULT", (int)face, 0u,
              NCALL("IDirect3DDevice9::SetRenderTarget", g_dev, 37u, 0u, surf[face]));
        check("IDirect3DDevice9::Clear HRESULT", (int)face, 0u,
              NCALL("IDirect3DDevice9::Clear", g_dev, 43u, 0u, 0u, 1u, 0xFF000000u | (0x2A2A2Au * (face + 1u)), F1, 0u));
        NCALL0("IDirect3DSurface9::Release", surf[face], 2u);
    }
    check("GetCubeMapSurface HRESULT, +X again", 0, 0u,
          NCALL("IDirect3DCubeTexture9::GetCubeMapSurface", cube, 18u, 0u, 0u, M7_OUTS + 0xbcu));
    check("GetCubeMapSurface answers +X level 0 with the same interface", 0, surf[0], peek32(M7_OUTS + 0xbcu));
    NCALL0("IDirect3DSurface9::Release", peek32(M7_OUTS + 0xbcu), 2u);
    check("IDirect3DDevice9::SetRenderTarget HRESULT, the back buffer again", -1, 0u,
          NCALL("IDirect3DDevice9::SetRenderTarget", g_dev, 37u, 0u, back));
    NCALL0("IDirect3DSurface9::Release", back, 2u);
}

static void family_cube(void) {
    if (target_is("d3dx9_42!D3DXCreateCubeTexture")) {
        /* Size 4 and MipLevels 0, which D3DX's rule makes a complete chain: 4, 2, 1. */
        if (!keep("d3dx9_42!D3DXCreateCubeTexture")) { witness_frame(); return; }
        const uint32_t e = sa2_d3d9_d3dx_create_cube_texture(
            (const uint32_t[]){g_dev, 4u, 0u, 0u, 21u, POOL_MANAGED, M7_OUTS + 0x90u}, CALLER);
        called("d3dx9_42!D3DXCreateCubeTexture", e);
        note_dx(e);
        check("D3DXCreateCubeTexture HRESULT", -1, 0u, e);
        const uint32_t cube = peek32(M7_OUTS + 0x90u);
        check("D3DXCreateCubeTexture writes an object", -1, 1u, cube ? 1u : 0u);
        if (cube) cube_answers(cube, 4u, 3u, 21u, 0u, POOL_MANAGED);
    } else if (target_is("d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx")) {
        /* As the game's call at 0x008668e2 makes it: Size D3DX_DEFAULT, Usage 0, Pool
         * MANAGED, both filters D3DX_DEFAULT, no colour key or palette; MipLevels 1 and
         * Format D3DFMT_UNKNOWN (the file's), and a D3DXIMAGE_INFO to read the file back. */
        const uint32_t len = put_dds_dxt1(M7_DDS, 1, 0u);
        if (!keep("d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx")) { witness_frame(); return; }
        sa2_guest_fill(M7_INFO, 0xee, 28u);
        const uint32_t e = sa2_d3d9_d3dx_cube_from_memory(
            (const uint32_t[]){g_dev, M7_DDS, len, D3DX_DEFAULT, 1u, 0u, 0u, POOL_MANAGED, D3DX_DEFAULT,
                               D3DX_DEFAULT, 0u, M7_INFO, 0u, M7_OUTS + 0x94u}, CALLER);
        called("d3dx9_42!D3DXCreateCubeTextureFromFileInMemoryEx", e);
        note_dx(e);
        check("D3DXCreateCubeTextureFromFileInMemoryEx HRESULT", -1, 0u, e);
        const uint32_t cube = peek32(M7_OUTS + 0x94u);
        check("D3DXCreateCubeTextureFromFileInMemoryEx writes an object", -1, 1u, cube ? 1u : 0u);
        check_info(1u, D3DRTYPE_CUBETEXTURE);
        check_texsrc(SA2_TEXK_CUBE, cube, len, 1u, 0x0000FE00u);
        if (cube) cube_answers(cube, 4u, 1u, DXT1, 0u, POOL_MANAGED);
    } else {
        /* The device's own, as the game's render-target creator makes it (0x00869759):
         * EdgeLength 16, Levels 1, D3DUSAGE_RENDERTARGET, A8R8G8B8, D3DPOOL_DEFAULT. */
        uint32_t cube = 0;
        if (keep("IDirect3DDevice9::CreateCubeTexture")) {
            pk32(M7_OUTS + 0x80u, 0xEEEEEEEEu);
            check("IDirect3DDevice9::CreateCubeTexture HRESULT", -1, 0u,
                  NCALL("IDirect3DDevice9::CreateCubeTexture", g_dev, 25u, 16u, 1u, USAGE_RENDERTARGET, 21u,
                        POOL_DEFAULT, M7_OUTS + 0x80u, 0u));
            cube = peek32(M7_OUTS + 0x80u);
            check("CreateCubeTexture writes an object", -1, 1u, cube && cube != 0xEEEEEEEEu ? 1u : 0u);
        }
        if (cube) {
            cube_answers(cube, 16u, 1u, 21u, USAGE_RENDERTARGET, POOL_DEFAULT);
            if (target_is("IDirect3DCubeTexture9::GetCubeMapSurface")) cube_faces(cube);
        }
    }
    witness_frame();
}

/* A volume texture's answers: GetLevelCount, and GetLevelDesc of every level, each field of
 * the D3DVOLUME_DESC (Format, Type, Usage, Pool, Width, Height, Depth) over 0xEE; under the
 * GetLevelDesc target its Type too, the D3DRTYPE_VOLUME Direct3D 9 writes. */
static void volume_answers(uint32_t vol, uint32_t edge, uint32_t depth, uint32_t levels, uint32_t fmt) {
    if (keep("IDirect3DVolumeTexture9::GetLevelCount"))
        check("IDirect3DVolumeTexture9::GetLevelCount", -1, levels,
              NCALL0("IDirect3DVolumeTexture9::GetLevelCount", vol, 13u));
    if (!keep("IDirect3DVolumeTexture9::GetLevelDesc")) return;
    const int full = target_is("IDirect3DVolumeTexture9::GetLevelDesc");
    for (uint32_t lv = 0; lv < levels; lv++) {
        sa2_guest_fill(M7_DESC, 0xee, 28u);
        check("IDirect3DVolumeTexture9::GetLevelDesc HRESULT", (int)lv, 0u,
              NCALL("IDirect3DVolumeTexture9::GetLevelDesc", vol, 17u, lv, M7_DESC));
        check("D3DVOLUME_DESC.Format", (int)lv, fmt, peek32(M7_DESC + 0u));
        check("D3DVOLUME_DESC.Usage", (int)lv, 0u, peek32(M7_DESC + 8u));
        check("D3DVOLUME_DESC.Pool", (int)lv, POOL_MANAGED, peek32(M7_DESC + 12u));
        check("D3DVOLUME_DESC.Width", (int)lv, mip(edge, lv), peek32(M7_DESC + 16u));
        check("D3DVOLUME_DESC.Height", (int)lv, mip(edge, lv), peek32(M7_DESC + 20u));
        check("D3DVOLUME_DESC.Depth", (int)lv, mip(depth, lv), peek32(M7_DESC + 24u));
        if (full) check("D3DVOLUME_DESC.Type", (int)lv, D3DRTYPE_VOLUME, peek32(M7_DESC + 4u));
    }
}

static void family_volume(void) {
    if (target_is("d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx")) {
        /* As the game's call at 0x00866848 makes it: Width, Height and Depth D3DX_DEFAULT
         * (the file's), Usage 0, Pool MANAGED, both filters D3DX_DEFAULT; MipLevels 1,
         * Format D3DFMT_UNKNOWN, and a D3DXIMAGE_INFO. */
        const uint32_t len = put_dds_dxt1(M7_DDS, 0, 2u);
        if (keep("d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx")) {
            sa2_guest_fill(M7_INFO, 0xee, 28u);
            const uint32_t e = sa2_d3d9_d3dx_volume_from_memory(
                (const uint32_t[]){g_dev, M7_DDS, len, D3DX_DEFAULT, D3DX_DEFAULT, D3DX_DEFAULT, 1u, 0u, 0u,
                                   POOL_MANAGED, D3DX_DEFAULT, D3DX_DEFAULT, 0u, M7_INFO, 0u, M7_OUTS + 0x98u},
                CALLER);
            called("d3dx9_42!D3DXCreateVolumeTextureFromFileInMemoryEx", e);
            note_dx(e);
            check("D3DXCreateVolumeTextureFromFileInMemoryEx HRESULT", -1, 0u, e);
            const uint32_t vol = peek32(M7_OUTS + 0x98u);
            check("D3DXCreateVolumeTextureFromFileInMemoryEx writes an object", -1, 1u, vol ? 1u : 0u);
            check_info(2u, D3DRTYPE_VOLUMETEXTURE);
            check_texsrc(SA2_TEXK_VOLUME, vol, len, 2u, 0x00200000u);
            if (vol) volume_answers(vol, 4u, 2u, 1u, DXT1);
        }
    } else if (keep("d3dx9_42!D3DXCreateVolumeTexture")) {
        /* 4x4x2, MipLevels 2, Usage 0, A8R8G8B8, MANAGED: levels 4x4x2 and 2x2x1. */
        const uint32_t e = sa2_d3d9_d3dx_create_volume_texture(
            (const uint32_t[]){g_dev, 4u, 4u, 2u, 2u, 0u, 21u, POOL_MANAGED, M7_OUTS + 0x9cu}, CALLER);
        called("d3dx9_42!D3DXCreateVolumeTexture", e);
        note_dx(e);
        check("D3DXCreateVolumeTexture HRESULT", -1, 0u, e);
        const uint32_t vol = peek32(M7_OUTS + 0x9cu);
        check("D3DXCreateVolumeTexture writes an object", -1, 1u, vol ? 1u : 0u);
        if (vol) volume_answers(vol, 4u, 2u, 2u, 21u);
    }
    witness_frame();
}

/* ------------------------------------------------------------ main */

static const char *json_escape(const char *s, char *out, size_t n) {
    size_t k = 0;
    for (; *s && k + 3 < n; s++) {
        if (*s == '"' || *s == '\\') out[k++] = '\\';
        out[k++] = *s;
    }
    out[k] = 0;
    return out;
}

static int ends_with(const char *s, const char *tail) {
    const size_t a = strlen(s), b = strlen(tail);
    return a >= b && !strcmp(s + a - b, tail);
}

int main(int argc, char **argv) {
    const char *name = NULL, *out = NULL;
    int list = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--target") && i + 1 < argc) name = argv[++i];
        else if (!strcmp(argv[i], "--out") && i + 1 < argc) out = argv[++i];
        else if (!strcmp(argv[i], "--variant") && i + 1 < argc) g_variant = argv[++i];
        else if (!strcmp(argv[i], "--omit")) g_omit = 1;
        else if (!strcmp(argv[i], "--list")) list = 1;
        else { fprintf(stderr, "rec_harness_m7: unknown argument %s\n", argv[i]); return 2; }
    }
    if (list) {
        printf("[");
        for (size_t i = 0; i < NTARGETS; i++)
            printf("%s\n [\"%s\", \"%s\", %d]", i ? "," : "", TARGETS[i].name, TARGETS[i].family,
                   TARGETS[i].prepared);
        printf("\n]\n");
        return 0;
    }
    for (size_t i = 0; name && i < NTARGETS; i++)
        if (!strcmp(TARGETS[i].name, name)) g_target = &TARGETS[i];
    if (!g_target || !out) {
        fprintf(stderr, "usage: rec_harness_m7 --target NAME [--omit] [--variant other-pp] --out DIR | --list\n");
        return 2;
    }
    if (*g_variant && (strcmp(g_variant, "other-pp") || strcmp(g_target->family, "reset"))) {
        fprintf(stderr, "rec_harness_m7: --variant %s is not one of %s's\n", g_variant, g_target->name);
        return 2;
    }
    const char *counts = getenv("SA2_REC_COUNTS");
    if (!counts || !getenv("SA2_REC_TRACE")) {
        fprintf(stderr, "rec_harness_m7: SA2_REC_TRACE and SA2_REC_COUNTS must name the stream and counts\n");
        return 2;
    }
    if (!strcmp(counts, "counts.json") || ends_with(counts, "/counts.json")) {
        fprintf(stderr, "rec_harness_m7: refused: a counts file named counts.json makes the case a recorded "
                        "run (tools/s4/m7_inputs.py run_dir_named_by); name it rec.counts.json\n");
        return 2;
    }
    unsetenv("SA2_D3D9_BREAK");
    unsetenv("SA2_REC_AUDIO");
    setenv("SA2_D3D9_DEVICE", "1", 1);
    setenv("SA2_D3DX_CONSTANT_TABLE", "1", 1);
    g_mem = (uint8_t *)calloc(1, GUEST_HI - GUEST_LO);
    if (!g_mem) return 2;
    sa2_d3d9_config_from_env();
    char path[4096];
    snprintf(path, sizeof path, "%s/expect.tsv", out);
    g_expect = fopen(path, "w");
    if (!g_expect) { fprintf(stderr, "rec_harness_m7: cannot write %s\n", path); return 2; }
    fprintf(g_expect, "frame\tx\ty\tstage_lo\tstage_hi\tb\tg\tr\ttol\twhat\n");

    device64();
    const char *f = g_target->family;
    if (!strcmp(f, "indexed")) family_indexed();
    else if (!strcmp(f, "freq")) family_instanced();
    else if (!strcmp(f, "vsbool")) family_constant(0, 1);
    else if (!strcmp(f, "vsint")) family_constant(0, 0);
    else if (!strcmp(f, "psbool")) family_constant(1, 1);
    else if (!strcmp(f, "psint")) family_constant(1, 0);
    else if (!strcmp(f, "query")) family_query();
    else if (!strcmp(f, "ctabquery")) family_ctabquery();
    else if (!strcmp(f, "ctabset")) family_ctabset();
    else if (!strcmp(f, "cube")) family_cube();
    else if (!strcmp(f, "volume")) family_volume();
    else family_reset();
    if (fclose(g_expect)) return 2;

    snprintf(path, sizeof path, "%s/answers.json", out);
    FILE *a = fopen(path, "w");
    if (!a) { fprintf(stderr, "rec_harness_m7: cannot write %s\n", path); return 2; }
    char esc[256];
    fprintf(a, "{\n \"schema\": \"sa2-m7-synthetic-answers/v1\",\n \"target\": \"%s\",\n \"family\": \"%s\",\n"
               " \"mode\": \"%s\",\n \"variant\": \"%s\",\n \"calls\": [",
            json_escape(g_target->name, esc, sizeof esc), f, g_omit ? "omit" : "positive", g_variant);
    for (int i = 0; i < g_ncalled; i++)
        fprintf(a, "%s\n  [\"%s\", %u]", i ? "," : "", json_escape(g_called[i], esc, sizeof esc), g_eax[i]);
    sa2_d3d9_stats st;
    sa2_d3d9_stats_get(&st);
    fprintf(a, "\n ],\n \"checks\": [");
    for (int i = 0; i < g_nchecks; i++)
        fprintf(a, "%s\n  [\"%s\", %d, %u, %u]", i ? "," : "", json_escape(g_check[i].what, esc, sizeof esc),
                g_check[i].at, g_check[i].want, g_check[i].got);
    fprintf(a, "\n ],\n \"checks_failed\": %d,\n \"texsrc_sha256\": \"%s\",", g_failed, g_texsrc_sha);
    fprintf(a, "\n \"device\": {\"method_calls\": %u, \"objects_built\": %u, \"present_calls\": %u, "
               "\"draw_calls\": %u, \"noop\": %u},\n \"guest_fnv\": \"%016llx\"\n}\n",
            st.method_calls, st.objects_built, st.present_calls, st.draw_calls, st.noop,
            (unsigned long long)fnv1a(g_mem, GUEST_HI - GUEST_LO));
    if (fclose(a)) return 2;
    if (g_failed) {
        fprintf(stderr, "rec_harness_m7: %d of the device's answers are not what Direct3D 9 gives "
                        "(answers.json \"checks\")\n", g_failed);
        for (int i = 0; i < g_nchecks; i++)
            if (g_check[i].want != g_check[i].got)
                fprintf(stderr, "  %s at %d: want %#x, the device gave %#x\n", g_check[i].what, g_check[i].at,
                        g_check[i].want, g_check[i].got);
        return 3;
    }
    return 0;
}
