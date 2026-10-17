// Host 2's Vulkan side of the shader translator (R1 U3): the device facts the profile is computed
// from, read from the device SDL_gpu chose, and the Vulkan API version and features the translated
// modules need, which SDL_gpu's device is created with (SDL_GPUVulkanOptions). Compiled with the
// translator's units (shader/build_translator.sh: DXVK's flags, the pinned checkout's Vulkan
// headers, which name the newest structures the facts read), so the device and d3d9gpu_shaderc
// share one query and one feature request.
#include "d9g_shader_profile.h"

#define VK_NO_PROTOTYPES
#include <vulkan/vulkan.h>

#include <algorithm>
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
  int level;   // the structure that holds it: 10, VkPhysicalDeviceFeatures; 11, 12 or 13, VkPhysicalDeviceVulkan1xFeatures
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
constexpr int kLevel_f10 = 10, kLevel_f11 = 11, kLevel_f12 = 12, kLevel_f13 = 13;
#define D9G_FEATURE(level, member, group) \
  {#member, Group::group, kLevel_##level, [](VulkanFeatures::Chain& c) -> VkBool32& { return c.level.member; }}
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
// The four only the 1.3 profile needs (its modules' PhysicalStorageBufferAddresses, VulkanMemoryModel and
// DemoteToHelperInvocation): the Core features above Vulkan 1.0.
bool profile13_only(const Feature& f) { return f.group == Group::Core && f.level > kLevel_f10; }

// A Vulkan version's major and minor (variant and patch dropped), comparable.
uint32_t major_minor(uint32_t v) { return VK_MAKE_API_VERSION(0, VK_API_VERSION_MAJOR(v), VK_API_VERSION_MINOR(v), 0); }
std::string version_text(uint32_t v) {
  return std::to_string(VK_API_VERSION_MAJOR(v)) + "." + std::to_string(VK_API_VERSION_MINOR(v)) + "." +
         std::to_string(VK_API_VERSION_PATCH(v));
}

// The throwaway instance lacking() and query_device_facts() read through. It asks for API 1.3 wherever
// the loader has vkEnumerateInstanceVersion (any 1.1 loader takes an apiVersion above its own; a device
// is then limited by its own version, not the loader's), and 1.0 where it has not (a 1.0 loader refuses
// any other).
struct Throwaway {
  PFN_vkGetInstanceProcAddr gipa;
  VkInstance inst = VK_NULL_HANDLE;
  uint32_t api = VK_API_VERSION_1_0;
  std::string error;
  explicit Throwaway(PFN_vkGetInstanceProcAddr g) : gipa(g) {
    auto create = reinterpret_cast<PFN_vkCreateInstance>(gipa(VK_NULL_HANDLE, "vkCreateInstance"));
    if (!create) { error = "no vkCreateInstance"; return; }
    if (gipa(VK_NULL_HANDLE, "vkEnumerateInstanceVersion")) api = VK_API_VERSION_1_3;
    VkApplicationInfo ai{VK_STRUCTURE_TYPE_APPLICATION_INFO};
    ai.apiVersion = api;
    VkInstanceCreateInfo ici{VK_STRUCTURE_TYPE_INSTANCE_CREATE_INFO};
    ici.pApplicationInfo = &ai;
    if (create(&ici, nullptr, &inst) != VK_SUCCESS) {
      inst = VK_NULL_HANDLE;
      error = std::string("vkCreateInstance (API ") + (api == VK_API_VERSION_1_3 ? "1.3" : "1.0") + ") failed";
    }
  }
  ~Throwaway() {
    if (!inst) return;
    auto destroy = reinterpret_cast<PFN_vkDestroyInstance>(gipa(inst, "vkDestroyInstance"));
    if (destroy) destroy(inst, nullptr);
  }
  Throwaway(const Throwaway&) = delete;
  Throwaway& operator=(const Throwaway&) = delete;
  template <class F> F fn(const char* name) const { return reinterpret_cast<F>(gipa(inst, name)); }
  std::vector<VkPhysicalDevice> devices() const {
    auto enumdev = fn<PFN_vkEnumeratePhysicalDevices>("vkEnumeratePhysicalDevices");
    uint32_t n = 0;
    std::vector<VkPhysicalDevice> devs;
    if (enumdev && enumdev(inst, &n, nullptr) == VK_SUCCESS && n) {
      devs.resize(n);
      if (enumdev(inst, &n, devs.data()) != VK_SUCCESS) devs.clear();
    }
    return devs;
  }
};

std::set<std::string> device_extensions(PFN_vkEnumerateDeviceExtensionProperties exts, VkPhysicalDevice d) {
  std::set<std::string> es;
  if (!exts) return es;
  uint32_t ne = 0;
  exts(d, nullptr, &ne, nullptr);
  std::vector<VkExtensionProperties> ev(ne);
  exts(d, nullptr, &ne, ev.data());
  for (auto& e : ev) es.insert(e.extensionName);
  return es;
}
}  // namespace

// The structures VulkanFeatures owns, chained as SDL_GPUVulkanOptions takes them: feature_list is
// the 1.1 struct, which chains the 1.2 and the 1.3 ones; the 1.0 features go separately. At vk11 only
// the 1.0 features are set and feature_list is null.
VulkanFeatures::VulkanFeatures(bool sub_dword, bool clip_distance, bool scalar_layout, ApiLevel level)
    : m(new Chain), level_(level), sub_dword_(sub_dword && level == ApiLevel::vk13), clip_distance_(clip_distance),
      scalar_layout_(scalar_layout && level == ApiLevel::vk13) {
  for (const Feature& f : kFeatures)
    if ((level == ApiLevel::vk13 || f.level == kLevel_f10) && (f.group != Group::SubDword || sub_dword_) &&
        (f.group != Group::ClipDistance || clip_distance_) && (f.group != Group::ScalarLayout || scalar_layout_))
      f.field(*m) = VK_TRUE;
}

VulkanFeatures::~VulkanFeatures() { delete m; }

uint32_t VulkanFeatures::api_version() const { return level_ == ApiLevel::vk11 ? VK_API_VERSION_1_1 : VK_API_VERSION_1_3; }
void* VulkanFeatures::feature_list() { return level_ == ApiLevel::vk11 ? nullptr : &m->f11; }
void* VulkanFeatures::features10() { return &m->f10; }

std::string VulkanFeatures::describe() const {
  std::string s = std::string("{\"api_version\": \"") + (level_ == ApiLevel::vk11 ? "1.1" : "1.3") + "\", \"features\": [";
  bool first = true;
  for (const Feature& f : kFeatures) {
    if (!f.field(*m)) continue;
    s += std::string(first ? "" : ", ") + "\"" + f.name + "\"";
    first = false;
  }
  return s + "], \"extensions\": []}";
}

std::string VulkanFeatures::lacking(void* gipa_ptr, bool* fits, std::string* how, Lacks* lacks,
                                    const SdlDemands& sdl_asked) const {
  *fits = false;
  if (lacks) *lacks = Lacks{};
  auto gipa = reinterpret_cast<PFN_vkGetInstanceProcAddr>(gipa_ptr);
  if (!gipa) { *how = "no vkGetInstanceProcAddr"; return ""; }
  Throwaway t(gipa);
  if (!t.inst) { *how = t.error; return ""; }
  auto props = t.fn<PFN_vkGetPhysicalDeviceProperties>("vkGetPhysicalDeviceProperties");
  auto feats10 = t.fn<PFN_vkGetPhysicalDeviceFeatures>("vkGetPhysicalDeviceFeatures");
  auto feats = t.fn<PFN_vkGetPhysicalDeviceFeatures2>("vkGetPhysicalDeviceFeatures2");
  auto exts = t.fn<PFN_vkEnumerateDeviceExtensionProperties>("vkEnumerateDeviceExtensionProperties");
  std::vector<VkPhysicalDevice> devs;
  if (props && feats10 && (feats || t.api == VK_API_VERSION_1_0)) devs = t.devices();
  const bool vk13 = level_ == ApiLevel::vk13;
  // Per device, for api13 and api11: a CPU device, one that takes the 1.3 profile, one at 1.1 or later.
  struct Judged { bool cpu, takes13, has11; };
  std::vector<Judged> judged;
  std::string out;
  for (VkPhysicalDevice pd : devs) {
    VkPhysicalDeviceProperties p{};
    props(pd, &p);
    // Each structure only at a version the device has (the instance's and its own, the lower): the 1.1,
    // 1.2 and 1.3 chains at 1.3; at 1.2 the 1.1 and 1.2 ones, with the demote extension's where listed,
    // for the four features only the 1.3 profile needs; below, and at vk11, the 1.0 features alone.
    const uint32_t eff = std::min(major_minor(t.api), major_minor(p.apiVersion));
    Chain have;
    bool read13 = false, read12 = false;
    if (vk13 && eff >= VK_API_VERSION_1_3) {
      VkPhysicalDeviceFeatures2 f2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
      f2.pNext = &have.f11;
      feats(pd, &f2);
      have.f10 = f2.features;
      read13 = true;
    } else if (vk13 && eff == VK_API_VERSION_1_2) {
      VkPhysicalDeviceShaderDemoteToHelperInvocationFeatures demote{
          VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_DEMOTE_TO_HELPER_INVOCATION_FEATURES};
      have.f12.pNext = device_extensions(exts, pd).count("VK_EXT_shader_demote_to_helper_invocation") ? &demote : nullptr;
      VkPhysicalDeviceFeatures2 f2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
      f2.pNext = &have.f11;
      feats(pd, &f2);
      have.f10 = f2.features;
      have.f12.pNext = &have.f13;
      have.f13.shaderDemoteToHelperInvocation = demote.shaderDemoteToHelperInvocation;
      read12 = true;
    } else {
      feats10(pd, &have.f10);
    }
    std::string names;
    auto name = [&](const std::string& n) { names += std::string(names.empty() ? "" : ", ") + n; };
    if (eff < VK_API_VERSION_1_1) name("the renderer needs Vulkan 1.1");
    else if (vk13 && eff < VK_API_VERSION_1_3) name("the 1.3 profile needs Vulkan 1.3");
    // SDL_gpu's own Vulkan 1.0 demands (SDL_gpu_vulkan.c VULKAN_INTERNAL_IsDeviceSuitable: the three it
    // always asks for and the opt-out ones `sdl` leaves on, less shaderClipDistance, which follows
    // ours), named "SDL: <feature>": a device lacking one is refused before our features are read
    struct SdlDemand {
      const char* name;
      VkBool32 supported;
      bool asked;
      bool Lacks::*lack;   // the opt-out ones' Lacks member, nullptr for the three always asked for
    };
    const SdlDemand sdl[] = {
        {"independentBlend", have.f10.independentBlend, true, nullptr},
        {"imageCubeArray", have.f10.imageCubeArray, true, nullptr},
        {"sampleRateShading", have.f10.sampleRateShading, true, nullptr},
        {"depthClamp", have.f10.depthClamp, sdl_asked.depth_clamp, &Lacks::depth_clamp},
        {"drawIndirectFirstInstance", have.f10.drawIndirectFirstInstance, sdl_asked.indirect_first_instance,
         &Lacks::indirect_first_instance},
        {"samplerAnisotropy", have.f10.samplerAnisotropy, sdl_asked.anisotropy, &Lacks::anisotropy}};
    for (const SdlDemand& d : sdl) {
      if (d.supported || !d.asked) continue;
      name(std::string("SDL: ") + d.name);
      if (lacks && d.lack) lacks->*d.lack = true;
    }
    // Ours, where read: at 1.3 every one, as before the 1.1 profile existed; otherwise the 1.0 ones, and
    // at 1.2 the 1.3 profile's own four. The groups' Lacks members from what was read at the level asked.
    for (const Feature& f : kFeatures) {
      const bool read = f.level == kLevel_f10 || read13 || (read12 && profile13_only(f));
      if (!f.field(*m) || !read || f.field(have)) continue;
      name(f.name);
      if (lacks && f.group == Group::SubDword) lacks->sub_dword = true;
      if (lacks && f.group == Group::ClipDistance) lacks->clip_distance = true;
      if (lacks && f.group == Group::ScalarLayout) lacks->scalar_layout = true;
    }
    bool takes13 = read13;
    for (const Feature& f : kFeatures)
      if (profile13_only(f)) takes13 = takes13 && f.field(have);
    judged.push_back({p.deviceType == VK_PHYSICAL_DEVICE_TYPE_CPU, takes13, eff >= VK_API_VERSION_1_1});
    *fits = *fits || names.empty();
    // The version beside the name wherever it can be the reason: at vk11, and at vk13 below 1.3. A device
    // read at 1.3 for the 1.3 profile keeps the text it had before the 1.1 profile existed, so a 1.3
    // device's retry that succeeds reports, and says on stderr, what it did ("Mali-G715: shaderClipDistance").
    const bool show_version = !vk13 || eff < VK_API_VERSION_1_3;
    out += std::string(out.empty() ? "" : "; ") + p.deviceName +
           (show_version ? " (Vulkan " + version_text(p.apiVersion) + ")" : std::string()) + ": " +
           (names.empty() ? "none" : names);
  }
  if (devs.empty()) { *how = "vkEnumeratePhysicalDevices found none"; return ""; }
  // api13 and api11 judge the devices SDL_gpu would choose among: the GPUs, or every device where none
  // is a GPU (SDL_gpu ranks a CPU device last).
  if (lacks) {
    const bool any_gpu = std::any_of(judged.begin(), judged.end(), [](const Judged& j) { return !j.cpu; });
    bool some13 = false, some11 = false;
    for (const Judged& j : judged)
      if (!any_gpu || !j.cpu) {
        some13 = some13 || j.takes13;
        some11 = some11 || j.has11;
      }
    lacks->api13 = vk13 && !some13;
    lacks->api11 = !some11;
  }
  *how = "vkGetPhysicalDeviceFeatures (and at 1.2 and 1.3, vkGetPhysicalDeviceFeatures2 with that version's chains) "
         "of every physical device";
  return out;
}

DeviceFactsQuery query_device_facts(void* gipa_ptr, const std::string& device_name, ApiLevel cap) {
  DeviceFactsQuery q;
  auto gipa = reinterpret_cast<PFN_vkGetInstanceProcAddr>(gipa_ptr);
  if (!gipa) { q.how = "no vkGetInstanceProcAddr"; return q; }
  Throwaway t(gipa);
  if (!t.inst) { q.how = t.error; return q; }
  auto props = t.fn<PFN_vkGetPhysicalDeviceProperties2>("vkGetPhysicalDeviceProperties2");
  auto feats = t.fn<PFN_vkGetPhysicalDeviceFeatures2>("vkGetPhysicalDeviceFeatures2");
  auto fmtprops = t.fn<PFN_vkGetPhysicalDeviceFormatProperties2>("vkGetPhysicalDeviceFormatProperties2");
  auto exts = t.fn<PFN_vkEnumerateDeviceExtensionProperties>("vkEnumerateDeviceExtensionProperties");
  std::vector<VkPhysicalDevice> devs;
  if (props && feats && fmtprops && exts) devs = t.devices();
  VkPhysicalDevice d = VK_NULL_HANDLE;
  uint32_t api = 0;
  int matches = 0;
  for (VkPhysicalDevice pd : devs) {
    VkPhysicalDeviceProperties2 p{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
    props(pd, &p);
    if (!device_name.empty() && device_name != p.properties.deviceName) continue;
    if (!matches++) {
      d = pd;
      api = p.properties.apiVersion;
    }
  }
  if (matches != 1) {
    q.how = device_name.empty() ? std::to_string(devs.size()) + " devices: the query needs exactly one (VK_DRIVER_FILES)"
                                : std::to_string(matches) + " physical devices named " + device_name;
    return q;
  }
  // The version each structure is read at: at vk11 the instance's, the device's own and 1.1, the lowest.
  // At vk13 the instance's and 1.3, whatever the device reports: SDL_gpu made the device at 1.3, which it
  // does only where the driver fills the Vulkan 1.1, 1.2 and 1.3 feature structures it asks for (it never
  // compares the device's own version), so a device reporting 1.2 that it accepted is read in full, as
  // before the 1.1 profile existed, and keeps the 1.3 profile it had (make_sdl_gpu names the mismatch).
  const uint32_t eff = cap == ApiLevel::vk11 ? std::min({major_minor(t.api), major_minor(api), VK_API_VERSION_1_1})
                                             : std::min(major_minor(t.api), VK_API_VERSION_1_3);
  if (eff < VK_API_VERSION_1_1) {
    q.how = major_minor(t.api) < VK_API_VERSION_1_1
                ? "the Vulkan loader has no vkEnumerateInstanceVersion (a 1.0 loader): the renderer needs Vulkan 1.1"
                : "the device reports Vulkan " + version_text(api) + ": the renderer needs Vulkan 1.1";
    return q;
  }
  const std::set<std::string> es = device_extensions(exts, d);
  const bool hasRaw = es.count("VK_NV_raw_access_chains"), hasFc2 = es.count("VK_KHR_shader_float_controls2"),
             hasHeap = es.count("VK_EXT_descriptor_heap");
  VkPhysicalDeviceDescriptorHeapFeaturesEXT fheap{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DESCRIPTOR_HEAP_FEATURES_EXT};
  VkPhysicalDeviceRawAccessChainsFeaturesNV fraw{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_RAW_ACCESS_CHAINS_FEATURES_NV};
  VkPhysicalDeviceShaderFloatControls2Features ffc2{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_SHADER_FLOAT_CONTROLS_2_FEATURES};
  VkPhysicalDeviceFeatures2 f{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2};
  VkPhysicalDeviceProperties2 p{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_PROPERTIES_2};
  // The extensions' own feature structures, on top of `chain`, where listed.
  auto with_extensions = [&](void* chain) {
    if (hasFc2) { ffc2.pNext = chain; chain = &ffc2; }
    if (hasRaw) { fraw.pNext = chain; chain = &fraw; }
    if (hasHeap) { fheap.pNext = chain; chain = &fheap; }
    return chain;
  };
  // R32_SFLOAT, R32_UINT and R32_SINT's optimal-tiling features in common (VkFormatProperties3).
  auto r32_features = [&]() {
    VkFormatFeatureFlags2 r32 = ~VkFormatFeatureFlags2(0);
    for (VkFormat fm : {VK_FORMAT_R32_SFLOAT, VK_FORMAT_R32_UINT, VK_FORMAT_R32_SINT}) {
      VkFormatProperties3 f3{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_3};
      VkFormatProperties2 f2{VK_STRUCTURE_TYPE_FORMAT_PROPERTIES_2};
      f2.pNext = &f3;
      fmtprops(d, fm, &f2);
      r32 &= f3.optimalTilingFeatures;
    }
    return r32;
  };
  DeviceFacts& o = q.facts;
  if (eff >= VK_API_VERSION_1_3) {
    VkPhysicalDeviceVulkan12Properties p12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_PROPERTIES};
    p.pNext = &p12;
    props(d, &p);
    VkPhysicalDeviceVulkan12Features f12{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_2_FEATURES};
    VkPhysicalDeviceVulkan11Features f11{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_VULKAN_1_1_FEATURES};
    f11.pNext = &f12;
    f.pNext = with_extensions(&f11);
    feats(d, &f);
    const VkFormatFeatureFlags2 r32 = r32_features();
    // dxvk_device.cpp:883-894: every one of the twelve indexing features
    const bool resIdx = f.features.shaderUniformBufferArrayDynamicIndexing && f.features.shaderSampledImageArrayDynamicIndexing &&
                        f.features.shaderStorageBufferArrayDynamicIndexing && f.features.shaderStorageImageArrayDynamicIndexing &&
                        f12.shaderUniformTexelBufferArrayDynamicIndexing && f12.shaderStorageTexelBufferArrayDynamicIndexing &&
                        f12.shaderUniformBufferArrayNonUniformIndexing && f12.shaderSampledImageArrayNonUniformIndexing &&
                        f12.shaderStorageBufferArrayNonUniformIndexing && f12.shaderStorageImageArrayNonUniformIndexing &&
                        f12.shaderUniformTexelBufferArrayNonUniformIndexing && f12.shaderStorageTexelBufferArrayNonUniformIndexing;
    o.driver_id = uint32_t(p12.driverID);
    o.shader_float16 = f12.shaderFloat16;
    o.storage_push_constant16 = f11.storagePushConstant16;
    o.storage_push_constant8 = f12.storagePushConstant8;
    o.r32_storage_read_without_format = (r32 & VK_FORMAT_FEATURE_2_STORAGE_READ_WITHOUT_FORMAT_BIT) != 0;
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
    o.resource_indexing = resIdx;
    o.sub_dword_access = f.features.shaderInt16 && f11.storageBuffer16BitAccess && f11.uniformAndStorageBuffer16BitAccess &&
                         f12.storageBuffer8BitAccess && f12.uniformAndStorageBuffer8BitAccess && f12.shaderInt8;
    q.driver_name = p12.driverName;
    q.driver_info = p12.driverInfo;
    q.how = "vkGetPhysicalDeviceProperties2 and vkGetPhysicalDeviceFeatures2 (Vulkan 1.1, 1.2 and the extensions' chains), "
            "vkGetPhysicalDeviceFormatProperties2 (R32 formats)";
  } else {
    // Below 1.3 (a device made at 1.1): no Vulkan 1.1, 1.2 or 1.3 structure. The driver's ID and names
    // from VK_KHR_driver_properties where listed (DXVK's driver rules read the ID); the 16-bit storage
    // features (core in 1.1); the R32 formats' features from VK_KHR_format_feature_flags2 where listed,
    // and otherwise shaderStorageImageReadWithoutFormat (Vulkan 1.0's whole-device answer). No float
    // control, 16-bit arithmetic, 8-bit access or descriptor indexing is read: SDL_gpu's 1.1 device
    // enables none of them.
    const bool hasDriver = es.count("VK_KHR_driver_properties"), hasFf2 = es.count("VK_KHR_format_feature_flags2");
    VkPhysicalDeviceDriverProperties drv{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DRIVER_PROPERTIES};
    if (hasDriver) p.pNext = &drv;
    props(d, &p);
    VkPhysicalDevice16BitStorageFeatures s16{VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_16BIT_STORAGE_FEATURES};
    f.pNext = with_extensions(&s16);
    feats(d, &f);
    const bool r32 = hasFf2 ? (r32_features() & VK_FORMAT_FEATURE_2_STORAGE_READ_WITHOUT_FORMAT_BIT) != 0
                            : bool(f.features.shaderStorageImageReadWithoutFormat);
    o.driver_id = hasDriver ? uint32_t(drv.driverID) : 0;
    o.storage_push_constant16 = s16.storagePushConstant16;
    o.r32_storage_read_without_format = r32;
    o.sub_dword_access = false;
    o.vk11 = true;
    if (hasDriver) {
      q.driver_name = drv.driverName;
      q.driver_info = drv.driverInfo;
    }
    q.how = "Vulkan " + version_text(api) + " read at 1.1: vkGetPhysicalDeviceProperties2 (" +
            (hasDriver ? "VK_KHR_driver_properties" : "no VK_KHR_driver_properties") +
            ") and vkGetPhysicalDeviceFeatures2 (16-bit storage and the extensions' chains), " +
            (hasFf2 ? "vkGetPhysicalDeviceFormatProperties2 (R32 formats)" : "shaderStorageImageReadWithoutFormat");
  }
  const auto& L = p.properties.limits;
  o.driver_version_raw = p.properties.driverVersion;
  o.min_storage_buffer_offset_alignment = uint32_t(L.minStorageBufferOffsetAlignment);
  o.max_uniform_buffer_range = L.maxUniformBufferRange;
  o.max_per_stage_descriptor_uniform_buffers = L.maxPerStageDescriptorUniformBuffers;
  o.nv_raw_access_chains = hasRaw && fraw.shaderRawAccessChains;
  o.float_controls2 = hasFc2 && ffc2.shaderFloatControls2;
  o.descriptor_heap = hasHeap && fheap.descriptorHeap;
  o.clip_distance = f.features.shaderClipDistance;
  q.device_name = p.properties.deviceName;
  q.ok = true;
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
  // only when read below 1.3 (DeviceFacts::vk11): a 1.3 device keeps the facts file it had before the field
  if (f.vk11) s << ",\n \"vk11\": true";
  s << "\n}\n";
  return s.str();
}

DeviceRequest first_device_request(bool vk11_forced, bool sub_dword_off, bool clip_forced_off, bool clamp_forced_off) {
  DeviceRequest r;
  r.level = vk11_forced ? ApiLevel::vk11 : ApiLevel::vk13;
  r.sub_dword = !vk11_forced && !sub_dword_off;
  r.clip_distance = !clip_forced_off;
  r.scalar_layout = !vk11_forced;
  r.sdl.depth_clamp = !clamp_forced_off;
  return r;
}

namespace {
// `r` without what `lacks` names of it.
DeviceRequest without(const DeviceRequest& r, const VulkanFeatures::Lacks& lacks) {
  DeviceRequest o = r;
  o.sub_dword = r.sub_dword && !lacks.sub_dword;
  o.clip_distance = r.clip_distance && !lacks.clip_distance;
  o.scalar_layout = r.scalar_layout && !lacks.scalar_layout;
  o.sdl = {r.sdl.depth_clamp && !lacks.depth_clamp, r.sdl.indirect_first_instance && !lacks.indirect_first_instance,
           r.sdl.anisotropy && !lacks.anisotropy};
  return o;
}
// What without(r, lacks) drops, by name ("" nothing).
std::string dropped_names(const DeviceRequest& r, const VulkanFeatures::Lacks& lacks) {
  std::string out;
  auto add = [&](bool dropped, const char* name) {
    if (dropped) out += std::string(out.empty() ? "" : ", ") + name;
  };
  add(r.sub_dword && lacks.sub_dword, "the sub-dword features");
  add(r.clip_distance && lacks.clip_distance, "shaderClipDistance");
  add(r.scalar_layout && lacks.scalar_layout, "scalarBlockLayout");
  add(r.sdl.depth_clamp && lacks.depth_clamp, "SDL's depthClamp");
  add(r.sdl.indirect_first_instance && lacks.indirect_first_instance, "SDL's drawIndirectFirstInstance");
  add(r.sdl.anisotropy && lacks.anisotropy, "SDL's samplerAnisotropy");
  return out;
}
}  // namespace

DeviceStep next_device_request(const DeviceRequest& last, DeviceAnswer answer, int retry, const VulkanFeatures::Lacks& lacks,
                               bool fits) {
  DeviceStep s;
  auto step_down = [&] {
    // the 1.1 request: the clip distance and SDL demands some device lacks dropped; no sub-dword features
    // and no scalar layout at vk11
    s.kind = DeviceStep::attempt;
    s.step_down = true;
    s.request = without(last, lacks);
    s.request.level = ApiLevel::vk11;
    s.request.sub_dword = false;
    s.request.scalar_layout = false;
    return s;
  };
  auto refuse = [&] {
    s.kind = DeviceStep::refuse_api11;
    return s;
  };
  // the step after a refusal, at last's level, as before a CPU step down existed
  auto after_refusal = [&] {
    if (last.level == ApiLevel::vk11 && lacks.api11) return refuse();
    if (last.level == ApiLevel::vk13 && retry == 0 && lacks.api13) return lacks.api11 ? refuse() : step_down();
    if (!fits)
      for (int r = retry; r < 2; r++) {
        const VulkanFeatures::Lacks use = r == 0 ? lacks : VulkanFeatures::Lacks::all();
        const std::string names = dropped_names(last, use);
        if (names.empty()) continue;
        s.kind = DeviceStep::attempt;
        s.request = without(last, use);
        s.retry = r;
        s.dropped = names;
        return s;
      }
    s.kind = DeviceStep::give_up;
    return s;
  };
  switch (answer) {
    case DeviceAnswer::accepted:
      return s;
    case DeviceAnswer::accepted_cpu:
      // a GPU-only 1.1 request that still landed on a CPU device: back to the CPU device's 1.3
      if (last.hardware_only) {
        s.kind = DeviceStep::restore;
        return s;
      }
      // at 1.3 a GPU that takes only the 1.1 profile is asked for at 1.1, GPUs only, with this request
      // kept to go back to (and kept there: a CPU device accepted on it again is done)
      if (last.level == ApiLevel::vk13 && !last.cpu_settled && lacks.api13 && !lacks.api11) {
        step_down();
        s.request.hardware_only = true;
        s.fallback = last;
        s.fallback.cpu_settled = true;
      }
      return s;
    case DeviceAnswer::unasked:
      if (last.level == ApiLevel::vk11 && lacks.api11) return refuse();
      s.kind = DeviceStep::attempt;
      s.request = last;
      return s;
    case DeviceAnswer::refused:
      break;
  }
  // the CPU device's 1.3 request, gone back to, refused this time: no other step
  if (last.cpu_settled) {
    s.kind = DeviceStep::give_up;
    return s;
  }
  after_refusal();
  // a GPU-only 1.1 request (and its retries) that no GPU takes: back to the CPU device's 1.3, not a failure
  if (last.hardware_only && (s.kind == DeviceStep::give_up || s.kind == DeviceStep::refuse_api11)) s.kind = DeviceStep::restore;
  return s;
}

std::string device_below13_note(uint32_t device_api) {
  if (!device_api || major_minor(device_api) >= VK_API_VERSION_1_3) return "";
  return "SDL_gpu made the device at Vulkan 1.3 although it reports Vulkan " + version_text(device_api) +
         " (its driver fills the 1.3 feature structures SDL_gpu checks): the 1.3 profile runs, its facts read in full";
}

}  // namespace d9g::shader
