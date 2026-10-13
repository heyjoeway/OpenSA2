// null_probe.cpp: DXVK's leg of the R1 null-stream probe (check C7, private/generated/s5/rulings/
// r1-shader-translator/PLAN.md section 2.4). A vertex shader reads an input its vertex declaration
// does not hold; DXVK binds its null stream there (binding 16, per instance, R32G32B32A32_SFLOAT,
// offset 0: d3d9_device.cpp:7704-7707, :7738-7742; VK_NULL_HANDLE, dxvk_context.cpp:7869), and the
// shader passes what it reads to the pixel shader as a colour. One full-target quad is drawn through
// the d3d9 library given by path (DXVK Native's libdxvk_d3d9.so.0, under host/player/player_env.sh's
// environment), the target is read back before any Present, and the pixel at its centre is printed:
// what a null binding reads on this driver, to the target's eight bits a channel. d3d9gpu_shaderc's
// probe-null mode is SDL_gpu's leg, with the value in a buffer of the second host's own.
//
//   null_probe /abs/path/libdxvk_d3d9.so.0 VS.bin PS.bin
//
// The shaders are the caller's (test_shaders.sh assembles them, test/r1_receipt.py synth): the
// vertex shader reads position (v0) and an input the declaration lacks, and writes it to a colour
// output; the pixel shader writes its colour input to oC0. Prints one JSON line and NULL_PROBE_OK.
#include <SDL3/SDL.h>
#include <windows.h>
#include <d3d9.h>

#include <dlfcn.h>

#include <cstdio>
#include <cstring>
#include <fstream>
#include <iterator>
#include <vector>

namespace {
using Create9 = IDirect3D9* (*)(UINT);
constexpr UINT W = 64, H = 64;

int fail(const char* what, HRESULT hr = 0) {
    std::fprintf(stderr, "null_probe: %s (hr=0x%08x)\n", what, unsigned(hr));
    std::printf("NULL_PROBE_FAIL %s\n", what);
    return 1;
}

std::vector<char> load(const char* path) {
    std::ifstream f(path, std::ios::binary);
    return std::vector<char>((std::istreambuf_iterator<char>(f)), {});
}
}  // namespace

int main(int argc, char** argv) {
    if (argc != 4 || argv[1][0] != '/') {
        std::fprintf(stderr, "usage: null_probe /abs/path/libdxvk_d3d9.so.0 VS.bin PS.bin\n");
        return 2;
    }
    SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");
    if (!SDL_Init(SDL_INIT_VIDEO)) return fail(SDL_GetError());
    SDL_Window* win = SDL_CreateWindow("null_probe", int(W), int(H), SDL_WINDOW_VULKAN | SDL_WINDOW_HIDDEN);
    if (!win) return fail(SDL_GetError());
    void* lib = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!lib) return fail(dlerror());
    auto create9 = reinterpret_cast<Create9>(dlsym(lib, "Direct3DCreate9"));
    IDirect3D9* d3d = create9 ? create9(D3D_SDK_VERSION) : nullptr;
    if (!d3d) return fail("Direct3DCreate9");
    D3DADAPTER_IDENTIFIER9 ident = {};
    d3d->GetAdapterIdentifier(D3DADAPTER_DEFAULT, 0, &ident);
    D3DPRESENT_PARAMETERS pp = {};
    pp.BackBufferWidth = W;
    pp.BackBufferHeight = H;
    pp.BackBufferFormat = D3DFMT_A8R8G8B8;
    pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.hDeviceWindow = reinterpret_cast<HWND>(win);
    pp.Windowed = TRUE;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    IDirect3DDevice9* dev = nullptr;
    HRESULT hr = d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, reinterpret_cast<HWND>(win),
                                   D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE, &pp, &dev);
    if (FAILED(hr)) return fail("CreateDevice", hr);
    auto vsCode = load(argv[2]), psCode = load(argv[3]);
    IDirect3DVertexShader9* vs = nullptr;
    IDirect3DPixelShader9* ps = nullptr;
    if (vsCode.empty() || FAILED(hr = dev->CreateVertexShader(reinterpret_cast<const DWORD*>(vsCode.data()), &vs)))
        return fail("CreateVertexShader", hr);
    if (psCode.empty() || FAILED(hr = dev->CreatePixelShader(reinterpret_cast<const DWORD*>(psCode.data()), &ps)))
        return fail("CreatePixelShader", hr);
    // position alone: the shader's second input has no element, so DXVK binds its null stream for it
    const D3DVERTEXELEMENT9 decl[] = {{0, 0, D3DDECLTYPE_FLOAT4, D3DDECLMETHOD_DEFAULT, D3DDECLUSAGE_POSITION, 0},
                                      D3DDECL_END()};
    IDirect3DVertexDeclaration9* vdecl = nullptr;
    if (FAILED(hr = dev->CreateVertexDeclaration(decl, &vdecl))) return fail("CreateVertexDeclaration", hr);
    const float quad[4][4] = {{-1, 1, 0.5f, 1}, {1, 1, 0.5f, 1}, {-1, -1, 0.5f, 1}, {1, -1, 0.5f, 1}};
    IDirect3DSurface9 *bb = nullptr, *readback = nullptr;
    dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &bb);
    if (FAILED(hr = dev->CreateOffscreenPlainSurface(W, H, D3DFMT_A8R8G8B8, D3DPOOL_SYSTEMMEM, &readback, nullptr)))
        return fail("CreateOffscreenPlainSurface", hr);
    dev->Clear(0, nullptr, D3DCLEAR_TARGET, 0xFF7F3F1Fu, 1.0f, 0);   // not a value a null read gives
    dev->BeginScene();
    dev->SetRenderState(D3DRS_ZENABLE, FALSE);
    dev->SetRenderState(D3DRS_CULLMODE, D3DCULL_NONE);
    dev->SetRenderState(D3DRS_ALPHABLENDENABLE, FALSE);
    dev->SetRenderState(D3DRS_ALPHATESTENABLE, FALSE);
    dev->SetRenderState(D3DRS_LIGHTING, FALSE);
    dev->SetVertexDeclaration(vdecl);
    dev->SetVertexShader(vs);
    dev->SetPixelShader(ps);
    if (FAILED(hr = dev->DrawPrimitiveUP(D3DPT_TRIANGLESTRIP, 2, quad, sizeof quad[0]))) return fail("DrawPrimitiveUP", hr);
    dev->EndScene();
    if (FAILED(hr = dev->GetRenderTargetData(bb, readback))) return fail("GetRenderTargetData", hr);
    D3DLOCKED_RECT lr = {};
    if (FAILED(hr = readback->LockRect(&lr, nullptr, D3DLOCK_READONLY))) return fail("LockRect", hr);
    const uint8_t* px = static_cast<const uint8_t*>(lr.pBits) + size_t(H / 2) * lr.Pitch + 4 * (W / 2);
    // every pixel of the quad must be the centre's (the read is per instance: one value)
    bool uniform = true;
    for (UINT y = 0; y < H; y++)
        for (UINT x = 0; x < W; x++)
            uniform = uniform && !std::memcmp(static_cast<const uint8_t*>(lr.pBits) + size_t(y) * lr.Pitch + 4 * x, px, 4);
    std::printf("{\"host\": \"dxvk\", \"adapter\": \"%s\", \"bgra\": [%u, %u, %u, %u], \"uniform\": %s}\n", ident.Description,
                px[0], px[1], px[2], px[3], uniform ? "true" : "false");
    readback->UnlockRect();
    readback->Release();
    bb->Release();
    vdecl->Release();
    vs->Release();
    ps->Release();
    dev->Release();
    d3d->Release();
    SDL_DestroyWindow(win);
    SDL_Quit();
    std::printf("%s\n", uniform ? "NULL_PROBE_OK" : "NULL_PROBE_FAIL the quad is not one colour");
    return uniform ? 0 : 1;
}
