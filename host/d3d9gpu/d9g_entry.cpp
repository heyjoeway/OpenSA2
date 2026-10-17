// d9g_entry.cpp: host/d3d9gpu's Direct3DCreate9 and the IDirect3D9 it answers. It is
// linked statically into sa2_player_gpu (host/d3d9gpu/build.sh: libsa2_d3d9gpu.a), the
// second host's player, which calls what the DXVK player calls on DXVK's library:
// Direct3DCreate9, GetAdapterIdentifier, CreateDevice. The renderer is that binary, so the
// device's report names it as its library (the file /proc/self/exe is, and its sha256),
// the file the player pins as files.player.
//
// The environment (read once, at Direct3DCreate9):
//   SA2_D3D9GPU_REPORT   the device's report (d9g_report.cpp); refused inside a git
//                        working tree outside its private/ (AGENTS.md rule 9)
//   SA2_D3D9GPU_FAULT    named faults for the negative controls only (d9g.h); printed on
//                        stderr and written into the report
//   SA2_D3D9GPU_PRESENT  window (default) or none: claim no window, and Present only
//                        submits (a headless run under SDL_VIDEO_DRIVER=offscreen)
//   SA2_D3D9GPU_DEBUG    1: SDL_gpu's debug mode (its usage checks; with SDL_ASSERT=abort
//                        a misuse ends the process). Verdict runs leave it unset.
//   SA2_D3D9GPU_CAPS_OFF a test run's override of the SDL patch's capabilities ("border",
//                        "depth-clamp", or both, comma-separated): the device maps as on an SDL
//                        without them, so the shader fallback's obligations are made on a device
//                        that supports both (d9g_gpu.h CapsOff). Refused, and the device not made,
//                        unless SA2_D3D9GPU_TEST_RUN=1 names the run a test run; written into the
//                        report (sdl_gpu.capability_override), which no verdict takes
//   SA2_D3D9GPU_NO_BC    1: no BC format is sampled; each DXT texture is made BGRA8 and decoded on
//                        the CPU, as on a GPU without BC formats (a Mali's); on Android also the
//                        system property debug.sa2.no_bc=1 (d9g_gpu_sdl.cpp make_sdl_gpu)
//   SA2_D3D9GPU_NO_CLIPDIST  1: the device is made without shaderClipDistance and the vertex shaders
//                        write no ClipDistance, as on a Mali-G715; on Android also debug.sa2.no_clipdist=1
//   SA2_D3D9GPU_NO_DEPTH_CLAMP  1: the device is made without SDL's depthClamp, so it has no depth
//                        clip and clamp and every draw owing the clamp is encoded clip-only, in any
//                        run, replays included (as on a Galaxy A05's Mali-G52); on Android also
//                        debug.sa2.no_depth_clamp=1; recorded in the report's env and
//                        sdl_gpu.depth_clamp_forced_off
//   SA2_D3D9GPU_VK11     1: the device is made at Vulkan 1.1 and every shader lowered to SPIR-V 1.3,
//                        the profile a driver offering only Vulkan 1.1 or 1.2 gets by itself (an Adreno
//                        610's), on any device; on Android also debug.sa2.vk11=1 (d9g_gpu_sdl.cpp
//                        make_sdl_gpu). Recorded in the report's sdl_gpu.vk11 (forced), not in its env
//                        block, so a 1.3 run's report keeps every line it had
//   SA2_D3D9GPU_TEST_RUN 1: a test run (the override's and the shader faults' one condition; it
//                        changes nothing else)
//   SA2_D3D9GPU_SHADER_DUMP  a directory: each shader's bytecode as <sha256>.bin at its creation (the
//                        R1 GPU script's corpus, host/d3d9gpu/test_shaders.sh); refused, and the
//                        device not made, inside a git working tree outside its private/
//   SA2_D3D9GPU_SHADER_FAULT a translator fault for the R1 canonical diff's controls
//                        (d9g_shader_internal.h); honoured only in a test run, and otherwise every
//                        shader's translation is refused by name
#include "d9g.h"

#include "sha256.h"

#include <SDL3/SDL.h>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>

namespace d9g {

namespace {
std::string env(const char* k) {
    const char* v = getenv(k);
    return v ? v : "";
}
}  // namespace

D3D9::D3D9(std::unique_ptr<Gpu> gpu) : gpu_(std::move(gpu)) {}

HRESULT STDMETHODCALLTYPE D3D9::QueryInterface(REFIID riid, void** ppv) {
    if (!ppv) return answer(I_D3D9, 0, E_POINTER);
    *ppv = nullptr;
    const GUID unk = __uuidof(IUnknown), own = __uuidof(IDirect3D9);
    if (!std::memcmp(&riid, &unk, sizeof(GUID)) || !std::memcmp(&riid, &own, sizeof(GUID))) {
        AddRef();
        *ppv = static_cast<IDirect3D9*>(this);
        return answer(I_D3D9, 0, S_OK);
    }
    return answer(I_D3D9, 0, E_NOINTERFACE);
}

ULONG STDMETHODCALLTYPE D3D9::Release() {
    if (!refs_) return 0;
    const ULONG n = --refs_;
    if (!n) delete this;   // a GPU device CreateDevice never took goes with it
    return n;
}

UINT STDMETHODCALLTYPE D3D9::GetAdapterCount() {
    answer(I_D3D9, 4, D3D_OK);
    return 1;
}

// The adapter as SDL_gpu names it. SDL exposes no PCI identifiers, so VendorId and
// DeviceId are 0; the device report carries SDL's own strings.
HRESULT STDMETHODCALLTYPE D3D9::GetAdapterIdentifier(UINT adapter, DWORD flags, D3DADAPTER_IDENTIFIER9* id) {
    if (adapter != 0 || !id) return answer(I_D3D9, 5, D3DERR_INVALIDCALL);
    std::memset(id, 0, sizeof *id);
    const GpuInfo& g = report().gpu;
    std::snprintf(id->Description, sizeof id->Description, "%s", g.device_name.c_str());
    std::snprintf(id->Driver, sizeof id->Driver, "SDL_gpu %s", g.driver.c_str());
    return answer(I_D3D9, 5, D3D_OK);
}

HRESULT STDMETHODCALLTYPE D3D9::CreateDevice(UINT adapter, D3DDEVTYPE type, HWND focus, DWORD behavior,
                                             D3DPRESENT_PARAMETERS* pp, IDirect3DDevice9** out) {
    Report& r = report();
    if (out) *out = nullptr;
    if (!out || !pp || adapter != 0) return answer(I_D3D9, 16, D3DERR_INVALIDCALL);
    if (type != D3DDEVTYPE_HAL) {
        not_implemented_value("device type " + std::to_string(uint32_t(type)));
        return answer(I_D3D9, 16, D3DERR_INVALIDCALL);
    }
    if (!gpu_) {
        not_implemented_value("a second CreateDevice on one IDirect3D9");
        return answer(I_D3D9, 16, D3DERR_NOTAVAILABLE);
    }
    bool ni = false;
    std::string why;
    HRESULT hr = check_present_parameters(*pp, &ni, &why);
    if (FAILED(hr)) {
        if (ni) not_implemented_value("CreateDevice: " + why);
        r.failure = "CreateDevice: " + why;
        r.write();
        return answer(I_D3D9, 16, hr);
    }
    if (!pp->BackBufferCount) pp->BackBufferCount = 1;   // DXVK normalises 0 to 1
    SDL_Window* win = reinterpret_cast<SDL_Window*>(pp->hDeviceWindow ? pp->hDeviceWindow : focus);
    r.present_interval = pp->PresentationInterval == D3DPRESENT_INTERVAL_IMMEDIATE ? "IMMEDIATE" : "ONE";
    if (r.present_mode == "window") {
        std::string mode;
        if (!win || !gpu_->attach_window(win, pp->PresentationInterval != D3DPRESENT_INTERVAL_IMMEDIATE, &mode)) {
            r.failure = "the window could not be claimed: " + gpu_->error();
            std::fprintf(stderr, "d3d9gpu: %s\n", r.failure.c_str());
            r.write();
            return answer(I_D3D9, 16, D3DERR_NOTAVAILABLE);
        }
        r.swapchain_mode = mode;
    } else {
        r.swapchain_mode = "none";
    }
    DeviceSetup s;
    s.pp = *pp;
    s.behavior = behavior;
    Device* dev = new Device(std::move(gpu_), s);
    if (!dev->init(&why)) {
        r.failure = why;
        std::fprintf(stderr, "d3d9gpu: %s\n", why.c_str());
        dev->Release();
        r.write();
        return answer(I_D3D9, 16, D3DERR_NOTAVAILABLE);
    }
    dev->write_report("device", false);
    *out = dev;
    return answer(I_D3D9, 16, D3D_OK);
}

}  // namespace d9g

// The factory, as DXVK's d3d9.h declares it.
extern "C" IDirect3D9* Direct3DCreate9(UINT sdk_version) {
    using namespace d9g;
    Report& r = report();
    r.path = env("SA2_D3D9GPU_REPORT");
    if (!r.path.empty()) {
        r.path_refused = refuse_output(r.path);
        if (!r.path_refused.empty()) std::fprintf(stderr, "d3d9gpu: report refused: %s\n", r.path_refused.c_str());
    }
    // The file this code runs from: the executable it is linked into, read as the player
    // reads files.player (its real_path and sha256), so the two can be held equal (dladdr
    // would give a symbol of the main program argv[0], which may be relative).
    {
#ifdef _WIN32
        // No /proc on Windows: the executable's own module file.
        wchar_t self[32768];
        const DWORD n = GetModuleFileNameW(nullptr, self, DWORD(sizeof self / sizeof self[0]));
        if (n > 0 && n < sizeof self / sizeof self[0]) {
            r.library_path = std::filesystem::path(std::wstring(self, n)).string();
            r.library_sha256 = sha256_file(r.library_path);
        }
#else
        std::error_code ec;
        const std::filesystem::path self = std::filesystem::read_symlink("/proc/self/exe", ec);
        if (!ec) {
            r.library_path = self.string();
            r.library_sha256 = sha256_file("/proc/self/exe");
        }
#endif
    }
    r.fault = parse_faults(getenv("SA2_D3D9GPU_FAULT"));
    // A translator fault (SA2_D3D9GPU_SHADER_FAULT, the R1 diff's controls) changes every module the
    // run's shaders become: it is the run's fault too, so a verdict refuses it as it refuses the
    // device's own (tools/s5/compare_hosts.py reads the report's "fault").
    if (const std::string sf = env("SA2_D3D9GPU_SHADER_FAULT"); !sf.empty())
        r.fault.names += (r.fault.names.empty() ? "shader:" : ",shader:") + sf;
    if (!r.fault.names.empty())
        std::fprintf(stderr, "d3d9gpu: FAULT INJECTED: %s%s%s -- a negative control's run, never a verdict\n",
                     r.fault.names.c_str(), r.fault.error.empty() ? "" : "; ", r.fault.error.c_str());
    const std::string present = env("SA2_D3D9GPU_PRESENT");
    if (!present.empty() && present != "window" && present != "none") {
        r.failure = "SA2_D3D9GPU_PRESENT is " + present + ", not window or none";
    }
    if (present == "none") r.present_mode = "none";
    if (!r.fault.error.empty()) r.failure = r.fault.error;
    r.shader_tx.dump_dir = env("SA2_D3D9GPU_SHADER_DUMP");
    if (!r.shader_tx.dump_dir.empty()) {
        r.shader_tx.dump_refused = refuse_output(r.shader_tx.dump_dir);
        std::error_code ec;
        if (r.shader_tx.dump_refused.empty() && !std::filesystem::is_directory(r.shader_tx.dump_dir, ec))
            r.shader_tx.dump_refused = "SA2_D3D9GPU_SHADER_DUMP " + r.shader_tx.dump_dir + " is not a directory";
        if (!r.shader_tx.dump_refused.empty() && r.failure.empty()) r.failure = r.shader_tx.dump_refused;
    }
    const CapsOff caps_off = parse_caps_off(getenv("SA2_D3D9GPU_CAPS_OFF"), getenv("SA2_D3D9GPU_TEST_RUN"));
    r.gpu.capability_override = caps_off.names;   // the report names it even where it is refused
    if (!caps_off.error.empty() && r.failure.empty()) r.failure = caps_off.error;
    if (!caps_off.names.empty() && caps_off.error.empty())
        std::fprintf(stderr, "d3d9gpu: CAPABILITY OVERRIDE (test run): %s forced off -- the shader fallback's leg, never a verdict\n",
                     caps_off.names.c_str());
    if (!r.failure.empty()) {
        std::fprintf(stderr, "d3d9gpu: %s\n", r.failure.c_str());
        r.phase = "Direct3DCreate9";
        r.write();
        return nullptr;
    }
    std::string err;
    std::unique_ptr<Gpu> gpu = make_sdl_gpu(env("SA2_D3D9GPU_DEBUG") == "1", caps_off, &err);
    if (!gpu) {
        r.failure = err;
        std::fprintf(stderr, "d3d9gpu: %s\n", err.c_str());
        r.phase = "Direct3DCreate9";
        r.write();
        return nullptr;
    }
    r.gpu = gpu->info();
    r.phase = "Direct3DCreate9";
    r.write();
    (void)sdk_version;
    return new D3D9(std::move(gpu));
}
