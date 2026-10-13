// A live child checkpoint after each successful Vulkan instance creation.
// The parent verifies the mapped validation library before this layer returns.
#include <vulkan/vulkan.h>
#include <vulkan/vk_layer.h>
#include <dlfcn.h>
#include <errno.h>
#include <limits.h>
#include <poll.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define EXPORT __attribute__((visibility("default")))
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



typedef struct Instance {
  void* key;
  PFN_vkGetInstanceProcAddr gipa;
  PFN_vkCreateDevice create_device;
  struct Instance* next;
} Instance;
typedef struct Device {
  void* key;
  PFN_vkGetDeviceProcAddr gdpa;
  struct Device* next;
} Device;
static Instance* instances;
static Device* devices;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t checkpoint_lock = PTHREAD_MUTEX_INITIALIZER;
static void* key_of(const void* p) { return p ? *(void* const*)p : NULL; }
static PFN_vkGetInstanceProcAddr instance_gipa(const void* p) {
  PFN_vkGetInstanceProcAddr result = NULL;
  pthread_mutex_lock(&lock);
  for (Instance* i = instances; i; i = i->next) if (i->key == key_of(p)) { result = i->gipa; break; }
  pthread_mutex_unlock(&lock);
  return result;
}
static PFN_vkGetDeviceProcAddr device_gdpa(const void* p) {
  PFN_vkGetDeviceProcAddr result = NULL;
  pthread_mutex_lock(&lock);
  for (Device* d = devices; d; d = d->next) if (d->key == key_of(p)) { result = d->gdpa; break; }
  pthread_mutex_unlock(&lock);
  return result;
}
static PFN_vkCreateDevice instance_create_device(const void* p) {
  PFN_vkCreateDevice result = NULL;
  pthread_mutex_lock(&lock);
  for (Instance* i = instances; i; i = i->next) if (i->key == key_of(p)) { result = i->create_device; break; }
  pthread_mutex_unlock(&lock);
  return result;
}
static int fail(const char* why) {
  fprintf(stderr, "SA2_VALIDATION_PROBE_FAIL %s\n", why);
  return 0;
}
static int json_string(char* out, size_t capacity, const char* value) {
  size_t n = 0;
  if (capacity < 3) return 0;
  out[n++] = '"';
  for (; *value; ++value) {
    unsigned char c = (unsigned char)*value;
    if (c < 32) return 0;
    if (c == '\\' || c == '"') { if (n + 2 >= capacity) return 0; out[n++] = '\\'; }
    if (n + 1 >= capacity) return 0;
    out[n++] = (char)c;
  }
  out[n++] = '"'; out[n] = 0;
  return 1;
}
static int acknowledged(int fd) {
  static int refused;
  if (refused) return 0;
  struct timespec deadline, now;
  if (clock_gettime(CLOCK_MONOTONIC, &deadline)) goto refusal;
  deadline.tv_sec += 30;
  struct pollfd wait = {fd, POLLIN, 0};
  for (;;) {
    if (clock_gettime(CLOCK_MONOTONIC, &now)) goto refusal;
    int64_t remaining = (int64_t)(deadline.tv_sec - now.tv_sec) * 1000000000 +
                        deadline.tv_nsec - now.tv_nsec;
    if (remaining <= 0) goto refusal;
    int milliseconds = (int)((remaining + 999999) / 1000000);
    int ready = poll(&wait, 1, milliseconds);
    if (ready < 0 && errno == EINTR) continue;
    if (ready != 1 || clock_gettime(CLOCK_MONOTONIC, &now) ||
        now.tv_sec > deadline.tv_sec ||
        (now.tv_sec == deadline.tv_sec && now.tv_nsec >= deadline.tv_nsec)) goto refusal;
    char answer[2];
    if ((wait.revents & POLLIN) && recv(fd, answer, sizeof answer, 0) == 1 && answer[0] == 'Y') return 1;
    goto refusal;
  }
refusal:
  refused = 1;
  shutdown(fd, SHUT_RDWR);
  return 0;
}
static int checkpoint(VkInstance instance) {
  const char* fd_text = getenv("SA2_VALIDATION_OBSERVER_FD");
  const char* wanted = getenv("SA2_VALIDATION_LIBRARY");
  if (!fd_text || !wanted) return fail("missing checkpoint descriptor or library");
  char* end = NULL;
  errno = 0;
  long fd_number = strtol(fd_text, &end, 10);
  if (errno || *end || fd_number < 0 || fd_number > INT_MAX) return fail("invalid checkpoint descriptor");
  int fd = (int)fd_number;
  char actual[PATH_MAX];
  if (!realpath(wanted, actual)) return fail("library realpath");
  FILE* maps = fopen("/proc/self/maps", "r");
  if (!maps) return fail("self maps");
  char* line = NULL; size_t line_cap = 0; int found = 0, conflicting = 0;
  while (getline(&line, &line_cap, maps) >= 0) {
    unsigned long long start, finish, offset, inode;
    unsigned int major, minor; char permissions[8]; int consumed = 0;
    if (sscanf(line, "%llx-%llx %7s %llx %x:%x %llu %n", &start, &finish, permissions, &offset,
               &major, &minor, &inode, &consumed) != 7) continue;
    char* path = line + consumed;
    path[strcspn(path, "\n")] = 0;
    if (!strcmp(path, actual)) found = 1;
    if (strstr(path, "libVkLayer_khronos_validation") && strcmp(path, actual)) conflicting = 1;
  }
  free(line); fclose(maps);
  if (!found || conflicting) return fail("missing or conflicting already-mapped validation library");
  // RTLD_NOLOAD cannot manufacture the mapping the checkpoint is meant to prove.
  void* library = dlopen(actual, RTLD_NOW | RTLD_NOLOAD | RTLD_LOCAL);
  if (!library) return fail("mapped library is not already loaded");
  PFN_vkEnumerateInstanceLayerProperties enumerate = (PFN_vkEnumerateInstanceLayerProperties)
      dlsym(library, "vkEnumerateInstanceLayerProperties");
  Dl_info symbol; char owner[PATH_MAX]; struct stat status, owner_status;
  int ok = enumerate && dladdr((void*)enumerate, &symbol) && realpath(symbol.dli_fname, owner) &&
           !strcmp(owner, actual) && stat(actual, &status) == 0 && stat(owner, &owner_status) == 0 &&
           status.st_dev == owner_status.st_dev && status.st_ino == owner_status.st_ino && S_ISREG(status.st_mode);
  uint32_t count = 0; VkLayerProperties properties[8];
  if (!ok || enumerate(&count, NULL) != VK_SUCCESS || count == 0 || count > 8) {
    dlclose(library); return fail("actual mapped library enumeration export");
  }
  uint32_t capacity = count;
  if (enumerate(&capacity, properties) != VK_SUCCESS || capacity != count) {
    dlclose(library); return fail("actual mapped layer properties");
  }
  uint32_t api = 0; int names = 0;
  for (uint32_t i = 0; i < count; ++i) if (!strcmp(properties[i].layerName, "VK_LAYER_KHRONOS_validation")) {
    api = properties[i].specVersion; ++names;
  }
  if (names != 1 || VK_API_VERSION_VARIANT(api) != 0) { dlclose(library); return fail("actual mapped API name"); }
  FILE* input = fopen(actual, "rb");
  struct stat before, after;
  if (!input || fstat(fileno(input), &before) || before.st_dev != status.st_dev || before.st_ino != status.st_ino ||
      before.st_size <= 0 || (uintmax_t)before.st_size > SIZE_MAX) {
    if (input) fclose(input);
    dlclose(library); return fail("mapped file identity before hashing");
  }
  void* bytes = mmap(NULL, (size_t)before.st_size, PROT_READ, MAP_PRIVATE, fileno(input), 0);
  if (bytes == MAP_FAILED) { fclose(input); dlclose(library); return fail("mapped library bytes"); }
  char digest[65]; sha_hex(bytes, (size_t)before.st_size, digest);
  munmap(bytes, (size_t)before.st_size);
  ok = fstat(fileno(input), &after) == 0 && after.st_dev == before.st_dev && after.st_ino == before.st_ino &&
       after.st_size == before.st_size && after.st_mtim.tv_sec == before.st_mtim.tv_sec &&
       after.st_mtim.tv_nsec == before.st_mtim.tv_nsec;
  fclose(input);
  if (!ok) { dlclose(library); return fail("mapped library changed while hashing"); }
  char path_json[PATH_MAX * 2 + 3], packet[PATH_MAX * 2 + 1024];
  if (!json_string(path_json, sizeof path_json, actual)) { dlclose(library); return fail("path encoding"); }
  int length = snprintf(packet, sizeof packet,
    "{\"schema\":\"sa2-validation-live/v1\",\"pid\":%ld,\"instance\":\"%p\",\"realpath\":%s,"
    "\"device\":%ju,\"inode\":%ju,\"sha256\":\"%s\",\"api_version\":\"%u.%u.%u\"}",
    (long)getpid(), (void*)instance, path_json, (uintmax_t)status.st_dev, (uintmax_t)status.st_ino,
    digest, VK_API_VERSION_MAJOR(api), VK_API_VERSION_MINOR(api), VK_API_VERSION_PATCH(api));
  ok = length > 0 && (size_t)length < sizeof packet && send(fd, packet, (size_t)length, MSG_NOSIGNAL) == length;
  if (ok) ok = acknowledged(fd);
  dlclose(library);
  return ok ? 1 : fail("missing, refused or timed-out parent acknowledgement");
}

static VKAPI_ATTR VkResult VKAPI_CALL probe_CreateInstance(const VkInstanceCreateInfo* ci,
    const VkAllocationCallbacks* allocator, VkInstance* instance) {
  VkLayerInstanceCreateInfo* link = (VkLayerInstanceCreateInfo*)ci->pNext;
  while (link && !(link->sType == VK_STRUCTURE_TYPE_LOADER_INSTANCE_CREATE_INFO && link->function == VK_LAYER_LINK_INFO))
    link = (VkLayerInstanceCreateInfo*)link->pNext;
  if (!link || !link->u.pLayerInfo) return VK_ERROR_INITIALIZATION_FAILED;
  PFN_vkGetInstanceProcAddr next = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
  link->u.pLayerInfo = link->u.pLayerInfo->pNext;
  PFN_vkCreateInstance create = (PFN_vkCreateInstance)next(NULL, "vkCreateInstance");
  if (!create) return VK_ERROR_INITIALIZATION_FAILED;
  VkResult result = create(ci, allocator, instance);
  if (result != VK_SUCCESS) return result;
  Instance* entry = calloc(1, sizeof *entry);
  pthread_mutex_lock(&checkpoint_lock);
  int verified = checkpoint(*instance);
  pthread_mutex_unlock(&checkpoint_lock);
  if (!entry || !verified) {
    free(entry);
    PFN_vkDestroyInstance destroy = (PFN_vkDestroyInstance)next(*instance, "vkDestroyInstance");
    if (destroy) destroy(*instance, allocator);
    *instance = VK_NULL_HANDLE;
    return VK_ERROR_INITIALIZATION_FAILED;
  }
  entry->key = key_of(*instance); entry->gipa = next;
  entry->create_device = (PFN_vkCreateDevice)next(*instance, "vkCreateDevice");
  pthread_mutex_lock(&lock); entry->next = instances; instances = entry; pthread_mutex_unlock(&lock);
  return result;
}
static VKAPI_ATTR void VKAPI_CALL probe_DestroyInstance(VkInstance instance, const VkAllocationCallbacks* allocator) {
  PFN_vkGetInstanceProcAddr next = instance_gipa(instance);
  PFN_vkDestroyInstance destroy = next ? (PFN_vkDestroyInstance)next(instance, "vkDestroyInstance") : NULL;
  pthread_mutex_lock(&lock);
  for (Instance** i = &instances; *i; i = &(*i)->next) if ((*i)->key == key_of(instance)) {
    Instance* old = *i; *i = old->next; free(old); break;
  }
  pthread_mutex_unlock(&lock);
  if (destroy) destroy(instance, allocator);
}
static VKAPI_ATTR VkResult VKAPI_CALL probe_CreateDevice(VkPhysicalDevice physical, const VkDeviceCreateInfo* ci,
    const VkAllocationCallbacks* allocator, VkDevice* device) {
  VkLayerDeviceCreateInfo* link = (VkLayerDeviceCreateInfo*)ci->pNext;
  while (link && !(link->sType == VK_STRUCTURE_TYPE_LOADER_DEVICE_CREATE_INFO && link->function == VK_LAYER_LINK_INFO))
    link = (VkLayerDeviceCreateInfo*)link->pNext;
  if (!link || !link->u.pLayerInfo) return VK_ERROR_INITIALIZATION_FAILED;
  PFN_vkGetInstanceProcAddr gipa = link->u.pLayerInfo->pfnNextGetInstanceProcAddr;
  PFN_vkGetDeviceProcAddr gdpa = link->u.pLayerInfo->pfnNextGetDeviceProcAddr;
  link->u.pLayerInfo = link->u.pLayerInfo->pNext;
  PFN_vkCreateDevice create = instance_create_device(physical);
  if (!create) create = (PFN_vkCreateDevice)gipa(VK_NULL_HANDLE, "vkCreateDevice");
  if (!create) return VK_ERROR_INITIALIZATION_FAILED;
  VkResult result = create(physical, ci, allocator, device);
  if (result != VK_SUCCESS) return result;
  Device* entry = calloc(1, sizeof *entry);
  if (!entry) {
    ((PFN_vkDestroyDevice)gdpa(*device, "vkDestroyDevice"))(*device, allocator);
    *device = VK_NULL_HANDLE; return VK_ERROR_OUT_OF_HOST_MEMORY;
  }
  entry->key = key_of(*device); entry->gdpa = gdpa;
  pthread_mutex_lock(&lock); entry->next = devices; devices = entry; pthread_mutex_unlock(&lock);
  return result;
}
static VKAPI_ATTR void VKAPI_CALL probe_DestroyDevice(VkDevice device, const VkAllocationCallbacks* allocator) {
  PFN_vkGetDeviceProcAddr next = device_gdpa(device);
  PFN_vkDestroyDevice destroy = next ? (PFN_vkDestroyDevice)next(device, "vkDestroyDevice") : NULL;
  pthread_mutex_lock(&lock);
  for (Device** d = &devices; *d; d = &(*d)->next) if ((*d)->key == key_of(device)) {
    Device* old = *d; *d = old->next; free(old); break;
  }
  pthread_mutex_unlock(&lock);
  if (destroy) destroy(device, allocator);
}
static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL probe_GetDeviceProcAddr(VkDevice device, const char* name) {
  if (!strcmp(name, "vkDestroyDevice")) return (PFN_vkVoidFunction)probe_DestroyDevice;
  if (!strcmp(name, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)probe_GetDeviceProcAddr;
  PFN_vkGetDeviceProcAddr next = device_gdpa(device);
  return next ? next(device, name) : NULL;
}
static VKAPI_ATTR PFN_vkVoidFunction VKAPI_CALL probe_GetInstanceProcAddr(VkInstance instance, const char* name) {
  if (!strcmp(name, "vkCreateInstance")) return (PFN_vkVoidFunction)probe_CreateInstance;
  if (!strcmp(name, "vkDestroyInstance")) return (PFN_vkVoidFunction)probe_DestroyInstance;
  if (!strcmp(name, "vkCreateDevice")) return (PFN_vkVoidFunction)probe_CreateDevice;
  if (!strcmp(name, "vkDestroyDevice")) return (PFN_vkVoidFunction)probe_DestroyDevice;
  if (!strcmp(name, "vkGetInstanceProcAddr")) return (PFN_vkVoidFunction)probe_GetInstanceProcAddr;
  if (!strcmp(name, "vkGetDeviceProcAddr")) return (PFN_vkVoidFunction)probe_GetDeviceProcAddr;
  PFN_vkGetInstanceProcAddr next = instance_gipa(instance);
  return next ? next(instance, name) : NULL;
}
EXPORT VKAPI_ATTR VkResult VKAPI_CALL vkNegotiateLoaderLayerInterfaceVersion(VkNegotiateLayerInterface* version) {
  if (version->loaderLayerInterfaceVersion > 2) version->loaderLayerInterfaceVersion = 2;
  version->pfnGetInstanceProcAddr = probe_GetInstanceProcAddr;
  version->pfnGetDeviceProcAddr = probe_GetDeviceProcAddr;
  version->pfnGetPhysicalDeviceProcAddr = NULL;
  return VK_SUCCESS;
}
