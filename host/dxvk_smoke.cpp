// dxvk_smoke.cpp: SDL3 window + DXVK Native d3d9 device, clear, 60 presents,
// GetRenderTargetData of the last frame to a PPM, centre-pixel check. Exit 0 = PASS.
#include <SDL3/SDL.h>
#include <windows.h>          // DXVK include/native/windows, via -I$I/include/dxvk
#include <d3d9.h>             // DXVK include/native/directx (MinGW-w64 d3d9.h)
#include <dlfcn.h>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

using PFN_Direct3DCreate9 = IDirect3D9* (*)(UINT);

static uint16_t x87_cw() { uint16_t cw; __asm__ __volatile__("fnstcw %0" : "=m"(cw)); return cw; }

static int fail(const char* what, HRESULT hr = 0) {
  std::fprintf(stderr, "FAIL: %s (hr=0x%08x)\n", what, unsigned(hr));
  return 2;
}

int main(int argc, char** argv) {
  if (argc < 3) {
    std::fprintf(stderr, "usage: %s /abs/libdxvk_d3d9.so.0 out.ppm [nofpu]\n", argv[0]);
    return 2;
  }
  const char* libPath = argv[1];
  const char* outPath = argv[2];
  const bool preserveFpu = !(argc > 3 && std::strcmp(argv[3], "nofpu") == 0);
  const UINT W = 640, H = 480;
  const uint8_t R = 0x20, G = 0x80, B = 0xC0;

  // No default WSI on native (wsi_platform.cpp:30-38), and Direct3DCreate9 does not catch.
  if (!std::getenv("DXVK_WSI_DRIVER")) return fail("DXVK_WSI_DRIVER unset (want SDL3)");

  SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");              // keep default SIGINT/SIGTERM
  if (!SDL_Init(SDL_INIT_VIDEO)) return fail(SDL_GetError());   // main thread
  std::printf("sdl video driver: %s\n", SDL_GetCurrentVideoDriver());
  SDL_Window* window = SDL_CreateWindow("dxvk-smoke", int(W), int(H), SDL_WINDOW_VULKAN);
  if (!window) return fail(SDL_GetError());

  void* lib = dlopen(libPath, RTLD_NOW | RTLD_LOCAL);   // finds our libSDL3.so.0 by soname
  if (!lib) return fail(dlerror());
  auto create9 = reinterpret_cast<PFN_Direct3DCreate9>(dlsym(lib, "Direct3DCreate9"));
  if (!create9) return fail("dlsym(Direct3DCreate9)");

  IDirect3D9* d3d = nullptr;
  try { d3d = create9(D3D_SDK_VERSION); }
  catch (...) { return fail("Direct3DCreate9 threw (DxvkError); see stderr"); }
  if (!d3d) return fail("Direct3DCreate9 returned NULL");

  D3DADAPTER_IDENTIFIER9 ident = {};
  if (SUCCEEDED(d3d->GetAdapterIdentifier(D3DADAPTER_DEFAULT, 0, &ident)))
    std::printf("adapter 0: %s (vendor %04x device %04x)\n", ident.Description,
                unsigned(ident.VendorId), unsigned(ident.DeviceId));

  D3DPRESENT_PARAMETERS pp = {};
  pp.BackBufferWidth        = W;
  pp.BackBufferHeight       = H;
  pp.BackBufferFormat       = D3DFMT_X8R8G8B8;
  pp.BackBufferCount        = 1;
  pp.MultiSampleType        = D3DMULTISAMPLE_NONE;
  pp.SwapEffect             = D3DSWAPEFFECT_DISCARD;
  pp.hDeviceWindow          = reinterpret_cast<HWND>(window);  // native_sdl3.h: HWND == SDL_Window*
  pp.Windowed               = TRUE;
  pp.EnableAutoDepthStencil = TRUE;
  pp.AutoDepthStencilFormat = D3DFMT_D24S8;
  pp.PresentationInterval   = D3DPRESENT_INTERVAL_ONE;

  DWORD flags = D3DCREATE_HARDWARE_VERTEXPROCESSING;
  if (preserveFpu) flags |= D3DCREATE_FPU_PRESERVE;           // d3d9_device.cpp:95-96

  const uint16_t cwBefore = x87_cw();
  IDirect3DDevice9* dev = nullptr;
  HRESULT hr = d3d->CreateDevice(D3DADAPTER_DEFAULT, D3DDEVTYPE_HAL,
                                 reinterpret_cast<HWND>(window), flags, &pp, &dev);
  std::printf("x87 control word %04x -> %04x (FPU_PRESERVE %s)\n",
              cwBefore, x87_cw(), preserveFpu ? "on" : "off");
  if (FAILED(hr)) return fail("CreateDevice", hr);

  IDirect3DSurface9* backBuffer = nullptr;
  IDirect3DSurface9* readback = nullptr;
  if (FAILED(hr = dev->GetBackBuffer(0, 0, D3DBACKBUFFER_TYPE_MONO, &backBuffer)))
    return fail("GetBackBuffer", hr);
  if (FAILED(hr = dev->CreateOffscreenPlainSurface(W, H, D3DFMT_X8R8G8B8, D3DPOOL_SYSTEMMEM,
                                                   &readback, nullptr)))
    return fail("CreateOffscreenPlainSurface", hr);

  for (int frame = 0; frame < 60; frame++) {
    SDL_Event event;
    while (SDL_PollEvent(&event)) { }                       // answer the compositor; ignore input
    dev->Clear(0, nullptr, D3DCLEAR_TARGET | D3DCLEAR_ZBUFFER, D3DCOLOR_XRGB(R, G, B), 1.0f, 0);
    dev->BeginScene();
    dev->EndScene();
    if (frame == 59 && FAILED(hr = dev->GetRenderTargetData(backBuffer, readback)))  // before Present
      return fail("GetRenderTargetData", hr);
    if (FAILED(hr = dev->Present(nullptr, nullptr, nullptr, nullptr)))
      return fail("Present", hr);
  }

  D3DLOCKED_RECT locked = {};
  if (FAILED(hr = readback->LockRect(&locked, nullptr, D3DLOCK_READONLY)))   // waits for the copy
    return fail("LockRect", hr);
  FILE* out = std::fopen(outPath, "wb");
  if (!out) return fail("fopen");
  std::fprintf(out, "P6\n%u %u\n255\n", W, H);
  std::vector<uint8_t> row(size_t(W) * 3);
  uint32_t centre = 0;
  for (UINT y = 0; y < H; y++) {
    const uint8_t* src = static_cast<const uint8_t*>(locked.pBits) + size_t(y) * size_t(locked.Pitch);
    for (UINT x = 0; x < W; x++) {                          // X8R8G8B8 is B, G, R, X in memory
      row[3 * x + 0] = src[4 * x + 2];
      row[3 * x + 1] = src[4 * x + 1];
      row[3 * x + 2] = src[4 * x + 0];
    }
    if (y == H / 2)
      centre = (uint32_t(row[3 * (W / 2)]) << 16) | (uint32_t(row[3 * (W / 2) + 1]) << 8)
             | row[3 * (W / 2) + 2];
    std::fwrite(row.data(), 1, row.size(), out);
  }
  std::fclose(out);
  readback->UnlockRect();

  const uint32_t want = (uint32_t(R) << 16) | (uint32_t(G) << 8) | B;
  const bool pass = centre == want;
  std::printf("centre pixel %06x, want %06x: %s\n", centre, want, pass ? "PASS" : "FAIL");

  readback->Release();
  backBuffer->Release();
  dev->Release();
  d3d->Release();
  SDL_DestroyWindow(window);
  SDL_Quit();
  return pass ? 0 : 1;                                        // no dlclose (section 3)
}
