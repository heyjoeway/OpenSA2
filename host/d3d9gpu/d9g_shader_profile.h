// The shader profile of host 2's translator (R1 U3): the Vulkan facts of the device SDL chose,
// and the shader options DXVK 3.1.1 derives from the same facts, so that host 2 compiles each
// shader with the options DXVK would use on that device (ruling 2: "reuse DXVK's shader
// compiler"; Documentation/decisions/2026-10-04-S5-user-rulings.md).
//
// determine_profile() is a port of DxvkDevice::determineShaderOptions
// (private/s5-tools/src/dxvk-3.1.1/src/dxvk/dxvk_device.cpp:764-900) and of the D3D9 device's
// adjustments (src/d3d9/d3d9_device.cpp:7874-7883), one cited rule per flag. It reads plain
// facts, never a Vulkan handle, so a test can hold it to literal expected flags for synthetic
// devices. The record (record()) is the canonical text the shader cache keys on and the report
// prints; two profiles with the same record compile every shader to the same bytes.
#pragma once

#include <cstdint>
#include <string>

namespace d9g::shader {

// The facts determineShaderOptions reads, as the device reports them.
struct DeviceFacts {
  uint32_t driver_id = 0;                // VkDriverId
  uint32_t driver_version_raw = 0;       // VkPhysicalDeviceProperties::driverVersion
  uint32_t min_storage_buffer_offset_alignment = 0;
  uint32_t max_uniform_buffer_range = 0;
  uint32_t max_per_stage_descriptor_uniform_buffers = 0;
  bool shader_float16 = false;           // vk12
  bool storage_push_constant16 = false;  // vk11
  bool storage_push_constant8 = false;   // vk12
  bool r32_storage_read_without_format = false;  // R32_SFLOAT & R32_UINT & R32_SINT optimal features
  bool nv_raw_access_chains = false;     // VK_NV_raw_access_chains shaderRawAccessChains
  bool sz_inf_nan_preserve[3] = {};      // vk12 shaderSignedZeroInfNanPreserveFloat16/32/64
  bool rte[3] = {};                      // shaderRoundingModeRTEFloat16/32/64
  bool rtz[3] = {};                      // shaderRoundingModeRTZFloat16/32/64
  bool denorm_flush[3] = {};             // shaderDenormFlushToZeroFloat16/32/64
  bool denorm_preserve[3] = {};          // shaderDenormPreserveFloat16/32/64
  uint32_t rounding_mode_independence = 0;  // VkShaderFloatControlsIndependence
  uint32_t denorm_behavior_independence = 0;
  bool float_controls2 = false;          // VK_KHR_shader_float_controls2 shaderFloatControls2
  bool descriptor_heap = false;          // VK_EXT_descriptor_heap descriptorHeap, as the device reports it
  bool resource_indexing = false;        // all twelve indexing features of dxvk_device.cpp:883-894
  // Host 2's own, not DXVK's: shaderInt8, shaderInt16 and 8- and 16-bit uniform and storage buffer
  // access, all on (VulkanFeatures::kSubDword), which the state buffer's u8 and u16 members need.
  // query_device_facts reads whether the device supports them; the device (d9g_gpu_sdl.cpp
  // make_sdl_gpu) sets whether SDL_gpu's device was created with them. Without them the
  // translator reads those members as 32-bit words (ShaderProfile::state_words).
  bool sub_dword_access = true;
  // Host 2's own as well: shaderClipDistance (Vulkan 1.0's optional feature), which every vertex
  // module needs as dxbc-spirv builds it (the sm3 converter's clipping function writes
  // ClipDistance[6] in every vertex shader: sm3_io_map.cpp, sm3_converter.cpp emitClipping), and
  // which many Android GPUs lack (Mali: Tensor, Exynos, MediaTek). query_device_facts reads whether
  // the device supports it; the device sets whether SDL_gpu's device was created with it. Without
  // it the translator removes the output (ShaderProfile::no_clip_distance).
  bool clip_distance = true;
};

// The D3D9 options that change shader compilation (host/player/dxvk.conf pins them).
struct D3D9Config {
  bool use_fp16 = false;                 // d3d9.useFP16
  bool force_sample_rate_shading = false;  // d3d9.forceSampleRateShading
  bool float_emulation_strict = true;    // d3d9.floatEmulation = Strict
};

// DxvkShaderCompileFlag and DxvkShaderSpirvFlag (src/dxvk/dxvk_shader.h), by name.
struct ShaderProfile {
  // compile flags
  bool supports_16bit_arithmetic = false;
  bool supports_sub_dword_push_data = false;
  bool typed_r32_load_requires_format = false;
  bool lower_sin_cos = false;
  bool lower_ftoi = false;
  bool lower_f32_to_f16 = false;
  bool lower_in_bounds_cbv_to_bda = false;
  bool lower_itof = false;
  bool lower_constant_arrays = false;
  bool semantic_io = false;
  bool enable_sample_rate_shading = false;
  // spirv flags
  bool export_point_size = false;
  bool supports_nv_raw_access_chains = false;
  bool supports_sz_inf_nan_preserve[3] = {};
  bool supports_rte[3] = {};
  bool supports_rtz[3] = {};
  bool supports_denorm_flush[3] = {};
  bool supports_denorm_preserve[3] = {};
  bool independent_round_mode = false;
  bool independent_denorm_mode = false;
  bool supports_float_controls2 = false;
  bool supports_descriptor_heap = false;
  bool supports_resource_indexing = false;
  // limits
  uint32_t min_storage_buffer_alignment = 0;
  uint32_t max_uniform_buffer_size = 0;
  int32_t max_uniform_buffer_count = -1;
  // D3D9 converter options (d3d9_shader.cpp:1115-1120 with the device's D3D9ShaderOptions)
  bool fast_float_emulation = false;
  // Host 2's own (d9g_shader_abi.cpp lower_sdl_abi): the state buffer's sub-dword members (DXVK's
  // u8 fog colour and alpha reference, its u16 dynamic float count and point sizes) declared as
  // the 32-bit words that hold them and read with bit extracts, on a device without
  // DeviceFacts::sub_dword_access. The buffer's bytes are the same either way. The record prints it
  // only when set, so a profile without it keeps the record (and the cache keys) it had before
  // the field existed.
  bool state_words = false;
  // Host 2's own (d9g_shader.cpp strip_clip_distance): every vertex module built without the
  // ClipDistance output, on a device without DeviceFacts::clip_distance. dxbc-spirv writes plane
  // i's distance where i < the state's clip plane count and 0 elsewhere, so with no plane enabled
  // the output clips nothing and removing it changes no pixel; the device refuses a draw with a
  // plane enabled on such a profile (d9g_device_draw.cpp, USER_CLIP_PLANES), since SA2 enables none
  // (the streams' scans: no SetClipPlane, no D3DRS_CLIPPLANEENABLE). Printed only when set, as
  // state_words.
  bool no_clip_distance = false;

  // Canonical "name=value" lines, sorted, one per field: the cache key's profile record.
  std::string record() const;
};

ShaderProfile determine_profile(const DeviceFacts& facts, const D3D9Config& d3d9 = {});

// DxvkDeviceCapabilities::decodeDriverVersion (src/dxvk/dxvk_device_info.cpp:1100-1117), packed as
// DXVK's Version packs it (src/util/util_version.h:20-21): major << 48 | minor << 24 | patch.
uint64_t decode_driver_version(uint32_t driver_id, uint32_t raw);
inline uint64_t version(uint32_t major, uint32_t minor, uint32_t patch) {
  return (uint64_t(major) << 48) | (uint64_t(minor) << 24) | uint64_t(patch);
}

// d9g_shader_vulkan.cpp: the facts of one Vulkan physical device, read on a throwaway Vulkan 1.3
// instance through `gipa` (a PFN_vkGetInstanceProcAddr: the loader SDL_gpu loaded, from
// SDL_Vulkan_GetVkGetInstanceProcAddr, or libvulkan's own). `device_name` names the device (the one
// SDL_gpu chose); empty, exactly one device must exist (VK_DRIVER_FILES naming one driver). It changes
// nothing any other instance holds.
struct DeviceFactsQuery {
  bool ok = false;
  std::string how;          // what was read, or why nothing was
  std::string device_name, driver_name, driver_info;
  DeviceFacts facts;
};
DeviceFactsQuery query_device_facts(void* gipa, const std::string& device_name);
// The facts as d3d9gpu_shaderc's facts file (schema sa2-d3d9gpu-shader-facts/v1).
std::string facts_json(const DeviceFactsQuery& q);

// The Vulkan API version and features the translated modules need (R0's chain, with what the
// validation layer named on it), as SDL_GPUVulkanOptions takes them: feature_list chains the 1.1,
// 1.2 and 1.3 feature structures; features10 is a VkPhysicalDeviceFeatures. One owner for both the
// device (d9g_gpu_sdl.cpp make_sdl_gpu) and d3d9gpu_shaderc's GPU modes, so they ask for the same.
//
// The six sub-dword features (shaderInt16, shaderInt8, storageBuffer16BitAccess,
// uniformAndStorageBuffer16BitAccess, storageBuffer8BitAccess, uniformAndStorageBuffer8BitAccess)
// are asked for only with sub_dword: many low-end and older GPUs, and SwiftShader, have none of
// them, and SDL_gpu refuses a device lacking any feature asked for. Without them the translator
// reads the state buffer as 32-bit words (ShaderProfile::state_words), which needs none.
// shaderClipDistance is asked for only with clip_distance: Mali GPUs lack it, and without it the
// translator builds no ClipDistance output (ShaderProfile::no_clip_distance). scalarBlockLayout is
// asked for only with scalar_layout: no module needs it (they follow the relaxed uniform buffer
// layout), so a device without it changes nothing the translator builds.
class VulkanFeatures {
 public:
  // The optional groups a device may lack, by what the translator does without them.
  struct Lacks {
    bool sub_dword = false;      // any of the six sub-dword features
    bool clip_distance = false;  // shaderClipDistance
    bool scalar_layout = false;  // scalarBlockLayout
  };
  explicit VulkanFeatures(bool sub_dword = true, bool clip_distance = true, bool scalar_layout = true);
  ~VulkanFeatures();
  VulkanFeatures(const VulkanFeatures&) = delete;
  VulkanFeatures& operator=(const VulkanFeatures&) = delete;
  uint32_t api_version() const;
  void* feature_list();
  void* features10();
  bool sub_dword() const { return sub_dword_; }
  bool clip_distance() const { return clip_distance_; }
  bool scalar_layout() const { return scalar_layout_; }
  std::string describe() const;   // a JSON object: the API version and the features by name
  // The features asked for that each Vulkan physical device does not support, read on a throwaway
  // Vulkan 1.3 instance through `gipa` (as query_device_facts reads): "<device>: a, b; <device>:
  // none". *fits is set when at least one device supports them all. "" with *how set when the
  // devices could not be read. *lacks (when given): the optional groups some device lacks of those
  // asked for. SDL_gpu's own Vulkan 1.0 demands a device lacks are named too ("SDL: depthClamp"),
  // and such a device does not fit.
  std::string lacking(void* gipa, bool* fits, std::string* how, Lacks* lacks = nullptr) const;
  struct Chain;
 private:
  Chain* m;
  bool sub_dword_;
  bool clip_distance_;
  bool scalar_layout_;
};

}  // namespace d9g::shader
