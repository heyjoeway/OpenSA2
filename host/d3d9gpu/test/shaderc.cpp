// d3d9gpu_shaderc: host 2's shader translator from the command line (R1). It translates Direct3D 9
// shader bytecode through host/d3d9gpu/d9g_shader* (DXVK's compiler, ruling 2) and writes SPIR-V and
// reflection; its GPU modes run the modules through SDL_gpu as the second host creates its device
// (host/d3d9gpu/test_shaders.sh runs them).
//
//   d3d9gpu_shaderc facts                    query the Vulkan device VK_DRIVER_FILES names; print
//                                            the facts determine_profile() reads, as JSON
//   d3d9gpu_shaderc profile FACTS.json       print the profile record those facts give
//   d3d9gpu_shaderc compile FACTS.json OUT [--bgra MASK] [--border-u M --border-v M --border-linear M]
//                          [--depth-clamp] [--sampler-modes WORD] [--cache DIR --repo DIR [--verify]] [--ftz-daz]
//                          [--force-sample-rate-shading] SHADER...
//                                            translate each shader (a .bin of tokens): OUT/<name>.spv
//                                            (unlinked: DXVK's getCode(nullptr, nullptr) shape),
//                                            OUT/<name>.json (reflection), OUT/<name>.ir.txt
//   d3d9gpu_shaderc link FACTS.json OUT VS.bin PS.bin NAME [--sampler-modes WORD]
//                                            the pixel shader (specialized at WORD) linked against the
//                                            vertex shader (DXVK's getCode(bindings, &linkage) shape):
//                                            OUT/NAME.spv, and OUT/NAME.json (what was linked)
//   d3d9gpu_shaderc load PLAN.json OUT [--no-features]
//                                            (GPU, R1's check C2) every object, variant, border
//                                            fallback variant and pair of r1_receipt.py's plan through
//                                            SDL_CreateGPUShader, each pair linked into a pipeline with
//                                            its census state;
//                                            OUT/<object>.spv and .json, OUT/variants/, OUT/pairs/
//                                            (the pairs' modules and their sa2-d3d9gpu-shader-pair/v1
//                                            records, spv_canon.py --linked-batch's host-2 side)
//   d3d9gpu_shaderc border OUT VS.bin PS.bin [--unspecialized]
//                                            (GPU, check C8) ruling 3's border fallback against
//                                            the SDL patch's CLAMP_TO_BORDER sampler: OUT/border.json
//                                            (the pixel shader specialized at its sampler-mode word,
//                                            or with --unspecialized the base, the frames' reference)
//   d3d9gpu_shaderc modes OUT VS.bin PS1.bin PS4.bin [--unspecialized]
//                                            (GPU, check C13) the sampler-mode specialization drawn at
//                                            every mode DXVK's rule gives a texture: OUT/modes.json,
//                                            OUT/modules/ (each case's module, the base's with
//                                            --unspecialized, for C12 and C11)
//   d3d9gpu_shaderc sampler-mode D3DFMT UPGRADED MAGFILTER [MIPMAPLODBIAS...]
//                                            DXVK's sampler mode for one register whose stage was given
//                                            those MIPMAPLODBIAS values in order (d9g_shader.h
//                                            fetch4_after, dxvk_sampler_mode)
//   d3d9gpu_shaderc probe-null OUT VS.bin PS.bin R G B A
//                                            (GPU, check C7) SDL_gpu's leg of test/null_probe.cpp:
//                                            the input DXVK leaves to its null stream fed (R, G, B, A)
//                                            per instance; the centre pixel, OUT/probe-null.json
//
// Exit status 0 only when every shader translated and built (and, for the GPU modes, their marker
// is OK); each failure is printed with the shader's name and the translator's reason. Nothing it
// writes is tracked: the inputs are shipped bytes or derived from them, so OUT must be under private/
// (the caller's responsibility; the cache refuses anything else itself).
#include "d9g_pipeline.h"
#include "d9g_sampler.h"
#include "d9g_shader.h"
#include "mini_json.h"
#include "sha256.h"

#include <SDL3/SDL.h>
#include <SDL3/SDL_vulkan.h>

#ifdef _WIN32
// Windows (tools/s7/host2_win_build.py): the Vulkan loader is vulkan-1.dll, and mkdir takes no mode.
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <direct.h>
#define mkdir(path, mode) _mkdir(path)
#else
#include <dlfcn.h>
#endif
#include <sys/stat.h>
#include <xmmintrin.h>

#include <algorithm>
#include <cmath>
#include <map>

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

using namespace d9g::shader;

namespace {

std::string read_file(const std::string& path, bool& ok) {
  std::ifstream in(path, std::ios::binary);
  ok = bool(in);
  return std::string((std::istreambuf_iterator<char>(in)), {});
}

std::string sha256_of(const void* data, size_t n) {
  Sha256 h;
  h.update(data, n);
  return h.hex();
}

std::string json_str(const std::string& s) {
  std::string o = "\"";
  for (char c : s) {
    if (c == '"' || c == '\\') { o += '\\'; o += c; }
    else if (uint8_t(c) < 0x20) { char b[8]; snprintf(b, sizeof b, "\\u%04x", c); o += b; }
    else o += c;
  }
  return o + "\"";
}

int facts() {
#ifdef _WIN32
  const HMODULE lib = LoadLibraryA("vulkan-1.dll");
  if (!lib) { fprintf(stderr, "no Vulkan loader\n"); return 2; }
  DeviceFactsQuery q = query_device_facts(reinterpret_cast<void*>(GetProcAddress(lib, "vkGetInstanceProcAddr")), "");
#else
  void* lib = dlopen("libvulkan.so.1", RTLD_NOW);
  if (!lib) { fprintf(stderr, "no Vulkan loader\n"); return 2; }
  DeviceFactsQuery q = query_device_facts(dlsym(lib, "vkGetInstanceProcAddr"), "");
#endif
  if (!q.ok) { fprintf(stderr, "facts: %s\n", q.how.c_str()); return 2; }
  fputs(facts_json(q).c_str(), stdout);
  return 0;
}

bool load_facts(const std::string& path, DeviceFacts& f, std::string& device) {
  bool ok;
  auto text = read_file(path, ok);
  if (!ok) { fprintf(stderr, "cannot read %s\n", path.c_str()); return false; }
  try {
    auto j = mini_json::parse(text);
    if (j["schema"].str() != "sa2-d3d9gpu-shader-facts/v1") throw std::runtime_error("schema");
    device = j["device"].str();
    f.driver_id = j["driver_id"].u32();
    f.driver_version_raw = j["driver_version_raw"].u32();
    f.min_storage_buffer_offset_alignment = j["min_storage_buffer_offset_alignment"].u32();
    f.max_uniform_buffer_range = j["max_uniform_buffer_range"].u32();
    f.max_per_stage_descriptor_uniform_buffers = j["max_per_stage_descriptor_uniform_buffers"].u32();
    f.shader_float16 = j["shader_float16"].boolean();
    f.storage_push_constant16 = j["storage_push_constant16"].boolean();
    f.storage_push_constant8 = j["storage_push_constant8"].boolean();
    f.r32_storage_read_without_format = j["r32_storage_read_without_format"].boolean();
    f.nv_raw_access_chains = j["nv_raw_access_chains"].boolean();
    for (int i = 0; i < 3; i++) {
      f.sz_inf_nan_preserve[i] = j["sz_inf_nan_preserve"][i].boolean();
      f.rte[i] = j["rte"][i].boolean();
      f.rtz[i] = j["rtz"][i].boolean();
      f.denorm_flush[i] = j["denorm_flush"][i].boolean();
      f.denorm_preserve[i] = j["denorm_preserve"][i].boolean();
    }
    f.rounding_mode_independence = j["rounding_mode_independence"].u32();
    f.denorm_behavior_independence = j["denorm_behavior_independence"].u32();
    f.float_controls2 = j["float_controls2"].boolean();
    f.descriptor_heap = j["descriptor_heap"].boolean();
    f.resource_indexing = j["resource_indexing"].boolean();
    // host 2's own fact, optional: a facts file written before it existed is a device that has them
    if (j.has("sub_dword_access")) f.sub_dword_access = j["sub_dword_access"].boolean();
    if (j.has("clip_distance")) f.clip_distance = j["clip_distance"].boolean();
  } catch (const std::exception& e) {
    fprintf(stderr, "%s: %s\n", path.c_str(), e.what());
    return false;
  }
  return true;
}

std::string io_json(const std::vector<IoVar>& vars) {
  std::ostringstream s;
  s << "[";
  for (size_t i = 0; i < vars.size(); i++) {
    const auto& v = vars[i];
    s << (i ? ", " : "") << "{\"builtin\": " << (v.builtin ? json_str(v.builtin_name) : "null")
      << ", \"location\": " << v.location << ", \"mask\": " << v.component_mask
      << ", \"semantic\": " << json_str(v.semantic) << ", \"semantic_index\": " << v.semantic_index << "}";
  }
  return s.str() + "]";
}

std::string ranges_json(const std::vector<ConstantRange>& rs) {
  std::ostringstream s;
  s << "[";
  for (size_t i = 0; i < rs.size(); i++)
    s << (i ? ", " : "") << "[" << rs[i].src << ", " << rs[i].dst << ", " << rs[i].count << ", "
      << (rs[i].dynamic ? "true" : "false") << ", " << (rs[i].defined ? "true" : "false") << "]";
  return s.str() + "]";
}

std::string reflection_json(const std::string& name, const std::string& bytecodeSha, const Translated& t,
                            const Built& b, const Variant& v, const std::string& profileSha) {
  const auto& r = t.reflection;
  std::ostringstream s;
  s << "{\n \"schema\": \"sa2-d3d9gpu-shader-reflection/v1\",\n \"name\": " << json_str(name)
    << ",\n \"bytecode_sha256\": " << json_str(bytecodeSha) << ",\n \"debug_name\": " << json_str(t.debug_name)
    << ",\n \"stage\": " << json_str(t.stage == Stage::Vertex ? "vertex" : "pixel") << ",\n \"version\": [" << r.major
    << ", " << r.minor << "],\n \"variant\": " << json_str(v.key()) << ",\n \"profile_sha256\": " << json_str(profileSha)
    << ",\n \"translator\": " << json_str(translator_identity()) << ",\n \"input_signature\": [";
  for (size_t i = 0; i < r.input_signature.size(); i++)
    s << (i ? ", " : "") << "[" << r.input_signature[i].first << ", " << r.input_signature[i].second << "]";
  s << "],\n \"sampler_mask\": " << r.sampler_mask << ",\n \"rt_mask\": " << r.rt_mask
    << ",\n \"flat_shading_inputs\": " << r.flat_shading_inputs << ",\n \"border_unwrapped\": {\"depth_compare\": "
    << r.border_unwrapped_depth_compare << ", \"gather\": " << r.border_unwrapped_gather << ", \"other\": "
    << r.border_unwrapped_other << "},\n \"sampler_mode_slots\": " << r.sampler_mode_slots
    << ",\n \"depth_compare_registers\": " << r.depth_compare_registers << ",\n \"gather_registers\": "
    << r.gather_registers << ",\n \"sampler_registers\": [";
  for (size_t i = 0; i < b.sampler_registers.size(); i++)
    s << (i ? ", " : "") << b.sampler_registers[i];
  s << "],\n \"num_samplers\": " << b.num_samplers << ",\n \"num_uniform_buffers\": " << b.num_uniform_buffers
    << ",\n \"uniforms\": [";
  for (size_t i = 0; i < b.uniforms.size(); i++)
    s << (i ? ", " : "") << "{\"slot\": " << b.uniforms[i].slot << ", \"bytes\": " << b.uniforms[i].bytes
      << ", \"kind\": " << json_str(b.uniforms[i].kind) << "}";
  s << "],\n \"dynamic_capped\": " << (b.dynamic_capped ? "true" : "false")
    << ",\n \"floats_dynamic\": " << (r.floats_dynamic ? "true" : "false")
    << ",\n \"float_ranges\": " << ranges_json(r.float_ranges) << ",\n \"int_ranges\": " << ranges_json(r.int_ranges)
    << ",\n \"bool_ranges\": " << ranges_json(r.bool_ranges)
    << ",\n \"static_buffer_is_dword_array\": " << (r.static_buffer_is_dword_array ? "true" : "false")
    << ",\n \"inputs\": " << io_json(r.inputs) << ",\n \"outputs\": " << io_json(r.outputs)
    << ",\n \"spirv_words\": " << b.spirv.size() << ",\n \"spirv_sha256\": "
    << json_str(sha256_of(b.spirv.data(), b.spirv.size() * 4)) << "\n}\n";
  return s.str();
}

bool write_file(const std::string& path, const void* data, size_t n) {
  std::ofstream out(path, std::ios::binary | std::ios::trunc);
  out.write(static_cast<const char*>(data), std::streamsize(n));
  return bool(out);
}

std::string stem(const std::string& path) {
  auto base = path.substr(path.find_last_of('/') + 1);
  auto dot = base.rfind('.');
  return dot == std::string::npos ? base : base.substr(0, dot);
}

// ---------------------------------------------------------------- the GPU modes (R1 U6)
// SDL_gpu's device made as the second host makes it (d9g_gpu_sdl.cpp make_sdl_gpu: Vulkan 1.3 and
// VulkanFeatures), in debug mode, the profile from its own facts (query_device_facts through the
// loader SDL loaded, as the device reads them). The validation layer is the caller's: SDL's debug
// mode enables VK_LAYER_KHRONOS_validation where the loader finds it, and test_shaders.sh names the
// pinned build (VK_ADD_LAYER_PATH, VK_INSTANCE_LAYERS) and fails on any "Validation Error" printed.
struct GpuSession {
  SDL_GPUDevice* dev = nullptr;
  std::string device_name, profile_sha;
  ShaderProfile profile;
  bool border_capability = false;
  bool open(bool with_features, std::string& err) {
    SDL_SetHint(SDL_HINT_NO_SIGNAL_HANDLERS, "1");
    if (!SDL_Init(SDL_INIT_VIDEO)) { err = std::string("SDL_Init: ") + SDL_GetError(); return false; }
    VulkanFeatures features;
    SDL_GPUVulkanOptions vo{};
    vo.vulkan_api_version = features.api_version();
    vo.feature_list = features.feature_list();
    vo.vulkan_10_physical_device_features = features.features10();
    SDL_PropertiesID props = SDL_CreateProperties();
    SDL_SetBooleanProperty(props, SDL_PROP_GPU_DEVICE_CREATE_SHADERS_SPIRV_BOOLEAN, true);
    SDL_SetBooleanProperty(props, SDL_PROP_GPU_DEVICE_CREATE_DEBUGMODE_BOOLEAN, true);
    SDL_SetStringProperty(props, SDL_PROP_GPU_DEVICE_CREATE_NAME_STRING, "vulkan");
    // --no-features (the validation layer's control): SDL's own default device, Vulkan 1.0 and its
    // minimal feature set, on which the modules' capabilities must be reported
    if (with_features) SDL_SetPointerProperty(props, SDL_PROP_GPU_DEVICE_CREATE_VULKAN_OPTIONS_POINTER, &vo);
    dev = SDL_CreateGPUDeviceWithProperties(props);
    SDL_DestroyProperties(props);
    if (!dev) { err = std::string("SDL_CreateGPUDeviceWithProperties: ") + SDL_GetError(); return false; }
    SDL_PropertiesID dp = SDL_GetGPUDeviceProperties(dev);
    device_name = SDL_GetStringProperty(dp, SDL_PROP_GPU_DEVICE_NAME_STRING, "");
    border_capability = SDL_GetBooleanProperty(dp, "SDL.gpu.device.sa2recomp.sampler_clamp_to_border", false);
    DeviceFactsQuery q = query_device_facts(reinterpret_cast<void*>(SDL_Vulkan_GetVkGetInstanceProcAddr()), device_name);
    if (!q.ok) { err = "the device facts: " + q.how; return false; }
    profile = determine_profile(q.facts);
    std::string rec = profile.record();
    profile_sha = sha256_of(rec.data(), rec.size());
    printf("DEVICE %s (%s %s) profile %s\n", device_name.c_str(), q.driver_name.c_str(), q.driver_info.c_str(),
           profile_sha.substr(0, 16).c_str());
    return true;
  }
  SDL_GPUShader* shader(const Built& b, Stage stage) const {
    SDL_GPUShaderCreateInfo ci{};
    ci.code_size = b.spirv.size() * sizeof(uint32_t);
    ci.code = reinterpret_cast<const Uint8*>(b.spirv.data());
    ci.entrypoint = "main";
    ci.format = SDL_GPU_SHADERFORMAT_SPIRV;
    ci.stage = stage == Stage::Vertex ? SDL_GPU_SHADERSTAGE_VERTEX : SDL_GPU_SHADERSTAGE_FRAGMENT;
    ci.num_samplers = b.num_samplers;
    ci.num_uniform_buffers = b.num_uniform_buffers;
    return SDL_CreateGPUShader(dev, &ci);
  }
  ~GpuSession() {
    if (dev) SDL_DestroyGPUDevice(dev);
    SDL_Quit();
  }
};

// The pixel shader's variant a device makes for a draw: specialized at the sampler-mode word DXVK's rule
// gives the textures and sampler states bound at its registers (d9g_shader.h dxvk_sampler_mode and
// sampler_modes_word, masked by the registers whose mode the shader reads), or the base where
// --unspecialized asks for it: the reference the specialized legs' frames are held to (the base keeps
// DXVK's depth-compare sample and gather on every register, and reads the word from its state buffer).
// word is that word either way: the state buffer carries it, as DXVK's spec data does.
Variant modes_variant(const Translated& base, const SamplerSlot (&slots)[16], bool unspecialized, uint32_t& word) {
  Variant v;
  word = sampler_modes_word(slots, base.reflection.sampler_mode_slots);
  if (!unspecialized)
    v.sampler_modes = word;
  return v;
}

// The pixel shader's state (uniform slot kPsState): spec dword 0's alpha compare op ALWAYS (7, as DXVK
// sets it with the alpha test off), spec dword 5 the sampler-mode word, as DXVK's spec data carries it
// (the base reads it; a specialized module reads nothing of it), the rest zero.
void push_ps_state(SDL_GPUCommandBuffer* cb, uint32_t sampler_modes) {
  uint8_t state[kStateBytesPs] = {};
  state[0] = 7;
  std::memcpy(state + 4 * 5, &sampler_modes, 4);
  SDL_PushGPUFragmentUniformData(cb, kPsState, state, sizeof state);
}

constexpr uint32_t kD3dFmtA8R8G8B8 = 21, kD3dFmtL8 = 50, kD3dFmtD24S8 = 75, kD3dFmtD16 = 80;
constexpr uint32_t kGet4 = 0x34544547u;   // MAKEFOURCC('G', 'E', 'T', '4'): D3DSAMP_MIPMAPLODBIAS turns fetch4 on
constexpr uint32_t kGet1 = 0x31544547u;   // MAKEFOURCC('G', 'E', 'T', '1'): and off (d9g_shader.h fetch4_after)
constexpr uint32_t kD3dTexfPointFilter = 1;

// One FLOAT4 attribute per input the vertex shader declares, at its location, from one buffer: the
// load check's own vertex input. U9 maps the device's vertex input by the input signature
// (BindInputLayout); until then the load check needs only that every input the module reads is fed.
struct ProbeInput {
  std::vector<SDL_GPUVertexAttribute> attrs;
  SDL_GPUVertexBufferDescription vb{};
};
ProbeInput probe_input(const Translated& vs) {
  ProbeInput p;
  for (const IoVar& v : vs.reflection.inputs) {
    if (v.builtin) continue;
    SDL_GPUVertexAttribute a{};
    a.location = v.location;
    a.buffer_slot = 0;
    a.format = SDL_GPU_VERTEXELEMENTFORMAT_FLOAT4;
    a.offset = uint32_t(16 * p.attrs.size());
    p.attrs.push_back(a);
  }
  p.vb.slot = 0;
  p.vb.pitch = uint32_t(16 * std::max<size_t>(1, p.attrs.size()));
  p.vb.input_rate = SDL_GPU_VERTEXINPUTRATE_VERTEX;
  return p;
}

std::string pair_json(const std::string& name, bool drawn, const std::string& vsName, const Translated& vs,
                      const Variant& vsVariant, const std::string& psName, const Translated& ps, const Variant& psVariant,
                      const Built& psB, bool compatible, const std::string& profileSha) {
  std::ostringstream s;
  s << "{\n \"schema\": \"sa2-d3d9gpu-shader-pair/v1\",\n \"name\": " << json_str(name) << ",\n \"drawn\": "
    << (drawn ? "true" : "false") << ",\n \"vs\": {\"name\": " << json_str(vsName) << ", \"debug_name\": "
    << json_str(vs.debug_name) << ", \"variant\": " << json_str(vsVariant.key()) << ", \"module\": "
    << json_str(name + ".vs.spv") << "},\n \"ps\": {\"name\": " << json_str(psName) << ", \"debug_name\": "
    << json_str(ps.debug_name) << ", \"variant\": " << json_str(psVariant.key()) << ", \"module\": "
    << json_str(name + ".ps.spv") << "},\n \"io_rewritten\": "
    << (psB.io_rewritten ? "true" : "false") << ",\n \"compatible\": " << (compatible ? "true" : "false")
    << ",\n \"profile_sha256\": " << json_str(profileSha) << ",\n \"translator\": " << json_str(translator_identity())
    << "\n}\n";
  return s.str();
}

// load PLAN.json OUT [--no-features]: every object, every variant and every pair the plan names
// (host/d3d9gpu/test/r1_receipt.py plan, from the device's dump and keys files), each through
// SDL_CreateGPUShader, and each pair linked into a graphics pipeline with its census state (its
// targets' formats and its primitive type; the vertex input is probe_input's). Nothing is drawn: C2
// is a load check, C3 holds the code. The objects are the base (Variant{}: the sampler-mode word
// read from the state buffer, C3's GPL half against DXVK's dump); every pixel shader is also
// specialized at the plan's sampler-mode word (OUT/specialized/), as are the pairs' pixel shaders and
// the border variants: the modules a device is to hand the driver once it computes each draw's word
// (R2-draws; the device still translates the base, d9g_device.cpp). SHADERS_LOAD_OK only when everything
// the plan names was created; anything not created, or not attempted, is named in SHADERS_LOAD_FAIL.
int load(const std::string& planPath, const std::string& out, bool withFeatures) {
  bool ok;
  std::string text = read_file(planPath, ok);
  if (!ok) { fprintf(stderr, "cannot read %s\n", planPath.c_str()); return 2; }
  mini_json::Value plan;
  try {
    plan = mini_json::parse(text);
    if (plan["schema"].str() != "sa2-d3d9gpu-r1-load-plan/v1") throw std::runtime_error("schema");
  } catch (const std::exception& e) {
    fprintf(stderr, "%s: not a load plan (%s)\n", planPath.c_str(), e.what());
    return 2;
  }
  GpuSession g;
  std::string err;
  if (!g.open(withFeatures, err)) { printf("FAIL device: %s\nSHADERS_LOAD_FAIL device\n", err.c_str()); return 1; }
  std::vector<std::string> missing;
  struct Obj { std::string bytes; Translated base; bool ok = false; };
  std::map<std::string, Obj> objs;
  std::vector<SDL_GPUShader*> made;
  size_t nShaders = 0, nVariants = 0, nPairs = 0, nSpecialized = 0;
  const std::string pairsDir = out + "/pairs", variantsDir = out + "/variants", specDir = out + "/specialized";
  mkdir(pairsDir.c_str(), 0755);
  mkdir(variantsDir.c_str(), 0755);
  mkdir(specDir.c_str(), 0755);
  auto modes_variant = [](const mini_json::Value& v) {
    Variant var;
    var.sampler_modes = v["sampler_modes"].u32();
    return var;
  };
  // the census state's names (r1_receipt.py plan): D3DFMT_A8R8G8B8 and X8R8G8B8 targets are SDL's
  // B8G8R8A8_UNORM (d9g_format.cpp), D24S8 the device's depth format (D24_UNORM_S8_UINT where SDL
  // supports it as a depth target, else D32_FLOAT_S8_UINT, as d9g_gpu_sdl.cpp chooses), and D3D9's
  // primitive types SDL's (a fan is drawn as a list through the fan cache, d9g_draw)
  const SDL_GPUTextureFormat depth24 = SDL_GPUTextureSupportsFormat(g.dev, SDL_GPU_TEXTUREFORMAT_D24_UNORM_S8_UINT,
      SDL_GPU_TEXTURETYPE_2D, SDL_GPU_TEXTUREUSAGE_DEPTH_STENCIL_TARGET) ? SDL_GPU_TEXTUREFORMAT_D24_UNORM_S8_UINT
                                                                         : SDL_GPU_TEXTUREFORMAT_D32_FLOAT_S8_UINT;
  const std::map<std::string, SDL_GPUTextureFormat> colourFormats = {{"B8G8R8A8_UNORM", SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM}};
  const std::map<std::string, SDL_GPUPrimitiveType> primitives = {
      {"POINTLIST", SDL_GPU_PRIMITIVETYPE_POINTLIST}, {"LINELIST", SDL_GPU_PRIMITIVETYPE_LINELIST},
      {"LINESTRIP", SDL_GPU_PRIMITIVETYPE_LINESTRIP}, {"TRIANGLELIST", SDL_GPU_PRIMITIVETYPE_TRIANGLELIST},
      {"TRIANGLESTRIP", SDL_GPU_PRIMITIVETYPE_TRIANGLESTRIP}};
  for (const auto& s : plan["shaders"].items) {
    const std::string name = s["name"].str();
    Obj& o = objs[name];
    o.bytes = read_file(s["bytecode"].str(), ok);
    if (!ok) { printf("FAIL shader %s: cannot read %s\n", name.c_str(), s["bytecode"].str().c_str()); missing.push_back(name); continue; }
    o.base = translate(o.bytes.data(), o.bytes.size(), g.profile, {});
    const char* want = s["stage"].str() == "vertex" ? "vertex" : "pixel";
    if (!o.base.ok || (o.base.stage == Stage::Vertex) != !strcmp(want, "vertex")) {
      printf("FAIL shader %s: %s\n", name.c_str(), o.base.ok ? "not the stage the plan names" : o.base.error.c_str());
      missing.push_back(name);
      continue;
    }
    Built b = build(o.base, g.profile, nullptr);
    SDL_GPUShader* sh = b.ok ? g.shader(b, o.base.stage) : nullptr;
    if (!sh) { printf("FAIL shader %s: %s\n", name.c_str(), b.ok ? SDL_GetError() : b.error.c_str()); missing.push_back(name); continue; }
    made.push_back(sh);
    o.ok = true;
    write_file(out + "/" + name + ".spv", b.spirv.data(), b.spirv.size() * 4);
    auto js = reflection_json(name, sha256_of(o.bytes.data(), o.bytes.size()), o.base, b, {}, g.profile_sha);
    write_file(out + "/" + name + ".json", js.data(), js.size());
    printf("SHADER %s %s words=%zu samplers=%u ubos=%u\n", name.c_str(), o.base.debug_name.c_str(), b.spirv.size(),
           b.num_samplers, b.num_uniform_buffers);
    nShaders++;
  }
  // every pixel shader specialized at its sampler-mode word (Variant::sampler_modes)
  std::map<std::pair<std::string, uint32_t>, Translated> specialized;
  for (const auto& v : plan["specialized"].items) {
    const std::string name = v["shader"].str();
    const Variant var = modes_variant(v);
    char label[96];
    snprintf(label, sizeof label, "%s.modes%#x", name.c_str(), *var.sampler_modes);
    auto it = objs.find(name);
    if (it == objs.end() || !it->second.ok) { printf("FAIL specialized %s: its shader did not load\n", label); missing.push_back(label); continue; }
    Translated t = translate(it->second.bytes.data(), it->second.bytes.size(), g.profile, var);
    Built b = t.ok ? build(t, g.profile, nullptr) : Built();
    SDL_GPUShader* sh = b.ok ? g.shader(b, t.stage) : nullptr;
    if (!sh) { printf("FAIL specialized %s: %s\n", label, !t.ok ? t.error.c_str() : !b.ok ? b.error.c_str() : SDL_GetError()); missing.push_back(label); continue; }
    made.push_back(sh);
    write_file(specDir + "/" + name + ".spv", b.spirv.data(), b.spirv.size() * 4);
    auto js = reflection_json(name, sha256_of(it->second.bytes.data(), it->second.bytes.size()), t, b, var, g.profile_sha);
    write_file(specDir + "/" + name + ".json", js.data(), js.size());
    printf("SPECIALIZED %s words=%zu slots=%#x depth_compare=%#x gather=%#x\n", label, b.spirv.size(),
           t.reflection.sampler_mode_slots, t.reflection.depth_compare_registers, t.reflection.gather_registers);
    specialized[{name, *var.sampler_modes}] = std::move(t);
    nSpecialized++;
  }
  std::map<std::pair<std::string, uint32_t>, Translated> variants;
  for (const auto& v : plan["variants"].items) {
    const std::string name = v["shader"].str();
    Variant var;
    var.bgra_mask = v["bgra"].u32();
    const std::string label = name + ".bgra" + std::to_string(var.bgra_mask);
    auto it = objs.find(name);
    if (it == objs.end() || !it->second.ok) { printf("FAIL variant %s: its shader did not load\n", label.c_str()); missing.push_back(label); continue; }
    Translated t = translate(it->second.bytes.data(), it->second.bytes.size(), g.profile, var);
    Built b = t.ok ? build(t, g.profile, nullptr) : Built();
    SDL_GPUShader* sh = b.ok ? g.shader(b, t.stage) : nullptr;
    if (!sh) { printf("FAIL variant %s: %s\n", label.c_str(), !t.ok ? t.error.c_str() : !b.ok ? b.error.c_str() : SDL_GetError()); missing.push_back(label); continue; }
    made.push_back(sh);
    write_file(variantsDir + "/" + label + ".spv", b.spirv.data(), b.spirv.size() * 4);
    printf("VARIANT %s words=%zu\n", label.c_str(), b.spirv.size());
    variants[{name, var.bgra_mask}] = std::move(t);
    nVariants++;
  }
  // ruling 3's border fallback variants (Variant::border_u, border_v, border_linear) the census's
  // BORDER samplers need on a device without CLAMP_TO_BORDER: each translated, built and created
  size_t nBorder = 0;
  for (const auto& v : plan["border_variants"].items) {
    const std::string name = v["shader"].str();
    Variant var = modes_variant(v);
    var.border_u = uint16_t(v["border_u"].u32());
    var.border_v = uint16_t(v["border_v"].u32());
    var.border_linear = uint16_t(v["border_linear"].u32());
    const std::string label = name + ".border-" + std::to_string(var.border_u) + "-" + std::to_string(var.border_v) + "-" +
                              std::to_string(var.border_linear);
    auto it = objs.find(name);
    if (it == objs.end() || !it->second.ok) { printf("FAIL border %s: its shader did not load\n", label.c_str()); missing.push_back(label); continue; }
    Translated t = translate(it->second.bytes.data(), it->second.bytes.size(), g.profile, var);
    Built b = t.ok ? build(t, g.profile, nullptr) : Built();
    SDL_GPUShader* sh = b.ok ? g.shader(b, t.stage) : nullptr;
    if (!sh) { printf("FAIL border %s: %s\n", label.c_str(), !t.ok ? t.error.c_str() : !b.ok ? b.error.c_str() : SDL_GetError()); missing.push_back(label); continue; }
    made.push_back(sh);
    write_file(variantsDir + "/" + label + ".spv", b.spirv.data(), b.spirv.size() * 4);
    // the reads of the border set the emulation leaves (d9g_shader.h Reflection::border_unwrapped_*): the
    // device's obligation at the draw (R2-draws), recorded by the load check
    printf("BORDER %s words=%zu ubos=%u unwrapped depth_compare=%u gather=%u other=%u\n", label.c_str(), b.spirv.size(),
           b.num_uniform_buffers, t.reflection.border_unwrapped_depth_compare, t.reflection.border_unwrapped_gather,
           t.reflection.border_unwrapped_other);
    nBorder++;
  }
  for (const auto& p : plan["pairs"].items) {
    const std::string name = p["name"].str(), vsName = p["vs"].str(), psName = p["ps"].str();
    const uint32_t bgra = p["bgra"].u32();
    auto vi = objs.find(vsName), pi = objs.find(psName);
    if (vi == objs.end() || pi == objs.end() || !vi->second.ok || !pi->second.ok) {
      printf("FAIL pair %s: a shader of it did not load\n", name.c_str());
      missing.push_back(name);
      continue;
    }
    const Translated* vs = &vi->second.base;
    if (bgra) {
      auto vv = variants.find({vsName, bgra});
      if (vv == variants.end()) { printf("FAIL pair %s: its BGRA variant %u did not load\n", name.c_str(), bgra); missing.push_back(name); continue; }
      vs = &vv->second;
    }
    Variant vsVariant;
    vsVariant.bgra_mask = bgra;
    // the pair's pixel shader specialized at the pair's word: the module a device is to hand the driver
    // for the pair's draws once it computes each draw's word (R2-draws; d9g_device.cpp still translates
    // the base)
    const Variant psVariant = modes_variant(p);
    auto si = specialized.find({psName, *psVariant.sampler_modes});
    if (si == specialized.end()) {
      printf("FAIL pair %s: its pixel shader specialized at %#x did not load\n", name.c_str(), *psVariant.sampler_modes);
      missing.push_back(name);
      continue;
    }
    const Translated& ps = si->second;
    Linkage link;
    link.vs = vs;
    Built vsB = build(*vs, g.profile, nullptr), psB = build(ps, g.profile, &link);
    SDL_GPUShader* vsh = vsB.ok ? g.shader(vsB, Stage::Vertex) : nullptr;
    SDL_GPUShader* psh = psB.ok ? g.shader(psB, Stage::Pixel) : nullptr;
    if (vsh) made.push_back(vsh);
    if (psh) made.push_back(psh);
    if (!vsh || !psh) {
      printf("FAIL pair %s: %s\n", name.c_str(), !vsB.ok ? vsB.error.c_str() : !psB.ok ? psB.error.c_str() : SDL_GetError());
      missing.push_back(name);
      continue;
    }
    ProbeInput in = probe_input(*vs);
    std::vector<SDL_GPUColorTargetDescription> ct;
    bool known = primitives.count(p["primitive"].str()) && (p["depth"].str() == "none" || p["depth"].str() == "D24S8");
    for (const auto& f : p["colour_formats"].items) {
      SDL_GPUColorTargetDescription d{};
      auto cf = colourFormats.find(f.str());
      known = known && cf != colourFormats.end();
      if (cf != colourFormats.end()) d.format = cf->second;
      ct.push_back(d);
    }
    if (!known) { printf("FAIL pair %s: a census state the load check has no SDL value for\n", name.c_str()); missing.push_back(name); continue; }
    SDL_GPUGraphicsPipelineCreateInfo pci{};
    pci.vertex_shader = vsh;
    pci.fragment_shader = psh;
    pci.vertex_input_state.vertex_buffer_descriptions = &in.vb;
    pci.vertex_input_state.num_vertex_buffers = in.attrs.empty() ? 0u : 1u;
    pci.vertex_input_state.vertex_attributes = in.attrs.data();
    pci.vertex_input_state.num_vertex_attributes = uint32_t(in.attrs.size());
    pci.primitive_type = primitives.at(p["primitive"].str());
    pci.rasterizer_state.fill_mode = SDL_GPU_FILLMODE_FILL;
    pci.rasterizer_state.cull_mode = SDL_GPU_CULLMODE_NONE;
    pci.rasterizer_state.front_face = SDL_GPU_FRONTFACE_CLOCKWISE;
    pci.rasterizer_state.enable_depth_clip = true;
    pci.target_info.color_target_descriptions = ct.data();
    pci.target_info.num_color_targets = uint32_t(ct.size());
    const bool ds = p["depth"].str() == "D24S8";
    if (ds) {
      pci.target_info.has_depth_stencil_target = true;
      pci.target_info.depth_stencil_format = depth24;
      pci.depth_stencil_state.enable_depth_test = true;
      pci.depth_stencil_state.enable_depth_write = true;
      pci.depth_stencil_state.compare_op = SDL_GPU_COMPAREOP_LESS_OR_EQUAL;
    }
    SDL_GPUGraphicsPipeline* gp = SDL_CreateGPUGraphicsPipeline(g.dev, &pci);
    if (!gp) { printf("FAIL pair %s: pipeline: %s\n", name.c_str(), SDL_GetError()); missing.push_back(name); continue; }
    SDL_ReleaseGPUGraphicsPipeline(g.dev, gp);
    const bool compatible = stages_compatible(*vs, ps, true);
    write_file(pairsDir + "/" + name + ".vs.spv", vsB.spirv.data(), vsB.spirv.size() * 4);
    write_file(pairsDir + "/" + name + ".ps.spv", psB.spirv.data(), psB.spirv.size() * 4);
    auto js = pair_json(name, p["drawn"].boolean(), vsName, *vs, vsVariant, psName, ps, psVariant, psB, compatible, g.profile_sha);
    write_file(pairsDir + "/" + name + ".json", js.data(), js.size());
    printf("PAIR %s vs %s ps %s bgra %u modes %#x drawn %d io_rewritten=%d compatible=%d attrs=%zu targets=%zu depth=%s prim=%s\n",
           name.c_str(), vsName.c_str(), psName.c_str(), bgra, *psVariant.sampler_modes, int(p["drawn"].boolean()),
           int(psB.io_rewritten), int(compatible), in.attrs.size(), ct.size(), ds ? "D24S8" : "none", p["primitive"].str().c_str());
    nPairs++;
  }
  for (SDL_GPUShader* sh : made) SDL_ReleaseGPUShader(g.dev, sh);
  const size_t wantS = plan["shaders"].size(), wantV = plan["variants"].size(), wantP = plan["pairs"].size();
  const size_t wantB = plan["border_variants"].size(), wantM = plan["specialized"].size();
  printf("LOAD shaders %zu/%zu variants %zu/%zu pairs %zu/%zu border %zu/%zu specialized %zu/%zu\n", nShaders, wantS, nVariants,
         wantV, nPairs, wantP, nBorder, wantB, nSpecialized, wantM);
  if (nShaders != wantS || nVariants != wantV || nPairs != wantP || nBorder != wantB || nSpecialized != wantM || !missing.empty() ||
      !wantS || !wantP || !wantM) {
    std::string names;
    for (const auto& m : missing) names += " " + m;
    printf("SHADERS_LOAD_FAIL%s\n", names.empty() ? " (counts)" : names.c_str());
    return 1;
  }
  printf("SHADERS_LOAD_OK\n");
  return 0;
}

// ---- the border fallback, measured (C8): ruling 3's emulation against the SDL patch's sampler
// A 640 x 480 target, a 4 x 4 texture of distinct opaque texels (R0's border tests, next/D/r0
// sdltest/sdlr0.c), a quad whose texture coordinates run -0.5 to 1.5, and three legs per filter and
// border colour: the patch (CLAMP_TO_BORDER with the colour, the base pixel shader), the emulation
// (CLAMP_TO_EDGE, as the device makes a BORDER sampler under SA2_D3D9GPU_CAPS_OFF=border, and the
// border variant with the colour in its buffer), and the control (CLAMP_TO_EDGE with the base pixel
// shader: the emulation off, so border reads are edge texels). The shaders are the caller's (synthetic,
// test_shaders.sh assembles them): a vertex shader passing position and texcoord0 through, a pixel
// shader sampling s0 at texcoord0. Point filtering must be bit-equal; linear's differing channels and
// largest difference are the named residue; the control must differ from the patch.
struct Frame { std::vector<uint8_t> bgra; bool ok = false; std::string err; };

Frame draw_border(GpuSession& g, SDL_GPUShader* vsh, SDL_GPUShader* psh, const Built& vsB, const Built& psB,
                  const Translated& vsT, SDL_GPUTexture* tex, SDL_GPUSampler* smp, const float colour[4], uint32_t sampler_modes) {
  const uint32_t W = 640, H = 480;
  Frame f;
  ProbeInput in = probe_input(vsT);
  SDL_GPUColorTargetDescription ctd{};
  ctd.format = SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM;
  SDL_GPUGraphicsPipelineCreateInfo pci{};
  pci.vertex_shader = vsh;
  pci.fragment_shader = psh;
  pci.vertex_input_state.vertex_buffer_descriptions = &in.vb;
  pci.vertex_input_state.num_vertex_buffers = 1;
  pci.vertex_input_state.vertex_attributes = in.attrs.data();
  pci.vertex_input_state.num_vertex_attributes = uint32_t(in.attrs.size());
  pci.primitive_type = SDL_GPU_PRIMITIVETYPE_TRIANGLESTRIP;
  pci.rasterizer_state.cull_mode = SDL_GPU_CULLMODE_NONE;
  pci.rasterizer_state.enable_depth_clip = true;
  pci.target_info.color_target_descriptions = &ctd;
  pci.target_info.num_color_targets = 1;
  SDL_GPUGraphicsPipeline* gp = SDL_CreateGPUGraphicsPipeline(g.dev, &pci);
  if (!gp) { f.err = std::string("pipeline: ") + SDL_GetError(); return f; }
  // the quad: position (x, y, 0.5, 1) and texcoord0 (u, v, 0, 1) at the vertex shader's input
  // locations, in probe_input's order; u and v run -0.5 to 1.5 across the target
  std::vector<float> verts;
  const float corners[4][4] = {{-1, -1, -0.5f, 1.5f}, {-1, 1, -0.5f, -0.5f}, {1, -1, 1.5f, 1.5f}, {1, 1, 1.5f, -0.5f}};
  for (const auto& c : corners) {
    for (const IoVar& v : vsT.reflection.inputs) {
      if (v.builtin) continue;
      if (v.semantic == "position") { verts.insert(verts.end(), {c[0], c[1], 0.5f, 1.0f}); }
      else { verts.insert(verts.end(), {c[2], c[3], 0.0f, 1.0f}); }
    }
  }
  SDL_GPUBufferCreateInfo bi{SDL_GPU_BUFFERUSAGE_VERTEX, uint32_t(verts.size() * 4), 0};
  SDL_GPUBuffer* vb = SDL_CreateGPUBuffer(g.dev, &bi);
  SDL_GPUTextureCreateInfo ti{};
  ti.type = SDL_GPU_TEXTURETYPE_2D;
  ti.format = SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM;
  ti.usage = SDL_GPU_TEXTUREUSAGE_COLOR_TARGET;
  ti.width = W; ti.height = H; ti.layer_count_or_depth = 1; ti.num_levels = 1;
  SDL_GPUTexture* rt = SDL_CreateGPUTexture(g.dev, &ti);
  SDL_GPUTransferBufferCreateInfo up{SDL_GPU_TRANSFERBUFFERUSAGE_UPLOAD, uint32_t(verts.size() * 4), 0};
  SDL_GPUTransferBuffer* ub = SDL_CreateGPUTransferBuffer(g.dev, &up);
  SDL_GPUTransferBufferCreateInfo down{SDL_GPU_TRANSFERBUFFERUSAGE_DOWNLOAD, W * H * 4, 0};
  SDL_GPUTransferBuffer* db = SDL_CreateGPUTransferBuffer(g.dev, &down);
  if (!vb || !rt || !ub || !db) { f.err = std::string("resources: ") + SDL_GetError(); return f; }
  std::memcpy(SDL_MapGPUTransferBuffer(g.dev, ub, false), verts.data(), verts.size() * 4);
  SDL_UnmapGPUTransferBuffer(g.dev, ub);
  SDL_GPUCommandBuffer* cb = SDL_AcquireGPUCommandBuffer(g.dev);
  SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
  SDL_GPUTransferBufferLocation src{ub, 0};
  SDL_GPUBufferRegion dst{vb, 0, uint32_t(verts.size() * 4)};
  SDL_UploadToGPUBuffer(cp, &src, &dst, false);
  SDL_EndGPUCopyPass(cp);
  // every uniform slot the shaders declare: zeros, but the pixel shader's state (slot 1: spec dword
  // 0's alpha compare op ALWAYS, 7, as DXVK sets it with the alpha test off) and the border colours
  // (slot 3, register 0)
  static uint8_t zeros[kSdlUniformWindow];
  for (uint32_t s = 0; s < vsB.num_uniform_buffers; s++) SDL_PushGPUVertexUniformData(cb, s, zeros, sizeof zeros);
  for (uint32_t s = 0; s < psB.num_uniform_buffers; s++) SDL_PushGPUFragmentUniformData(cb, s, zeros, sizeof zeros);
  push_ps_state(cb, sampler_modes);
  if (psB.num_uniform_buffers > kPsBorderColours) {
    float colours[kBorderColourRegisters * 4] = {};
    std::memcpy(colours, colour, 16);
    SDL_PushGPUFragmentUniformData(cb, kPsBorderColours, colours, sizeof colours);
  }
  SDL_GPUColorTargetInfo ct{};
  ct.texture = rt;
  ct.load_op = SDL_GPU_LOADOP_CLEAR;
  ct.store_op = SDL_GPU_STOREOP_STORE;
  SDL_GPURenderPass* rp = SDL_BeginGPURenderPass(cb, &ct, 1, nullptr);
  SDL_BindGPUGraphicsPipeline(rp, gp);
  SDL_GPUBufferBinding vbb{vb, 0};
  SDL_BindGPUVertexBuffers(rp, 0, &vbb, 1);
  SDL_GPUTextureSamplerBinding tsb{tex, smp};
  SDL_BindGPUFragmentSamplers(rp, 0, &tsb, 1);
  SDL_DrawGPUPrimitives(rp, 4, 1, 0, 0);
  SDL_EndGPURenderPass(rp);
  cp = SDL_BeginGPUCopyPass(cb);
  SDL_GPUTextureRegion reg{};
  reg.texture = rt; reg.w = W; reg.h = H; reg.d = 1;
  SDL_GPUTextureTransferInfo tti{db, 0, 0, 0};
  SDL_DownloadFromGPUTexture(cp, &reg, &tti);
  SDL_EndGPUCopyPass(cp);
  SDL_GPUFence* fence = SDL_SubmitGPUCommandBufferAndAcquireFence(cb);
  if (!fence || !SDL_WaitForGPUFences(g.dev, true, &fence, 1)) { f.err = std::string("submit: ") + SDL_GetError(); return f; }
  SDL_ReleaseGPUFence(g.dev, fence);
  f.bgra.resize(size_t(W) * H * 4);
  std::memcpy(f.bgra.data(), SDL_MapGPUTransferBuffer(g.dev, db, false), f.bgra.size());
  SDL_UnmapGPUTransferBuffer(g.dev, db);
  SDL_ReleaseGPUTransferBuffer(g.dev, ub);
  SDL_ReleaseGPUTransferBuffer(g.dev, db);
  SDL_ReleaseGPUTexture(g.dev, rt);
  SDL_ReleaseGPUBuffer(g.dev, vb);
  SDL_ReleaseGPUGraphicsPipeline(g.dev, gp);
  f.ok = true;
  return f;
}

int border(const std::string& out, const std::string& vsPath, const std::string& psPath, bool unspecialized) {
  bool ok1, ok2;
  std::string vsb = read_file(vsPath, ok1), psb = read_file(psPath, ok2);
  if (!ok1 || !ok2) { fprintf(stderr, "cannot read the shaders\n"); return 2; }
  GpuSession g;
  std::string err;
  if (!g.open(true, err)) { printf("FAIL device: %s\nBORDER_FALLBACK_FAIL device\n", err.c_str()); return 1; }
  if (!g.border_capability) {
    printf("BORDER_FALLBACK_FAIL the patch's leg needs CLAMP_TO_BORDER (SDL_PROP_GPU_DEVICE_SA2RECOMP_SAMPLER_CLAMP_TO_BORDER_BOOLEAN)\n");
    return 1;
  }
  Translated vsT = translate(vsb.data(), vsb.size(), g.profile, {});
  // s0 holds a B8G8R8A8 colour texture with a plain sampler: DXVK's rule gives it mode 0
  SamplerSlot slots[16] = {};
  slots[0].bound = true;
  slots[0].d3dfmt = kD3dFmtA8R8G8B8;
  uint32_t word = 0;
  const Variant pv = modes_variant(translate(psb.data(), psb.size(), g.profile, {}), slots, unspecialized, word);
  Translated psT = translate(psb.data(), psb.size(), g.profile, pv);
  if (!vsT.ok || !psT.ok) { printf("BORDER_FALLBACK_FAIL translation: %s\n", (vsT.ok ? psT.error : vsT.error).c_str()); return 1; }
  Built vsB = build(vsT, g.profile, nullptr), psB = build(psT, g.profile, nullptr);
  SDL_GPUShader* vsh = vsB.ok ? g.shader(vsB, Stage::Vertex) : nullptr;
  SDL_GPUShader* psh = psB.ok ? g.shader(psB, Stage::Pixel) : nullptr;
  if (!vsh || !psh) { printf("BORDER_FALLBACK_FAIL shaders: %s\n", SDL_GetError()); return 1; }
  printf("MODES variant %s\n", pv.key().c_str());
  // the texture: R0's 4 x 4 texels, opaque, distinct, no zero channel, as B, G, R, A
  uint8_t texels[4 * 4 * 4];
  for (int i = 0; i < 16; i++) {
    texels[4 * i + 0] = uint8_t(40 + 13 * i); texels[4 * i + 1] = uint8_t(200 - 9 * i);
    texels[4 * i + 2] = uint8_t(90 + 7 * i); texels[4 * i + 3] = 255;
  }
  SDL_GPUTextureCreateInfo ti{};
  ti.type = SDL_GPU_TEXTURETYPE_2D;
  ti.format = SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM;
  ti.usage = SDL_GPU_TEXTUREUSAGE_SAMPLER;
  ti.width = 4; ti.height = 4; ti.layer_count_or_depth = 1; ti.num_levels = 1;
  SDL_GPUTexture* tex = SDL_CreateGPUTexture(g.dev, &ti);
  {
    SDL_GPUTransferBufferCreateInfo up{SDL_GPU_TRANSFERBUFFERUSAGE_UPLOAD, sizeof texels, 0};
    SDL_GPUTransferBuffer* ub = SDL_CreateGPUTransferBuffer(g.dev, &up);
    std::memcpy(SDL_MapGPUTransferBuffer(g.dev, ub, false), texels, sizeof texels);
    SDL_UnmapGPUTransferBuffer(g.dev, ub);
    SDL_GPUCommandBuffer* cb = SDL_AcquireGPUCommandBuffer(g.dev);
    SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
    SDL_GPUTextureTransferInfo src{ub, 0, 0, 0};
    SDL_GPUTextureRegion dst{};
    dst.texture = tex; dst.w = 4; dst.h = 4; dst.d = 1;
    SDL_UploadToGPUTexture(cp, &src, &dst, false);
    SDL_EndGPUCopyPass(cp);
    SDL_SubmitGPUCommandBuffer(cb);
    SDL_WaitForGPUIdle(g.dev);
    SDL_ReleaseGPUTransferBuffer(g.dev, ub);
  }
  struct Colour { const char* name; int sdl; float rgba[4]; };
  const Colour colours[] = {{"transparent_black", d9g::kBorderTransparentBlack, {0, 0, 0, 0}},
                            {"opaque_white", d9g::kBorderOpaqueWhite, {1, 1, 1, 1}}};
  std::ostringstream js;
  js << "{\n \"schema\": \"sa2-d3d9gpu-border-fallback/v1\",\n \"device\": " << json_str(g.device_name)
     << ",\n \"profile_sha256\": " << json_str(g.profile_sha) << ",\n \"pixel_shader_variant\": " << json_str(pv.key())
     << ",\n \"cases\": [";
  bool pass = true;
  int n = 0;
  std::vector<SDL_GPUShader*> extra;
  for (bool linear : {false, true}) {
    Variant v = pv;
    v.border_u = v.border_v = 1;
    v.border_linear = linear ? 1 : 0;
    Translated emT = translate(psb.data(), psb.size(), g.profile, v);
    Built emB = emT.ok ? build(emT, g.profile, nullptr) : Built();
    SDL_GPUShader* emh = emB.ok ? g.shader(emB, Stage::Pixel) : nullptr;
    if (!emh) { printf("BORDER_FALLBACK_FAIL the border variant: %s\n", !emT.ok ? emT.error.c_str() : emB.error.c_str()); return 1; }
    extra.push_back(emh);
    for (const Colour& c : colours) {
      auto sampler = [&](bool border) {
        SDL_GPUSamplerCreateInfo si{};
        si.min_filter = si.mag_filter = linear ? SDL_GPU_FILTER_LINEAR : SDL_GPU_FILTER_NEAREST;
        si.mipmap_mode = SDL_GPU_SAMPLERMIPMAPMODE_NEAREST;
        si.address_mode_u = si.address_mode_v = si.address_mode_w =
            border ? d9g::kAddressClampToBorder : SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_EDGE;
        si.props = SDL_CreateProperties();
        if (border) SDL_SetNumberProperty(si.props, d9g::kPropSamplerBorderColour, c.sdl);
        SDL_GPUSampler* s = SDL_CreateGPUSampler(g.dev, &si);
        SDL_DestroyProperties(si.props);
        return s;
      };
      SDL_GPUSampler* sb = sampler(true);
      SDL_GPUSampler* se = sampler(false);
      Frame patch = draw_border(g, vsh, psh, vsB, psB, vsT, tex, sb, c.rgba, word);
      Frame emul = draw_border(g, vsh, emh, vsB, emB, vsT, tex, se, c.rgba, word);
      Frame ctrl = draw_border(g, vsh, psh, vsB, psB, vsT, tex, se, c.rgba, word);
      SDL_ReleaseGPUSampler(g.dev, sb);
      SDL_ReleaseGPUSampler(g.dev, se);
      if (!patch.ok || !emul.ok || !ctrl.ok) {
        printf("BORDER_FALLBACK_FAIL draw: %s%s%s\n", patch.err.c_str(), emul.err.c_str(), ctrl.err.c_str());
        return 1;
      }
      auto diff = [](const Frame& a, const Frame& b, uint64_t& channels, int& maxd) {
        channels = 0; maxd = 0;
        for (size_t i = 0; i < a.bgra.size(); i++) {
          const int d = std::abs(int(a.bgra[i]) - int(b.bgra[i]));
          if (d) { channels++; maxd = std::max(maxd, d); }
        }
      };
      uint64_t dEm, dCtrl;
      int mEm, mCtrl;
      diff(patch, emul, dEm, mEm);
      diff(patch, ctrl, dCtrl, mCtrl);
      // the patch's leg reads the border outside the texture: the target's corner pixel (u, v = -0.5)
      // is the colour itself (a check that the comparison's reference is the border, not edge texels)
      const uint8_t want[4] = {uint8_t(c.rgba[2] * 255), uint8_t(c.rgba[1] * 255), uint8_t(c.rgba[0] * 255), uint8_t(c.rgba[3] * 255)};
      const bool cornerIsBorder = !std::memcmp(patch.bgra.data(), want, 4);
      const bool caseOk = cornerIsBorder && dCtrl > 0 && (linear || dEm == 0);
      pass = pass && caseOk;
      const std::string fname = out + "/border-" + (linear ? "linear-" : "point-") + c.name;
      write_file(fname + "-patch.bgra", patch.bgra.data(), patch.bgra.size());
      write_file(fname + "-emulation.bgra", emul.bgra.data(), emul.bgra.size());
      js << (n++ ? ",\n  " : "\n  ") << "{\"filter\": " << json_str(linear ? "linear" : "point") << ", \"colour\": "
         << json_str(c.name) << ", \"channels\": " << patch.bgra.size() << ", \"patch_corner_is_border\": "
         << (cornerIsBorder ? "true" : "false") << ", \"emulation_differing_channels\": " << dEm
         << ", \"emulation_max_delta\": " << mEm << ", \"control_differing_channels\": " << dCtrl
         << ", \"control_max_delta\": " << mCtrl << ", \"patch_sha256\": " << json_str(sha256_of(patch.bgra.data(), patch.bgra.size()))
         << ", \"emulation_sha256\": " << json_str(sha256_of(emul.bgra.data(), emul.bgra.size()))
         << ", \"control_sha256\": " << json_str(sha256_of(ctrl.bgra.data(), ctrl.bgra.size()))
         << ", \"required\": " << json_str(linear ? "a residue, measured; the control differs" : "bit-equal; the control differs")
         << ", \"pass\": " << (caseOk ? "true" : "false") << "}";
      printf("BORDER %s %s: emulation %llu channels differ (max %d), control %llu (max %d), corner border %d: %s\n",
             linear ? "linear" : "point", c.name, (unsigned long long)dEm, mEm, (unsigned long long)dCtrl, mCtrl,
             int(cornerIsBorder), caseOk ? "ok" : "FAIL");
    }
  }
  js << "\n ],\n \"pass\": " << (pass ? "true" : "false") << "\n}\n";
  const std::string s = js.str();
  write_file(out + "/border.json", s.data(), s.size());
  for (SDL_GPUShader* sh : extra) SDL_ReleaseGPUShader(g.dev, sh);
  SDL_ReleaseGPUShader(g.dev, vsh);
  SDL_ReleaseGPUShader(g.dev, psh);
  SDL_ReleaseGPUTexture(g.dev, tex);
  printf("%s\n", pass ? "BORDER_FALLBACK_OK" : "BORDER_FALLBACK_FAIL");
  return pass ? 0 : 1;
}

// ---- the null stream (C7): SDL_gpu's leg of null_probe.cpp. The same two shaders, the position
// per vertex from slot 0, and the input DXVK leaves to its null stream fed from the device's
// kNullStreamSlot as the device's vertex input gives it (d9g_pipeline.h, U9): the per-instance rate
// with pitch 0, a 16-byte buffer holding `value` (the second host's own buffer for DXVK's binding 16,
// holding what DXVK's null binding reads on this driver: the probe's DXVK leg measures it). One
// full-target quad; the centre pixel printed, and every pixel held to it.
int probe_null(const std::string& out, const std::string& vsPath, const std::string& psPath, const float value[4],
               bool unspecialized) {
  bool ok1, ok2;
  std::string vsb = read_file(vsPath, ok1), psb = read_file(psPath, ok2);
  if (!ok1 || !ok2) { fprintf(stderr, "cannot read the shaders\n"); return 2; }
  GpuSession g;
  std::string err;
  if (!g.open(true, err)) { printf("NULL_PROBE_FAIL device: %s\n", err.c_str()); return 1; }
  Translated vsT = translate(vsb.data(), vsb.size(), g.profile, {});
  const SamplerSlot slots[16] = {};   // nothing bound: the null probe's pixel shader samples nothing
  uint32_t word = 0;
  const Variant pv = modes_variant(translate(psb.data(), psb.size(), g.profile, {}), slots, unspecialized, word);
  Translated psT = translate(psb.data(), psb.size(), g.profile, pv);
  if (!vsT.ok || !psT.ok) { printf("NULL_PROBE_FAIL translation: %s\n", (vsT.ok ? psT.error : vsT.error).c_str()); return 1; }
  Built vsB = build(vsT, g.profile, nullptr), psB = build(psT, g.profile, nullptr);
  SDL_GPUShader* vsh = vsB.ok ? g.shader(vsB, Stage::Vertex) : nullptr;
  SDL_GPUShader* psh = psB.ok ? g.shader(psB, Stage::Pixel) : nullptr;
  if (!vsh || !psh) { printf("NULL_PROBE_FAIL shaders: %s\n", SDL_GetError()); return 1; }
  std::vector<SDL_GPUVertexAttribute> attrs;
  uint32_t perVertex = 0, perInstance = 0;
  for (const IoVar& v : vsT.reflection.inputs) {
    if (v.builtin) continue;
    SDL_GPUVertexAttribute a{};
    a.location = v.location;
    a.format = SDL_GPU_VERTEXELEMENTFORMAT_FLOAT4;
    a.buffer_slot = v.semantic == "position" ? 0u : d9g::kNullStreamSlot;
    (a.buffer_slot ? perInstance : perVertex)++;
    attrs.push_back(a);
  }
  if (perVertex != 1 || perInstance != 1) { printf("NULL_PROBE_FAIL the vertex shader reads %u positions and %u other inputs, not one each\n", perVertex, perInstance); return 1; }
  SDL_GPUVertexBufferDescription vbs[2] = {{0, 16, SDL_GPU_VERTEXINPUTRATE_VERTEX, 0},
                                           {d9g::kNullStreamSlot, 0, SDL_GPU_VERTEXINPUTRATE_INSTANCE, 0}};
  const uint32_t W = 64, H = 64;
  SDL_GPUColorTargetDescription ctd{};
  ctd.format = SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM;
  SDL_GPUGraphicsPipelineCreateInfo pci{};
  pci.vertex_shader = vsh;
  pci.fragment_shader = psh;
  pci.vertex_input_state.vertex_buffer_descriptions = vbs;
  pci.vertex_input_state.num_vertex_buffers = 2;
  pci.vertex_input_state.vertex_attributes = attrs.data();
  pci.vertex_input_state.num_vertex_attributes = uint32_t(attrs.size());
  pci.primitive_type = SDL_GPU_PRIMITIVETYPE_TRIANGLESTRIP;
  pci.rasterizer_state.cull_mode = SDL_GPU_CULLMODE_NONE;
  pci.rasterizer_state.enable_depth_clip = true;
  pci.target_info.color_target_descriptions = &ctd;
  pci.target_info.num_color_targets = 1;
  SDL_GPUGraphicsPipeline* gp = SDL_CreateGPUGraphicsPipeline(g.dev, &pci);
  if (!gp) { printf("NULL_PROBE_FAIL pipeline: %s\n", SDL_GetError()); return 1; }
  const float quad[4][4] = {{-1, 1, 0.5f, 1}, {1, 1, 0.5f, 1}, {-1, -1, 0.5f, 1}, {1, -1, 0.5f, 1}};
  uint8_t data[sizeof quad + 16];
  std::memcpy(data, quad, sizeof quad);
  std::memcpy(data + sizeof quad, value, 16);
  SDL_GPUBufferCreateInfo bi{SDL_GPU_BUFFERUSAGE_VERTEX, sizeof data, 0};
  SDL_GPUBuffer* vb = SDL_CreateGPUBuffer(g.dev, &bi);
  SDL_GPUTextureCreateInfo ti{};
  ti.type = SDL_GPU_TEXTURETYPE_2D;
  ti.format = SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM;
  ti.usage = SDL_GPU_TEXTUREUSAGE_COLOR_TARGET;
  ti.width = W; ti.height = H; ti.layer_count_or_depth = 1; ti.num_levels = 1;
  SDL_GPUTexture* rt = SDL_CreateGPUTexture(g.dev, &ti);
  SDL_GPUTransferBufferCreateInfo up{SDL_GPU_TRANSFERBUFFERUSAGE_UPLOAD, sizeof data, 0};
  SDL_GPUTransferBuffer* ub = SDL_CreateGPUTransferBuffer(g.dev, &up);
  SDL_GPUTransferBufferCreateInfo down{SDL_GPU_TRANSFERBUFFERUSAGE_DOWNLOAD, W * H * 4, 0};
  SDL_GPUTransferBuffer* db = SDL_CreateGPUTransferBuffer(g.dev, &down);
  if (!vb || !rt || !ub || !db) { printf("NULL_PROBE_FAIL resources: %s\n", SDL_GetError()); return 1; }
  std::memcpy(SDL_MapGPUTransferBuffer(g.dev, ub, false), data, sizeof data);
  SDL_UnmapGPUTransferBuffer(g.dev, ub);
  SDL_GPUCommandBuffer* cb = SDL_AcquireGPUCommandBuffer(g.dev);
  SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
  SDL_GPUTransferBufferLocation src{ub, 0};
  SDL_GPUBufferRegion dst{vb, 0, sizeof data};
  SDL_UploadToGPUBuffer(cp, &src, &dst, false);
  SDL_EndGPUCopyPass(cp);
  static uint8_t zeros[kSdlUniformWindow];
  for (uint32_t s = 0; s < vsB.num_uniform_buffers; s++) SDL_PushGPUVertexUniformData(cb, s, zeros, sizeof zeros);
  for (uint32_t s = 0; s < psB.num_uniform_buffers; s++) SDL_PushGPUFragmentUniformData(cb, s, zeros, sizeof zeros);
  push_ps_state(cb, word);
  SDL_GPUColorTargetInfo ct{};
  ct.texture = rt;
  ct.load_op = SDL_GPU_LOADOP_CLEAR;
  ct.clear_color = {0x7F / 255.0f, 0x3F / 255.0f, 0x1F / 255.0f, 1.0f};   // null_probe.cpp's clear colour
  ct.store_op = SDL_GPU_STOREOP_STORE;
  SDL_GPURenderPass* rp = SDL_BeginGPURenderPass(cb, &ct, 1, nullptr);
  SDL_BindGPUGraphicsPipeline(rp, gp);
  // SDL's Vulkan backend binds the full range starting at slot 0, including unused slots.
  SDL_GPUBufferBinding vbb[d9g::kNullStreamSlot + 1];
  for (auto& binding : vbb) binding = {vb, uint32_t(sizeof quad)};
  vbb[0] = {vb, 0};
  SDL_BindGPUVertexBuffers(rp, 0, vbb, d9g::kNullStreamSlot + 1);
  SDL_DrawGPUPrimitives(rp, 4, 1, 0, 0);
  SDL_EndGPURenderPass(rp);
  cp = SDL_BeginGPUCopyPass(cb);
  SDL_GPUTextureRegion reg{};
  reg.texture = rt; reg.w = W; reg.h = H; reg.d = 1;
  SDL_GPUTextureTransferInfo tti{db, 0, 0, 0};
  SDL_DownloadFromGPUTexture(cp, &reg, &tti);
  SDL_EndGPUCopyPass(cp);
  SDL_GPUFence* fence = SDL_SubmitGPUCommandBufferAndAcquireFence(cb);
  if (!fence || !SDL_WaitForGPUFences(g.dev, true, &fence, 1)) { printf("NULL_PROBE_FAIL submit: %s\n", SDL_GetError()); return 1; }
  SDL_ReleaseGPUFence(g.dev, fence);
  std::vector<uint8_t> px(size_t(W) * H * 4);
  std::memcpy(px.data(), SDL_MapGPUTransferBuffer(g.dev, db, false), px.size());
  SDL_UnmapGPUTransferBuffer(g.dev, db);
  const uint8_t* c = &px[(size_t(H / 2) * W + W / 2) * 4];
  bool uniform = true;
  for (size_t i = 0; i < px.size(); i += 4) uniform = uniform && !std::memcmp(&px[i], c, 4);
  std::ostringstream js;
  js << "{\"host\": \"sdlgpu\", \"device\": " << json_str(g.device_name) << ", \"pixel_shader_variant\": " << json_str(pv.key())
     << ", \"value\": [" << value[0] << ", " << value[1]
     << ", " << value[2] << ", " << value[3] << "], \"bgra\": [" << unsigned(c[0]) << ", " << unsigned(c[1]) << ", "
     << unsigned(c[2]) << ", " << unsigned(c[3]) << "], \"uniform\": " << (uniform ? "true" : "false") << "}";
  const std::string line = js.str();
  write_file(out + "/probe-null.json", line.data(), line.size());
  printf("%s\n%s\n", line.c_str(), uniform ? "NULL_PROBE_OK" : "NULL_PROBE_FAIL the quad is not one colour");
  SDL_ReleaseGPUTransferBuffer(g.dev, ub);
  SDL_ReleaseGPUTransferBuffer(g.dev, db);
  SDL_ReleaseGPUTexture(g.dev, rt);
  SDL_ReleaseGPUBuffer(g.dev, vb);
  SDL_ReleaseGPUGraphicsPipeline(g.dev, gp);
  SDL_ReleaseGPUShader(g.dev, vsh);
  SDL_ReleaseGPUShader(g.dev, psh);
  return uniform ? 0 : 1;
}

// ---- the sampler-mode specialization on the GPU (C13): synthetic draws in which each sampler register
// takes the mode DXVK's rule gives its texture and sampler state (d9g_shader.h dxvk_sampler_mode): a
// colour texture (0), D16 and D24S8 depth textures (2, or 3 where the device stores D24S8 as
// D32_FLOAT_S8_UINT, IsUpgradedToD32f), a D24S8 texture forced to D32_FLOAT_S8_UINT (3: the reference
// clamped), an L8 texture whose stage was given D3DSAMP_MIPMAPLODBIAS GET4 and then a plain bias, with
// point magnification (1: fetch4, DXVK's switch being sticky, d9g_shader.h fetch4_after), the same
// texture after GET4 and then GET1 (0: the control), and the four together in one shader of four
// samplers. Each case names the modes it must take, literally, and DXVK's rule must give them. Each case
// is drawn with the pixel shader specialized at that word (the check) or, with --unspecialized, with the
// base and the same word in its state buffer (the reference, whose frames the check's must equal, and
// under the validation layer the must-fail control: the base's depth-compare sample on a colour view is
// VUID-vkCmdDraw-None-06479 at draw time); both write each case's module and frame hash. A depth
// texture is cleared to a known depth and sampled with LESS_OR_EQUAL (d9g_sampler.cpp's compare state
// for a depth texture), the reference running across the quad, so the compare's result is held pixel by
// pixel; a colour sample is held to its texels; the fetch4 case must differ from its control.
struct ModeSlot { const char* what; uint32_t d3dfmt; SDL_GPUTextureFormat fmt; bool upgraded; std::vector<uint32_t> bias; float clear; };
struct ModeCase { const char* name; bool multi; float z0, z1; std::vector<ModeSlot> slots; std::vector<uint32_t> want; };

int modes(const std::string& out, const std::string& vsPath, const std::string& ps1Path, const std::string& ps4Path,
          bool unspecialized) {
  bool ok1, ok2, ok3;
  std::string vsb = read_file(vsPath, ok1), ps1 = read_file(ps1Path, ok2), ps4 = read_file(ps4Path, ok3);
  if (!ok1 || !ok2 || !ok3) { fprintf(stderr, "cannot read the shaders\n"); return 2; }
  GpuSession g;
  std::string err;
  if (!g.open(true, err)) { printf("SAMPLER_MODES_FAIL device: %s\n", err.c_str()); return 1; }
  const uint32_t W = 64, H = 64;
  const SDL_GPUTextureUsageFlags depthUse = SDL_GPU_TEXTUREUSAGE_SAMPLER | SDL_GPU_TEXTUREUSAGE_DEPTH_STENCIL_TARGET;
  // D24S8 as the device stores it (d9g_gpu_sdl.cpp's choice for a depth target), and whether that is
  // DXVK's D32 float upgrade
  const bool d24 = SDL_GPUTextureSupportsFormat(g.dev, SDL_GPU_TEXTUREFORMAT_D24_UNORM_S8_UINT, SDL_GPU_TEXTURETYPE_2D, depthUse);
  const SDL_GPUTextureFormat d24fmt = d24 ? SDL_GPU_TEXTUREFORMAT_D24_UNORM_S8_UINT : SDL_GPU_TEXTUREFORMAT_D32_FLOAT_S8_UINT;
  const ModeSlot colour{"colour", kD3dFmtA8R8G8B8, SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM, false, {}, 0.0f};
  const ModeSlot d16{"D16", kD3dFmtD16, SDL_GPU_TEXTUREFORMAT_D16_UNORM, false, {}, 0.5f};
  const ModeSlot d24s8{"D24S8", kD3dFmtD24S8, d24fmt, !d24, {}, 0.5f};
  const ModeSlot d32clamp{"D24S8 as D32_FLOAT_S8_UINT", kD3dFmtD24S8, SDL_GPU_TEXTUREFORMAT_D32_FLOAT_S8_UINT, true, {}, 1.0f};
  const ModeSlot l8{"L8 after GET4 then 0", kD3dFmtL8, SDL_GPU_TEXTUREFORMAT_R8_UNORM, false, {kGet4, 0u}, 0.0f};
  const ModeSlot l8off{"L8 after GET4 then GET1", kD3dFmtL8, SDL_GPU_TEXTUREFORMAT_R8_UNORM, false, {kGet4, kGet1}, 0.0f};
  // the reference (texcoord0.z) runs z0 to z1 left to right; the last field is the modes each case must
  // take, register by register (D24S8 is 3 where the device stores it as D32 float)
  const std::vector<ModeCase> cases = {
      {"colour", false, 0.0f, 1.0f, {colour}, {0u}},
      {"d16", false, 0.0f, 1.0f, {d16}, {2u}},
      {"d24s8", false, 0.0f, 1.0f, {d24s8}, {d24 ? 2u : 3u}},
      {"d24s8_d32f_clamp", false, 1.0f, 2.0f, {d32clamp}, {3u}},
      {"fetch4", false, 0.0f, 1.0f, {l8}, {1u}},
      {"fetch4_control_mode0", false, 0.0f, 1.0f, {l8off}, {0u}},
      {"mixed", true, 0.0f, 2.0f, {colour, d16, l8, d32clamp}, {0u, 2u, 1u, 3u}},
  };
  Translated vsT = translate(vsb.data(), vsb.size(), g.profile, {});
  Built vsB = vsT.ok ? build(vsT, g.profile, nullptr) : Built();
  SDL_GPUShader* vsh = vsB.ok ? g.shader(vsB, Stage::Vertex) : nullptr;
  if (!vsh) { printf("SAMPLER_MODES_FAIL the vertex shader: %s\n", vsT.ok ? vsB.error.c_str() : vsT.error.c_str()); return 1; }
  std::ostringstream js;
  js << "{\n \"schema\": \"sa2-d3d9gpu-sampler-modes/v1\",\n \"device\": " << json_str(g.device_name)
     << ",\n \"profile_sha256\": " << json_str(g.profile_sha) << ",\n \"specialized\": " << (unspecialized ? "false" : "true")
     << ",\n \"d24s8_format\": " << json_str(d24 ? "D24_UNORM_S8_UINT" : "D32_FLOAT_S8_UINT") << ",\n \"cases\": [";
  bool pass = true;
  std::map<std::string, std::string> shas;
  int n = 0;
  for (const ModeCase& c : cases) {
    const std::string& psb = c.multi ? ps4 : ps1;
    SamplerSlot slots[16] = {};
    for (size_t r = 0; r < c.slots.size(); r++) {
      slots[r].bound = true;
      slots[r].d3dfmt = c.slots[r].d3dfmt;
      slots[r].upgraded_to_d32f = c.slots[r].upgraded;
      for (uint32_t w : c.slots[r].bias) slots[r].fetch4_enabled = fetch4_after(slots[r].fetch4_enabled, w);
      slots[r].mag_filter = kD3dTexfPointFilter;
    }
    const Translated base = translate(psb.data(), psb.size(), g.profile, {});
    uint32_t word = 0;
    const Variant pv = modes_variant(base, slots, unspecialized, word);
    Translated psT = translate(psb.data(), psb.size(), g.profile, pv);
    Built psB = psT.ok ? build(psT, g.profile, nullptr) : Built();
    SDL_GPUShader* psh = psB.ok ? g.shader(psB, Stage::Pixel) : nullptr;
    if (!psh) { printf("SAMPLER_MODES_FAIL %s: the pixel shader: %s\n", c.name, !psT.ok ? psT.error.c_str() : psB.error.c_str()); return 1; }
    {
      // the case's module and reflection (the base's with --unspecialized), for C12's pure deletion and
      // C11's texture types
      mkdir((out + "/modules").c_str(), 0755);
      write_file(out + "/modules/" + c.name + ".spv", psB.spirv.data(), psB.spirv.size() * 4);
      auto rj = reflection_json(c.name, sha256_of(psb.data(), psb.size()), psT, psB, pv, g.profile_sha);
      write_file(out + "/modules/" + c.name + ".json", rj.data(), rj.size());
    }
    // textures: a 4 x 4 colour texture of distinct opaque texels, a 4 x 4 R8 texture of distinct values,
    // a depth texture cleared to its depth
    std::vector<SDL_GPUTexture*> texs;
    std::vector<SDL_GPUSampler*> smps;
    SDL_GPUCommandBuffer* cb = SDL_AcquireGPUCommandBuffer(g.dev);
    for (const ModeSlot& sl : c.slots) {
      const bool depth = sl.fmt == SDL_GPU_TEXTUREFORMAT_D16_UNORM || sl.fmt == SDL_GPU_TEXTUREFORMAT_D24_UNORM_S8_UINT ||
                         sl.fmt == SDL_GPU_TEXTUREFORMAT_D32_FLOAT_S8_UINT;
      SDL_GPUTextureCreateInfo ti{};
      ti.type = SDL_GPU_TEXTURETYPE_2D;
      ti.format = sl.fmt;
      ti.usage = depth ? depthUse : SDL_GPU_TEXTUREUSAGE_SAMPLER;
      ti.width = 4; ti.height = 4; ti.layer_count_or_depth = 1; ti.num_levels = 1;
      SDL_GPUTexture* t = SDL_CreateGPUTexture(g.dev, &ti);
      if (!t) { printf("SAMPLER_MODES_FAIL %s: a %s texture: %s\n", c.name, sl.what, SDL_GetError()); return 1; }
      texs.push_back(t);
      if (depth) {
        SDL_GPUDepthStencilTargetInfo dsi{};
        dsi.texture = t;
        dsi.clear_depth = sl.clear;
        dsi.load_op = SDL_GPU_LOADOP_CLEAR;
        dsi.store_op = SDL_GPU_STOREOP_STORE;
        dsi.stencil_load_op = SDL_GPU_LOADOP_CLEAR;
        dsi.stencil_store_op = SDL_GPU_STOREOP_STORE;
        SDL_EndGPURenderPass(SDL_BeginGPURenderPass(cb, nullptr, 0, &dsi));
      } else {
        std::vector<uint8_t> texels;
        for (int i = 0; i < 16; i++) {
          if (sl.fmt == SDL_GPU_TEXTUREFORMAT_R8_UNORM) texels.push_back(uint8_t(8 + 15 * i));
          else texels.insert(texels.end(), {uint8_t(40 + 13 * i), uint8_t(200 - 9 * i), uint8_t(90 + 7 * i), 255});
        }
        SDL_GPUTransferBufferCreateInfo up{SDL_GPU_TRANSFERBUFFERUSAGE_UPLOAD, uint32_t(texels.size()), 0};
        SDL_GPUTransferBuffer* ub = SDL_CreateGPUTransferBuffer(g.dev, &up);
        std::memcpy(SDL_MapGPUTransferBuffer(g.dev, ub, false), texels.data(), texels.size());
        SDL_UnmapGPUTransferBuffer(g.dev, ub);
        SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
        SDL_GPUTextureTransferInfo src{ub, 0, 0, 0};
        SDL_GPUTextureRegion dst{};
        dst.texture = t; dst.w = 4; dst.h = 4; dst.d = 1;
        SDL_UploadToGPUTexture(cp, &src, &dst, false);
        SDL_EndGPUCopyPass(cp);
        SDL_ReleaseGPUTransferBuffer(g.dev, ub);
      }
      SDL_GPUSamplerCreateInfo si{};
      si.min_filter = si.mag_filter = SDL_GPU_FILTER_NEAREST;
      si.mipmap_mode = SDL_GPU_SAMPLERMIPMAPMODE_NEAREST;
      si.address_mode_u = si.address_mode_v = si.address_mode_w = SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_EDGE;
      si.enable_compare = depth;
      si.compare_op = depth ? SDL_GPU_COMPAREOP_LESS_OR_EQUAL : SDL_GPU_COMPAREOP_INVALID;
      SDL_GPUSampler* sm = SDL_CreateGPUSampler(g.dev, &si);
      if (!sm) { printf("SAMPLER_MODES_FAIL %s: a sampler: %s\n", c.name, SDL_GetError()); return 1; }
      smps.push_back(sm);
    }
    // the quad: position (x, y, 0.5, 1) and texcoord0 (u, v, z, 1), u 0 to 1 and z z0 to z1 left to right
    ProbeInput in = probe_input(vsT);
    std::vector<float> verts;
    const float corners[4][3] = {{-1, -1, 0}, {-1, 1, 0}, {1, -1, 1}, {1, 1, 1}};
    for (const auto& k : corners) {
      const float u = k[2], v = k[1] > 0 ? 0.0f : 1.0f, z = k[2] ? c.z1 : c.z0;
      for (const IoVar& io : vsT.reflection.inputs) {
        if (io.builtin) continue;
        if (io.semantic == "position") verts.insert(verts.end(), {k[0], k[1], 0.5f, 1.0f});
        else verts.insert(verts.end(), {u, v, z, 1.0f});
      }
    }
    SDL_GPUColorTargetDescription ctd{};
    ctd.format = SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM;
    SDL_GPUGraphicsPipelineCreateInfo pci{};
    pci.vertex_shader = vsh;
    pci.fragment_shader = psh;
    pci.vertex_input_state.vertex_buffer_descriptions = &in.vb;
    pci.vertex_input_state.num_vertex_buffers = 1;
    pci.vertex_input_state.vertex_attributes = in.attrs.data();
    pci.vertex_input_state.num_vertex_attributes = uint32_t(in.attrs.size());
    pci.primitive_type = SDL_GPU_PRIMITIVETYPE_TRIANGLESTRIP;
    pci.rasterizer_state.cull_mode = SDL_GPU_CULLMODE_NONE;
    pci.rasterizer_state.enable_depth_clip = true;
    pci.target_info.color_target_descriptions = &ctd;
    pci.target_info.num_color_targets = 1;
    SDL_GPUGraphicsPipeline* gp = SDL_CreateGPUGraphicsPipeline(g.dev, &pci);
    SDL_GPUBufferCreateInfo bi{SDL_GPU_BUFFERUSAGE_VERTEX, uint32_t(verts.size() * 4), 0};
    SDL_GPUBuffer* vb = SDL_CreateGPUBuffer(g.dev, &bi);
    SDL_GPUTextureCreateInfo ri{};
    ri.type = SDL_GPU_TEXTURETYPE_2D;
    ri.format = SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM;
    ri.usage = SDL_GPU_TEXTUREUSAGE_COLOR_TARGET;
    ri.width = W; ri.height = H; ri.layer_count_or_depth = 1; ri.num_levels = 1;
    SDL_GPUTexture* rt = SDL_CreateGPUTexture(g.dev, &ri);
    SDL_GPUTransferBufferCreateInfo up{SDL_GPU_TRANSFERBUFFERUSAGE_UPLOAD, uint32_t(verts.size() * 4), 0};
    SDL_GPUTransferBuffer* ub = SDL_CreateGPUTransferBuffer(g.dev, &up);
    SDL_GPUTransferBufferCreateInfo down{SDL_GPU_TRANSFERBUFFERUSAGE_DOWNLOAD, W * H * 4, 0};
    SDL_GPUTransferBuffer* db = SDL_CreateGPUTransferBuffer(g.dev, &down);
    if (!gp || !vb || !rt || !ub || !db) { printf("SAMPLER_MODES_FAIL %s: resources: %s\n", c.name, SDL_GetError()); return 1; }
    std::memcpy(SDL_MapGPUTransferBuffer(g.dev, ub, false), verts.data(), verts.size() * 4);
    SDL_UnmapGPUTransferBuffer(g.dev, ub);
    SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
    SDL_GPUTransferBufferLocation src{ub, 0};
    SDL_GPUBufferRegion dst{vb, 0, uint32_t(verts.size() * 4)};
    SDL_UploadToGPUBuffer(cp, &src, &dst, false);
    SDL_EndGPUCopyPass(cp);
    static uint8_t zeros[kSdlUniformWindow];
    for (uint32_t k = 0; k < vsB.num_uniform_buffers; k++) SDL_PushGPUVertexUniformData(cb, k, zeros, sizeof zeros);
    for (uint32_t k = 0; k < psB.num_uniform_buffers; k++) SDL_PushGPUFragmentUniformData(cb, k, zeros, sizeof zeros);
    push_ps_state(cb, word);
    SDL_GPUColorTargetInfo ct{};
    ct.texture = rt;
    ct.load_op = SDL_GPU_LOADOP_CLEAR;
    ct.store_op = SDL_GPU_STOREOP_STORE;
    SDL_GPURenderPass* rp = SDL_BeginGPURenderPass(cb, &ct, 1, nullptr);
    SDL_BindGPUGraphicsPipeline(rp, gp);
    SDL_GPUBufferBinding vbb{vb, 0};
    SDL_BindGPUVertexBuffers(rp, 0, &vbb, 1);
    std::vector<SDL_GPUTextureSamplerBinding> tsb;
    for (size_t r = 0; r < texs.size(); r++) tsb.push_back({texs[r], smps[r]});
    SDL_BindGPUFragmentSamplers(rp, 0, tsb.data(), uint32_t(tsb.size()));
    SDL_DrawGPUPrimitives(rp, 4, 1, 0, 0);
    SDL_EndGPURenderPass(rp);
    cp = SDL_BeginGPUCopyPass(cb);
    SDL_GPUTextureRegion reg{};
    reg.texture = rt; reg.w = W; reg.h = H; reg.d = 1;
    SDL_GPUTextureTransferInfo tti{db, 0, 0, 0};
    SDL_DownloadFromGPUTexture(cp, &reg, &tti);
    SDL_EndGPUCopyPass(cp);
    SDL_GPUFence* fence = SDL_SubmitGPUCommandBufferAndAcquireFence(cb);
    if (!fence || !SDL_WaitForGPUFences(g.dev, true, &fence, 1)) { printf("SAMPLER_MODES_FAIL %s: submit: %s\n", c.name, SDL_GetError()); return 1; }
    SDL_ReleaseGPUFence(g.dev, fence);
    std::vector<uint8_t> px(size_t(W) * H * 4);
    std::memcpy(px.data(), SDL_MapGPUTransferBuffer(g.dev, db, false), px.size());
    SDL_UnmapGPUTransferBuffer(g.dev, db);
    // what each case must show (B, G, R, A bytes): a depth compare's result is 1 where the reference
    // (z0 + (z1 - z0) * u, clamped to [0, 1] at mode 3) is at most the cleared depth, else 0, the same
    // in every channel it fills; a plain colour sample is its texel
    auto at = [&](uint32_t x, uint32_t y, int ch) { return int(px[(size_t(y) * W + x) * 4 + size_t(ch)]); };
    uint32_t checked = 0, wrong = 0;
    auto expect_compare = [&](int ch, const ModeSlot& sl, uint32_t mode) {
      for (uint32_t x = 2; x < W; x += 4) {
        const float u = (x + 0.5f) / W, z = c.z0 + (c.z1 - c.z0) * u;
        const float ref = mode == 3u ? std::min(std::max(z, 0.0f), 1.0f) : z;
        if (std::fabs(z - sl.clear) < 0.05f) continue;   // where the reference crosses the depth, rounding decides
        const int want = ref <= sl.clear ? 255 : 0;
        checked++;
        if (at(x, H / 2, ch) != want) wrong++;
      }
    };
    // DXVK's rule must give each register the mode the case names
    std::vector<uint32_t> slotModes;
    for (size_t r = 0; r < c.slots.size(); r++) slotModes.push_back(dxvk_sampler_mode(slots[r]));
    const bool modesAsNamed = slotModes == c.want;
    // a colour texture's texels at their centres, in the output channels it fills
    auto expect_texels = [&](std::initializer_list<int> chans) {
      for (uint32_t ty = 0; ty < 4; ty++)
        for (uint32_t tx = 0; tx < 4; tx++) {
          const int i = int(ty * 4 + tx);
          const uint8_t want[4] = {uint8_t(40 + 13 * i), uint8_t(200 - 9 * i), uint8_t(90 + 7 * i), 255};
          for (int ch : chans) { checked++; if (at(tx * 16 + 8, ty * 16 + 8, ch) != want[ch]) wrong++; }
        }
    };
    if (!c.multi) {
      const uint32_t m = c.want[0];
      if (m >= 2u) for (int ch = 0; ch < 4; ch++) expect_compare(ch, c.slots[0], m);
      if (m == 0u && c.slots[0].fmt == SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM) expect_texels({0, 1, 2, 3});
    } else {
      // R: s0's colour sample's x (its texel's R), G: s1's D16 compare, B: s2's gather (held by the frame
      // against the base's and by fetch4's control), A: s3's clamped compare
      expect_texels({2});
      expect_compare(1, c.slots[1], c.want[1]);
      expect_compare(3, c.slots[3], c.want[3]);
    }
    const std::string sha = sha256_of(px.data(), px.size());
    shas[c.name] = sha;
    bool caseOk = wrong == 0 && modesAsNamed;
    if (!strcmp(c.name, "fetch4_control_mode0")) caseOk = caseOk && shas["fetch4"] != sha;
    pass = pass && caseOk;
    js << (n++ ? ",\n  " : "\n  ") << "{\"name\": " << json_str(c.name) << ", \"word\": " << word << ", \"variant\": "
       << json_str(pv.key()) << ", \"modes_as_named\": " << (modesAsNamed ? "true" : "false") << ", \"slots\": [";
    for (size_t r = 0; r < c.slots.size(); r++) {
      js << (r ? ", " : "") << "{\"register\": " << r << ", \"texture\": " << json_str(c.slots[r].what) << ", \"d3dfmt\": "
         << c.slots[r].d3dfmt << ", \"upgraded_to_d32f\": " << (c.slots[r].upgraded ? "true" : "false")
         << ", \"lod_bias_writes\": [";
      for (size_t k = 0; k < c.slots[r].bias.size(); k++) js << (k ? ", " : "") << c.slots[r].bias[k];
      js << "], \"fetch4_enabled\": " << (slots[r].fetch4_enabled ? "true" : "false") << ", \"mode\": " << slotModes[r]
         << ", \"named\": " << c.want[r] << "}";
    }
    js << "], \"depth_compare_registers\": " << psT.reflection.depth_compare_registers << ", \"gather_registers\": "
       << psT.reflection.gather_registers << ", \"pixels_checked\": " << checked << ", \"pixels_wrong\": " << wrong
       << ", \"frame_sha256\": " << json_str(sha) << ", \"pass\": " << (caseOk ? "true" : "false") << "}";
    printf("MODES %s word %#x variant %s: %u pixels checked, %u wrong, depth_compare %#x gather %#x: %s\n", c.name, word,
           pv.key().c_str(), checked, wrong, psT.reflection.depth_compare_registers, psT.reflection.gather_registers,
           caseOk ? "ok" : "FAIL");
    SDL_ReleaseGPUTransferBuffer(g.dev, ub);
    SDL_ReleaseGPUTransferBuffer(g.dev, db);
    SDL_ReleaseGPUTexture(g.dev, rt);
    SDL_ReleaseGPUBuffer(g.dev, vb);
    SDL_ReleaseGPUGraphicsPipeline(g.dev, gp);
    for (SDL_GPUTexture* t : texs) SDL_ReleaseGPUTexture(g.dev, t);
    for (SDL_GPUSampler* sm : smps) SDL_ReleaseGPUSampler(g.dev, sm);
    SDL_ReleaseGPUShader(g.dev, psh);
  }
  SDL_ReleaseGPUShader(g.dev, vsh);
  js << "\n ],\n \"pass\": " << (pass ? "true" : "false") << "\n}\n";
  const std::string text = js.str();
  write_file(out + "/modes.json", text.data(), text.size());
  printf("%s\n", pass ? "SAMPLER_MODES_OK" : "SAMPLER_MODES_FAIL");
  return pass ? 0 : 1;
}

}  // namespace

int main(int argc, char** argv) {
  // whole lines on stdout: a layer's or SDL's stderr text written into the same log (the capture
  // layer's account when the loader unloads it) then falls between lines, never inside a marker
  setvbuf(stdout, nullptr, _IOLBF, 0);
  if (argc >= 2 && !strcmp(argv[1], "facts"))
    return facts();
  if (argc >= 5 && !strcmp(argv[1], "sampler-mode")) {
    // sampler-mode D3DFMT UPGRADED MAGFILTER [MIPMAPLODBIAS...]: DXVK's sampler mode for a register holding
    // a texture of that format (0: none bound) under that magnification filter, after its stage was given
    // those D3DSAMP_MIPMAPLODBIAS values in order (d9g_shader.h fetch4_after and dxvk_sampler_mode)
    SamplerSlot slot;
    slot.d3dfmt = uint32_t(strtoul(argv[2], nullptr, 0));
    slot.bound = slot.d3dfmt != 0;
    slot.upgraded_to_d32f = strtoul(argv[3], nullptr, 0) != 0;
    slot.mag_filter = uint32_t(strtoul(argv[4], nullptr, 0));
    for (int k = 5; k < argc; k++) slot.fetch4_enabled = fetch4_after(slot.fetch4_enabled, uint32_t(strtoul(argv[k], nullptr, 0)));
    printf("%u\n", dxvk_sampler_mode(slot));
    return 0;
  }
  if (argc >= 4 && !strcmp(argv[1], "load"))
    return load(argv[2], argv[3], !(argc >= 5 && !strcmp(argv[4], "--no-features")));
  const bool unspecialized = argc >= 2 && !strcmp(argv[argc - 1], "--unspecialized");
  const int nargs = argc - (unspecialized ? 1 : 0);
  if (nargs == 5 && !strcmp(argv[1], "border"))
    return border(argv[2], argv[3], argv[4], unspecialized);
  if (nargs == 9 && !strcmp(argv[1], "probe-null")) {
    const float value[4] = {strtof(argv[5], nullptr), strtof(argv[6], nullptr), strtof(argv[7], nullptr), strtof(argv[8], nullptr)};
    return probe_null(argv[2], argv[3], argv[4], value, unspecialized);
  }
  if (nargs == 6 && !strcmp(argv[1], "modes"))
    return modes(argv[2], argv[3], argv[4], argv[5], unspecialized);
  if (argc >= 3 && !strcmp(argv[1], "profile")) {
    DeviceFacts f;
    std::string device;
    if (!load_facts(argv[2], f, device))
      return 2;
    auto rec = determine_profile(f).record();
    printf("# %s\n%s", device.c_str(), rec.c_str());
    return 0;
  }
  if (argc >= 5 && (!strcmp(argv[1], "compile") || !strcmp(argv[1], "link"))) {
    DeviceFacts f;
    std::string device;
    if (!load_facts(argv[2], f, device))
      return 2;
    D3D9Config d3d9;
    for (int k = 4; k < argc; k++)
      if (!strcmp(argv[k], "--force-sample-rate-shading")) d3d9.force_sample_rate_shading = true;
    ShaderProfile profile = determine_profile(f, d3d9);
    std::string rec = profile.record();
    std::string profileSha = sha256_of(rec.data(), rec.size());
    std::string out = argv[3];
    if (!strcmp(argv[1], "link")) {
      // link FACTS OUT VS.bin PS.bin NAME [--sampler-modes WORD]: the pixel shader specialized at WORD
      Variant psVariant;
      if (argc == 9 && !strcmp(argv[7], "--sampler-modes")) {
        psVariant.sampler_modes = uint32_t(strtoul(argv[8], nullptr, 0));
      } else if (argc != 7) {
        fprintf(stderr, "link FACTS OUT VS.bin PS.bin NAME [--sampler-modes WORD]\n");
        return 2;
      }
      bool ok1, ok2;
      auto vsb = read_file(argv[4], ok1), psb = read_file(argv[5], ok2);
      if (!ok1 || !ok2) { fprintf(stderr, "cannot read the pair\n"); return 2; }
      Translated vs = translate(vsb.data(), vsb.size(), profile, {});
      Translated ps = translate(psb.data(), psb.size(), profile, psVariant);
      if (!vs.ok || !ps.ok) { fprintf(stderr, "FAIL %s: %s\n", argv[6], (vs.ok ? ps.error : vs.error).c_str()); return 1; }
      Linkage link;
      link.vs = &vs;
      Built b = build(ps, profile, &link);
      if (!b.ok) { fprintf(stderr, "FAIL %s: %s\n", argv[6], b.error.c_str()); return 1; }
      write_file(out + "/" + argv[6] + ".spv", b.spirv.data(), b.spirv.size() * 4);
      // what was linked, for a reader holding the module to its pair (r1_receipt.py specialize)
      std::ostringstream lj;
      lj << "{\n \"schema\": \"sa2-d3d9gpu-shader-link/v1\",\n \"name\": " << json_str(argv[6]) << ",\n \"vs_bytecode_sha256\": "
         << json_str(sha256_of(vsb.data(), vsb.size())) << ",\n \"ps_bytecode_sha256\": " << json_str(sha256_of(psb.data(), psb.size()))
         << ",\n \"ps_variant\": " << json_str(psVariant.key()) << ",\n \"profile_sha256\": " << json_str(profileSha)
         << ",\n \"translator\": " << json_str(translator_identity()) << "\n}\n";
      const std::string ljs = lj.str();
      write_file(out + "/" + argv[6] + ".json", ljs.data(), ljs.size());
      printf("LINKED %s io_rewritten=%d compatible=%d words=%zu\n", argv[6], int(b.io_rewritten),
             int(stages_compatible(vs, ps, true)), b.spirv.size());
      return 0;
    }
    Variant variant;
    std::string cacheRoot, repo;
    bool verify = false;
    int i = 4;
    for (; i < argc && argv[i][0] == '-' && argv[i][1] == '-'; i++) {
      if (!strcmp(argv[i], "--bgra") && i + 1 < argc) variant.bgra_mask = uint32_t(strtoul(argv[++i], nullptr, 0));
      else if (!strcmp(argv[i], "--border-u") && i + 1 < argc) variant.border_u = uint16_t(strtoul(argv[++i], nullptr, 0));
      else if (!strcmp(argv[i], "--border-v") && i + 1 < argc) variant.border_v = uint16_t(strtoul(argv[++i], nullptr, 0));
      else if (!strcmp(argv[i], "--border-linear") && i + 1 < argc) variant.border_linear = uint16_t(strtoul(argv[++i], nullptr, 0));
      else if (!strcmp(argv[i], "--depth-clamp")) variant.depth_clamp = true;
      else if (!strcmp(argv[i], "--sampler-modes") && i + 1 < argc) variant.sampler_modes = uint32_t(strtoul(argv[++i], nullptr, 0));
      else if (!strcmp(argv[i], "--cache") && i + 1 < argc) cacheRoot = argv[++i];
      else if (!strcmp(argv[i], "--repo") && i + 1 < argc) repo = argv[++i];
      else if (!strcmp(argv[i], "--verify")) verify = true;
      else if (!strcmp(argv[i], "--force-sample-rate-shading")) {}  // read above, into the profile
      else if (!strcmp(argv[i], "--ftz-daz")) _mm_setcsr(_mm_getcsr() | 0x8040u);  // FTZ | DAZ
      else { fprintf(stderr, "unknown option %s\n", argv[i]); return 2; }
    }
    std::unique_ptr<Cache> cache;
    if (!cacheRoot.empty()) {
      cache = std::make_unique<Cache>(cacheRoot, repo, verify);
      if (!cache->ok()) { fprintf(stderr, "REFUSED %s\n", cache->error().c_str()); return 2; }
    }
    int failures = 0, done = 0;
    for (; i < argc; i++) {
      bool ok;
      auto code = read_file(argv[i], ok);
      std::string name = stem(argv[i]);
      if (!ok) { fprintf(stderr, "FAIL %s: cannot read\n", name.c_str()); failures++; continue; }
      std::string codeSha = sha256_of(code.data(), code.size());
      Translated t = translate(code.data(), code.size(), profile, variant, true);
      if (!t.ok) { printf("FAIL %s: %s\n", name.c_str(), t.error.c_str()); failures++; continue; }
      write_file(out + "/" + name + ".ir.txt", t.ir_text.data(), t.ir_text.size());
      Built b;
      bool built;
      if (cache) {
        CacheKey key{codeSha, t.stage == Stage::Vertex ? "vertex" : "pixel", variant.key(), translator_identity(), profileSha, "unlinked"};
        CachedModule module;
        built = cache->get(key, module, [&](CachedModule& m) {
          Built fresh = build(t, profile, nullptr);
          if (!fresh.ok) { b = fresh; return false; }
          m.spirv = fresh.spirv;
          m.bindings = encode_bindings(fresh);
          return true;
        });
        if (built) {
          b.ok = decode_bindings(module.bindings, b);
          b.spirv = module.spirv;
          if (!b.ok) { printf("FAIL %s: a cache entry's bindings do not read\n", name.c_str()); failures++; continue; }
        }
      } else {
        b = build(t, profile, nullptr);
        built = b.ok;
      }
      if (!built) { printf("FAIL %s: %s\n", name.c_str(), b.error.c_str()); failures++; continue; }
      write_file(out + "/" + name + ".spv", b.spirv.data(), b.spirv.size() * 4);
      auto js = reflection_json(name, codeSha, t, b, variant, profileSha);
      write_file(out + "/" + name + ".json", js.data(), js.size());
      printf("OK %s %s %s words=%zu samplers=%u ubos=%u\n", name.c_str(), t.debug_name.c_str(),
             t.stage == Stage::Vertex ? "vertex" : "pixel", b.spirv.size(), b.num_samplers, b.num_uniform_buffers);
      done++;
    }
    if (cache) {
      const auto& st = cache->stats();
      printf("CACHE hits=%u misses=%u verified=%u corrupt=%u\n", st.hits, st.misses, st.verified, st.corrupt);
    }
    printf("%s %d translated, %d failed (profile %s)\n", failures ? "SHADERC_FAIL" : "SHADERC_OK", done, failures,
           profileSha.substr(0, 16).c_str());
    return failures ? 1 : 0;
  }
  fprintf(stderr, "usage: d3d9gpu_shaderc facts | profile FACTS | compile FACTS OUT [opts] SHADER... | link FACTS OUT VS PS NAME\n");
  return 2;
}
