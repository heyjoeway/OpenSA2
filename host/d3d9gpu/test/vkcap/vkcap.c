// vkcap: an explicit Vulkan layer of the project's own that records what an application hands the
// driver as shaders (R1 U8, the recorder of check C3's linked half). It records, for DXVK and for
// SDL_gpu alike:
//   - every vkCreateShaderModule's code, as VKCAP_OUT/module-<sha256>.spv;
//   - every graphics pipeline's stages (the module's sha256, or the code chained in the stage's
//     pNext as VkShaderModuleCreateInfo, which DXVK uses), entry point, and VkSpecializationInfo
//     (map entries and data), with the pipeline's create flags, its VkPipelineCreateFlags2, its
//     graphics-pipeline-library flags and its linked library count, its device, its
//     rasterization state's depthClampEnable and chained depth-clip state, and its vertex input
//     (each attribute's location, binding, format and offset, each binding's stride and rate:
//     where the pipeline itself carries the vertex input interface, null where it does not, and
//     "dynamic" where it is dynamic state), as one JSON line each in VKCAP_OUT/pipelines.jsonl;
//   - every vkCreateDevice: the enabled extensions, the sType of every structure in the create
//     info's pNext chain (the loader's own excepted), and the depthClamp and depthClipEnable
//     features it enables, one JSON line each in VKCAP_OUT/devices.jsonl;
//   - every vkCreateSampler: its device, address modes and border colour, in
//     VKCAP_OUT/samplers.jsonl.
// Every record carries "load", the process and the moment the loader loaded this layer: the loader
// unloads a layer with its last instance and loads it afresh for the next, so one process can
// hold several loads, each counting its devices from 0. A record that cannot be written is
// counted, never dropped silently: when a load ends the layer prints
// "vkcap: load=L records=N write_failures=M" on stderr, and a reader of the records requires
// M = 0 and the N to add up to what it reads.
// It changes nothing it passes on. A control build (-DVKCAP_CONTROL_ZERO_WRITE_MASK) zeroes every
// colour attachment's write mask in what it passes on, so a comparison that cannot see the layer is
// shown blind (C9); its records are of the create infos the application passed in, not of the masked
// copy the driver receives.
//
// Loaded only by VK_ADD_LAYER_PATH (the directory of vkcap.json, generated from vkcap.json.in) and
// VK_INSTANCE_LAYERS=VK_LAYER_SA2_vkcap, by the R1 scripts; host/player/player_env.sh disables
// implicit layers only, so the player's captures never load it unasked. Built against the pinned
// DXVK checkout's Vulkan headers (include/vulkan/include). VKCAP_OUT must be set, or the layer
// records nothing and says so once on stderr.
#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>

#include <errno.h>
#include <fcntl.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define VKCAP_EXPORT __attribute__((visibility("default")))

// --- SHA-256 (FIPS 180-4), for file names and the pipeline records ---------------------------
typedef struct { uint32_t h[8]; uint8_t buf[64]; size_t fill; uint64_t total; } Sha;
static const uint32_t K256[64] = {
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2};
#define ROR(x, n) (((x) >> (n)) | ((x) << (32 - (n))))
static void sha_block(Sha* s, const uint8_t* p) {
  uint32_t w[64], a, b, c, d, e, f, g, h;
  for (int i = 0; i < 16; i++) w[i] = (uint32_t)p[4 * i] << 24 | (uint32_t)p[4 * i + 1] << 16 | (uint32_t)p[4 * i + 2] << 8 | p[4 * i + 3];
  for (int i = 16; i < 64; i++) {
    uint32_t s0 = ROR(w[i - 15], 7) ^ ROR(w[i - 15], 18) ^ (w[i - 15] >> 3);
    uint32_t s1 = ROR(w[i - 2], 17) ^ ROR(w[i - 2], 19) ^ (w[i - 2] >> 10);
    w[i] = w[i - 16] + s0 + w[i - 7] + s1;
  }
  a = s->h[0]; b = s->h[1]; c = s->h[2]; d = s->h[3]; e = s->h[4]; f = s->h[5]; g = s->h[6]; h = s->h[7];
  for (int i = 0; i < 64; i++) {
    uint32_t t1 = h + (ROR(e, 6) ^ ROR(e, 11) ^ ROR(e, 25)) + ((e & f) ^ (~e & g)) + K256[i] + w[i];
    uint32_t t2 = (ROR(a, 2) ^ ROR(a, 13) ^ ROR(a, 22)) + ((a & b) ^ (a & c) ^ (b & c));
    h = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
  }
  s->h[0] += a; s->h[1] += b; s->h[2] += c; s->h[3] += d; s->h[4] += e; s->h[5] += f; s->h[6] += g; s->h[7] += h;
}
static void sha_hex(const void* data, size_t n, char out[65]) {
  Sha s = {{0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19}, {0}, 0, 0};
  const uint8_t* p = (const uint8_t*)data;
  s.total = n;
  while (n >= 64) { sha_block(&s, p); p += 64; n -= 64; }
  uint8_t last[128] = {0};
  memcpy(last, p, n);
  last[n] = 0x80;
  size_t blocks = n + 9 > 64 ? 2 : 1;
  uint64_t bits = s.total * 8;
  for (int i = 0; i < 8; i++) last[blocks * 64 - 1 - i] = (uint8_t)(bits >> (8 * i));
  for (size_t i = 0; i < blocks; i++) sha_block(&s, last + 64 * i);
  for (int i = 0; i < 8; i++) sprintf(out + 8 * i, "%08x", s.h[i]);
}

// --- dispatch -------------------------------------------------------------------------------
typedef struct {
  void* key;
  PFN_vkGetInstanceProcAddr gipa;
  PFN_vkDestroyInstance destroy_instance;
  PFN_vkCreateDevice create_device;
} InstanceDispatch;
typedef struct {
  void* key;
  unsigned long id;  // the devices.jsonl seq of this device
  PFN_vkGetDeviceProcAddr gdpa;
  PFN_vkDestroyDevice destroy_device;
  PFN_vkCreateSampler create_sampler;
  PFN_vkCreateShaderModule create_shader_module;
  PFN_vkDestroyShaderModule destroy_shader_module;
  PFN_vkCreateGraphicsPipelines create_graphics_pipelines;
} DeviceDispatch;

#define MAX_HANDLES 64
static InstanceDispatch g_instances[MAX_HANDLES];
static DeviceDispatch g_devices[MAX_HANDLES];
typedef struct { VkShaderModule module; char sha[65]; } ModuleRecord;
static ModuleRecord* g_modules;
static size_t g_module_count, g_module_cap;
static pthread_mutex_t g_lock = PTHREAD_MUTEX_INITIALIZER;
static unsigned long g_pipeline_seq, g_device_seq;
static unsigned long g_records, g_write_failures;  // under g_lock
static char g_load[64];

__attribute__((constructor)) static void vkcap_load(void) {
  struct timespec ts;
  clock_gettime(CLOCK_REALTIME, &ts);
  snprintf(g_load, sizeof g_load, "%d.%lld.%09ld", (int)getpid(), (long long)ts.tv_sec, ts.tv_nsec);
}

// Called with g_lock held. A record that cannot be written whole is a failure, counted.
static FILE* open_record(const char* file) {
  const char* d = getenv("VKCAP_OUT");
  if (!d || !*d) return NULL;
  char path[4096];
  mkdir(d, 0755);
  snprintf(path, sizeof path, "%s/%s", d, file);
  FILE* f = fopen(path, "a");
  if (!f) g_write_failures++;
  return f;
}
static void close_record(FILE* f) {
  if (!f) return;
  int bad = ferror(f);
  if (fclose(f) != 0 || bad) g_write_failures++;
  else g_records++;
}

__attribute__((destructor)) static void vkcap_report(void) {
  const char* d = getenv("VKCAP_OUT");
  if (d && *d) fprintf(stderr, "vkcap: load=%s records=%lu write_failures=%lu\n", g_load, g_records, g_write_failures);
}

static void* key_of(const void* handle) { return *(void* const*)handle; }

static InstanceDispatch* instance_of(const void* handle) {
  for (int i = 0; i < MAX_HANDLES; i++) if (g_instances[i].key == key_of(handle)) return &g_instances[i];
  return NULL;
}
static DeviceDispatch* device_of(const void* handle) {
  for (int i = 0; i < MAX_HANDLES; i++) if (g_devices[i].key == key_of(handle)) return &g_devices[i];
  return NULL;
}

static const char* out_dir(void) {
  static int warned;
  const char* d = getenv("VKCAP_OUT");
  if ((!d || !*d) && !warned) {
    warned = 1;
    fprintf(stderr, "vkcap: VKCAP_OUT is not set; recording nothing\n");
  }
  return d && *d ? d : NULL;
}

// A module is written once per capture directory, by the one caller whose exclusive create succeeds:
// DXVK's worker threads can hand the driver the same inline module at the same moment, and a
// check-then-write would let both write it and both count it, one file for two records.
static void write_module(const uint32_t* code, size_t bytes, char sha[65]) {
  sha_hex(code, bytes, sha);
  const char* d = out_dir();
  if (!d) return;
  mkdir(d, 0755);
  char path[4096];
  snprintf(path, sizeof path, "%s/module-%s.spv", d, sha);
  int fd = open(path, O_WRONLY | O_CREAT | O_EXCL, 0644);
  if (fd < 0 && errno == EEXIST) return;   // already written, by this load or an earlier one
  FILE* f = fd < 0 ? NULL : fdopen(fd, "wb");
  if (!f && fd >= 0) close(fd);
  int ok = f && fwrite(code, 1, bytes, f) == bytes;
  if (f && fclose(f) != 0) ok = 0;
  pthread_mutex_lock(&g_lock);
  if (ok) g_records++; else g_write_failures++;
  pthread_mutex_unlock(&g_lock);
}

static const void* find_in_chain(const void* pnext, VkStructureType type) {
  for (const VkBaseInStructure* s = (const VkBaseInStructure*)pnext; s; s = s->pNext)
    if (s->sType == type) return s;
  return NULL;
}

// --- intercepts ---------------------------------------------------------------------------------
static VKAPI_ATTR VkResult VKAPI_CALL vkcap_CreateShaderModule(VkDevice device, const VkShaderModuleCreateInfo* ci,
                                                              const VkAllocationCallbacks* alloc, VkShaderModule* module) {
  DeviceDispatch* dd = device_of(device);
  VkResult r = dd->create_shader_module(device, ci, alloc, module);
  if (r == VK_SUCCESS) {
    char sha[65];
    write_module(ci->pCode, ci->codeSize, sha);
    pthread_mutex_lock(&g_lock);
    if (g_module_count == g_module_cap) {
      g_module_cap = g_module_cap ? 2 * g_module_cap : 256;
      g_modules = (ModuleRecord*)realloc(g_modules, g_module_cap * sizeof(ModuleRecord));
    }
    g_modules[g_module_count].module = *module;
    memcpy(g_modules[g_module_count].sha, sha, 65);
    g_module_count++;
    pthread_mutex_unlock(&g_lock);
  }
  return r;
}

static VKAPI_ATTR void VKAPI_CALL vkcap_DestroyShaderModule(VkDevice device, VkShaderModule module, const VkAllocationCallbacks* alloc) {
  DeviceDispatch* dd = device_of(device);
  pthread_mutex_lock(&g_lock);
  for (size_t i = 0; i < g_module_count; i++)
    if (g_modules[i].module == module) { g_modules[i] = g_modules[--g_module_count]; break; }
  pthread_mutex_unlock(&g_lock);
  dd->destroy_shader_module(device, module, alloc);
}

static const char* stage_name(VkShaderStageFlagBits s) {
  switch (s) {
    case VK_SHADER_STAGE_VERTEX_BIT: return "vertex";
    case VK_SHADER_STAGE_FRAGMENT_BIT: return "fragment";
    case VK_SHADER_STAGE_GEOMETRY_BIT: return "geometry";
    default: return "other";
  }
}

#define MAX_STAGES 8
static void record_pipeline(unsigned long device_id, const VkGraphicsPipelineCreateInfo* ci, VkResult result) {
  const char* d = out_dir();
  if (!d) return;
  // every stage's module first (an inline module is written here, without the lock held), so the
  // record below is written whole under the lock
  char shas[MAX_STAGES][65];
  int inl[MAX_STAGES];
  const uint32_t stages = ci->stageCount;
  if (stages > MAX_STAGES) {   // more stages than a graphics pipeline has: never recorded in part
    pthread_mutex_lock(&g_lock);
    g_write_failures++;
    pthread_mutex_unlock(&g_lock);
    return;
  }
  for (uint32_t i = 0; i < stages; i++) {
    const VkPipelineShaderStageCreateInfo* st = &ci->pStages[i];
    const VkShaderModuleCreateInfo* code = (const VkShaderModuleCreateInfo*)find_in_chain(st->pNext, VK_STRUCTURE_TYPE_SHADER_MODULE_CREATE_INFO);
    shas[i][0] = 0;
    inl[i] = code != NULL;
    if (code) write_module(code->pCode, code->codeSize, shas[i]);
  }
  pthread_mutex_lock(&g_lock);
  for (uint32_t i = 0; i < stages; i++)
    if (!inl[i])
      for (size_t k = 0; k < g_module_count; k++)
        if (g_modules[k].module == ci->pStages[i].module) { memcpy(shas[i], g_modules[k].sha, 65); break; }
  FILE* f = open_record("pipelines.jsonl");
  if (!f) { pthread_mutex_unlock(&g_lock); return; }
  const VkPipelineCreateFlags2CreateInfoKHR* f2 =
      (const VkPipelineCreateFlags2CreateInfoKHR*)find_in_chain(ci->pNext, VK_STRUCTURE_TYPE_PIPELINE_CREATE_FLAGS_2_CREATE_INFO_KHR);
  const VkGraphicsPipelineLibraryCreateInfoEXT* gpl =
      (const VkGraphicsPipelineLibraryCreateInfoEXT*)find_in_chain(ci->pNext, VK_STRUCTURE_TYPE_GRAPHICS_PIPELINE_LIBRARY_CREATE_INFO_EXT);
  const VkPipelineLibraryCreateInfoKHR* lib =
      (const VkPipelineLibraryCreateInfoKHR*)find_in_chain(ci->pNext, VK_STRUCTURE_TYPE_PIPELINE_LIBRARY_CREATE_INFO_KHR);
  fprintf(f, "{\"load\": \"%s\", \"seq\": %lu, \"device\": %lu, \"result\": %d, \"flags\": %u, \"flags2\": %llu, \"gpl_flags\": %u, \"libraries\": %u, ",
          g_load, g_pipeline_seq++, device_id, (int)result, (unsigned)ci->flags, f2 ? (unsigned long long)f2->flags : 0ull,
          gpl ? (unsigned)gpl->flags : 0u, lib ? lib->libraryCount : 0u);
  const VkPipelineRasterizationStateCreateInfo* rs = ci->pRasterizationState;
  if (rs) {
    const VkPipelineRasterizationDepthClipStateCreateInfoEXT* clip =
        (const VkPipelineRasterizationDepthClipStateCreateInfoEXT*)find_in_chain(
            rs->pNext, VK_STRUCTURE_TYPE_PIPELINE_RASTERIZATION_DEPTH_CLIP_STATE_CREATE_INFO_EXT);
    fprintf(f, "\"raster\": {\"depth_clamp\": %u, \"depth_clip\": ", (unsigned)rs->depthClampEnable);
    if (clip) fprintf(f, "%u}, ", (unsigned)clip->depthClipEnable);
    else fprintf(f, "null}, ");
  } else {
    fprintf(f, "\"raster\": null, ");
  }
  // The vertex input, read only where the pipeline carries the vertex input interface itself: not
  // linked from libraries, and either no graphics-pipeline-library pipeline or one with the
  // vertex-input-interface part (elsewhere the spec has the driver ignore pVertexInputState).
  int dynamic_vi = 0;
  if (ci->pDynamicState)
    for (uint32_t i = 0; i < ci->pDynamicState->dynamicStateCount; i++)
      if (ci->pDynamicState->pDynamicStates[i] == VK_DYNAMIC_STATE_VERTEX_INPUT_EXT) dynamic_vi = 1;
  const int own_vi = (!lib || lib->libraryCount == 0) &&
                     (!gpl || (gpl->flags & VK_GRAPHICS_PIPELINE_LIBRARY_VERTEX_INPUT_INTERFACE_BIT_EXT));
  const VkPipelineVertexInputStateCreateInfo* vi = own_vi ? ci->pVertexInputState : NULL;
  if (own_vi && dynamic_vi) {
    fprintf(f, "\"vertex_input\": \"dynamic\", ");
  } else if (vi) {
    fprintf(f, "\"vertex_input\": {\"attributes\": [");
    for (uint32_t i = 0; i < vi->vertexAttributeDescriptionCount; i++) {
      const VkVertexInputAttributeDescription* a = &vi->pVertexAttributeDescriptions[i];
      fprintf(f, "%s[%u, %u, %d, %u]", i ? ", " : "", a->location, a->binding, (int)a->format, a->offset);
    }
    fprintf(f, "], \"bindings\": [");
    for (uint32_t i = 0; i < vi->vertexBindingDescriptionCount; i++) {
      const VkVertexInputBindingDescription* b = &vi->pVertexBindingDescriptions[i];
      fprintf(f, "%s[%u, %u, %d]", i ? ", " : "", b->binding, b->stride, (int)b->inputRate);
    }
    fprintf(f, "]}, ");
  } else {
    fprintf(f, "\"vertex_input\": null, ");
  }
  fprintf(f, "\"stages\": [");
  for (uint32_t i = 0; i < stages; i++) {
    const VkPipelineShaderStageCreateInfo* st = &ci->pStages[i];
    fprintf(f, "%s{\"stage\": \"%s\", \"module\": \"%s\", \"inline\": %s, \"entry\": \"%s\", \"spec\": ", i ? ", " : "",
            stage_name(st->stage), shas[i], inl[i] ? "true" : "false", st->pName ? st->pName : "");
    if (st->pSpecializationInfo) {
      const VkSpecializationInfo* sp = st->pSpecializationInfo;
      fprintf(f, "{\"entries\": [");
      for (uint32_t m = 0; m < sp->mapEntryCount; m++)
        fprintf(f, "%s[%u, %u, %zu]", m ? ", " : "", sp->pMapEntries[m].constantID, sp->pMapEntries[m].offset, sp->pMapEntries[m].size);
      fprintf(f, "], \"data\": \"");
      for (size_t b = 0; b < sp->dataSize; b++) fprintf(f, "%02x", ((const uint8_t*)sp->pData)[b]);
      fprintf(f, "\"}}");
    } else {
      fprintf(f, "null}");
    }
  }
  fprintf(f, "]}\n");
  close_record(f);
  pthread_mutex_unlock(&g_lock);
}

static VKAPI_ATTR VkResult VKAPI_CALL vkcap_CreateGraphicsPipelines(VkDevice device, VkPipelineCache cache, uint32_t count,
                                                                  const VkGraphicsPipelineCreateInfo* infos,
                                                                  const VkAllocationCallbacks* alloc, VkPipeline* pipelines) {
  DeviceDispatch* dd = device_of(device);
#ifdef VKCAP_CONTROL_ZERO_WRITE_MASK
  // The control build: every colour attachment's writes are masked off.
  VkGraphicsPipelineCreateInfo* copy = (VkGraphicsPipelineCreateInfo*)malloc(count * sizeof(*copy));
  memcpy(copy, infos, count * sizeof(*copy));
  VkPipelineColorBlendStateCreateInfo* blends = (VkPipelineColorBlendStateCreateInfo*)calloc(count, sizeof(*blends));
  VkPipelineColorBlendAttachmentState* att[64] = {0};
  for (uint32_t i = 0; i < count && i < 64; i++) {
    if (!infos[i].pColorBlendState) continue;
    blends[i] = *infos[i].pColorBlendState;
    att[i] = (VkPipelineColorBlendAttachmentState*)calloc(blends[i].attachmentCount + 1, sizeof(**att));
    if (blends[i].pAttachments)
      memcpy(att[i], blends[i].pAttachments, blends[i].attachmentCount * sizeof(**att));
    for (uint32_t a = 0; a < blends[i].attachmentCount; a++) att[i][a].colorWriteMask = 0;
    blends[i].pAttachments = att[i];
    copy[i].pColorBlendState = &blends[i];
  }
  VkResult r = dd->create_graphics_pipelines(device, cache, count, copy, alloc, pipelines);
  for (uint32_t i = 0; i < count && i < 64; i++) free(att[i]);
  free(blends);
  free(copy);
#else
  VkResult r = dd->create_graphics_pipelines(device, cache, count, infos, alloc, pipelines);
#endif
  for (uint32_t i = 0; i < count; i++) record_pipeline(dd->id, &infos[i], r);
  return r;
}

static VKAPI_ATTR VkResult VKAPI_CALL vkcap_CreateSampler(VkDevice device, const VkSamplerCreateInfo* ci,
                                                         const VkAllocationCallbacks* alloc, VkSampler* sampler) {
  DeviceDispatch* dd = device_of(device);
  VkResult r = dd->create_sampler(device, ci, alloc, sampler);
  if (out_dir()) {
    pthread_mutex_lock(&g_lock);
    FILE* f = open_record("samplers.jsonl");
    if (f) {
      fprintf(f, "{\"load\": \"%s\", \"device\": %lu, \"result\": %d, \"address\": [%d, %d, %d], \"border_color\": %d, \"pnext\": %s}\n",
              g_load, dd->id, (int)r, (int)ci->addressModeU, (int)ci->addressModeV, (int)ci->addressModeW, (int)ci->borderColor,
              ci->pNext ? "true" : "false");
      close_record(f);
    }
    pthread_mutex_unlock(&g_lock);
  }
  return r;
}

// devices.jsonl: what the application asked of vkCreateDevice, written after the call with its
// result, so a device whose creation fails is recorded too. Called with g_lock held.
static void record_device(unsigned long id, const VkDeviceCreateInfo* ci, VkResult r) {
  if (!out_dir()) return;
  FILE* f = open_record("devices.jsonl");
  if (!f) return;
  fprintf(f, "{\"load\": \"%s\", \"seq\": %lu, \"result\": %d, \"extensions\": [", g_load, id, (int)r);
  for (uint32_t i = 0; i < ci->enabledExtensionCount; i++)
    fprintf(f, "%s\"%s\"", i ? ", " : "", ci->ppEnabledExtensionNames[i]);
  fprintf(f, "], \"pnext\": [");
  int n = 0;
  int clip = -1, clamp = ci->pEnabledFeatures ? (int)ci->pEnabledFeatures->depthClamp : -1;
  for (const VkBaseInStructure* s = (const VkBaseInStructure*)ci->pNext; s; s = s->pNext) {
    if (s->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO) continue;
    fprintf(f, "%s%d", n++ ? ", " : "", (int)s->sType);
    if (s->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_DEPTH_CLIP_ENABLE_FEATURES_EXT)
      clip = (int)((const VkPhysicalDeviceDepthClipEnableFeaturesEXT*)s)->depthClipEnable;
    if (s->sType == VK_STRUCTURE_TYPE_PHYSICAL_DEVICE_FEATURES_2)
      clamp = (int)((const VkPhysicalDeviceFeatures2*)s)->features.depthClamp;
  }
  fprintf(f, "], \"features_in_create_info\": %s, \"depth_clamp\": ", ci->pEnabledFeatures ? "true" : "false");
  if (clamp < 0) fprintf(f, "null"); else fprintf(f, "%d", clamp);
  fprintf(f, ", \"depth_clip_enable\": ");
  if (clip < 0) fprintf(f, "null"); else fprintf(f, "%d", clip);
  fprintf(f, "}\n");
  close_record(f);
}

static VKAPI_ATTR VkResult VKAPI_CALL vkcap_CreateDevice(VkPhysicalDevice phys, const VkDeviceCreateInfo* ci,
                                                        const VkAllocationCallbacks* alloc, VkDevice* device) {
  VkLayerDeviceCreateInfo* link = (VkLayerDeviceCreateInfo*)ci->pNext;
  while (link && !(link->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO && link->function == VK_LAYER_LINK_INFO))
    link = (VkLayerDeviceCreateInfo*)link->pNext;
  if (!link) return VK_ERROR_INITIALIZATION_FAILED;
  PFN_vkGetInstanceProcAddr gipa = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
  PFN_vkGetDeviceProcAddr gdpa = link->u.pLayerInfo->pfnNextGetDeviceProcAddr;
  link->u.pLayerInfo = link->u.pLayerInfo->pNext;
  InstanceDispatch* id = instance_of(phys);
  PFN_vkCreateDevice create = id ? id->create_device : (PFN_vkCreateDevice)gipa(NULL, "vkCreateDevice");
  VkResult r = create(phys, ci, alloc, device);
  pthread_mutex_lock(&g_lock);
  unsigned long dev_id = g_device_seq++;
  record_device(dev_id, ci, r);
  pthread_mutex_unlock(&g_lock);
  if (r != VK_SUCCESS) return r;
  pthread_mutex_lock(&g_lock);
  for (int i = 0; i < MAX_HANDLES; i++) {
    if (g_devices[i].key) continue;
    g_devices[i].key = key_of(*device);
    g_devices[i].id = dev_id;
    g_devices[i].gdpa = gdpa;
    g_devices[i].create_sampler = (PFN_vkCreateSampler)gdpa(*device, "vkCreateSampler");
    g_devices[i].destroy_device = (PFN_vkDestroyDevice)gdpa(*device, "vkDestroyDevice");
    g_devices[i].create_shader_module = (PFN_vkCreateShaderModule)gdpa(*device, "vkCreateShaderModule");
    g_devices[i].destroy_shader_module = (PFN_vkDestroyShaderModule)gdpa(*device, "vkDestroyShaderModule");
    g_devices[i].create_graphics_pipelines = (PFN_vkCreateGraphicsPipelines)gdpa(*device, "vkCreateGraphicsPipelines");
    break;
  }
  pthread_mutex_unlock(&g_lock);
  return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL vkcap_DestroyDevice(VkDevice device, const VkAllocationCallbacks* alloc) {
  DeviceDispatch* dd = device_of(device);
  PFN_vkDestroyDevice destroy = dd->destroy_device;
  pthread_mutex_lock(&g_lock);
  memset(dd, 0, sizeof(*dd));
  pthread_mutex_unlock(&g_lock);
  destroy(device, alloc);
}

static VKAPI_ATTR VkResult VKAPI_CALL vkcap_CreateInstance(const VkInstanceCreateInfo* ci, const VkAllocationCallbacks* alloc,
                                                          VkInstance* instance) {
  VkLayerInstanceCreateInfo* link = (VkLayerInstanceCreateInfo*)ci->pNext;
  while (link && !(link->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO && link->function == VK_LAYER_LINK_INFO))
    link = (VkLayerInstanceCreateInfo*)link->pNext;
  if (!link) return VK_ERROR_INITIALIZATION_FAILED;
  PFN_vkGetInstanceProcAddr gipa = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
  link->u.pLayerInfo = link->u.pLayerInfo->pNext;
  PFN_vkCreateInstance create = (PFN_vkCreateInstance)gipa(NULL, "vkCreateInstance");
  VkResult r = create(ci, alloc, instance);
  if (r != VK_SUCCESS) return r;
  pthread_mutex_lock(&g_lock);
  for (int i = 0; i < MAX_HANDLES; i++) {
    if (g_instances[i].key) continue;
    g_instances[i].key = key_of(*instance);
    g_instances[i].gipa = gipa;
    g_instances[i].destroy_instance = (PFN_vkDestroyInstance)gipa(*instance, "vkDestroyInstance");
    g_instances[i].create_device = (PFN_vkCreateDevice)gipa(*instance, "vkCreateDevice");
    break;
  }
  pthread_mutex_unlock(&g_lock);
  return VK_SUCCESS;
}

static VKAPI_ATTR void VKAPI_CALL vkcap_DestroyInstance(VkInstance instance, const VkAllocationCallbacks* alloc) {
  InstanceDispatch* id = instance_of(instance);
  PFN_vkDestroyInstance destroy = id->destroy_instance;
  pthread_mutex_lock(&g_lock);
  memset(id, 0, sizeof(*id));
  pthread_mutex_unlock(&g_lock);
  destroy(instance, alloc);
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkcap_GetDeviceProcAddr(VkDevice device, const char* name);

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkcap_GetInstanceProcAddr(VkInstance instance, const char* name) {
#define HOOK(fn) if (!strcmp(name, "vk" #fn)) return (PFN_vkVoidFunction)vkcap_##fn
  HOOK(CreateInstance);
  HOOK(DestroyInstance);
  HOOK(CreateDevice);
  HOOK(DestroyDevice);
  HOOK(CreateShaderModule);
  HOOK(DestroyShaderModule);
  HOOK(CreateGraphicsPipelines);
  HOOK(CreateSampler);
  if (!strcmp(name, "vkGetInstanceProcAddr")) return (PFN_vkVoidFunction)vkcap_GetInstanceProcAddr;
  if (!strcmp(name, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)vkcap_GetDeviceProcAddr;
  if (!instance) return NULL;
  InstanceDispatch* id = instance_of(instance);
  return id ? id->gipa(instance, name) : NULL;
}

VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL vkcap_GetDeviceProcAddr(VkDevice device, const char* name) {
  HOOK(DestroyDevice);
  HOOK(CreateShaderModule);
  HOOK(DestroyShaderModule);
  HOOK(CreateGraphicsPipelines);
  HOOK(CreateSampler);
  if (!strcmp(name, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)vkcap_GetDeviceProcAddr;
#undef HOOK
  DeviceDispatch* dd = device_of(device);
  return dd ? dd->gdpa(device, name) : NULL;
}

VKCAP_EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkNegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface* v) {
  if (v->loaderLayerInterfaceVersion > 2) v->loaderLayerInterfaceVersion = 2;
  v->pfnGetInstanceProcAddr = vkcap_GetInstanceProcAddr;
  v->pfnGetDeviceProcAddr = vkcap_GetDeviceProcAddr;
  v->pfnGetPhysicalDeviceProcAddr = NULL;
  return VK_SUCCESS;
}
