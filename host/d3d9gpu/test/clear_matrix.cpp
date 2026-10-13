// clear_matrix.cpp: the clear matrix (clear_matrix.h) through a Direct3D 9 factory. It
// makes an SDL3 window, the device (1280x720 A8R8G8B8, automatic D24S8, DISCARD, one back
// buffer, IMMEDIATE, HARDWARE_VERTEXPROCESSING | FPU_PRESERVE, as the player makes it),
// runs the matrix and prints one line per case. A GPU and a window are needed; nothing is
// presented, and the window may be hidden (--hidden). Two builds:
//
//   d3d9gpu_clear_matrix /abs/path/libX.so [--hidden] > lines.tsv
//       the factory of a library dlopened by absolute path: DXVK Native's
//       libdxvk_d3d9.so.0, under host/player/player_env.sh's environment (DXVK's leg)
//   sa2_player_gpu --clear-matrix [--hidden] > lines.tsv
//       with SA2_CLEAR_MATRIX_STATIC, linked into the second host's player
//       (host/d3d9gpu/build.sh): host/d3d9gpu's own Direct3DCreate9, so the matrix runs
//       the file the second host's runs run (SDL_gpu's leg)
#include <SDL3/SDL.h>
#include <windows.h>
#include <d3d9.h>

#ifndef _WIN32
#include <dlfcn.h>
#endif

#include <cstdio>
#include <cstring>
#include <string>

#include "clear_matrix.h"

namespace {

using Create9 = IDirect3D9* (*)(UINT);

// The matrix through `create9`, or, when it is null, through the Direct3DCreate9 of the
// library at `lib` (absolute), dlopened once the window exists.
int run_matrix(Create9 create9, const char* lib, bool hidden) {
    SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");
    if (!SDL_Init(SDL_INIT_VIDEO)) { std::fprintf(stderr, "SDL_Init: %s\n", SDL_GetError()); return 3; }
    SDL_Window* win = SDL_CreateWindow("d3d9gpu clear matrix", 1280, 720, SDL_WINDOW_VULKAN | (hidden ? SDL_WINDOW_HIDDEN : 0));
    if (!win) { std::fprintf(stderr, "SDL_CreateWindow: %s\n", SDL_GetError()); return 3; }
    if (!create9) {
#ifdef _WIN32
        const HMODULE handle = LoadLibraryA(lib);
        if (!handle) { std::fprintf(stderr, "LoadLibraryA: error %lu\n", (unsigned long)GetLastError()); return 3; }
        create9 = reinterpret_cast<Create9>(reinterpret_cast<void*>(GetProcAddress(handle, "Direct3DCreate9")));
#else
        void* handle = dlopen(lib, RTLD_NOW | RTLD_LOCAL);
        if (!handle) { std::fprintf(stderr, "dlopen: %s\n", dlerror()); return 3; }
        create9 = reinterpret_cast<Create9>(dlsym(handle, "Direct3DCreate9"));
#endif
        if (!create9) { std::fprintf(stderr, "no Direct3DCreate9\n"); return 3; }
    }
    IDirect3D9* d3d = create9(D3D_SDK_VERSION);
    if (!d3d) { std::fprintf(stderr, "Direct3DCreate9 returned NULL\n"); return 3; }
    D3DPRESENT_PARAMETERS pp{};
    pp.BackBufferWidth = 1280;
    pp.BackBufferHeight = 720;
    pp.BackBufferFormat = D3DFMT_A8R8G8B8;
    pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.hDeviceWindow = reinterpret_cast<HWND>(win);
    pp.Windowed = TRUE;
    pp.EnableAutoDepthStencil = TRUE;
    pp.AutoDepthStencilFormat = D3DFMT_D24S8;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    IDirect3DDevice9* dev = nullptr;
    const HRESULT hr = d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL, reinterpret_cast<HWND>(win),
                                         D3DCREATE_HARDWARE_VERTEXPROCESSING | D3DCREATE_FPU_PRESERVE, &pp, &dev);
    if (FAILED(hr) || !dev) { std::fprintf(stderr, "CreateDevice: 0x%08x\n", unsigned(hr)); return 3; }
    clear_matrix::print(clear_matrix::run(dev), stdout);
    const ULONG left = dev->Release();
    d3d->Release();
    SDL_DestroyWindow(win);
    SDL_Quit();
    if (left) { std::fprintf(stderr, "the device still holds %lu references\n", (unsigned long)left); return 1; }
    return 0;
}

}  // namespace

#ifdef SA2_CLEAR_MATRIX_STATIC
// argv[0] is "--clear-matrix" (host/player/player.cpp main).
int sa2_clear_matrix_main(int argc, char** argv) {
    const bool hidden = argc > 1 && std::strcmp(argv[1], "--hidden") == 0;
    if (argc > (hidden ? 2 : 1)) {
        std::fprintf(stderr, "usage: sa2_player_gpu --clear-matrix [--hidden]\n");
        return 2;
    }
    return run_matrix(&Direct3DCreate9, nullptr, hidden);
}
#else
int main(int argc, char** argv) {
#ifdef _WIN32
    // An absolute Windows path: a drive and a separator ("C:\\..." or "C:/..."), or a UNC path.
    const bool absolute = argc >= 2 && ((std::strlen(argv[1]) > 2 && argv[1][1] == ':' &&
                                         (argv[1][2] == '\\' || argv[1][2] == '/')) ||
                                        (argv[1][0] == '\\' && argv[1][1] == '\\'));
    if (!absolute) {
#else
    if (argc < 2 || argv[1][0] != '/') {
#endif
        std::fprintf(stderr, "usage: d3d9gpu_clear_matrix /abs/path/lib.so [--hidden]\n");
        return 2;
    }
    const bool hidden = argc > 2 && std::strcmp(argv[2], "--hidden") == 0;
    return run_matrix(nullptr, argv[1], hidden);
}
#endif
