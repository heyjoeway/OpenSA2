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

// The Vulkan API version host 2's device is made at. vk13 is the profile the translator was built
// for (SPIR-V 1.6, the Vulkan memory model, buffer device addresses, demote). vk11 is for drivers that
// offer only Vulkan 1.1 or 1.2 (the Adreno 610 of GitHub #5): SDL_gpu makes the device at API 1.1 with
// Vulkan 1.0 features only, and every module is lowered to SPIR-V 1.3 (d9g_shader_abi.cpp lower_vk11).
enum class ApiLevel : uint8_t { vk13, vk11 };

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
  // Host 2's own as well: the device was made at Vulkan 1.1 (ApiLevel::vk11). query_device_facts sets
  // it where it read the device below 1.3; the device (make_sdl_gpu) sets it to the level SDL_gpu's
  // device was made at. With it the profile is the 1.1 one (ShaderProfile::vk11).
  bool vk11 = false;
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
  // Host 2's own (d9g_shader_abi.cpp lower_vk11): a device made at Vulkan 1.1 (DeviceFacts::vk11).
  // Each module is lowered after the build to SPIR-V 1.3 with the GLSL450 memory model, its interface
  // trimmed to inputs and outputs and each demote (the alpha test's) made OpKill, and anything the
  // lowering cannot prove the same is refused by name. SDL_gpu's 1.1 device enables no Vulkan 1.2 feature
  // and no extension, so the profile also takes state_words, and clears 16-bit arithmetic, sub-dword
  // push data, every float-control mode, float controls 2, raw access chains, the descriptor heap and
  // resource indexing. Printed only when set, as state_words, so every 1.3 record stays as it was.
  bool vk11 = false;

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

// d9g_shader_vulkan.cpp: the facts of one Vulkan physical device, read on a throwaway instance through
// `gipa` (a PFN_vkGetInstanceProcAddr: the loader SDL_gpu loaded, from SDL_Vulkan_GetVkGetInstanceProcAddr,
// or libvulkan's own). `device_name` names the device (the one SDL_gpu chose); empty, exactly one device
// must exist (VK_DRIVER_FILES naming one driver). It changes nothing any other instance holds.
// The instance asks for API 1.3 wherever the loader has vkEnumerateInstanceVersion (any 1.1 loader), 1.0
// where it has not. Each structure is read only at a version that has it: at `cap` vk11 (the device made
// at 1.1) the lowest of the instance's, the device's own and 1.1; at vk13 (the device made at 1.3) the
// instance's and 1.3, whatever the device reports, as SDL_gpu made it at 1.3 only where its driver filled
// the 1.1, 1.2 and 1.3 feature structures (make_sdl_gpu names a device below 1.3 made so). At 1.3 the
// Vulkan 1.1, 1.2 and 1.3 chains are read, as before the 1.1 profile existed. Below it facts.vk11 is set,
// and only the 16-bit storage features, the extensions' own chains, VK_KHR_driver_properties (the
// driver's ID, which DXVK's driver rules read) where listed, and the R32 formats' features from
// VK_KHR_format_feature_flags2 where listed (shaderStorageImageReadWithoutFormat otherwise) are read: no
// float control, 16-bit arithmetic, 8-bit access or descriptor indexing, which SDL_gpu's 1.1 device
// enables none of. Below 1.1 nothing is read (ok false, the reason named).
struct DeviceFactsQuery {
  bool ok = false;
  std::string how;          // what was read, or why nothing was
  std::string device_name, driver_name, driver_info;
  DeviceFacts facts;
};
DeviceFactsQuery query_device_facts(void* gipa, const std::string& device_name, ApiLevel cap = ApiLevel::vk13);
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
//
// At ApiLevel::vk11 the API version is 1.1 and only Vulkan 1.0 features are asked for:
// robustBufferAccess, and shaderClipDistance with clip_distance. SDL_gpu's 1.1 path takes no Vulkan 1.1,
// 1.2 or 1.3 feature structure and enables no extension, so feature_list is null, and sub_dword and
// scalar_layout are off (the vk11 profile reads the state as words and needs no scalar layout).
class VulkanFeatures {
 public:
  // The optional groups a device may lack, by what the translator does without them, and the
  // three of SDL_gpu's own opt-out Vulkan 1.0 demands the device can be made without (SdlDemands).
  // api13 and api11 judge the devices SDL_gpu would choose among: the GPUs, or every device where none
  // is a GPU (lavapipe alone). Lacks::all() leaves both false.
  struct Lacks {
    bool sub_dword = false;      // any of the six sub-dword features
    bool clip_distance = false;  // shaderClipDistance
    bool scalar_layout = false;  // scalarBlockLayout
    bool depth_clamp = false;              // SDL's depthClamp
    bool indirect_first_instance = false;  // SDL's drawIndirectFirstInstance
    bool anisotropy = false;               // SDL's samplerAnisotropy
    // None of them takes the 1.3 profile: each reports below Vulkan 1.3, or lacks one of the four
    // features only it needs (bufferDeviceAddress, vulkanMemoryModel, vulkanMemoryModelDeviceScope,
    // shaderDemoteToHelperInvocation).
    bool api13 = false;
    bool api11 = false;          // none of them reports Vulkan 1.1 or later
    static Lacks all() { return {true, true, true, true, true, true}; }
  };
  // SDL_gpu's opt-out Vulkan 1.0 demands (SDL_gpu_vulkan.c VULKAN_INTERNAL_PrepareVulkan: on by
  // default, each off with its SDL_PROP_GPU_DEVICE_CREATE_FEATURE_*_BOOLEAN false) that a device
  // request leaves on, less shaderClipDistance, which follows clip_distance. None is needed by a
  // module. depthClamp is the patch's depth clip and clamp's (SDL builds the clamp only where it
  // was asked for, so a device made without it reports none and draws clip-only or refuses:
  // GpuInfo::depth_clamp_clip_only); host 2 issues no indirect draw, and draws with first instance
  // 0; a sampler asking for anisotropy on a device made without samplerAnisotropy is made without it
  // (d9g_gpu_sdl.cpp sampler_object).
  struct SdlDemands {
    bool depth_clamp = true;
    bool indirect_first_instance = true;
    bool anisotropy = true;
  };
  explicit VulkanFeatures(bool sub_dword = true, bool clip_distance = true, bool scalar_layout = true,
                          ApiLevel level = ApiLevel::vk13);
  ~VulkanFeatures();
  VulkanFeatures(const VulkanFeatures&) = delete;
  VulkanFeatures& operator=(const VulkanFeatures&) = delete;
  uint32_t api_version() const;
  void* feature_list();   // null at vk11
  void* features10();
  ApiLevel level() const { return level_; }
  bool sub_dword() const { return sub_dword_; }
  bool clip_distance() const { return clip_distance_; }
  bool scalar_layout() const { return scalar_layout_; }
  std::string describe() const;   // a JSON object: the API version and the features by name
  // The features asked for that each Vulkan physical device does not support, read on a throwaway
  // instance through `gipa` as query_device_facts reads (API 1.3 where the loader has
  // vkEnumerateInstanceVersion, 1.0 where not), each device's structures only at a version it has:
  // "<device>: a, b; <device> (Vulkan 1.1.128): none", the version beside a device read at vk11 or below
  // 1.3 (a device read at 1.3 for the 1.3 profile keeps the text it had before the 1.1 profile existed,
  // "<device>: a, b"). *fits is set when at least one
  // device supports them all. "" with *how set when the devices could not be read. *lacks (when
  // given): the optional groups some device lacks of those asked for, and api13 and api11. SDL_gpu's
  // own Vulkan 1.0 demands a device lacks are named too ("SDL: depthClamp"), the opt-out ones only
  // where `sdl` leaves them asked for, and such a device does not fit. At vk13 a device below 1.3 is
  // named "the 1.3 profile needs Vulkan 1.3", with its Vulkan 1.0 lacks (and, at 1.2, the four 1.3
  // profile features it lacks), and fits nowhere; at either level a device below 1.1 is named "the
  // renderer needs Vulkan 1.1".
  std::string lacking(void* gipa, bool* fits, std::string* how, Lacks* lacks, const SdlDemands& sdl) const;
  struct Chain;
 private:
  Chain* m;
  ApiLevel level_;
  bool sub_dword_;
  bool clip_distance_;
  bool scalar_layout_;
};

// How make_sdl_gpu chooses the device, as data, so a test holds the choice to literal expectations
// without SDL (d9g_gpu_sdl.cpp runs it). A request is the level, the optional groups asked for and
// SDL_gpu's opt-out demands left on.
struct DeviceRequest {
  ApiLevel level = ApiLevel::vk13;
  bool sub_dword = true, clip_distance = true, scalar_layout = true;
  VulkanFeatures::SdlDemands sdl;
  // The CPU step down's 1.1 request and its retries: GPUs only (SDL_gpu's
  // SDL_PROP_GPU_DEVICE_CREATE_VULKAN_REQUIRE_HARDWARE_ACCELERATION_BOOLEAN, which no other request sets),
  // so SDL_gpu cannot settle on the CPU device again at 1.1.
  bool hardware_only = false;
  // The CPU device's 1.3 request, gone back to after no GPU took the 1.1 one: the CPU device is kept.
  bool cpu_settled = false;
};
// The first request: the vk11 switch's (SA2_D3D9GPU_VK11=1, debug.sa2.vk11=1) at vk11, otherwise the
// full 1.3 request, less what a test run's override and the forcing switches drop.
DeviceRequest first_device_request(bool vk11_forced, bool sub_dword_off, bool clip_forced_off, bool clamp_forced_off);
// How SDL_gpu answered a request: not yet asked (before the first vk11 request, whose devices are
// read first), refused, accepted, or accepted on a CPU device (lavapipe and the like).
enum class DeviceAnswer : uint8_t { unasked, refused, accepted, accepted_cpu };
struct DeviceStep {
  // restore: back to the CPU device's 1.3 request (the fallback the CPU step down returned)
  enum Kind : uint8_t { done, attempt, refuse_api11, give_up, restore };
  Kind kind = done;
  DeviceRequest request;  // attempt: what to ask for next
  int retry = 0;          // attempt: the retry's index at its level (0 the first after the level's first request)
  bool step_down = false; // attempt: the first request at vk11, after the 1.3 profile was found unusable
  std::string dropped;    // a retry: what it drops, by name ("shaderClipDistance, SDL's depthClamp")
  // the CPU step down: the 1.3 request SDL_gpu accepted on the CPU device, cpu_settled, which a restore
  // asks for again
  DeviceRequest fallback;
};
// The step after `last` was answered `answer`. `retry`: the retries made at last's level after its first
// request (0 after the first). `lacks` and `fits`: what the devices lack of `last` (VulkanFeatures::lacking
// at its level), read after its level's first refusal, and for accepted_cpu after that acceptance.
//   - Accepted: done. Accepted on a CPU device at vk13 where a GPU takes the 1.1 profile but no GPU the
//     1.3 one (lacks.api13 and not api11): a step down to vk11, GPUs only (hardware_only), so that GPU
//     draws, not the CPU, with `last` returned as the fallback, cpu_settled. A GPU may still be refused at
//     1.1 for what lacks does not read (SDL_gpu refuses a non-conformant driver, such as hasvk, and one
//     without a present queue): where the GPU-only request is refused and its retries give up or end in
//     refuse_api11, or it is accepted on a CPU device after all, restore, and the CPU device keeps its 1.3.
//     A cpu_settled request accepted on the CPU device is done, and refused, give_up: no loop.
//   - Refused at vk13 at its first request with lacks.api13: refuse_api11 where lacks.api11 (no device
//     reports 1.1), otherwise a step down to vk11, without the clip distance and SDL demands lacks names.
//     Never for any other reason: a device that takes the 1.3 profile and lacks something else keeps
//     the 1.3 retries.
//   - At vk11 with lacks.api11: refuse_api11, before any request reaches SDL_gpu (unasked included).
//   - Otherwise a refusal is retried at the same level as before the 1.1 profile existed: once without
//     the optional groups and SDL demands lacks names, then once without every one still asked for,
//     each skipped when it drops nothing; none where a device fits (it lacks nothing to drop) and none
//     after the second (give_up).
DeviceStep next_device_request(const DeviceRequest& last, DeviceAnswer answer, int retry, const VulkanFeatures::Lacks& lacks,
                               bool fits);
// make_sdl_gpu's line for a device SDL_gpu made at Vulkan 1.3 whose own apiVersion (`device_api`) is
// below 1.3, which SDL_gpu does where the driver fills the 1.3 feature structures it checks; "" at 1.3
// or later, and where unread (0).
std::string device_below13_note(uint32_t device_api);

}  // namespace d9g::shader
