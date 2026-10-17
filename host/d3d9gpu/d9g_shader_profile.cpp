// Port of DXVK 3.1.1's shader-option rules for host 2's translator (R1 U3). Every rule cites the
// line of the pinned checkout (private/s5-tools/src/dxvk-3.1.1) it ports; nothing here is host 2's
// own choice except the record's text format.
#include "d9g_shader_profile.h"

#include <algorithm>
#include <sstream>
#include <vector>

namespace d9g::shader {

namespace {
// VkDriverId values (vulkan_core.h, Vulkan 1.2 core), the ones DXVK's rules name.
constexpr uint32_t kAmdProprietary = 1, kAmdOpenSource = 2, kMesaRadv = 3, kNvidiaProprietary = 4,
                   kIntelProprietaryWindows = 5, kIntelOpenSourceMesa = 6, kMesaLlvmpipe = 13,
                   kMesaTurnip = 18, kMesaNvk = 24, kMesaHoneykrisp = 26;
constexpr uint32_t kIndependenceNone = 2;  // VK_SHADER_FLOAT_CONTROLS_INDEPENDENCE_NONE
constexpr uint32_t kMaxNumUniformBufferSlots = 128;  // dxvk_limits.h MaxNumUniformBufferSlots

// DxvkAdapter::matchesDriver (src/dxvk/dxvk_adapter.cpp:354-372): a zero bound is no bound.
bool matches(const DeviceFacts& f, uint32_t driver, uint64_t min_ver = 0, uint64_t max_ver = 0) {
  bool m = f.driver_id == driver;
  uint64_t v = decode_driver_version(f.driver_id, f.driver_version_raw);
  if (min_ver) m = m && v >= min_ver;
  if (max_ver) m = m && v < max_ver;
  return m;
}
}  // namespace

uint64_t decode_driver_version(uint32_t driver_id, uint32_t raw) {
  switch (driver_id) {
    case kNvidiaProprietary:  // dxvk_device_info.cpp:1102-1106: 10|8|8|6
      return version((raw >> 22) & 0x3ff, (raw >> 14) & 0x0ff, (raw >> 6) & 0x0ff);
    case kIntelProprietaryWindows:  // :1109-1110
      return version(raw >> 14, raw & 0x3fff, 0);
    default:  // :1112-1116, VK_API_VERSION_MAJOR/MINOR/PATCH
      return version((raw >> 22) & 0x7f, (raw >> 12) & 0x3ff, raw & 0xfff);
  }
}

ShaderProfile determine_profile(const DeviceFacts& f, const D3D9Config& d3d9) {
  ShaderProfile p;
  // dxvk_device.cpp:765-766
  p.min_storage_buffer_alignment = f.min_storage_buffer_offset_alignment;
  // :768-769
  p.supports_16bit_arithmetic = f.shader_float16;
  // :771-772
  p.supports_sub_dword_push_data = f.storage_push_constant16 && f.storage_push_constant8;
  // :774-780
  p.typed_r32_load_requires_format = !f.r32_storage_read_without_format;
  // :782-788 (dxvk.lowerSinCos is Auto: the tristate leaves the driver rule)
  p.lower_sin_cos = f.driver_id == kIntelOpenSourceMesa || f.driver_id == kIntelProprietaryWindows;
  // :790-795
  if (f.driver_id != kMesaRadv) {
    p.lower_ftoi = true;
    p.lower_f32_to_f16 = true;
  }
  // :797-801
  p.lower_in_bounds_cbv_to_bda = f.driver_id == kMesaRadv || f.driver_id == kAmdOpenSource || f.driver_id == kAmdProprietary;
  // :803-806: NVIDIA before 580
  p.lower_itof = matches(f, kNvidiaProprietary, 0, version(580, 0, 0));
  // :808-812
  p.max_uniform_buffer_size = f.max_uniform_buffer_range;
  p.max_uniform_buffer_count = f.max_per_stage_descriptor_uniform_buffers < kMaxNumUniformBufferSlots
      ? int32_t(f.max_per_stage_descriptor_uniform_buffers) : -1;
  // :814-816: ANV before Mesa 25.0.3
  p.export_point_size = matches(f, kIntelOpenSourceMesa, 0, version(25, 0, 3));
  // :818-819
  p.supports_nv_raw_access_chains = f.nv_raw_access_chains;
  // :821-829
  p.lower_constant_arrays = f.driver_id != kMesaRadv && f.driver_id != kMesaNvk && f.driver_id != kMesaTurnip &&
                            f.driver_id != kMesaHoneykrisp && f.driver_id != kMesaLlvmpipe &&
                            f.driver_id != kIntelOpenSourceMesa;
  // :831-866
  for (int i = 0; i < 3; i++) {
    p.supports_sz_inf_nan_preserve[i] = f.sz_inf_nan_preserve[i];
    p.supports_rte[i] = f.rte[i];
    p.supports_rtz[i] = f.rtz[i];
    p.supports_denorm_flush[i] = f.denorm_flush[i];
    p.supports_denorm_preserve[i] = f.denorm_preserve[i];
  }
  // :868-872
  p.independent_round_mode = f.rounding_mode_independence != kIndependenceNone;
  p.independent_denorm_mode = f.denorm_behavior_independence != kIndependenceNone;
  // :874-875
  p.supports_float_controls2 = f.float_controls2;
  // :877-878 and :896-898, with canUseDescriptorHeap's gate (dxvk_device_info.cpp:497-512; the
  // dxvk.enableDescriptorHeap tristate is Auto, so the driver rule stands)
  bool heap = f.descriptor_heap;
  if (heap) {
    bool enable = f.driver_id == kMesaRadv || f.driver_id == kMesaNvk || f.driver_id == kMesaLlvmpipe ||
                  f.driver_id == kIntelOpenSourceMesa || f.driver_id == kAmdProprietary;
    if (f.driver_id == kNvidiaProprietary)
      enable = decode_driver_version(f.driver_id, f.driver_version_raw) >= version(595, 84, 0);
    heap = enable;
  }
  p.supports_descriptor_heap = heap;
  // :881-898
  p.supports_resource_indexing = f.resource_indexing || heap;
  // The D3D9 device's adjustments, d3d9_device.cpp:7874-7883
  p.semantic_io = true;
  p.enable_sample_rate_shading = d3d9.force_sample_rate_shading;
  if (!d3d9.use_fp16)
    p.supports_16bit_arithmetic = false;
  // d3d9_shader.cpp:1115: fastFloatEmulation = d3d9FloatEmulation == Enabled; Strict is not
  p.fast_float_emulation = !d3d9.float_emulation_strict;
  // Host 2's own: no sub-dword access, so the state buffer is read as 32-bit words, and no 16-bit
  // arithmetic either (min16's int16 needs shaderInt16)
  if (!f.sub_dword_access) {
    p.state_words = true;
    p.supports_16bit_arithmetic = false;
  }
  // Host 2's own: no shaderClipDistance, so no vertex module writes ClipDistance
  if (!f.clip_distance)
    p.no_clip_distance = true;
  // Host 2's own: a device made at Vulkan 1.1 (ShaderProfile::vk11). SDL_gpu's 1.1 device has Vulkan 1.0
  // features only and no extension, so nothing the 1.2 features or the extensions give is used: the state
  // is read as 32-bit words, and no float-control mode, float controls 2, raw access chain, descriptor
  // heap or resource indexing (optimizeDescriptorIndexing) is asked of the builder. DXVK's driver and
  // limit rules stand.
  if (f.vk11) {
    p.vk11 = true;
    p.state_words = true;
    p.supports_16bit_arithmetic = false;
    p.supports_sub_dword_push_data = false;
    for (int i = 0; i < 3; i++) {
      p.supports_sz_inf_nan_preserve[i] = false;
      p.supports_rte[i] = false;
      p.supports_rtz[i] = false;
      p.supports_denorm_flush[i] = false;
      p.supports_denorm_preserve[i] = false;
    }
    p.independent_round_mode = false;
    p.independent_denorm_mode = false;
    p.supports_float_controls2 = false;
    p.supports_nv_raw_access_chains = false;
    p.supports_descriptor_heap = false;
    p.supports_resource_indexing = false;
  }
  return p;
}

std::string ShaderProfile::record() const {
  std::vector<std::string> lines;
  auto add = [&](const char* name, long long v) {
    std::ostringstream s;
    s << name << "=" << v;
    lines.push_back(s.str());
  };
  add("compile.supports_16bit_arithmetic", supports_16bit_arithmetic);
  add("compile.supports_sub_dword_push_data", supports_sub_dword_push_data);
  add("compile.typed_r32_load_requires_format", typed_r32_load_requires_format);
  add("compile.lower_sin_cos", lower_sin_cos);
  add("compile.lower_ftoi", lower_ftoi);
  add("compile.lower_f32_to_f16", lower_f32_to_f16);
  add("compile.lower_in_bounds_cbv_to_bda", lower_in_bounds_cbv_to_bda);
  add("compile.lower_itof", lower_itof);
  add("compile.lower_constant_arrays", lower_constant_arrays);
  add("compile.semantic_io", semantic_io);
  add("compile.enable_sample_rate_shading", enable_sample_rate_shading);
  add("spirv.export_point_size", export_point_size);
  add("spirv.supports_nv_raw_access_chains", supports_nv_raw_access_chains);
  static const char* kBits[3] = {"16", "32", "64"};
  for (int i = 0; i < 3; i++) {
    add(("spirv.sz_inf_nan_preserve" + std::string(kBits[i])).c_str(), supports_sz_inf_nan_preserve[i]);
    add(("spirv.rte" + std::string(kBits[i])).c_str(), supports_rte[i]);
    add(("spirv.rtz" + std::string(kBits[i])).c_str(), supports_rtz[i]);
    add(("spirv.denorm_flush" + std::string(kBits[i])).c_str(), supports_denorm_flush[i]);
    add(("spirv.denorm_preserve" + std::string(kBits[i])).c_str(), supports_denorm_preserve[i]);
  }
  add("spirv.independent_round_mode", independent_round_mode);
  add("spirv.independent_denorm_mode", independent_denorm_mode);
  add("spirv.supports_float_controls2", supports_float_controls2);
  add("spirv.supports_descriptor_heap", supports_descriptor_heap);
  add("spirv.supports_resource_indexing", supports_resource_indexing);
  add("limits.min_storage_buffer_alignment", min_storage_buffer_alignment);
  add("limits.max_uniform_buffer_size", max_uniform_buffer_size);
  add("limits.max_uniform_buffer_count", max_uniform_buffer_count);
  add("d3d9.fast_float_emulation", fast_float_emulation);
  if (state_words)   // only when set: a profile without it keeps its record (ShaderProfile::state_words)
    add("host2.state_words", state_words);
  if (no_clip_distance)   // only when set, as state_words
    add("host2.no_clip_distance", no_clip_distance);
  if (vk11)   // only when set, as state_words: every 1.3 record stays as it was
    add("host2.vk11", vk11);
  std::sort(lines.begin(), lines.end());
  std::string out;
  for (const auto& l : lines)
    out += l + "\n";
  return out;
}

}  // namespace d9g::shader
