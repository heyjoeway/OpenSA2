// Host 2's Vulkan side of the shader translator (R1 U3): the device facts the profile is computed
// from, read from the device SDL_gpu chose, and the Vulkan API version and features the translated
// modules need, which SDL_gpu's device is created with (SDL_GPUVulkanOptions). Compiled with the
// translator's units (shader/build_translator.sh: DXVK's flags, the pinned checkout's Vulkan
// headers, which name the newest structures the facts read), so the device and d3d9gpu_shaderc
// share one query and one feature request.
#include "d9g_shader_profile.h"

#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>

#include <cstdio>
#include <set>
#include <sstream>
#include <utility>
#include <vector>

namespace d9g::shader {

namespace {

std::string json_str(const std::string& s) {
  std::string o = "\"";
  for (char c : s) {
    if (c == '"' || c == '\\') { o += '\\'; o += c; }
    else if (uint8_t(c) < 0x20) { char b[8]; std::snprintf(b, sizeof b, "\\u%04x", c); o += b; }
    else o += c;
  }
  return o + "\"";
}

const char* tf(bool b) { return b ? "true" : "false"; }

}  // namespace

// What the modules dxbc-spirv builds for the game's shaders declare, each feature named by the
// capability or the validation rule that needs it (Vulkan's SPIR-V environment appendix):
//   ClipDistance                         shaderClipDistance (1.0)
//   Int16                                shaderInt16 (1.0)
//   Int8                                 shaderInt8 (1.2)
//   8-bit members in a Uniform block     storageBuffer8BitAccess, uniformAndStorageBuffer8BitAccess (1.2):
//                                        D3D9SharedPushData's u8 alphaRef and fogColor in the state buffer
//   16-bit members in a Uniform block    storageBuffer16BitAccess, uniformAndStorageBuffer16BitAccess (1.1):
//                                        D3D9VsPushData's u16 point sizes
//   the state buffer's scalar layout     scalarBlockLayout (1.2)
//   PhysicalStorageBufferAddresses       bufferDeviceAddress (1.2)
//   VulkanMemoryModel                    vulkanMemoryModel, vulkanMemoryModelDeviceScope (1.2)
//   DemoteToHelperInvocation             shaderDemoteToHelperInvocation (1.3)
// R0's chain (private/generated/s5/next/D/r0/sdltest/sdlpipes.c) plus the four the validation layer
// named on it (8- and 16-bit uniform access, shaderInt8, shaderInt16). Int8, Int16 and the 8- and
// 16-bit members come from the state buffer's sub-dword members only (test/spv_explained.json), so
// the six features marked sub_dword are asked for only with VulkanFeatures(true); with state_words
// (d9g_shader_abi.cpp) no module declares them. ClipDistance comes from the vertex modules' clipping
// output only (sm3_io_map.cpp: ClipDistance[6], written in every vertex shader), so shaderClipDistance
// is asked for only with VulkanFeatures(.., true); with no_clip_distance (d9g_shader.cpp) no module
// declares it. scalarBlockLayout is R0's, but no module needs it: every module of the four streams,
// in each profile, follows the relaxed uniform buffer layout rules (spirv-val --target-env vulkan1.3
// without --scalar-block-layout; test_s5_d3d9gpu_shader.py test_every_module_is_valid_spirv holds the
// synthetic ones), so it is asked for only with VulkanFeatures(.., .., true), and dropped only on a
// device that lacks it (Vulkan 1.3 does not require it). robustBufferAccess: legal robust vertex
// fetches; the Device names unproved out-of-bounds values as unmapped. In describe()'s order.
namespace {
enum class Group { Core, SubDword, ClipDistance, ScalarLayout };
struct Feature {
  const char* name;
  Group group;
  VkBool32& (*field)(VulkanFeatures::Chain&);
};
}  // namespace

struct VulkanFeatures::Chain {
  VkPhysicalDeviceFeatures f10{};
  VkPhysicalDeviceVulkan11Features f11{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES};
  VkPhysicalDeviceVulkan12Features f12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};
  VkPhysicalDeviceVulkan13Features f13{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_3_FEATURES};
  Chain() {
    f11.pNext = &f12;
    f12.pNext = &f13;
  }
  Chain(const Chain&) = delete;
  Chain& operator=(const Chain&) = delete;
};

namespace {
#define D9G_FEATURE(level, member, group) \
  {#member, Group::group, [](VulkanFeatures::Chain& c) -> VkBool32& { return c.level.member; }}
const Feature kFeatures[] = {
    D9G_FEATURE(f10, robustBufferAccess, Core),
    D9G_FEATURE(f10, shaderClipDistance, ClipDistance),
    D9G_FEATURE(f10, shaderInt16, SubDword),
    D9G_FEATURE(f11, storageBuffer16BitAccess, SubDword),
    D9G_FEATURE(f11, uniformAndStorageBuffer16BitAccess, SubDword),
    D9G_FEATURE(f12, storageBuffer8BitAccess, SubDword),
    D9G_FEATURE(f12, uniformAndStorageBuffer8BitAccess, SubDword),
    D9G_FEATURE(f12, shaderInt8, SubDword),
    D9G_FEATURE(f12, scalarBlockLayout, ScalarLayout),
    D9G_FEATURE(f12, bufferDeviceAddress, Core),
    D9G_FEATURE(f12, vulkanMemoryModel, Core),
    D9G_FEATURE(f12, vulkanMemoryModelDeviceScope, Core),
    D9G_FEATURE(f13, shaderDemoteToHelperInvocation, Core),
};
#undef D9G_FEATURE
}  // namespace

// The structures VulkanFeatures owns, chained as SDL_GPUVulkanOptions takes them: feature_list is
// the 1.1 struct, which chains the 1.2 and the 1.3 ones; the 1.0 features go separately.
VulkanFeatures::VulkanFeatures(bool sub_dword, bool clip_distance, bool scalar_layout)
    : m(new Chain), sub_dword_(sub_dword), clip_distance_(clip_distance), scalar_layout_(scalar_layout) {
  for (const Feature& f : kFeatures)
    if ((f.group != Group::SubDword || sub_dword) && (f.group != Group::ClipDistance || clip_distance) &&
        (f.group != Group::ScalarLayout || scalar_layout))
      f.field(*m) = VK_TRUE;
}

VulkanFeatures::~VulkanFeatures() { delete m; }

uint32_t VulkanFeatures::api_version() const { return VK_API_VERSION_1_3; }
void* VulkanFeatures::feature_list() { return &m->f11; }
void* VulkanFeatures::features10() { return &m->f10; }

std::string VulkanFeatures::describe() const {
  std::string s = "{\"api_version\": \"1.3\", \"features\": [";
  bool first = true;
  for (const Feature& f : kFeatures) {
    if (!f.field(*m)) continue;
    s += std::string(first ? "" : ", ") + "\"" + f.name + "\"";
    first = false;
  }
  return s + "], \"extensions\": []}";
}

std::string VulkanFeatures::lacking(void* gipa_ptr, bool* fits, std::string* how, Lacks* lacks) const {
  *fits = false;
  if (lacks) *lacks = Lacks{};
  auto gipa = reinterpret_cast<PFN_vkGetInstanceProcAddr>(gipa_ptr);
  if (!gipa) { *how = "no vkGetInstanceProcAddr"; return ""; }
  auto create = reinterpret_cast<PFN_vkCreateInstance>(gipa(VK_NULL_HANDLE, "vkCreateInstance"));
  if (!create) { *how = "no vkCreateInstance"; return ""; }
  VkApplicationInfo ai{VK_STRUCTURE_TYPE_APPLICATION_INFO};
  ai.apiVersion = VK_API_VERSION_1_3;
  VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
  ici.pApplicationInfo = &ai;
  VkInstance inst = VK_NULL_HANDLE;
  if (create(&ici, nullptr, &inst) != VK_SUCCESS) { *how = "vkCreateInstance (API 1.3) failed"; return ""; }
  auto destroy = reinterpret_cast<PFN_vkDestroyInstance>(gipa(inst, "vkDestroyInstance"));
  auto enumdev = reinterpret_cast<PFN_vkEnumeratePhysicalDevices>(gipa(inst, "vkEnumeratePhysicalDevices"));
  auto props = reinterpret_cast<PFN_vkGetPhysicalDeviceProperties>(gipa(inst, "vkGetPhysicalDeviceProperties"));
  auto feats = reinterpret_cast<PFN_vkGetPhysicalDeviceFeatures2>(gipa(inst, "vkGetPhysicalDeviceFeatures2"));
  uint32_t n = 0;
  std::vector<VkPhysicalDevice> devs;
  if (enumdev && props && feats && enumdev(inst, &n, nullptr) == VK_SUCCESS && n) {
    devs.resize(n);
    if (enumdev(inst, &n, devs.data()) != VK_SUCCESS) devs.clear();
  }
  std::string out;
  for (VkPhysicalDevice pd : devs) {
    VkPhysicalDeviceProperties p{};
    props(pd, &p);
    Chain have;
    VkPhysicalDeviceFeatures2 f2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
    f2.pNext = &have.f11;
    feats(pd, &f2);
    have.f10 = f2.features;
    std::string names;
    // SDL_gpu's own Vulkan 1.0 demands (SDL_gpu_vulkan.c VULKAN_INTERNAL_IsDeviceSuitable: the three it
    // always asks for and the opt-out ones on by default, less shaderClipDistance, which follows ours),
    // named "SDL: <feature>": a device lacking one is refused before our features are read
    const std::pair<const char*, VkBool32> sdl[] = {
        {"independentBlend", have.f10.independentBlend}, {"imageCubeArray", have.f10.imageCubeArray},
        {"sampleRateShading", have.f10.sampleRateShading}, {"depthClamp", have.f10.depthClamp},
        {"drawIndirectFirstInstance", have.f10.drawIndirectFirstInstance},
        {"samplerAnisotropy", have.f10.samplerAnisotropy}};
    for (const auto& [name, supported] : sdl)
      if (!supported) names += std::string(names.empty() ? "" : ", ") + "SDL: " + name;
    for (const Feature& f : kFeatures) {
      if (!f.field(*m) || f.field(have)) continue;
      names += std::string(names.empty() ? "" : ", ") + f.name;
      if (lacks && f.group == Group::SubDword) lacks->sub_dword = true;
      if (lacks && f.group == Group::ClipDistance) lacks->clip_distance = true;
      if (lacks && f.group == Group::ScalarLayout) lacks->scalar_layout = true;
    }
    *fits = *fits || names.empty();
    out += std::string(out.empty() ? "" : "; ") + p.deviceName + ": " + (names.empty() ? "none" : names);
  }
  if (destroy) destroy(inst, nullptr);
  if (devs.empty()) { *how = "vkEnumeratePhysicalDevices found none"; return ""; }
  *how = "vkGetPhysicalDeviceFeatures2 (Vulkan 1.1, 1.2 and 1.3 chains) of every physical device";
  return out;
}

DeviceFactsQuery query_device_facts(void* gipa_ptr, const std::string& device_name) {
  DeviceFactsQuery q;
  auto gipa = reinterpret_cast<PFN_vkGetInstanceProcAddr>(gipa_ptr);
  if (!gipa) { q.how = "no vkGetInstanceProcAddr"; return q; }
  auto create = reinterpret_cast<PFN_vkCreateInstance>(gipa(VK_NULL_HANDLE, "vkCreateInstance"));
  if (!create) { q.how = "no vkCreateInstance"; return q; }
  VkApplicationInfo ai{VK_STRUCTURE_TYPE_APPLICATION_INFO};
  ai.apiVersion = VK_API_VERSION_1_3;
  VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
  ici.pApplicationInfo = &ai;
  VkInstance inst = VK_NULL_HANDLE;
  if (create(&ici, nullptr, &inst) != VK_SUCCESS) { q.how = "vkCreateInstance (API 1.3) failed"; return q; }
  auto destroy = reinterpret_cast<PFN_vkDestroyInstance>(gipa(inst, "vkDestroyInstance"));
  auto enumdev = reinterpret_cast<PFN_vkEnumeratePhysicalDevices>(gipa(inst, "vkEnumeratePhysicalDevices"));
  auto props = reinterpret_cast<PFN_vkGetPhysicalDeviceProperties2>(gipa(inst, "vkGetPhysicalDeviceProperties2"));
  auto feats = reinterpret_cast<PFN_vkGetPhysicalDeviceFeatures2>(gipa(inst, "vkGetPhysicalDeviceFeatures2"));
  auto fmtprops = reinterpret_cast<PFN_vkGetPhysicalDeviceFormatProperties2>(gipa(inst, "vkGetPhysicalDeviceFormatProperties2"));
  auto exts = reinterpret_cast<PFN_vkEnumerateDeviceExtensionProperties>(gipa(inst, "vkEnumerateDeviceExtensionProperties"));
  uint32_t n = 0;
  std::vector<VkPhysicalDevice> devs;
  if (enumdev && props && feats && fmtprops && exts && enumdev(inst, &n, nullptr) == VK_SUCCESS && n) {
    devs.resize(n);
    if (enumdev(inst, &n, devs.data()) != VK_SUCCESS) devs.clear();
  }
  VkPhysicalDevice d = VK_NULL_HANDLE;
  int matches = 0;
  for (VkPhysicalDevice pd : devs) {
    VkPhysicalDeviceProperties2 p{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
    props(pd, &p);
    if (!device_name.empty() && device_name != p.properties.deviceName) continue;
    if (!matches++) d = pd;
  }
  if (matches != 1) {
    q.how = device_name.empty() ? std::to_string(devs.size()) + " devices: the query needs exactly one (VK_DRIVER_FILES)"
                                : std::to_string(matches) + " physical devices named " + device_name;
    if (destroy) destroy(inst, nullptr);
    return q;
  }
  VkPhysicalDeviceVulkan12Properties p12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_PROPERTIES};
  VkPhysicalDeviceProperties2 p{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
  p.pNext = &p12;
  props(d, &p);
  uint32_t ne = 0;
  exts(d, nullptr, &ne, nullptr);
  std::vector<VkExtensionProperties> ev(ne);
  exts(d, nullptr, &ne, ev.data());
  std::set<std::string> es;
  for (auto& e : ev) es.insert(e.extensionName);
  const bool hasRaw = es.count("VK_NV_raw_access_chains"), hasFc2 = es.count("VK_KHR_shader_float_controls2"),
             hasHeap = es.count("VK_EXT_descriptor_heap");
  VkPhysicalDeviceDescriptorHeapFeaturesEXT fheap{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DESCRIPTOR_HEAP_FEATURES_EXT};
  VkPhysicalDeviceRawAccessChainsFeaturesNV fraw{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RAW_ACCESS_CHAINS_FEATURES_NV};
  VkPhysicalDeviceShaderFloatControls2Features ffc2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT_CONTROLS_2_FEATURES};
  VkPhysicalDeviceVulkan12Features f12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};
  VkPhysicalDeviceVulkan11Features f11{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES};
  VkPhysicalDeviceFeatures2 f{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
  void* chain = &f12;
  f11.pNext = chain; chain = &f11;
  if (hasFc2) { ffc2.pNext = chain; chain = &ffc2; }
  if (hasRaw) { fraw.pNext = chain; chain = &fraw; }
  if (hasHeap) { fheap.pNext = chain; chain = &fheap; }
  f.pNext = chain;
  feats(d, &f);
  VkFormatFeatureFlags2 r32 = ~VkFormatFeatureFlags2(0);
  for (VkFormat fm : {VK_FORMAT_R32_SFLOAT, VK_FORMAT_R32_UINT, VK_FORMAT_R32_SINT}) {
    VkFormatProperties3 f3{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3};
    VkFormatProperties2 f2{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2};
    f2.pNext = &f3;
    fmtprops(d, fm, &f2);
    r32 &= f3.optimalTilingFeatures;
  }
  // dxvk_device.cpp:883-894: every one of the twelve indexing features
  const bool resIdx = f.features.shaderUniformBufferArrayDynamicIndexing && f.features.shaderSampledImageArrayDynamicIndexing &&
                      f.features.shaderStorageBufferArrayDynamicIndexing && f.features.shaderStorageImageArrayDynamicIndexing &&
                      f12.shaderUniformTexelBufferArrayDynamicIndexing && f12.shaderStorageTexelBufferArrayDynamicIndexing &&
                      f12.shaderUniformBufferArrayNonUniformIndexing && f12.shaderSampledImageArrayNonUniformIndexing &&
                      f12.shaderStorageBufferArrayNonUniformIndexing && f12.shaderStorageImageArrayNonUniformIndexing &&
                      f12.shaderUniformTexelBufferArrayNonUniformIndexing && f12.shaderStorageTexelBufferArrayNonUniformIndexing;
  const auto& L = p.properties.limits;
  DeviceFacts& o = q.facts;
  o.driver_id = uint32_t(p12.driverID);
  o.driver_version_raw = p.properties.driverVersion;
  o.min_storage_buffer_offset_alignment = uint32_t(L.minStorageBufferOffsetAlignment);
  o.max_uniform_buffer_range = L.maxUniformBufferRange;
  o.max_per_stage_descriptor_uniform_buffers = L.maxPerStageDescriptorUniformBuffers;
  o.shader_float16 = f12.shaderFloat16;
  o.storage_push_constant16 = f11.storagePushConstant16;
  o.storage_push_constant8 = f12.storagePushConstant8;
  o.r32_storage_read_without_format = (r32 & VK_FORMAT_FEATURE_2_STORAGE_READ_WITHOUT_FORMAT_BIT) != 0;
  o.nv_raw_access_chains = hasRaw && fraw.shaderRawAccessChains;
  const VkBool32 sz[3] = {p12.shaderSignedZeroInfNanPreserveFloat16, p12.shaderSignedZeroInfNanPreserveFloat32,
                          p12.shaderSignedZeroInfNanPreserveFloat64};
  const VkBool32 rte[3] = {p12.shaderRoundingModeRTEFloat16, p12.shaderRoundingModeRTEFloat32, p12.shaderRoundingModeRTEFloat64};
  const VkBool32 rtz[3] = {p12.shaderRoundingModeRTZFloat16, p12.shaderRoundingModeRTZFloat32, p12.shaderRoundingModeRTZFloat64};
  const VkBool32 ftz[3] = {p12.shaderDenormFlushToZeroFloat16, p12.shaderDenormFlushToZeroFloat32,
                           p12.shaderDenormFlushToZeroFloat64};
  const VkBool32 dp[3] = {p12.shaderDenormPreserveFloat16, p12.shaderDenormPreserveFloat32, p12.shaderDenormPreserveFloat64};
  for (int i = 0; i < 3; i++) {
    o.sz_inf_nan_preserve[i] = sz[i];
    o.rte[i] = rte[i];
    o.rtz[i] = rtz[i];
    o.denorm_flush[i] = ftz[i];
    o.denorm_preserve[i] = dp[i];
  }
  o.rounding_mode_independence = uint32_t(p12.roundingModeIndependence);
  o.denorm_behavior_independence = uint32_t(p12.denormBehaviorIndependence);
  o.float_controls2 = hasFc2 && ffc2.shaderFloatControls2;
  o.descriptor_heap = hasHeap && fheap.descriptorHeap;
  o.resource_indexing = resIdx;
  o.sub_dword_access = f.features.shaderInt16 && f11.storageBuffer16BitAccess && f11.uniformAndStorageBuffer16BitAccess &&
                       f12.storageBuffer8BitAccess && f12.uniformAndStorageBuffer8BitAccess && f12.shaderInt8;
  o.clip_distance = f.features.shaderClipDistance;
  q.device_name = p.properties.deviceName;
  q.driver_name = p12.driverName;
  q.driver_info = p12.driverInfo;
  q.ok = true;
  q.how = "vkGetPhysicalDeviceProperties2 and vkGetPhysicalDeviceFeatures2 (Vulkan 1.1, 1.2 and the extensions' chains), "
          "vkGetPhysicalDeviceFormatProperties2 (R32 formats)";
  if (destroy) destroy(inst, nullptr);
  return q;
}

std::string facts_json(const DeviceFactsQuery& q) {
  const DeviceFacts& f = q.facts;
  auto b3 = [](const bool* v) {
    return std::string("[") + tf(v[0]) + ", " + tf(v[1]) + ", " + tf(v[2]) + "]";
  };
  std::ostringstream s;
  s << "{\n \"schema\": \"sa2-d3d9gpu-shader-facts/v1\",\n \"device\": " << json_str(q.device_name)
    << ",\n \"driver_name\": " << json_str(q.driver_name) << ",\n \"driver_info\": " << json_str(q.driver_info)
    << ",\n \"driver_id\": " << f.driver_id << ",\n \"driver_version_raw\": " << f.driver_version_raw
    << ",\n \"min_storage_buffer_offset_alignment\": " << f.min_storage_buffer_offset_alignment
    << ",\n \"max_uniform_buffer_range\": " << f.max_uniform_buffer_range
    << ",\n \"max_per_stage_descriptor_uniform_buffers\": " << f.max_per_stage_descriptor_uniform_buffers
    << ",\n \"shader_float16\": " << tf(f.shader_float16) << ",\n \"storage_push_constant16\": " << tf(f.storage_push_constant16)
    << ",\n \"storage_push_constant8\": " << tf(f.storage_push_constant8)
    << ",\n \"r32_storage_read_without_format\": " << tf(f.r32_storage_read_without_format)
    << ",\n \"nv_raw_access_chains\": " << tf(f.nv_raw_access_chains)
    << ",\n \"sz_inf_nan_preserve\": " << b3(f.sz_inf_nan_preserve) << ",\n \"rte\": " << b3(f.rte)
    << ",\n \"rtz\": " << b3(f.rtz) << ",\n \"denorm_flush\": " << b3(f.denorm_flush)
    << ",\n \"denorm_preserve\": " << b3(f.denorm_preserve)
    << ",\n \"rounding_mode_independence\": " << f.rounding_mode_independence
    << ",\n \"denorm_behavior_independence\": " << f.denorm_behavior_independence
    << ",\n \"float_controls2\": " << tf(f.float_controls2) << ",\n \"descriptor_heap\": " << tf(f.descriptor_heap)
    << ",\n \"resource_indexing\": " << tf(f.resource_indexing)
    << ",\n \"sub_dword_access\": " << tf(f.sub_dword_access);
  // only when the device lacks it: a device that has it keeps the facts file it had before the field
  if (!f.clip_distance) s << ",\n \"clip_distance\": false";
  s << "\n}\n";
  return s.str();
}

}  // namespace d9g::shader
