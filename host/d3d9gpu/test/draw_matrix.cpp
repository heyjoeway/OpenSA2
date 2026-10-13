// draw_matrix.cpp: literal, self-authored COM draw/order/lifetime probes for both factories.
// No Present or OS input. Each checkpoint completes GetRenderTargetData and checks every pixel.
//   d3d9gpu_draw_matrix sdlgpu --out /absolute/private/result [--case NAME] [--omit-draw NAME]
//   d3d9gpu_draw_matrix /absolute/libdxvk_d3d9.so.0 --out /absolute/private/result ...
#include "sha256.h"

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <stdexcept>
#include <string>
#include <vector>

#ifndef SA2_DRAW_MATRIX_CHECKS_ONLY
#include <SDL3/SDL.h>
#include <windows.h>
#include <d3d9.h>
#include <dlfcn.h>
#endif

namespace {
constexpr unsigned W = 64, H = 64;
constexpr uint32_t Blue = 0xff0000ffu, Red = 0xffff0000u, Green = 0xff00ff00u, Zero = 0;
using Pixels = std::vector<uint8_t>;
enum class Pattern { red, green, zero_blue, red_green, red_blue_green, red_point, zero_point };
struct Case { const char* name; Pattern pattern; unsigned calls; };
constexpr Case Cases[] = {
    {"dp_start_offset", Pattern::red, 1},
    {"dip_signed_base_start", Pattern::red, 1},
    {"dp_fan", Pattern::red, 1},
    {"dpup_caller_lifetime", Pattern::green, 1},
    {"dipup_prefix_caller_lifetime", Pattern::green, 1},
    {"uniform_snapshot", Pattern::red_green, 2},
    {"vb_upload_order", Pattern::red_green, 2},
    {"draw_clear_draw", Pattern::red_blue_green, 2},
    {"release_after_draw", Pattern::red, 1},
    {"readback_texture_draw", Pattern::red, 2},
    {"no_input_unbound", Pattern::red_point, 1},
    {"no_input_unused_real", Pattern::red_point, 1},
    {"null_only_unbound", Pattern::zero_point, 1},
    {"null_only_unused_real", Pattern::zero_point, 1},
    {"real0_null15", Pattern::zero_blue, 1},
};
const Case& find_case(const std::string& name) {
    for (const auto& c : Cases) if (name == c.name) return c;
    throw std::runtime_error("unknown case: " + name);
}
uint32_t expected_color(Pattern p, unsigned x, unsigned y) {
    switch (p) {
    case Pattern::red: return Red;
    case Pattern::green: return Green;
    case Pattern::zero_blue: return x < 32 ? Zero : Blue;
    case Pattern::red_green: return x < 32 ? Red : Green;
    case Pattern::red_blue_green: return x < 32 ? Red : x < 48 ? Blue : Green;
    case Pattern::red_point: return x == 32 && y == 32 ? Red : Blue;
    case Pattern::zero_point: return x == 32 && y == 32 ? Zero : Blue;
    }
    throw std::runtime_error("invalid pattern");
}
Pixels expected(Pattern p) {
    Pixels result(W * H * 4);
    for (unsigned y = 0; y < H; ++y) for (unsigned x = 0; x < W; ++x) {
        const auto color = expected_color(p, x, y);
        for (unsigned ch = 0; ch < 4; ++ch) result[(y * W + x) * 4 + ch] = uint8_t(color >> (ch * 8));
    }
    return result;
}
std::string hash(const Pixels& p) { Sha256 h; h.update(p.data(), p.size()); return h.hex(); }
std::string json_quote(const std::string& value) {
    std::string out = "\"";
    for (const unsigned char ch : value) {
        if (ch == '"' || ch == '\\') { out += '\\'; out += char(ch); }
        else if (ch < 32) {
            char escaped[7]; std::snprintf(escaped, sizeof escaped, "\\u%04x", unsigned(ch)); out += escaped;
        } else out += char(ch);
    }
    return out + "\"";
}
const char* pattern_name(Pattern p) {
    switch (p) {
    case Pattern::red: return "solid-red";
    case Pattern::green: return "solid-green";
    case Pattern::zero_blue: return "left-zero-right-blue";
    case Pattern::red_green: return "left-red-right-green";
    case Pattern::red_blue_green: return "left-red-middle-blue-right-green";
    case Pattern::red_point: return "blue-with-red-at-32-32";
    case Pattern::zero_point: return "blue-with-zero-at-32-32";
    }
    throw std::runtime_error("invalid pattern");
}
bool pixels_match(const Pixels& actual, Pattern p) { return actual == expected(p); }
class InstrumentDefect : public std::runtime_error { using std::runtime_error::runtime_error; };
int self_test() {
    Pixels clear(W * H * 4), black(W * H * 4, 0);
    for (size_t i = 0; i < clear.size(); i += 4) {
        clear[i] = 255; clear[i + 3] = 255;
    }
    for (const auto& c : Cases) {
        if (!pixels_match(expected(c.pattern), c.pattern) || pixels_match(clear, c.pattern))
            throw std::runtime_error(std::string("literal/no-op self-test: ") + c.name);
        Pixels wrong = expected(c.pattern); wrong[0] ^= 1;
        if (pixels_match(wrong, c.pattern)) throw std::runtime_error("one-channel mutation accepted");
        if (pixels_match(black, c.pattern))
            throw std::runtime_error("black no-op accepted");
        if (find_case(c.name).pattern != c.pattern) throw std::runtime_error("case lookup changed pattern");
        std::printf("{\"case\":%s,\"expected_pattern\":%s,\"expected_sha256\":%s}\n",
            json_quote(c.name).c_str(), json_quote(pattern_name(c.pattern)).c_str(), json_quote(hash(expected(c.pattern))).c_str());
    }
    const Pixels split = expected(Pattern::red_green);
    if (split[0] != 0 || split[2] != 255 || split[31 * 4 + 2] != 255 || split[32 * 4 + 1] != 255)
        throw std::runtime_error("literal split boundary");
    const Pixels point = expected(Pattern::red_point);
    const size_t center = (32 * W + 32) * 4;
    if (point[center] != 0 || point[center + 2] != 255 || point[center - 4] != 255 || point[center + 4] != 255)
        throw std::runtime_error("literal point footprint");
    std::puts("DRAW_MATRIX_CPU_OK");
    return 0;
}

#ifndef SA2_DRAW_MATRIX_CHECKS_ONLY
template<class T> class Com {
    T* p_ = nullptr;
public:
    Com() = default;
    Com(const Com&) = delete;
    Com& operator=(const Com&) = delete;
    ~Com() { reset(); }
    T* get() const { return p_; }
    T* operator->() const { return p_; }
    T** put() { if (p_) throw std::runtime_error("COM output already owned"); return &p_; }
    ULONG reset() { T* old = p_; p_ = nullptr; return old ? old->Release() : 0; }
};
void checked(HRESULT hr, const char* operation) {
    if (FAILED(hr)) {
        char message[192]; std::snprintf(message, sizeof message, "%s failed: HRESULT 0x%08x", operation, unsigned(hr));
        throw std::runtime_error(message);
    }
}
template<class T> void release_zero(Com<T>& object, const char* name) {
    if (object.reset()) throw std::runtime_error(std::string("COM references remain after unbind: ") + name);
}
template<class T> void checked_output(HRESULT hr, const Com<T>& object, const char* operation) {
    checked(hr, operation);
    if (!object.get()) throw std::runtime_error(std::string(operation) + " succeeded with null output");
}
struct Platform {
    SDL_Window* window = nullptr;
    bool initialized = false;
    Platform() {
        SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");
        if (!SDL_Init(SDL_INIT_VIDEO)) throw std::runtime_error(std::string("SDL_Init: ") + SDL_GetError());
        initialized = true;
        window = SDL_CreateWindow("d3d9gpu draw matrix", W, H, SDL_WINDOW_VULKAN | SDL_WINDOW_HIDDEN);
        if (!window) { SDL_Quit(); initialized = false; throw std::runtime_error(std::string("SDL_CreateWindow: ") + SDL_GetError()); }
    }
    ~Platform() { if (window) SDL_DestroyWindow(window); if (initialized) SDL_Quit(); }
};
struct Library {
    void* handle = nullptr;
    ~Library() { if (handle) dlclose(handle); }
};
struct Vertex { float x, y, z, w; };
struct TexVertex { Vertex position; float u, v; };
using Quad = std::array<Vertex, 4>;
Quad quad(float left = -1, float right = 1) {
    return {{{left, 1, .5f, 1}, {right, 1, .5f, 1}, {left, -1, .5f, 1}, {right, -1, .5f, 1}}};
}
const Vertex Prefix = {-3, -3, .5f, 1};
void poison_bytes(void* memory, size_t size) {
    // Volatile writes make caller mutation observable even in an optimized probe build.
    auto* bytes = static_cast<volatile uint8_t*>(memory);
    for (size_t i = 0; i < size; ++i) bytes[i] = 0;
}

// Literal SM3 tokens generated from the self-authored assembly retained in shaders.json.
// VS position: dcl_position v0; dcl_position o0; mov o0,v0.
constexpr DWORD PositionVS[] = {0xfffe0300u, 0x0200001fu, 0x80000000u, 0x900f0000u, 0x0200001fu,
    0x80000000u, 0xe00f0000u, 0x02000001u, 0xe00f0000u, 0x90e40000u, 0x0000ffffu};
// PS constant: mov oC0,c0. c0 comes from SetPixelShaderConstantF at each draw.
constexpr DWORD ConstantPS[] = {0xffff0300u, 0x02000001u, 0x800f0800u, 0xa0e40000u, 0x0000ffffu};
// VS point: def c0,0,0,.5,1; dcl_position o0; mov o0,c0. No fetched input.
constexpr DWORD PointVS[] = {0xfffe0300u, 0x05000051u, 0xa00f0000u, 0, 0, 0x3f000000u, 0x3f800000u,
    0x0200001fu, 0x80000000u, 0xe00f0000u, 0x02000001u, 0xe00f0000u, 0xa0e40000u, 0x0000ffffu};
// VS null point: the declaration lacks TEXCOORD1; its measured zero is passed to COLOR0.
constexpr DWORD NullPointVS[] = {0xfffe0300u, 0x05000051u, 0xa00f0000u, 0, 0, 0x3f000000u, 0x3f800000u,
    0x0200001fu, 0x80010005u, 0x900f0000u, 0x0200001fu, 0x80000000u, 0xe00f0000u,
    0x0200001fu, 0x8000000au, 0xe00f0001u, 0x02000001u, 0xe00f0000u, 0xa0e40000u,
    0x02000001u, 0xe00f0001u, 0x90e40000u, 0x0000ffffu};
constexpr DWORD NullQuadVS[] = {0xfffe0300u, 0x0200001fu, 0x80000000u, 0x900f0000u,
    0x0200001fu, 0x80010005u, 0x900f0001u, 0x0200001fu, 0x80000000u, 0xe00f0000u,
    0x0200001fu, 0x8000000au, 0xe00f0001u, 0x02000001u, 0xe00f0000u, 0x90e40000u,
    0x02000001u, 0xe00f0001u, 0x90e40001u, 0x0000ffffu};
constexpr DWORD ColorPS[] = {0xffff0300u, 0x0200001fu, 0x8000000au, 0x900f0000u,
    0x02000001u, 0x800f0800u, 0x90e40000u, 0x0000ffffu};
constexpr DWORD TextureVS[] = {0xfffe0300u, 0x0200001fu, 0x80000000u, 0x900f0000u,
    0x0200001fu, 0x80000005u, 0x900f0001u, 0x0200001fu, 0x80000000u, 0xe00f0000u,
    0x0200001fu, 0x80000005u, 0xe00f0001u, 0x02000001u, 0xe00f0000u, 0x90e40000u,
    0x02000001u, 0xe00f0001u, 0x90e40001u, 0x0000ffffu};
constexpr DWORD TexturePS[] = {0xffff0300u, 0x0200001fu, 0x80000005u, 0x900f0000u,
    0x0200001fu, 0x90000000u, 0xa00f0800u, 0x03000042u, 0x800f0000u, 0x90e40000u,
    0xa0e40800u, 0x02000001u, 0x800f0800u, 0x80e40000u, 0x0000ffffu};

struct Runner {
    Platform platform;
    Library library;
    Com<IDirect3D9> d3d;
    Com<IDirect3DDevice9> dev;
    Com<IDirect3DSurface9> target, readback;
    Com<IDirect3DVertexDeclaration9> position_decl, texture_decl;
    Com<IDirect3DVertexShader9> position_vs, point_vs, null_point_vs, null_quad_vs, texture_vs;
    Com<IDirect3DPixelShader9> constant_ps, color_ps, texture_ps;
    std::filesystem::path out;
    std::string omission, current;
    unsigned calls = 0, omitted = 0, checkpoints = 0;
    bool scene = false;
    Runner(const std::string& factory, const std::filesystem::path& output, const std::string& omit)
        : out(output), omission(omit) {
        using Create9 = IDirect3D9* (*)(UINT);
        Create9 create = &Direct3DCreate9;
        if (factory != "sdlgpu") {
            library.handle = dlopen(factory.c_str(), RTLD_NOW | RTLD_LOCAL);
            if (!library.handle) throw std::runtime_error(std::string("dlopen: ") + dlerror());
            create = reinterpret_cast<Create9>(dlsym(library.handle, "Direct3DCreate9"));
            if (!create) throw std::runtime_error("dlsym Direct3DCreate9 failed");
        }
        *d3d.put() = create(D3D_SDK_VERSION);
        if (!d3d.get()) throw std::runtime_error("Direct3DCreate9 returned null");
        Dl_info loaded{};
        if (!dladdr(reinterpret_cast<const void*>(create), &loaded) || !loaded.dli_fname)
            throw std::runtime_error("cannot identify selected Direct3DCreate9 library");
        const auto executable = std::filesystem::canonical("/proc/self/exe").string();
        const auto factory_file = std::filesystem::canonical(loaded.dli_fname).string();
        if (factory_file != (factory == "sdlgpu" ? executable : std::filesystem::canonical(factory).string()))
            throw std::runtime_error("selected Direct3DCreate9 came from a different file");
        const auto executable_hash = sha256_file(executable), factory_hash = sha256_file(factory_file);
        if (executable_hash.empty() || factory_hash.empty()) throw std::runtime_error("factory/executable hashing failed");
        D3DADAPTER_IDENTIFIER9 adapter{};
        checked(d3d->GetAdapterIdentifier(0, 0, &adapter), "GetAdapterIdentifier");
        D3DPRESENT_PARAMETERS pp{};
        pp.BackBufferWidth = W; pp.BackBufferHeight = H; pp.BackBufferFormat = D3DFMT_A8R8G8B8;
        pp.BackBufferCount = 1; pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
        pp.hDeviceWindow = reinterpret_cast<HWND>(platform.window); pp.Windowed = TRUE;
        pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
        checked_output(d3d->CreateDevice(0, D3DDEVTYPE_HAL, pp.hDeviceWindow,
            D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE, &pp, dev.put()), dev, "CreateDevice");
        checked_output(dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, target.put()), target, "GetBackBuffer");
        checked_output(dev->CreateOffscreenPlainSurface(W, H, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM,
            readback.put(), nullptr), readback, "CreateOffscreenPlainSurface");
        const D3DVERTEXELEMENT9 positions[] = {{0, 0, D3DDECLTYPE_FLOAT4, 0, D3DDECLUSAGE_POSITION, 0}, D3DDECL_END()};
        const D3DVERTEXELEMENT9 textured[] = {{0, 0, D3DDECLTYPE_FLOAT4, 0, D3DDECLUSAGE_POSITION, 0},
            {0, 16, D3DDECLTYPE_FLOAT2, 0, D3DDECLUSAGE_TEXCOORD, 0}, D3DDECL_END()};
        checked_output(dev->CreateVertexDeclaration(positions, position_decl.put()), position_decl, "CreateVertexDeclaration(position)");
        checked_output(dev->CreateVertexDeclaration(textured, texture_decl.put()), texture_decl, "CreateVertexDeclaration(texture)");
        checked_output(dev->CreateVertexShader(PositionVS, position_vs.put()), position_vs, "CreateVertexShader(position)");
        checked_output(dev->CreateVertexShader(PointVS, point_vs.put()), point_vs, "CreateVertexShader(point)");
        checked_output(dev->CreateVertexShader(NullPointVS, null_point_vs.put()), null_point_vs, "CreateVertexShader(null-point)");
        checked_output(dev->CreateVertexShader(NullQuadVS, null_quad_vs.put()), null_quad_vs, "CreateVertexShader(null-quad)");
        checked_output(dev->CreateVertexShader(TextureVS, texture_vs.put()), texture_vs, "CreateVertexShader(texture)");
        checked_output(dev->CreatePixelShader(ConstantPS, constant_ps.put()), constant_ps, "CreatePixelShader(constant)");
        checked_output(dev->CreatePixelShader(ColorPS, color_ps.put()), color_ps, "CreatePixelShader(color)");
        checked_output(dev->CreatePixelShader(TexturePS, texture_ps.put()), texture_ps, "CreatePixelShader(texture)");
        std::filesystem::create_directories(out);
        std::printf("{\"type\":\"header\",\"schema\":1,\"factory\":%s,\"adapter\":%s,"
            "\"width\":64,\"height\":64,\"format\":\"BGRA8\",\"window\":\"hidden\",\"presents\":0,\"omit_draw\":%s,"
            "\"executable\":%s,\"executable_sha256\":%s,\"factory_file\":%s,\"factory_sha256\":%s}\n",
            json_quote(factory).c_str(), json_quote(adapter.Description).c_str(), json_quote(omission).c_str(),
            json_quote(executable).c_str(), json_quote(executable_hash).c_str(), json_quote(factory_file).c_str(), json_quote(factory_hash).c_str());
    }
    ~Runner() { if (scene) dev->EndScene(); }
    void begin() { if (!scene) { checked(dev->BeginScene(), "BeginScene"); scene = true; } }
    void end() { if (scene) { checked(dev->EndScene(), "EndScene"); scene = false; } }
    void unbind() {
        checked(dev->SetStreamSource(0, nullptr, 0, 0), "SetStreamSource(unbind)");
        checked(dev->SetIndices(nullptr), "SetIndices(unbind)");
        checked(dev->SetTexture(0, nullptr), "SetTexture(unbind)");
    }
    void reset(const Case& c) {
        end(); unbind(); current = c.name; calls = omitted = 0;
        checked(dev->SetVertexDeclaration(position_decl.get()), "SetVertexDeclaration");
        checked(dev->SetVertexShader(position_vs.get()), "SetVertexShader");
        checked(dev->SetPixelShader(constant_ps.get()), "SetPixelShader");
        const std::pair<D3DRENDERSTATETYPE, DWORD> states[] = {
            {D3DRS_ZENABLE, FALSE}, {D3DRS_ZWRITEENABLE, FALSE}, {D3DRS_CULLMODE, D3DCULL_NONE},
            {D3DRS_ALPHABLENDENABLE, FALSE}, {D3DRS_ALPHATESTENABLE, FALSE}, {D3DRS_LIGHTING, FALSE},
            {D3DRS_FOGENABLE, FALSE}, {D3DRS_STENCILENABLE, FALSE}, {D3DRS_SCISSORTESTENABLE, FALSE},
            {D3DRS_SRGBWRITEENABLE, FALSE}, {D3DRS_COLORWRITEENABLE, 15}, {D3DRS_POINTSIZE, 0x3f800000u},
            {D3DRS_POINTSIZE_MIN, 0x3f800000u}, {D3DRS_POINTSIZE_MAX, 0x3f800000u},
            {D3DRS_POINTSPRITEENABLE, FALSE}, {D3DRS_POINTSCALEENABLE, FALSE},
        };
        for (const auto& state : states) checked(dev->SetRenderState(state.first, state.second), "SetRenderState");
        D3DVIEWPORT9 vp{0, 0, W, H, 0, 1}; checked(dev->SetViewport(&vp), "SetViewport");
        checked(dev->Clear(0, nullptr, D3DCLEAR_TARGET, Blue, 1, 0), "Clear(base)");
    }
    void color(uint32_t argb) {
        const float c[] = {float((argb >> 16) & 255) / 255, float((argb >> 8) & 255) / 255,
            float(argb & 255) / 255, float(argb >> 24) / 255};
        checked(dev->SetPixelShaderConstantF(0, c, 1), "SetPixelShaderConstantF");
    }
    template<class F> void draw(F operation, const char* name) {
        begin(); ++calls;
        if (omission == current) { ++omitted; return; }
        checked(operation(), name);
    }
    void up(const Quad& q) {
        draw([&] { return dev->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, q.data(), sizeof(Vertex)); }, "DrawPrimitiveUP");
    }
    void upload(IDirect3DVertexBuffer9* vb, const void* bytes, UINT size) {
        void* mapped = nullptr; checked(vb->Lock(0, size, &mapped, 0), "VertexBuffer::Lock");
        if (!mapped) { checked(vb->Unlock(), "VertexBuffer::Unlock"); throw std::runtime_error("null VB lock"); }
        std::memcpy(mapped, bytes, size); checked(vb->Unlock(), "VertexBuffer::Unlock");
    }
    void make_vb(Com<IDirect3DVertexBuffer9>& vb, const void* bytes, UINT size) {
        checked_output(dev->CreateVertexBuffer(size, 0, 0, D3DPOOL_DEFAULT, vb.put(), nullptr), vb, "CreateVertexBuffer");
        upload(vb.get(), bytes, size);
    }
    void bind_vb(IDirect3DVertexBuffer9* vb, UINT offset = 0) {
        checked(dev->SetStreamSource(0, vb, offset, sizeof(Vertex)), "SetStreamSource");
    }
    void dp(UINT start = 0, D3DPRIMITIVETYPE type = D3DPT_TRIANGLESTRIP, UINT count = 2) {
        draw([&] { return dev->DrawPrimitive(type, start, count); }, "DrawPrimitive");
    }
    Pixels checkpoint(const char* label, Pattern pattern) {
        end(); checked(dev->GetRenderTargetData(target.get(), readback.get()), "GetRenderTargetData(before Present)");
        D3DLOCKED_RECT lock{}; checked(readback->LockRect(&lock, nullptr, D3DLOCK_READONLY), "Surface::LockRect");
        Pixels pixels;
        try {
            if (!lock.pBits || lock.Pitch < int(W * 4)) throw std::runtime_error("invalid readback row");
            pixels.resize(W * H * 4);
            for (unsigned y = 0; y < H; ++y)
                std::memcpy(pixels.data() + y * W * 4, static_cast<const uint8_t*>(lock.pBits) + size_t(y) * size_t(lock.Pitch), W * 4);
        } catch (...) { readback->UnlockRect(); throw; }
        checked(readback->UnlockRect(), "Surface::UnlockRect");
        const auto file = out / (current + "-" + label + ".bgra");
        std::ofstream raw(file, std::ios::binary | std::ios::trunc);
        raw.write(reinterpret_cast<const char*>(pixels.data()), std::streamsize(pixels.size())); raw.close();
        if (!raw) throw std::runtime_error("failed raw BGRA write: " + file.string());
        const Pixels wanted = expected(pattern);
        const bool match = pixels == wanted;
        size_t mismatch = 0; while (mismatch < pixels.size() && pixels[mismatch] == wanted[mismatch]) ++mismatch;
        std::printf("{\"type\":\"checkpoint\",\"case\":%s,\"checkpoint\":%s,\"draw_calls\":%u,"
            "\"omitted_calls\":%u,\"before_present\":true,\"expected_pattern\":%s,\"raw\":%s,\"sha256\":%s,"
            "\"expected_sha256\":%s,\"matched\":%s,\"first_mismatch_byte\":%zu}\n",
            json_quote(current).c_str(), json_quote(label).c_str(), calls, omitted, json_quote(pattern_name(pattern)).c_str(),
            json_quote(file.string()).c_str(), json_quote(hash(pixels)).c_str(), json_quote(hash(wanted)).c_str(),
            match ? "true" : "false", mismatch);
        std::fflush(stdout); ++checkpoints;
        if (!match) throw std::runtime_error("literal pixel expectation failed: " + current + "/" + label);
        return pixels;
    }
    void run(const Case& c) {
        reset(c);
        bool final_checked = false;
        if (current == "dp_start_offset") {
            const auto q = quad(); std::array<Vertex, 6> v{Prefix, Prefix, q[0], q[1], q[2], q[3]};
            Com<IDirect3DVertexBuffer9> vb; make_vb(vb, v.data(), sizeof v); bind_vb(vb.get(), sizeof(Vertex));
            color(Red); dp(1);
        } else if (current == "dip_signed_base_start") {
            const auto q = quad(); std::array<Vertex, 6> v{Prefix, q[0], q[1], q[2], q[3], Prefix};
            const uint16_t index[] = {0, 0, 2, 3, 4, 4, 3, 5};
            Com<IDirect3DVertexBuffer9> vb; Com<IDirect3DIndexBuffer9> ib;
            make_vb(vb, v.data(), sizeof v); bind_vb(vb.get());
            checked_output(dev->CreateIndexBuffer(sizeof index, 0, D3DFMT_INDEX16, D3DPOOL_DEFAULT, ib.put(), nullptr), ib, "CreateIndexBuffer");
            void* p = nullptr; checked(ib->Lock(0, sizeof index, &p, 0), "IndexBuffer::Lock");
            if (!p) { checked(ib->Unlock(), "IndexBuffer::Unlock"); throw std::runtime_error("null IB lock"); }
            std::memcpy(p, index, sizeof index); checked(ib->Unlock(), "IndexBuffer::Unlock");
            checked(dev->SetIndices(ib.get()), "SetIndices"); color(Red);
            draw([&] { return dev->DrawIndexedPrimitive(D3DPT_TRIANGLELIST, -1, 2, 4, 2, 2); }, "DrawIndexedPrimitive");
        } else if (current == "dp_fan") {
            const auto q = quad(); std::array<Vertex, 5> v{Prefix, q[0], q[1], q[3], q[2]};
            Com<IDirect3DVertexBuffer9> vb; make_vb(vb, v.data(), sizeof v); bind_vb(vb.get());
            color(Red); dp(1, D3DPT_TRIANGLEFAN);
        } else if (current == "dpup_caller_lifetime") {
            auto q = quad(); color(Green); up(q); poison_bytes(q.data(), sizeof q);
            checkpoint("final", c.pattern); final_checked = true;
        } else if (current == "dipup_prefix_caller_lifetime") {
            const auto q = quad(); std::array<Vertex, 6> v{Prefix, Prefix, q[0], q[1], q[2], q[3]};
            std::array<uint16_t, 6> i{2, 3, 4, 4, 3, 5}; color(Green);
            draw([&] { return dev->DrawIndexedPrimitiveUP(D3DPT_TRIANGLELIST, 2, 4, 2, i.data(),
                D3DFMT_INDEX16, v.data(), sizeof(Vertex)); }, "DrawIndexedPrimitiveUP");
            poison_bytes(v.data(), sizeof v); poison_bytes(i.data(), sizeof i);
            checkpoint("final", c.pattern); final_checked = true;
        } else if (current == "uniform_snapshot") {
            color(Red); up(quad(-1, 0)); color(Green); up(quad(0, 1));
        } else if (current == "vb_upload_order") {
            const auto left = quad(-1, 0), right = quad(0, 1); Com<IDirect3DVertexBuffer9> vb;
            make_vb(vb, left.data(), sizeof left); bind_vb(vb.get()); color(Red); dp();
            upload(vb.get(), right.data(), sizeof right); color(Green); dp();
        } else if (current == "draw_clear_draw") {
            color(Red); up(quad());
            const D3DRECT right_half{32, 0, 64, 64};
            checked(dev->Clear(1, &right_half, D3DCLEAR_TARGET, Blue, 1, 0), "Clear(right half between draws)");
            color(Green); up(quad(.5f, 1));
        } else if (current == "release_after_draw") {
            const auto q = quad(); Com<IDirect3DVertexBuffer9> vb;
            make_vb(vb, q.data(), sizeof q); bind_vb(vb.get()); color(Red); dp();
            checked(dev->SetStreamSource(0, nullptr, 0, 0), "SetStreamSource(release after draw)");
            release_zero(vb, "released vertex buffer");
        } else if (current == "readback_texture_draw") {
            color(Red); up(quad()); const auto pixels = checkpoint("seed", Pattern::red);
            Com<IDirect3DTexture9> texture;
            checked_output(dev->CreateTexture(1, 1, 1, 0, D3DFMT_A8R8G8B8, D3DPOOL_MANAGED, texture.put(), nullptr), texture, "CreateTexture");
            D3DLOCKED_RECT lock{}; checked(texture->LockRect(0, &lock, nullptr, 0), "Texture::LockRect");
            if (!lock.pBits || lock.Pitch < 4) { checked(texture->UnlockRect(0), "Texture::UnlockRect"); throw std::runtime_error("invalid texture lock"); }
            std::memcpy(lock.pBits, pixels.data() + (32 * W + 32) * 4, 4);
            checked(texture->UnlockRect(0), "Texture::UnlockRect");
            checked(dev->Clear(0, nullptr, D3DCLEAR_TARGET, Blue, 1, 0), "Clear(before sampled draw)");
            checked(dev->SetVertexDeclaration(texture_decl.get()), "SetVertexDeclaration(texture)");
            checked(dev->SetVertexShader(texture_vs.get()), "SetVertexShader(texture)");
            checked(dev->SetPixelShader(texture_ps.get()), "SetPixelShader(texture)");
            checked(dev->SetTexture(0, texture.get()), "SetTexture");
            const std::pair<D3DSAMPLERSTATETYPE, DWORD> states[] = {
                {D3DSAMP_ADDRESSU, D3DTADDRESS_CLAMP}, {D3DSAMP_ADDRESSV, D3DTADDRESS_CLAMP},
                {D3DSAMP_ADDRESSW, D3DTADDRESS_CLAMP}, {D3DSAMP_MINFILTER, D3DTEXF_POINT},
                {D3DSAMP_MAGFILTER, D3DTEXF_POINT}, {D3DSAMP_MIPFILTER, D3DTEXF_NONE}, {D3DSAMP_SRGBTEXTURE, 0},
            };
            for (const auto& state : states) checked(dev->SetSamplerState(0, state.first, state.second), "SetSamplerState");
            const auto q = quad(); std::array<TexVertex, 4> v{{{q[0], .5f, .5f}, {q[1], .5f, .5f},
                {q[2], .5f, .5f}, {q[3], .5f, .5f}}};
            draw([&] { return dev->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, v.data(), sizeof(TexVertex)); }, "DrawPrimitiveUP(texture)");
        } else if (current == "real0_null15") {
            checked(dev->SetVertexShader(null_quad_vs.get()), "SetVertexShader(null-quad)");
            checked(dev->SetPixelShader(color_ps.get()), "SetPixelShader(color)");
            const auto q = quad(-1, 0); Com<IDirect3DVertexBuffer9> vb; make_vb(vb, q.data(), sizeof q); bind_vb(vb.get()); dp();
        } else {
            const bool null_only = current.find("null_only_") == 0;
            checked(dev->SetVertexShader(null_only ? null_point_vs.get() : point_vs.get()), "SetVertexShader(no-real-input)");
            checked(dev->SetPixelShader(null_only ? color_ps.get() : constant_ps.get()), "SetPixelShader(no-real-input)");
            Com<IDirect3DVertexBuffer9> unused;
            if (current.find("unused_real") != std::string::npos) {
                make_vb(unused, &Prefix, sizeof Prefix); bind_vb(unused.get(), sizeof Prefix);
            }
            color(Red); dp(0, D3DPT_POINTLIST, 1);
        }
        if (calls != c.calls) throw std::runtime_error("unexpected draw-call count: " + current);
        if (!final_checked) checkpoint("final", c.pattern);
        unbind();
    }
    void close() {
        end(); unbind(); checked(dev->SetVertexShader(nullptr), "SetVertexShader(unbind)");
        checked(dev->SetPixelShader(nullptr), "SetPixelShader(unbind)");
        checked(dev->SetVertexDeclaration(nullptr), "SetVertexDeclaration(unbind)");
        release_zero(position_vs, "position VS"); release_zero(point_vs, "point VS");
        release_zero(null_point_vs, "null point VS"); release_zero(null_quad_vs, "null quad VS"); release_zero(texture_vs, "texture VS");
        release_zero(constant_ps, "constant PS"); release_zero(color_ps, "color PS"); release_zero(texture_ps, "texture PS");
        release_zero(position_decl, "position declaration"); release_zero(texture_decl, "texture declaration");
        release_zero(readback, "system-memory readback");
        target.reset(); // The device still owns its backbuffer until final device release.
        if (dev.reset()) throw std::runtime_error("device references remain after explicit unbind/release");
        release_zero(d3d, "Direct3D9");
    }
};
#endif
} // namespace

int main(int argc, char** argv) {
    try {
        if (argc == 2 && std::strcmp(argv[1], "--self-test") == 0) return self_test();
#ifdef SA2_DRAW_MATRIX_CHECKS_ONLY
        throw std::runtime_error("this CPU-only build accepts --self-test only");
#else
        if (argc < 4 || (std::strcmp(argv[1], "sdlgpu") != 0 && argv[1][0] != '/')) {
            std::fprintf(stderr, "usage: d3d9gpu_draw_matrix sdlgpu|/absolute/lib.so --out /absolute/dir [--case NAME] [--omit-draw NAME]\n");
            return 2;
        }
        std::string out, selected, omission;
        for (int i = 2; i < argc; i += 2) {
            if (i + 1 >= argc) throw std::runtime_error("option missing value");
            const std::string option = argv[i];
            std::string* destination = option == "--out" ? &out : option == "--case" ? &selected : option == "--omit-draw" ? &omission : nullptr;
            if (!destination || !destination->empty()) throw std::runtime_error("unknown or repeated option: " + option);
            *destination = argv[i + 1];
        }
        if (out.empty() || out[0] != '/') throw std::runtime_error("--out must be an absolute directory");
        if (!selected.empty()) find_case(selected);
        if (!omission.empty()) { find_case(omission); if (!selected.empty() && selected != omission) throw std::runtime_error("omitted case is not selected"); }
        Runner runner(argv[1], out, omission);
        unsigned ran = 0;
        for (const auto& c : Cases) if (selected.empty() || selected == c.name) { runner.run(c); ++ran; }
        runner.close();
        // All supported cases have positive pixel witnesses; reaching here with an omission
        // means either the producer's literal check or the control is defective.
        if (!omission.empty()) throw InstrumentDefect("draw omission unexpectedly survived literal expectations");
        std::printf("{\"type\":\"summary\",\"cases\":%u,\"checkpoints\":%u,\"presents\":0,\"passed\":true}\n", ran, runner.checkpoints);
        std::puts("DRAW_MATRIX_OK"); return 0;
#endif
    } catch (const InstrumentDefect& error) {
        std::fprintf(stderr, "draw_matrix instrument defect: %s\n", error.what());
        std::printf("{\"type\":\"instrument_defect\",\"reason\":%s}\n", json_quote(error.what()).c_str());
        std::puts("DRAW_MATRIX_INSTRUMENT_DEFECT"); return 3;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "draw_matrix: %s\n", error.what());
        std::printf("{\"type\":\"failure\",\"reason\":%s}\n", json_quote(error.what()).c_str());
        std::puts("DRAW_MATRIX_FAIL"); return 1;
    }
}
